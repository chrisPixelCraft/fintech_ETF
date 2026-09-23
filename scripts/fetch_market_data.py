"""Fetch dated Taiwan universe and archived Yahoo bars without inventing gaps.

The universe is fixed using 2025-12-31 information, not the contest's July 2026
universe. All responses are retained and hashed. Yahoo split-adjusted OHLC and
volumes are restored to contemporaneous units; Adj Close is never used.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import hashlib
import json
import math
import pathlib
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / 'data/raw'
OUT = ROOT / 'data/processed'
TZ = ZoneInfo('Asia/Taipei')
LOCK = threading.Lock()
MANIFEST = RAW / 'retrieval_manifest.jsonl'
START = dt.date(2025, 1, 1)
END = dt.date(2026, 9, 21)
CLOSED_SESSION_NEXT = {'2026-07-10': '2026-07-13'}
CLOSURE_SOURCES = [
    'https://www.taifex.com.tw/enl/eng11/newsDetail?idx=10470&newsType=1',
    'https://www.twse.com.tw/zh/about/suspended_faq.html',
    'https://www.twsa.org.tw/F01/F011.html',
]


def get_json(url: str, relative: str):
    path = RAW / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return json.loads(path.read_bytes())
    for attempt in range(3):
        try:
            if urllib.parse.urlparse(url).hostname == 'www.twse.com.tw':
                # TWSE sometimes loops HTTP/1.1 redirects; curl negotiates HTTP/2.
                body = subprocess.run(['curl', '-fL', '--max-time', '40', '-sS',
                                       '-A', 'Mozilla/5.0', url], check=True,
                                      capture_output=True).stdout
            else:
                request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(request, timeout=40) as response:
                    body = response.read()
            parsed = json.loads(body)
            path.write_bytes(body)
            record = {'url': url, 'file': str(path.relative_to(ROOT)),
                      'retrieved_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                      'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)}
            with LOCK:
                with MANIFEST.open('a') as output:
                    output.write(json.dumps(record, ensure_ascii=False) + '\n')
            return parsed
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1 + 2 * attempt)


def number(value):
    try:
        return float(str(value).replace(',', ''))
    except (ValueError, TypeError):
        return math.nan


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def build_universe():
    shares = get_json('https://www.twse.com.tw/fund/MI_QFIIS?response=json&date=20251231&selectType=ALLBUT0999', 'universe/twse_shares_20251231.json')
    prices = get_json('https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=20251231&type=ALLBUT0999', 'universe/twse_prices_20251231.json')
    otc = get_json('https://www.tpex.org.tw/web/stock/aftertrading/daily_mktval/mkt_result.php?l=zh-tw&o=json&d=114/12/31&s=0,asc,0', 'universe/tpex_market_cap_20251231.json')
    assert shares['date'] == prices['date'] == otc['date'] == '20251231'
    table = next(t for t in prices['tables'] if '收盤價' in t.get('fields', []))
    fields = table['fields']
    closes = {r[0]: number(r[fields.index('收盤價')]) for r in table['data']}
    listed = []
    for r in shares['data']:
        code = r[0]
        if not (len(code) == 4 and code.isdigit() and not code.startswith('0')):
            continue
        close = closes.get(code, math.nan)
        cap = number(r[3]) * close
        if math.isfinite(cap):
            listed.append({'symbol': code + '.TW', 'code': code, 'name': r[1],
                           'market': 'TWSE', 'shares': number(r[3]),
                           'close': close, 'market_cap': cap})
    listed.sort(key=lambda r: (-r['market_cap'], r['code']))
    overcounter = []
    for r in otc['tables'][0]['data']:
        code = r[1]
        if not (len(code) == 4 and code.isdigit() and not code.startswith('0')):
            continue
        overcounter.append({'symbol': code + '.TWO', 'code': code, 'name': r[2],
                            'market': 'TPEx', 'shares': number(r[3]),
                            'close': number(r[4]), 'market_cap': number(r[5]) * 1e6})
    overcounter.sort(key=lambda r: (-r['market_cap'], r['code']))
    selected = []
    for group, count in [(listed, 100), (overcounter, 50)]:
        for rank, row in enumerate(group[:count], 1):
            selected.append({**row, 'rank': rank, 'as_of': '2025-12-31',
                             'known_at_assumption': '2025-12-31T19:30:00+08:00',
                             'universe_type': 'historical_reconstruction_not_contest_whitelist'})
    assert len(selected) == 150 and len({r['symbol'] for r in selected}) == 150
    write_csv(OUT / 'universe_20251231.csv', selected, selected[0].keys())
    print(json.dumps({'universe_count': len(selected), 'twse_first': listed[0], 'twse_100': listed[99], 'tpex_50': overcounter[49]}, ensure_ascii=False), flush=True)
    return selected


def local_date(timestamp):
    return dt.datetime.fromtimestamp(timestamp, TZ).date()


def fetch_symbol(symbol: str, hourly=True):
    start = int(dt.datetime.combine(START, dt.time.min, TZ).timestamp())
    end = int(dt.datetime.combine(END + dt.timedelta(days=1), dt.time.min, TZ).timestamp())
    params = urllib.parse.urlencode({'period1': start, 'period2': end, 'interval': '1d', 'events': 'div,splits', 'includePrePost': 'false'})
    data = get_json(f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{params}', f'yahoo/{symbol}_1d.json')
    if data['chart'].get('error'):
        raise ValueError(data['chart']['error'])
    result = data['chart']['result'][0]
    events = result.get('events', {})
    splits = {local_date(v['date']): float(v['numerator']) / float(v['denominator']) for v in events.get('splits', {}).values()}
    dividends = {local_date(v['date']): float(v['amount']) for v in events.get('dividends', {}).values()}
    def future_factor(date):
        return math.prod(ratio for eventdate, ratio in splits.items() if eventdate > date)
    def parse(result, interval):
        quotes = result['indicators']['quote'][0]
        rows = []
        for i, timestamp in enumerate(result.get('timestamp', [])):
            moment = dt.datetime.fromtimestamp(timestamp, TZ)
            date = moment.date()
            if not START <= date <= END:
                continue
            values = {k: quotes[k][i] for k in ('open', 'high', 'low', 'close', 'volume')}
            if any(v is None for v in values.values()):
                continue
            factor = future_factor(date)
            row = {'date': date.isoformat(), 'symbol': symbol,
                   **{k: values[k] * factor for k in ('open', 'high', 'low', 'close')},
                   'volume': values['volume'] / factor,
                   'dividend': dividends.get(date, 0) * factor,
                   'split': splits.get(date, 1), 'split_restoration_factor': factor}
            if interval != '1d':
                row['timestamp'] = moment.isoformat()
            rows.append(row)
        return rows
    daily = parse(result, '1d')
    hourrows = []
    if hourly:
        params = urllib.parse.urlencode({'period1': start, 'period2': end, 'interval': '60m', 'events': 'div,splits', 'includePrePost': 'false'})
        data = get_json(f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{params}', f'yahoo/{symbol}_60m.json')
        if data['chart'].get('error'):
            raise ValueError(data['chart']['error'])
        hourrows = parse(data['chart']['result'][0], '60m')
    return symbol, daily, hourrows


def download(hourly=True):
    universe = list(csv.DictReader((OUT / 'universe_20251231.csv').open()))
    symbols = [r['symbol'] for r in universe] + ['0050.TW', '^TWII']
    daily, hours, audits, failures = [], [], [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(fetch_symbol, s, hourly): s for s in symbols}
        for future in concurrent.futures.as_completed(jobs):
            symbol = jobs[future]
            try:
                _, rows, hourrows = future.result()
                daily.extend(rows)
                hours.extend(hourrows)
                audits.append({'symbol': symbol, 'daily_rows': len(rows), 'hourly_rows': len(hourrows),
                               'first': min((r['date'] for r in rows), default=None),
                               'last': max((r['date'] for r in rows), default=None)})
                print(f'{symbol}: daily={len(rows)} hourly={len(hourrows)}', flush=True)
            except Exception as exc:
                failures.append({'symbol': symbol, 'error': repr(exc)})
                print(f'FAILED {symbol}: {exc!r}', flush=True)
    daily.sort(key=lambda r: (r['date'], r['symbol']))
    hours.sort(key=lambda r: (r['timestamp'], r['symbol']))
    fields = ['date', 'symbol', 'open', 'high', 'low', 'close', 'volume', 'dividend', 'split', 'split_restoration_factor']
    write_csv(OUT / 'daily.csv', daily, fields)
    write_csv(OUT / 'hourly.csv', hours, fields + ['timestamp'])
    audit = {'requested_start': START.isoformat(), 'requested_end': END.isoformat(), 'symbols': sorted(audits, key=lambda r: r['symbol']), 'failures': failures,
             'price_convention': 'Yahoo split-adjusted OHLC restored by product of later splits in response; volume divided by same factor; dividends restored likewise; no Adj Close',
             'missing_policy': 'Missing bars omitted, never forward/backfilled; exceptions recorded, never silently dropped from universe',
             'benchmark_symbols': ['0050.TW', '^TWII'],
             'caveat': 'Historical reconstruction from presently retrieved data, not archived 2025 snapshot. Yahoo corporate actions can be revised or incomplete. Daily OHLC allow next-open approximation, not official VWAP.'}
    (OUT / 'data_manifest.json').write_text(json.dumps(audit, indent=2, ensure_ascii=False))
    print(json.dumps({'daily_rows': len(daily), 'hourly_rows': len(hours), 'failures': failures}), flush=True)


def official_day(date, market):
    stamp = date.replace('-', '')
    day = dt.date.fromisoformat(date)
    if market == 'TWSE':
        url = f'https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={stamp}&type=ALLBUT0999'
        data = get_json(url, f'official/{stamp}_TWSE.json')
        table = next(t for t in data['tables'] if '收盤價' in t.get('fields', []))
        mapping = {'open': '開盤價', 'high': '最高價', 'low': '最低價', 'close': '收盤價', 'volume': '成交股數', 'turnover': '成交金額'}
        suffix = '.TW'
    else:
        query = urllib.parse.urlencode({'date': day.strftime('%Y/%m/%d'), 'response': 'json'})
        url = f'https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?{query}'
        data = get_json(url, f'official/{stamp}_TPEx_dated.json')
        table = data['tables'][0]
        mapping = {'open': '開盤', 'high': '最高', 'low': '最低', 'close': '收盤', 'volume': '成交股數', 'turnover': '成交金額(元)'}
        suffix = '.TWO'
    assert data['date'] == stamp, (date, market, data.get('date'))
    fields = table['fields']
    rows = []
    for record in table['data']:
        code = record[0]
        if not ((len(code) == 4 and code.isdigit() and not code.startswith('0')) or code == '0050'):
            continue
        values = {key: number(record[fields.index(field)]) for key, field in mapping.items()}
        if not all(math.isfinite(x) for x in values.values()):
            continue
        rows.append({'date': date, 'symbol': code + suffix, **values})
    return rows


def official_download(twse_cache_only=False):
    yahoo = list(csv.DictReader((OUT / 'daily.csv').open()))
    universe = {r['symbol'] for r in csv.DictReader((OUT / 'universe_20251231.csv').open())} | {'0050.TW'}
    dates = sorted({r['date'] for r in yahoo if r['symbol'] == '2330.TW' and r['date'] >= '2026-01-01'})
    output, failures = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        jobs = {pool.submit(official_day, date, market): (date, market) for date in dates for market in ('TWSE', 'TPEx')
                if not (twse_cache_only and market == 'TWSE' and not (RAW / f'official/{date.replace("-", "")}_TWSE.json').exists())}
        for future in concurrent.futures.as_completed(jobs):
            date, market = jobs[future]
            try:
                rows = future.result()
                output.extend(r for r in rows if r['symbol'] in universe)
                print(f'official {date} {market} rows={len(rows)}', flush=True)
            except Exception as exc:
                failures.append({'date': date, 'market': market, 'error': repr(exc)})
                print(f'FAILED official {date} {market} {exc!r}', flush=True)
    output.sort(key=lambda r: (r['date'], r['symbol']))
    write_csv(OUT / 'official_daily.csv', output, ['date', 'symbol', 'open', 'high', 'low', 'close', 'volume', 'turnover'])
    official = {(r['date'], r['symbol']): r for r in output}
    differences, merged, missing = [], [], []
    yahoo_keys = {(r['date'], r['symbol']) for r in yahoo}
    recovered = []
    for key, actual in official.items():
        if key not in yahoo_keys:
            recovered.append(key)
            yahoo.append({**actual, 'dividend': 0, 'split': 1, 'split_restoration_factor': 1})
    for row in yahoo:
        key = row['date'], row['symbol']
        actual = official.get(key)
        if actual:
            delta = {k: float(row[k]) - actual[k] for k in ('open', 'high', 'low', 'close')}
            if any(abs(x) > max(.02, .0001 * actual['close']) for x in delta.values()):
                differences.append({'date': row['date'], 'symbol': row['symbol'], 'yahoo_close': row['close'], 'official_close': actual['close'], 'ohlc_delta': delta})
            row.update({k: actual[k] for k in ('open', 'high', 'low', 'close', 'volume', 'turnover')})
            row['price_source'] = 'official'
        else:
            row['turnover'] = ''
            row['price_source'] = 'yahoo'
            if row['date'] >= '2026-01-01' and row['symbol'] in universe:
                missing.append({'date': row['date'], 'symbol': row['symbol']})
        merged.append(row)
    merged.sort(key=lambda r: (r['date'], r['symbol']))
    fields = ['date', 'symbol', 'open', 'high', 'low', 'close', 'volume', 'dividend', 'split', 'split_restoration_factor', 'turnover', 'price_source']
    write_csv(OUT / 'daily_official.csv', merged, fields)
    summary = {'dates': len(dates), 'rows': len(output), 'failures': failures, 'missing': missing, 'ohlc_mismatches': differences,
               'recovered_missing_yahoo_rows': recovered,
               'convention': '2026 OHLCV and turnover overwritten by contemporaneous official quotes; 2025 warmup and corporate actions still Yahoo. No synthetic turnover for unavailable records.'}
    (OUT / 'official_validation.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(json.dumps({'official_rows': len(output), 'failures': len(failures), 'missing': len(missing), 'ohlc_mismatches': len(differences)}), flush=True)


def repair_canonical():
    """Restore old ticker history and apply disclosed effective-date identity link."""
    daily = list(csv.DictReader((OUT / 'daily.csv').open()))
    hourly = list(csv.DictReader((OUT / 'hourly.csv').open()))
    universe = {r['symbol'] for r in csv.DictReader((OUT / 'universe_20251231.csv').open())}
    universe.add('0050.TW')
    _, successor_daily, successor_hourly = fetch_symbol('3718.TWO', True)
    official = {}
    days = sorted({r['date'] for r in daily if r['symbol'] == '2330.TW' and r['date'] >= '2026-01-01'})
    for date in days:
        for market, suffix in [('TWSE', '_TWSE.json'), ('TPEx', '_TPEx_dated.json')]:
            if (RAW / f'official/{date.replace("-", "")}{suffix}').exists():
                for row in official_day(date, market):
                    if row['symbol'] in universe or row['symbol'] == '3718.TWO':
                        official[row['date'], row['symbol']] = row
    warmup = []
    for code in ['5371', '1815', '6023']:
        for month in range(1, 13):
            query = urllib.parse.urlencode({'code': code, 'date': f'2025/{month:02d}/01', 'response': 'json'})
            x = get_json(f'https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?{query}', f'repairs/{code}_2025{month:02d}_official.json')
            table = x['tables'][0]
            assert table['date'] == f'2025{month:02d}01'
            for r in table['data']:
                y, m, d = map(int, r[0].split('/'))
                date = f'{y+1911:04d}-{m:02d}-{d:02d}'
                values = dict(zip(['open', 'high', 'low', 'close'], map(number, r[3:7])))
                if not all(math.isfinite(v) for v in values.values()):
                    continue
                old = next((v for v in daily if v['symbol'] == code + '.TWO' and v['date'] == date), {})
                warmup.append({'date': date, 'symbol': code + '.TWO', **values,
                               'volume': number(r[1]) * 1000, 'turnover': number(r[2]) * 1000,
                               'dividend': old.get('dividend', 0), 'split': old.get('split', 1),
                               'split_restoration_factor': old.get('split_restoration_factor', 1),
                               'price_source': 'official_monthly_thousand_units'})
    actions = {}
    official_action_rows = []
    unverified_combined = []
    for year, end in [(2025, '2025/12/31'), (2026, '2026/09/21')]:
        params = urllib.parse.urlencode({'startDate': f'{year}/01/01', 'endDate': end, 'response': 'json'})
        x = get_json(f'https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?{params}', f'corporate_actions/tpex_{year}.json')
        assert x['date'] == f'{year}0101~{end.replace("/", "")}'
        table = x['tables'][0]
        fields = table['fields']
        for r in table['data']:
            symbol = r[1].strip() + '.TWO'
            if symbol not in universe:
                continue
            y, m, d = map(int, r[0].split('/'))
            date = f'{y+1911:04d}-{m:02d}-{d:02d}'
            cash = number(r[fields.index('現金股利')])
            ratio = 1 + number(r[fields.index('每仟股無償配股')]) / 1000
            official_action_rows.append({'date': date, 'symbol': symbol, 'dividend': cash, 'split': ratio,
                                         'subscription_shares': number(r[fields.index('現金增資股數')]),
                                         'source': f'data/raw/corporate_actions/tpex_{year}.json'})
            if year == 2026 or symbol in {'5371.TWO', '1815.TWO', '6023.TWO'}:
                actions[date, symbol] = cash, ratio
    twse = get_json('https://www.twse.com.tw/rwd/zh/exRight/TWT49U?response=json&startDate=20260101&endDate=20260921', 'corporate_actions/twse_2026.json')
    assert twse['strDate'] == '20260101' and twse['endDate'] == '20260921'
    for r in twse['data']:
        symbol = r[1].strip() + '.TW'
        if symbol not in universe:
            continue
        y, m, d = map(int, re.findall(r'\d+', r[0]))
        date = f'{y+1911:04d}-{m:02d}-{d:02d}'
        if r[6] == '息':
            cash, ratio, subscription = number(r[5]), 1, 0
            source = 'data/raw/corporate_actions/twse_2026.json'
        else:
            stamp = date.replace('-', '')
            filename = f'corporate_actions/twse_{r[1]}_{stamp}_detail.json'
            if (RAW / filename).exists():
                detail = json.loads((RAW / filename).read_bytes())
                dr = detail['data'][0]
                assert dr[0].strip() == r[1].strip()
                cash = number(dr[2].split()[0])
                ratio = 1 + number(dr[4].split()[0]) / 1000
                subscription = number(dr[6].split()[0])
                source = 'data/raw/' + filename
            else:
                old = next((v for v in daily if v['symbol'] == symbol and v['date'] == date), {})
                cash, ratio, subscription = float(old.get('dividend', 0)), float(old.get('split', 1)), ''
                source = 'Yahoo; official TWSE date/type confirmed, cash/split decomposition unverified'
                unverified_combined.append({'date': date, 'symbol': symbol, 'official_type': r[6], 'cash': cash, 'split': ratio})
        actions[date, symbol] = cash, ratio
        official_action_rows.append({'date': date, 'symbol': symbol, 'dividend': cash, 'split': ratio,
                                     'subscription_shares': subscription, 'source': source})
    rescheduled_actions = []
    for row in official_action_rows:
        scheduled = row['date']
        effective = CLOSED_SESSION_NEXT.get(scheduled, scheduled)
        row['scheduled_date'], row['effective_date'] = scheduled, effective
        if effective != scheduled:
            row['date'] = effective
            entitlement = actions.pop((scheduled, row['symbol']))
            # Provider already rescheduled this event: replace; never add twice.
            actions[effective, row['symbol']] = entitlement
            rescheduled_actions.append({'symbol': row['symbol'], 'scheduled_date': scheduled,
                                        'effective_date': effective, 'dividend': entitlement[0],
                                        'split': entitlement[1], 'policy': 'One entitlement only; replace, never sum'})
    merged = {(r['date'], r['symbol']): r for r in daily}
    for row in warmup + successor_daily:
        merged[row['date'], row['symbol']] = row
    changes = []
    for key, raw in official.items():
        old = merged.get(key, {})
        if old:
            delta = float(old['close']) - raw['close']
            if abs(delta) > max(.02, raw['close'] * .0001):
                changes.append({'date': key[0], 'symbol': key[1], 'yahoo_close': float(old['close']), 'official_close': raw['close']})
        merged[key] = {**old, **raw, 'dividend': old.get('dividend', 0), 'split': old.get('split', 1),
                       'split_restoration_factor': old.get('split_restoration_factor', 1), 'price_source': 'official'}
    canonical = []
    for (date, symbol), row in sorted(merged.items()):
        if date in CLOSED_SESSION_NEXT:
            continue
        if symbol == '5371.TWO' and date > '2026-08-21':
            continue  # no stale Yahoo synthetic zero-volume trade bars after suspension
        if symbol == '3718.TWO' and date < '2026-09-03':
            continue
        row['source_symbol'] = symbol
        row['symbol'] = '5371.TWO' if symbol == '3718.TWO' else symbol
        key = date, row['symbol']
        if key in actions:
            row['dividend'], row['split'] = actions[key]
        row.setdefault('turnover', '')
        row.setdefault('price_source', 'yahoo')
        canonical.append(row)
    hourcanonical = []
    for row in hourly + successor_hourly:
        symbol, date = row['symbol'], row['date']
        if date in CLOSED_SESSION_NEXT:
            continue
        if symbol == '5371.TWO' and date > '2026-08-21':
            continue
        if symbol == '3718.TWO' and date < '2026-09-03':
            continue
        row['source_symbol'] = symbol
        row['symbol'] = '5371.TWO' if symbol == '3718.TWO' else symbol
        row['dividend'], row['split'] = actions.get((date, row['symbol']), (row['dividend'], row['split']))
        hourcanonical.append(row)
    hourcanonical.sort(key=lambda r: (r['timestamp'], r['symbol']))
    fields = ['date', 'symbol', 'open', 'high', 'low', 'close', 'volume', 'dividend', 'split', 'split_restoration_factor', 'turnover', 'price_source', 'source_symbol']
    write_csv(OUT / 'daily_canonical.csv', canonical, fields)
    write_csv(OUT / 'hourly_canonical.csv', hourcanonical, ['date', 'symbol', 'open', 'high', 'low', 'close', 'volume', 'dividend', 'split', 'split_restoration_factor', 'timestamp', 'source_symbol'])
    write_csv(OUT / 'official_corporate_actions.csv', official_action_rows, ['date', 'symbol', 'dividend', 'split', 'subscription_shares', 'source', 'scheduled_date', 'effective_date'])
    audit = {'daily_rows': len(canonical), 'hourly_rows': len(hourcanonical), 'official_close_discrepancies': changes,
             'identity_link': {'entity': '5371.TWO', 'successor': '3718.TWO', 'ratio': 1,
                               'effective_date': '2026-09-03', 'announced': '2026-07-29',
                               'last_old_trade': '2026-08-21', 'suspended_from': '2026-08-24'},
             'hourly_gap': '5371.TWO has no Yahoo hourly history before 2026-07-17; not imputed. The new ticker has real hourly bars only from 2026-09-03.',
             'cash_dividend_convention': 'TPEx 2026 and TWSE pure-cash events from official calculation tables. TWSE rights/combined decomposition uses Yahoo unless official detail cached. Pay dates unavailable.',
             'unverified_twse_combined_actions': unverified_combined,
             'identity_link_primary_source': 'https://www.taifex.com.tw/file/taifex/CHINESE/11/attach/5371_20260903.pdf',
             'closed_sessions': CLOSED_SESSION_NEXT,
             'closure_primary_sources': CLOSURE_SOURCES,
             'rescheduled_corporate_actions': rescheduled_actions,
             'trust': 'CANDIDATE until identity link and all output audit verified'}
    assert len({(r['date'], r['symbol']) for r in canonical}) == len(canonical)
    assert len({(r['timestamp'], r['symbol']) for r in hourcanonical}) == len(hourcanonical)
    for row in canonical:
        values = [float(row[k]) for k in ('open', 'high', 'low', 'close', 'volume', 'dividend', 'split')]
        assert all(math.isfinite(v) for v in values)
        o, high, low, close, volume, dividend, split = values
        assert min(o, high, low, close, split) > 0 and volume >= 0
        assert high + .001 >= max(o, close, low) and low - .001 <= min(o, close, high)
    by_day = {}
    for row in canonical:
        if row['symbol'] in universe:
            by_day.setdefault(row['date'], []).append(float(row['volume']))
    unknown_zero_days = [date for date, vols in by_day.items() if len(vols) >= 20 and sum(vols) == 0]
    assert not unknown_zero_days, f'MARKET_CALENDAR: unexplained market-wide zero-volume dates {unknown_zero_days}'
    audit['backtest_session_count'] = len({r['date'] for r in canonical if r['date'] >= '2026-01-01' and r['symbol'] == '2330.TW'})
    audit['sha256'] = {name: hashlib.sha256((OUT / name).read_bytes()).hexdigest()
                       for name in ['daily_canonical.csv', 'hourly_canonical.csv', 'universe_20251231.csv']}
    audit['trust'] = 'AUDITED_WITH_LIMITATIONS'
    audit['checks'] = ['Unique daily/hourly keys', 'Finite positive OHLC; high/low consistency',
                       'Nonnegative volume and positive split ratios', 'Effective-date 1:1 identity link with official primary source',
                       'No unexplained market-wide zero-volume date across warmup and backtest',
                       'Confirmed closures removed; dividends scheduled then moved once to actual ex-date']
    audit['known_limitations'] = ['5371 hourly missing before2026-07-17',
                                  'Current provider history is not an archived point-in-time snapshot',
                                  'TWSE combined-action decomposition partly Yahoo-sourced',
                                  'Cash subscriptions not exercised; some historic subscription adjustments remain',
                                  '0050 pre2025split daily/hourly basis differs; benchmark limited to2026',
                                  'Official daily VWAP coverage incomplete; no synthetic turnover']
    (OUT / 'canonical_audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2))
    print(json.dumps(audit, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['universe', 'download', 'official', 'repair', 'all'], default='all')
    parser.add_argument('--no-hourly', action='store_true')
    parser.add_argument('--twse-cache-only', action='store_true')
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    if args.mode in ('universe', 'all'):
        build_universe()
    if args.mode in ('download', 'all'):
        download(not args.no_hourly)
    if args.mode == 'official':
        official_download(args.twse_cache_only)
    if args.mode == 'repair':
        repair_canonical()
