"""Isolated 2025-to-now input acquisition; never writes original run artifacts."""
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
BASE = ROOT / 'data/extended'
RAW = BASE / 'raw'
OUT = BASE / 'processed'
TZ = ZoneInfo('Asia/Taipei')
START = dt.date(2024, 1, 1)
HOURLY_START = dt.date(2024, 10, 1)
END = dt.date(2026, 9, 21)
LOCK = threading.Lock()
CLOSED = {'2024-07-24': '2024-07-26', '2024-07-25': '2024-07-26', '2026-07-10': '2026-07-13'}
SPECIAL_EVENTS = [
    {'symbol': '3481.TW', 'halt_start': '2024-08-15', 'resume': '2024-08-26', 'split': .88, 'cash_per_old_share': 1.2, 'type': 'capital_reduction_refund', 'source': 'https://www.taifex.com.tw/file/taifex/CHINESE/11/attach/3481_20240826.pdf'},
    {'symbol': '2371.TW', 'halt_start': '2025-06-12', 'resume': '2025-06-23', 'split': .95, 'cash_per_old_share': .5, 'type': 'capital_reduction_refund', 'source': 'https://www.taifex.com.tw/file/taifex/CHINESE/11/attach/2371_20250623.pdf'},
    {'symbol': '2327.TW', 'halt_start': '2025-08-14', 'resume': '2025-08-25', 'split': 4., 'cash_per_old_share': 0., 'type': 'face_value_split', 'source': 'https://www.taifex.com.tw/file/taifex/CHINESE/11/attach/2327_20250825.pdf'},
    {'symbol': '5904.TWO', 'halt_start': '2026-07-30', 'resume': '2026-08-10', 'split': 10., 'cash_per_old_share': 0., 'type': 'face_value_split', 'source': 'https://www.taifex.com.tw/file/taifex/CHINESE/11/attach/5904_20260810.pdf'},
]


def number(value):
    try:
        return float(str(value).replace(',', '').split()[0])
    except (ValueError, TypeError, IndexError):
        return math.nan


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def get(url, relative, reuse=None):
    path = RAW / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return json.loads(path.read_bytes())
    if reuse and pathlib.Path(reuse).exists():
        body = pathlib.Path(reuse).read_bytes()
    else:
        time.sleep(.35)
        if urllib.parse.urlparse(url).hostname == 'www.twse.com.tw':
            body = subprocess.run(['curl', '-fL', '--max-time', '45', '-sS', '-A', 'Mozilla/5.0', url], capture_output=True, check=True).stdout
        else:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=45) as response:
                body = response.read()
    parsed = json.loads(body)
    path.write_bytes(body)
    record = {'url': url, 'file': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(body).hexdigest(),
              'retrieved_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'reused_from': str(reuse) if reuse else None}
    with LOCK:
        with (RAW / 'retrieval_manifest.jsonl').open('a') as out:
            out.write(json.dumps(record, ensure_ascii=False) + '\n')
    return parsed


def universe():
    s = get('https://www.twse.com.tw/fund/MI_QFIIS?response=json&date=20241231&selectType=ALLBUT0999', 'universe/twse_shares_20241231.json', '/tmp/extended_twse_shares_20241231.json')
    p = get('https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=20241231&type=ALLBUT0999', 'universe/twse_prices_20241231.json', '/tmp/extended_twse_prices_20241231.json')
    t = get('https://www.tpex.org.tw/web/stock/aftertrading/daily_mktval/mkt_result.php?l=zh-tw&o=json&d=113/12/31&s=0,asc,0', 'universe/tpex_cap_20241231.json', '/tmp/extended_tpex_cap_20241231.json')
    assert s['date'] == p['date'] == t['date'] == '20241231'
    table = next(x for x in p['tables'] if '收盤價' in x.get('fields', []))
    close = {r[0]: number(r[8]) for r in table['data']}
    groups = [[], []]
    for r in s['data']:
        if not re.fullmatch('[1-9][0-9]{3}', r[0]):
            continue
        cap = number(r[3]) * close.get(r[0], math.nan)
        if math.isfinite(cap):
            groups[0].append({'symbol': r[0] + '.TW', 'code': r[0], 'name': r[1], 'market': 'TWSE', 'shares': number(r[3]), 'close': close[r[0]], 'market_cap': cap})
    for r in t['tables'][0]['data']:
        if re.fullmatch('[1-9][0-9]{3}', r[1]):
            groups[1].append({'symbol': r[1] + '.TWO', 'code': r[1], 'name': r[2], 'market': 'TPEx', 'shares': number(r[3]), 'close': number(r[4]), 'market_cap': number(r[5]) * 1e6})
    selected = []
    for group, n in zip(groups, [100, 50]):
        group.sort(key=lambda r: (-r['market_cap'], r['symbol']))
        for rank, r in enumerate(group[:n], 1):
            selected.append({**r, 'rank': rank, 'as_of': '2024-12-31', 'known_at_assumption': '2024-12-31T19:30:00+08:00', 'universe_type': 'historical_reconstruction_not_contest_whitelist'})
    assert len(selected) == len({r['symbol'] for r in selected}) == 150
    write_csv(OUT / 'universe_20241231.csv', selected, selected[0].keys())
    print('PIT universe saved:150 stocks,2024-12-31', flush=True)


def chart(symbol, interval):
    startdate = HOURLY_START if interval == '60m' else START
    p1 = int(dt.datetime.combine(startdate, dt.time.min, TZ).timestamp())
    p2 = int(dt.datetime.combine(END + dt.timedelta(days=1), dt.time.min, TZ).timestamp())
    params = urllib.parse.urlencode({'period1': p1, 'period2': p2, 'interval': interval, 'events': 'div,splits', 'includePrePost': 'false'})
    x = get(f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?{params}', f'yahoo/{symbol}_{interval}.json')
    if x['chart'].get('error'):
        raise RuntimeError(x['chart']['error'])
    return x['chart']['result'][0]


def parse_chart(symbol, result, daily_result, hourly=False):
    def localdate(ts):
        return dt.datetime.fromtimestamp(ts, TZ).date()
    events = daily_result.get('events', {})
    splits = {localdate(v['date']): v['numerator'] / v['denominator'] for v in events.get('splits', {}).values()}
    dividends = {localdate(v['date']): v['amount'] for v in events.get('dividends', {}).values()}
    quotes = result['indicators']['quote'][0]
    rows = []
    for i, ts in enumerate(result.get('timestamp', [])):
        moment = dt.datetime.fromtimestamp(ts, TZ)
        day = moment.date()
        values = {k: quotes[k][i] for k in ('open', 'high', 'low', 'close', 'volume')}
        if any(v is None for v in values.values()) or not START <= day <= END:
            continue
        factor = math.prod(r for date, r in splits.items() if date > day)
        row = {'date': day.isoformat(), 'symbol': symbol, **{k: values[k] * factor for k in ('open', 'high', 'low', 'close')},
               'volume': values['volume'] / factor, 'dividend': dividends.get(day, 0) * factor,
               'split': splits.get(day, 1), 'split_restoration_factor': factor}
        if hourly:
            row['timestamp'] = moment.isoformat()
        rows.append(row)
    return rows


def one_symbol(symbol):
    d = chart(symbol, '1d')
    daily = parse_chart(symbol, d, d)
    try:
        h = chart(symbol, '60m')
        hourly = parse_chart(symbol, h, d, True)
        error = None
    except Exception as exc:
        hourly, error = [], repr(exc)
    return daily, hourly, error


def download():
    symbols = [r['symbol'] for r in csv.DictReader((OUT / 'universe_20241231.csv').open())] + ['0050.TW', '^TWII', '3718.TWO']
    daily, hourly, failures, audit = [], [], [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {pool.submit(one_symbol, s): s for s in symbols}
        for future in concurrent.futures.as_completed(jobs):
            symbol = jobs[future]
            try:
                d, h, error = future.result()
                daily.extend(d); hourly.extend(h)
                audit.append({'symbol': symbol, 'daily_count': len(d), 'hourly_count': len(h), 'first_daily': min((r['date'] for r in d), default=None), 'last_daily': max((r['date'] for r in d), default=None), 'first_hourly': min((r['date'] for r in h), default=None), 'hourly_error': error})
                print(symbol, len(d), len(h), error or '', flush=True)
            except Exception as exc:
                failures.append({'symbol': symbol, 'error': repr(exc)})
                print('FAILED', symbol, repr(exc), flush=True)
    daily.sort(key=lambda r: (r['date'], r['symbol']))
    hourly.sort(key=lambda r: (r['timestamp'], r['symbol']))
    fields = ['date', 'symbol', 'open', 'high', 'low', 'close', 'volume', 'dividend', 'split', 'split_restoration_factor']
    write_csv(OUT / 'daily_yahoo.csv', daily, fields)
    write_csv(OUT / 'hourly_yahoo.csv', hourly, fields + ['timestamp'])
    (OUT / 'provider_coverage.json').write_text(json.dumps({'symbols': audit, 'failures': failures, 'daily_start': str(START), 'hourly_start': str(HOURLY_START), 'end': str(END)}, indent=2, ensure_ascii=False))
    print('Downloads complete',len(daily),len(hourly),failures, flush=True)


def source_repairs():
    """Fetch bounded, dated official action tables and lost-ticker daily histories."""
    for year in (2024, 2025, 2026):
        end = '2026/09/21' if year == 2026 else f'{year}/12/31'
        params = urllib.parse.urlencode({'startDate': f'{year}/01/01', 'endDate': end, 'response': 'json'})
        get(f'https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?{params}', f'actions/tpex_{year}.json', ROOT / f'data/raw/corporate_actions/tpex_{year}.json')
        url = f'https://www.twse.com.tw/rwd/zh/exRight/TWT49U?response=json&startDate={year}0101&endDate={end.replace("/", "")}'
        get(url, f'actions/twse_{year}.json', ROOT / f'data/raw/corporate_actions/twse_{year}.json')
        print('Actions', year, flush=True)
    for code, market, periods in [('2888', 'TWSE', [(y, m) for y in (2024, 2025) for m in range(1, 13) if y == 2024 or m <= 7]),
                                  ('5371', 'TPEx', [(2024, m) for m in range(1, 13)]),
                                  ('6589', 'TPEx', [(y, m) for y in (2024, 2025) for m in range(1, 13) if y == 2024 or m <= 7]),
                                  ('0050', 'TWSE', [(2024, 1), (2024, 12), (2025, 2), (2025, 6)])]:
        for year, month in periods:
            if market == 'TPEx':
                query = urllib.parse.urlencode({'code': code, 'date': f'{year}/{month:02d}/01', 'response': 'json'})
                url = f'https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?{query}'
            else:
                url = f'https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={year}{month:02d}01&stockNo={code}'
            get(url, f'repairs/{code}_{year}{month:02d}_{market}.json')
            print('Repair', code, year, month, flush=True)
    errors = []
    try:
        h = chart('2888.TW', '60m')
        print('Unexpected 2888 hourly available', len(h.get('timestamp', [])), flush=True)
    except Exception as exc:
        errors.append({'symbol': '2888.TW', 'interval': '60m', 'error': repr(exc), 'policy': 'No fabricated hourly history; preserve universe member and official daily history.'})
    d, h, error = one_symbol('6589.TW')
    fields = ['date', 'symbol', 'open', 'high', 'low', 'close', 'volume', 'dividend', 'split', 'split_restoration_factor']
    write_csv(OUT / 'transferred_daily.csv', d, fields)
    write_csv(OUT / 'transferred_hourly.csv', h, fields + ['timestamp'])
    (OUT / 'unavailable_intraday.json').write_text(json.dumps(errors, indent=2))
    print('Transferred 6589', len(d), len(h), error, flush=True)


def detail_repairs():
    errors = []
    selected = {r['symbol'] for r in csv.DictReader((OUT / 'universe_20241231.csv').open())} | {'0050.TW', '6589.TW'}
    for year in (2024, 2025, 2026):
        bulk = json.loads((RAW / f'actions/twse_{year}.json').read_text())
        requests = [r for r in bulk['data'] if r[1].strip() + '.TW' in selected and r[6] != '息']
        for row in requests:
            yy, mm, dd = map(int, re.findall(r'\d+', row[0]))
            stamp = f'{yy+1911:04d}{mm:02d}{dd:02d}'
            code = row[1].strip()
            try:
                x = get(f'https://www.twse.com.tw/rwd/zh/exRight/TWT49UDetail?response=json&STK_NO={code}&T1={stamp}',
                        f'actions/detail_{code}_{stamp}.json', ROOT / f'data/raw/corporate_actions/twse_{code}_{stamp}_detail.json')
                assert x.get('data'), x
                print('Detail', code, stamp, x['data'][0], flush=True)
            except Exception as exc:
                errors.append({'code': code, 'date': stamp, 'error': repr(exc)})
                print('Detail failed', code, stamp, repr(exc), flush=True)
    (OUT / 'action_detail_failures.json').write_text(json.dumps(errors, ensure_ascii=False, indent=2))


def unit_repairs():
    for code, market, periods in [('6023', 'TPEx', [(2024, m) for m in range(1, 13)]),
                                  ('3260', 'TPEx', [(2025, 2), (2025, 3)]),
                                  ('3037', 'TWSE', [(2025, 11)]),
                                  ('3362', 'TPEx', [(2026, 4)]),
                                  ('6561', 'TPEx', [(2024, 10)])]:
        for year, month in periods:
            if market == 'TPEx':
                query = urllib.parse.urlencode({'code': code, 'date': f'{year}/{month:02d}/01', 'response': 'json'})
                url = f'https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock?{query}'
            else:
                url = f'https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date={year}{month:02d}01&stockNo={code}'
            get(url, f'repairs/{code}_{year}{month:02d}_{market}.json')
            print('Unit repair', code, year, month, flush=True)


def gap_repairs():
    """Recover one provider-wide missing session; respect each source's limits."""
    get('https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=2025%2F08%2F01&response=json',
        'gap_20250801/tpex.json', '/tmp/extended_tpex_20250801.json')
    members = list(csv.DictReader((OUT / 'universe_20241231.csv').open()))
    symbols = [r['symbol'] for r in members if r['market'] == 'TWSE' and r['symbol'] != '2888.TW'] + ['6589.TW']
    for symbol in symbols:
        code = symbol.split('.')[0]
        # FinMind documents 300 unauthenticated requests/hour. This bounded100
        # request repair uses public single-stock access, not paid whole-market API.
        params = urllib.parse.urlencode({'dataset': 'TaiwanStockPrice', 'data_id': code, 'start_date': '2025-07-31', 'end_date': '2025-08-04'})
        x = get(f'https://api.finmindtrade.com/api/v4/data?{params}', f'gap_20250801/finmind_{code}.json',
                '/tmp/extended_finmind_2330_probe.json' if code == '2330' else None)
        assert x.get('status') == 200 and any(r['date'] == '2025-08-01' for r in x['data']), (symbol, x)
        print('Gap recovery', symbol, len(x['data']), flush=True)


def zero_quote_audit():
    import pandas as pd
    daily = pd.read_csv(OUT / 'daily_canonical.csv')
    candidates = RAW / 'zero_volume_candidates.csv'
    zeros = pd.read_csv(candidates) if candidates.exists() else daily[(daily.volume == 0) & (daily.symbol != '^TWII')]
    if not candidates.exists():
        zeros.to_csv(candidates, index=False)
    for date in sorted(zeros[zeros.symbol.str.endswith('.TWO')].date.unique()):
        params = urllib.parse.urlencode({'date': date.replace('-', '/'), 'response': 'json'})
        x = get(f'https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?{params}',
                f'zero_audit/tpex_{date.replace("-", "")}.json', ROOT / f'data/raw/official/{date.replace("-", "")}_TPEx_dated.json')
        assert x['date'] == date.replace('-', '')
        print('Zero audit TPEx', date, flush=True)
    symbols = sorted(set(zeros[zeros.symbol.str.endswith('.TW')].symbol) | {'6589.TW'})
    for symbol in symbols:
        code = symbol.split('.')[0]
        params = urllib.parse.urlencode({'dataset': 'TaiwanStockPrice', 'data_id': code, 'start_date': str(START), 'end_date': str(END)})
        x = get(f'https://api.finmindtrade.com/api/v4/data?{params}', f'zero_audit/finmind_{code}.json')
        assert x.get('status') == 200 and x.get('data'), (symbol, x)
        print('Zero audit FinMind', symbol, len(x['data']), flush=True)


def canonical_symbol(symbol):
    return {'6589.TW': '6589.TWO', '3718.TWO': '5371.TWO'}.get(symbol, symbol)


def roc_date(value):
    y, m, d = map(int, re.findall(r'\d+', value))
    return f'{y+1911:04d}-{m:02d}-{d:02d}'


def normalize():
    """Build independent extended canonical inputs with explicit effective dates."""
    import pandas as pd

    members = set(pd.read_csv(OUT / 'universe_20241231.csv').symbol)
    required = members | {'0050.TW', '^TWII'}
    daily = pd.concat([pd.read_csv(OUT / 'daily_yahoo.csv'), pd.read_csv(OUT / 'transferred_daily.csv')], ignore_index=True)
    hourly = pd.concat([pd.read_csv(OUT / 'hourly_yahoo.csv'), pd.read_csv(OUT / 'transferred_hourly.csv')], ignore_index=True)
    # Yahoo omits this event and adjusts daily but NOT hourly historical units.
    before = (daily.symbol == '0050.TW') & (daily.date < '2025-06-18')
    daily.loc[before, ['open', 'high', 'low', 'close', 'dividend']] *= 4
    daily.loc[before, 'volume'] /= 4
    daily.loc[before, 'split_restoration_factor'] *= 4
    # Yahoo's paid-rights adjustment is not a share split. Calibrate against the
    # dated exchange close; 256 independent intraday opens validate this factor.
    cap_snapshot = pd.read_csv(OUT / 'universe_20241231.csv')
    raw_2812 = daily.loc[(daily.symbol == '2812.TW') & (daily.date == '2024-12-31'), 'close'].iloc[0]
    official_2812 = cap_snapshot.loc[cap_snapshot.symbol == '2812.TW', 'close'].iloc[0]
    rights_factor_2812 = official_2812 / raw_2812
    before_rights = (daily.symbol == '2812.TW') & (daily.date < '2025-10-28')
    daily.loc[before_rights, ['open', 'high', 'low', 'close']] *= rights_factor_2812
    for frame in (daily, hourly):
        frame['source_symbol'] = frame.symbol
        transferred = (frame.symbol == '6589.TW') & (frame.date < '2025-07-21')
        frame.loc[transferred, 'source_symbol'] = '6589.TWO'
        frame['symbol'] = frame.symbol.map(canonical_symbol)
    daily['price_source'] = 'yahoo_historical_units'
    daily.loc[before_rights, 'price_source'] = 'yahoo_paid_rights_restored_official_20241231_calibration'
    daily['turnover'] = math.nan
    merged = {(r['date'], r['symbol']): r for r in daily.to_dict('records')}
    reused = []
    # Reuse audited official quotes only, not prior-run universe membership or actions.
    oldpath = ROOT / 'data/processed/daily_canonical.csv'
    old = pd.read_csv(oldpath)
    for r in old.to_dict('records'):
        if r['symbol'] not in required or r.get('price_source') == 'yahoo':
            continue
        key = r['date'], r['symbol']
        base = merged.get(key, {'date': r['date'], 'symbol': r['symbol'], 'dividend': 0., 'split': 1., 'split_restoration_factor': 1.})
        for field in ['open', 'high', 'low', 'close', 'volume', 'turnover', 'price_source', 'source_symbol']:
            base[field] = r[field]
        merged[key] = base
        reused.append(key)
    official_monthly_comparisons = []
    for path in sorted((RAW / 'repairs').glob('*.json')):
        code, stamp, market = path.stem.split('_')
        symbol = canonical_symbol(code + ('.TW' if market == 'TWSE' else '.TWO'))
        x = json.loads(path.read_bytes())
        table = x if market == 'TWSE' else x['tables'][0]
        assert table['date'] == stamp + '01', (path, table.get('date'))
        for r in table.get('data', []):
            date = roc_date(r[0])
            values = dict(zip(['open', 'high', 'low', 'close'], map(number, r[3:7])))
            if not all(math.isfinite(v) and v > 0 for v in values.values()):
                continue
            key = date, symbol
            base = merged.get(key, {'date': date, 'symbol': symbol, 'dividend': 0., 'split': 1., 'split_restoration_factor': 1.})
            if 'close' in base:
                official_monthly_comparisons.append({'date': date, 'symbol': symbol, 'provider_close': base['close'], 'official_close': values['close'], 'relative_difference': base['close'] / values['close'] - 1})
            base.update(values)
            unit = 1000 if market == 'TPEx' else 1
            base.update(volume=number(r[1]) * unit, turnover=number(r[2]) * unit,
                        source_symbol=code + ('.TW' if market == 'TWSE' else '.TWO'),
                        price_source='official_monthly_thousand_units' if unit == 1000 else 'official_monthly')
            merged[key] = base
    gap_rows = []
    gap_tpex = RAW / 'gap_20250801/tpex.json'
    if gap_tpex.exists():
        x = json.loads(gap_tpex.read_bytes())
        assert x['date'] == '20250801'
        for r in x['tables'][0]['data']:
            symbol = r[0].strip() + '.TWO'
            values = {'open': number(r[4]), 'high': number(r[5]), 'low': number(r[6]), 'close': number(r[2]), 'volume': number(r[8]), 'turnover': number(r[9])}
            if symbol in required and all(math.isfinite(v) and v > 0 for k, v in values.items() if k != 'turnover'):
                gap_rows.append({'date': '2025-08-01', 'symbol': symbol, 'source_symbol': symbol, **values, 'price_source': 'official_tpex_daily'})
    finmind_neighbor_checks = []
    for path in sorted((RAW / 'gap_20250801').glob('finmind_*.json')):
        x = json.loads(path.read_bytes())
        assert x['status'] == 200
        for r in x['data']:
            raw_symbol = str(r['stock_id']) + '.TW'
            symbol = canonical_symbol(raw_symbol)
            values = {'open': float(r['open']), 'high': float(r['max']), 'low': float(r['min']), 'close': float(r['close']), 'volume': float(r['Trading_Volume']), 'turnover': float(r['Trading_money'])}
            if r['date'] == '2025-08-01':
                gap_rows.append({'date': r['date'], 'symbol': symbol, 'source_symbol': raw_symbol, **values, 'price_source': 'finmind_unadjusted_daily_gap_repair'})
            elif (r['date'], symbol) in merged:
                oldrow = merged[r['date'], symbol]
                finmind_neighbor_checks.append({'date': r['date'], 'symbol': symbol, 'provider_close': oldrow['close'], 'finmind_close': values['close'], 'relative_difference': oldrow['close'] / values['close'] - 1})
    for r in gap_rows:
        key = r['date'], r['symbol']
        base = merged.get(key, {'date': r['date'], 'symbol': r['symbol'], 'dividend': 0., 'split': 1., 'split_restoration_factor': 1.})
        base.update(r)
        merged[key] = base
    zero_audit = []
    zero_candidates = RAW / 'zero_volume_candidates.csv'
    if zero_candidates.exists():
        for original in pd.read_csv(zero_candidates).to_dict('records'):
            symbol, date = original['symbol'], original['date']
            code = symbol.split('.')[0]
            values, source_volume = None, None
            if symbol.endswith('.TW') or (symbol == '6589.TWO' and date >= '2025-07-21'):
                source = RAW / f'zero_audit/finmind_{code}.json'
                x = json.loads(source.read_bytes())
                found = next((r for r in x['data'] if r['date'] == date), None)
                if found:
                    source_volume = float(found['Trading_Volume'])
                    values = {'open': float(found['open']), 'high': float(found['max']), 'low': float(found['min']), 'close': float(found['close']), 'volume': source_volume, 'turnover': float(found['Trading_money'])}
                price_source = 'finmind_zero_quote_repair'
            else:
                source = RAW / f'zero_audit/tpex_{date.replace("-", "")}.json'
                x = json.loads(source.read_bytes())
                assert x['date'] == date.replace('-', '')
                found = next((r for r in x['tables'][0]['data'] if r[0].strip() == code), None)
                if found:
                    source_volume = number(found[8])
                    values = {'open': number(found[4]), 'high': number(found[5]), 'low': number(found[6]), 'close': number(found[2]), 'volume': source_volume, 'turnover': number(found[9])}
                price_source = 'official_tpex_zero_quote_repair'
            key = date, symbol
            if values and source_volume > 0:
                assert all(math.isfinite(v) and v > 0 for k, v in values.items() if k in ['open', 'high', 'low', 'close', 'volume'])
                merged[key].update(values)
                merged[key]['price_source'] = price_source
                status = 'REPAIRED_POSITIVE_TRADING_VOLUME'
            elif source_volume == 0:
                merged[key]['price_source'] = 'zero_volume_quote_independently_corroborated'
                status = 'RETAINED_CORROBORATED_NO_TRADING'
            else:
                assert any(ev['symbol'] == symbol and ev['halt_start'] <= date < ev['resume'] for ev in SPECIAL_EVENTS), (date, symbol, 'Unexplained zero-volume data')
                status = 'REMOVE_DOCUMENTED_MULTI_DAY_HALT'
            zero_audit.append({'date': date, 'symbol': symbol, 'original_volume': 0, 'source_volume': source_volume, 'status': status, 'source': str(source.relative_to(ROOT))})
    rows, unverified, action_differences = [], [], []
    for year in (2024, 2025, 2026):
        for market in ('twse', 'tpex'):
            path = RAW / f'actions/{market}_{year}.json'
            x = json.loads(path.read_bytes())
            data = x['data'] if market == 'twse' else x['tables'][0]['data']
            fields = x['fields'] if market == 'twse' else x['tables'][0]['fields']
            for r in data:
                raw_symbol = r[1].strip() + ('.TW' if market == 'twse' else '.TWO')
                symbol = canonical_symbol(raw_symbol)
                if symbol not in required:
                    continue
                scheduled = roc_date(r[0])
                effective = CLOSED.get(scheduled, scheduled)
                oldrow = merged.get((effective, symbol), {})
                source = str(path.relative_to(ROOT))
                if market == 'tpex':
                    cash = number(r[fields.index('現金股利')])
                    ratio = 1 + number(r[fields.index('每仟股無償配股')]) / 1000
                    subscription = number(r[fields.index('現金增資股數')])
                    prior, reference = number(r[3]), number(r[4])
                elif r[6] == '息':
                    cash, ratio, subscription = number(r[5]), 1., 0.
                    prior, reference = number(r[3]), number(r[4])
                else:
                    detail = RAW / f'actions/detail_{r[1].strip()}_{scheduled.replace("-", "")}.json'
                    prior, reference = number(r[3]), number(r[4])
                    if detail.exists():
                        dr = json.loads(detail.read_bytes())['data'][0]
                        assert dr[0].strip() == r[1].strip()
                        cash, ratio, subscription = number(dr[2]), 1 + number(dr[4]) / 1000, number(dr[6])
                        source = str(detail.relative_to(ROOT))
                    else:
                        cash, ratio, subscription = oldrow.get('dividend', 0.), oldrow.get('split', 1.), math.nan
                        source = 'Yahoo decomposition; official TWSE event/date/reference verified'
                        unverified.append({'date': effective, 'symbol': symbol, 'dividend': cash, 'split': ratio, 'prior_close': prior, 'reference_price': reference})
                assert math.isfinite(cash) and math.isfinite(ratio) and cash >= 0 and ratio > 0, (r, cash, ratio)
                if oldrow:
                    if abs(oldrow.get('dividend', 0) - cash) > .001 or abs(oldrow.get('split', 1) - ratio) > .00001:
                        action_differences.append({'date': effective, 'symbol': symbol, 'provider_dividend': oldrow.get('dividend', 0), 'provider_split': oldrow.get('split', 1), 'official_dividend': cash, 'official_split': ratio})
                    oldrow['dividend'], oldrow['split'] = cash, ratio
                rows.append({'date': effective, 'symbol': symbol, 'dividend': cash, 'split': ratio, 'subscription_shares': subscription,
                             'scheduled_date': scheduled, 'effective_date': effective, 'prior_close': prior, 'reference_price': reference,
                             'source': source, 'reference_residual_if_no_subscription': (prior - cash) / ratio - reference if subscription == 0 else math.nan})
    split_key = ('2025-06-18', '0050.TW')
    merged[split_key]['split'] = 4.
    rows.append({'date': split_key[0], 'symbol': split_key[1], 'dividend': 0., 'split': 4., 'subscription_shares': 0.,
                 'scheduled_date': split_key[0], 'effective_date': split_key[0], 'prior_close': math.nan, 'reference_price': math.nan,
                 'source': 'https://www.twse.com.tw/zh/ETFortune/announcement?company=A00005&date=20250617&fund=0050&seq=1&type=other',
                 'reference_residual_if_no_subscription': math.nan})
    for row in rows:
        row['cash_entitlement_type'] = 'cash_dividend' if row['dividend'] else 'share_event'
    for ev in SPECIAL_EVENTS:
        key = ev['resume'], ev['symbol']
        assert key in merged and merged[key]['volume'] > 0, ev
        merged[key]['split'] = ev['split']
        merged[key]['dividend'] = ev['cash_per_old_share']
        rows.append({'date': ev['resume'], 'symbol': ev['symbol'], 'dividend': ev['cash_per_old_share'], 'split': ev['split'], 'subscription_shares': 0.,
                     'scheduled_date': ev['resume'], 'effective_date': ev['resume'], 'provider_event_date': ev['halt_start'], 'prior_close': math.nan,
                     'reference_price': math.nan, 'source': ev['source'], 'reference_residual_if_no_subscription': math.nan, 'cash_entitlement_type': ev['type']})
    action_map = {(r['date'], r['symbol']): (r['dividend'], r['split']) for r in rows}
    assert len(action_map) == len(rows), 'Duplicate economic action after effective-date normalization'
    daily = pd.DataFrame(merged.values())
    removed = []
    for name, frame in [('daily', daily), ('hourly', hourly)]:
        closed = frame.date.isin(CLOSED)
        suspended_5371 = (frame.symbol == '5371.TWO') & (frame.date > '2026-08-21') & (frame.date < '2026-09-03')
        stale_5371 = (frame.source_symbol == '5371.TWO') & (frame.date >= '2026-09-03')
        suspended_0050 = (frame.symbol == '0050.TW') & (frame.date >= '2025-06-11') & (frame.date < '2025-06-18')
        prelisting = (frame.symbol == '4772.TWO') & (frame.date < '2024-09-20')
        capital_halts = pd.Series(False, index=frame.index)
        for ev in SPECIAL_EVENTS:
            capital_halts |= (frame.symbol == ev['symbol']) & (frame.date >= ev['halt_start']) & (frame.date < ev['resume'])
        discard = closed | suspended_5371 | stale_5371 | suspended_0050 | prelisting | capital_halts
        removed.append({'table': name, 'closed_market_bars': int(closed.sum()), '5371_suspended_or_stale': int((suspended_5371 | stale_5371).sum()), '0050_suspended': int(suspended_0050.sum()), '4772_pre_regular_listing': int(prelisting.sum()), 'documented_capital_event_halts': int(capital_halts.sum())})
        frame.drop(frame[discard].index, inplace=True)
        frame.drop(frame[~frame.symbol.isin(required)].index, inplace=True)
        for idx, row in frame.iterrows():
            if (row.date, row.symbol) in action_map:
                frame.loc[idx, ['dividend', 'split']] = action_map[row.date, row.symbol]
        frame.sort_values(['timestamp' if name == 'hourly' else 'date', 'symbol'], inplace=True)
        assert not frame.duplicated(['timestamp' if name == 'hourly' else 'date', 'symbol']).any()
    daily_fields = ['date', 'symbol', 'open', 'high', 'low', 'close', 'volume', 'dividend', 'split', 'split_restoration_factor', 'turnover', 'price_source', 'source_symbol']
    hourly_fields = ['date', 'symbol', 'open', 'high', 'low', 'close', 'volume', 'dividend', 'split', 'split_restoration_factor', 'timestamp', 'source_symbol']
    for frame in (daily, hourly):
        assert not frame[['open', 'high', 'low', 'close', 'volume', 'dividend', 'split']].isna().any().any()
        assert (frame[['open', 'high', 'low', 'close', 'split']] > 0).all().all()
        assert (frame.volume >= 0).all()
        bad_high = frame.high + .001 < frame[['open', 'close', 'low']].max(axis=1)
        bad_low = frame.low - .001 > frame[['open', 'close', 'high']].min(axis=1)
        assert not (bad_high | bad_low).any(), frame[bad_high | bad_low].head(20).to_dict('records')
    market_days = daily[daily.symbol.isin(members)].groupby('date').agg(n=('symbol', 'count'), volume=('volume', 'sum'))
    assert market_days[(market_days.n >= 20) & (market_days.volume == 0)].empty, 'MARKET_CALENDAR unexplained zero-volume day'
    assert members.issubset(set(daily.symbol)), members - set(daily.symbol)
    daily[daily_fields].to_csv(OUT / 'daily_canonical.csv', index=False)
    hourly[hourly_fields].to_csv(OUT / 'hourly_canonical.csv', index=False)
    pd.DataFrame(rows).to_csv(OUT / 'official_corporate_actions.csv', index=False)
    pd.DataFrame(official_monthly_comparisons).to_csv(OUT / 'official_monthly_comparisons.csv', index=False)
    pd.DataFrame(action_differences).to_csv(OUT / 'corporate_action_corrections.csv', index=False)
    pd.DataFrame(zero_audit).to_csv(OUT / 'zero_volume_source_audit.csv', index=False)
    pd.DataFrame(finmind_neighbor_checks).to_csv(OUT / 'finmind_neighbor_validation.csv', index=False)
    coverage = daily[daily.symbol.isin(members)].groupby('date').symbol.nunique()
    assert coverage.min() >= 145, f'MARKET_COVERAGE: widespread missing rows {coverage[coverage < 145].to_dict()}'
    cal = sorted(daily.date.unique())
    hh = hourly.copy()
    hh['hour'] = hh.timestamp.str[11:16]
    complete = hh[hh.hour.isin(['09:00', '10:00', '11:00', '12:00'])].groupby(['symbol', 'date']).hour.nunique()
    completed = complete[complete == 4].reset_index()
    warmup = completed[completed.date < '2025-01-01'].groupby('symbol').size()
    audit = {'trust': 'CANDIDATE_PENDING_INDEPENDENT_VERIFICATION', 'start': str(START), 'end': str(END),
             'daily_rows': len(daily), 'hourly_rows': len(hourly), 'universe_rows': len(members),
             'market_sessions_by_year': {str(y): sum(d.startswith(str(y)) for d in cal) for y in (2024, 2025, 2026)},
             'backtest_sessions': sum(d >= '2025-01-01' for d in cal), 'marketwide_zero_volume_dates': [],
             'official_price_rows_reused': len(reused), 'old_canonical_sha256': hashlib.sha256(oldpath.read_bytes()).hexdigest(),
             '2812_paid_rights_price_factor': {'factor': rights_factor_2812, 'dates': '2024-01-02 through2025-10-27', 'calibration_date': '2024-12-31', 'official_close': official_2812, 'raw_provider_close': raw_2812, 'shares_and_volume_unchanged': True, 'limitation': 'Constant adjustment factor inferred from dated official close and256matching intraday opens; full monthly source download blocked by TWSE Anti-DDoS.'},
             'removed_nontrading_bars': removed, 'closed_market_next_open': CLOSED,
             'action_count': len(rows), 'unverified_twse_action_decomposition': unverified,
             '20250801_gap_recovery': {'official_tpex_rows': sum(r['price_source'] == 'official_tpex_daily' for r in gap_rows), 'finmind_rows': sum(r['price_source'].startswith('finmind') for r in gap_rows), 'reason': 'Yahoo dailyOHLCV are all-null for nearly allstocks onthis actualtradingday; never interpreted as closure.', 'finmind_access': 'Documented public individualstock endpoint,100requests below300/hour unauthenticated limit; no loginorfees.', 'volume_basis': 'FinMind Trading_Volume includes blocktrades; not assumed equal to Yahoo regularsession volume.'},
             'minimum_daily_universe_coverage': int(coverage.min()),
             'capital_events_rescheduled_to_tradable_resume': SPECIAL_EVENTS,
             'zero_volume_source_audit_counts': pd.Series([r['status'] for r in zero_audit]).value_counts().to_dict(),
             'rescheduled_actions': [r for r in rows if r['scheduled_date'] != r['effective_date']],
             'missing_initial_4h_warmup': [{'symbol': s, 'complete_4h_bars_by_20241231': int(warmup.get(s, 0))} for s in sorted(members) if warmup.get(s, 0) < 50],
             'cash_dividend_basis': 'Cash dividend OR explicitly classified capital-reduction refund per PRE-action old share; add q_before*dividend, then multiply shares by split. No double split multiplier on cash.',
             'known_limitations': ['Reconstructed historical universe and present provider history are not archived point-in-time data.',
                                   '2888 hourly history unavailable today; retained universe member and official daily quotes, but 4H-coverage entry cannot occur.',
                                   '6589 hourly history starts its 2025-07-21 transfer; no fabricated earlier intraday quotes.',
                                   '5371 hourly history unavailable before 2026-07-17; successor3718 is mapped1:1 only from2026-09-03.',
                                   '2888 merger creates0.672 common2887 plus0.175 preferred2887I: not spliced; engine must reject any position crossing merger without full entitlement support.',
                                   'Cash pay dates unavailable; ex-date receivable is distinct from cash; fixedv1 model pays accumulated dividends at terminaldate. Cash subscriptions not exercised.',
                                   'Official quote coverage partial; next-open model, not complete official VWAP.'],
             'sha256': {name: hashlib.sha256((OUT / name).read_bytes()).hexdigest() for name in ['daily_canonical.csv', 'hourly_canonical.csv', 'universe_20241231.csv', 'official_corporate_actions.csv']}}
    (OUT / 'canonical_audit.json').write_text(json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=True))
    print(json.dumps({k: audit[k] for k in ['trust', 'daily_rows', 'hourly_rows', 'market_sessions_by_year', 'missing_initial_4h_warmup', 'sha256']}, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['universe', 'download', 'repairs', 'details', 'units', 'gaps', 'zero-audit', 'normalize', 'all'], default='all')
    args = parser.parse_args()
    RAW.mkdir(parents=True, exist_ok=True); OUT.mkdir(parents=True, exist_ok=True)
    if args.mode in ['all', 'universe']:
        universe()
    if args.mode in ['all', 'download']:
        download()
    if args.mode == 'repairs':
        source_repairs()
    if args.mode == 'details':
        detail_repairs()
    if args.mode == 'units':
        unit_repairs()
    if args.mode == 'normalize':
        normalize()
    if args.mode == 'gaps':
        gap_repairs()
    if args.mode == 'zero-audit':
        zero_quote_audit()
