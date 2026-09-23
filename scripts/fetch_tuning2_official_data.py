"""Isolated acquisition for the retrospective fixed official-2026 universe.

This script never writes to the prior canonical inputs. A successful download is
not an audited data set; normalization explicitly reports unresolved actions.
"""
from __future__ import annotations
import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import hashlib
import html
import json
import re
import math
from pathlib import Path
import subprocess
import threading
import time
import urllib.parse
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'data/tuning_2nd/official_universe'
RAW = BASE / 'raw'
OUT = BASE / 'processed'
TZ = ZoneInfo('Asia/Taipei')
LOCK = threading.Lock()
CLOSED = {'2024-07-24': '2024-07-26', '2024-07-25': '2024-07-26',
          '2026-07-10': '2026-07-13'}
SPECIAL = [
    dict(symbol='8932.TWO', halt_start='2024-08-29', effective='2024-09-09', split=2.,
         source='https://www.tpex.org.tw/storage/eb_data/11309/11300087991.html'),
    dict(symbol='8932.TWO', halt_start='2026-02-25', effective='2026-03-09', split=2.,
         source='https://www.tpex.org.tw/storage/eb_data/11503/11500008331.html'),
    dict(symbol='6919.TW', halt_start='2025-07-14', effective='2025-07-21', split=10.,
         source='https://mopsov.twse.com.tw/nas/STR/691920260206M001.pdf',
         date_source='https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=20250701&stockNo=6919'),
]
NO_QUOTES = [
    dict(symbol='3491.TWO', date='2024-03-13', source='data/extended/raw/zero_audit/tpex_20240313.json'),
    dict(symbol='3491.TWO', date='2026-07-15', source='data/raw/official/20260715_TPEx_dated.json'),
]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fetch(url, relative):
    path = RAW / relative
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    time.sleep(.35)
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    result = subprocess.run(['curl', '-fL', '--max-time', '40', '-sS',
                             '-A', 'Mozilla/5.0', url], capture_output=True)
    record = dict(url=url, path=str(path.relative_to(ROOT)), retrieved_at_utc=started,
                  returncode=result.returncode)
    if result.returncode:
        record['error'] = result.stderr.decode(errors='replace')[:1000]
    else:
        path.write_bytes(result.stdout)
        record.update(sha256=digest(path), bytes=len(result.stdout))
    with LOCK:
        with (RAW / 'retrieval_manifest.jsonl').open('a') as target:
            target.write(json.dumps(record, ensure_ascii=False) + '\n')
    if result.returncode:
        raise RuntimeError(record['error'])
    return path


def symbols():
    official = pd.read_csv(ROOT / 'data/reference/universe_competition_20260731.csv')
    existing = set(pd.read_csv(ROOT / 'data/v2/market_daily.csv', usecols=['symbol']).symbol)
    return sorted(set(official.yahoo_symbol) - existing - {'3718.TWO'})


def chart(symbol, interval):
    start = '2024-10-01' if interval == '60m' else '2024-01-01'
    p1 = int(dt.datetime.fromisoformat(start).replace(tzinfo=TZ).timestamp())
    p2 = int(dt.datetime(2026, 9, 22, tzinfo=TZ).timestamp())
    query = urllib.parse.urlencode(dict(period1=p1, period2=p2, interval=interval,
                                        events='div,splits', includePrePost='false'))
    path = fetch(f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}',
                 f'yahoo/{symbol}_{interval}.json')
    body = json.loads(path.read_bytes())
    if body['chart'].get('error'):
        raise ValueError(body['chart']['error'])
    data = body['chart']['result'][0]
    return dict(symbol=symbol, interval=interval, rows=len(data.get('timestamp', [])),
                first_date=dt.datetime.fromtimestamp(data['timestamp'][0], TZ).isoformat()
                if data.get('timestamp') else None,
                events=data.get('events', {}), source=str(path.relative_to(ROOT)))


def download():
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(symbol, interval) for symbol in symbols() for interval in ('1d', '60m')]
    rows = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = {pool.submit(chart, *job): job for job in jobs}
        for future in as_completed(pending):
            symbol, interval = pending[future]
            try:
                row = future.result()
                print(symbol, interval, row['rows'], flush=True)
            except Exception as exc:
                row = dict(symbol=symbol, interval=interval, error=str(exc))
                print('FAILED', symbol, interval, str(exc), flush=True)
            rows.append(row)
    (OUT / 'download_coverage.json').write_text(json.dumps(rows, indent=2, ensure_ascii=False))
    for mode in (2, 4):
        try:
            path = fetch(f'https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}',
                         f'listing/isin_{mode}.html')
            print('LISTING', mode, path.stat().st_size, flush=True)
        except Exception as exc:
            print('LISTING FAILED', mode, str(exc), flush=True)


def enrich():
    """Unadjusted prices and the small set of official mixed/right events."""
    errors = []
    for symbol in symbols():
        code = symbol.split('.')[0]
        query = urllib.parse.urlencode(dict(dataset='TaiwanStockPrice', data_id=code,
                                            start_date='2024-01-01', end_date='2026-09-21'))
        try:
            path = fetch('https://api.finmindtrade.com/api/v4/data?' + query,
                         f'finmind/{symbol}.json')
            result = json.loads(path.read_bytes())
            if result.get('status') != 200:
                raise ValueError(result)
            print('UNADJUSTED', symbol, len(result['data']), flush=True)
        except Exception as exc:
            errors.append(dict(symbol=symbol, purpose='unadjusted_prices', error=str(exc)))
            print('UNADJUSTED FAILED', symbol, str(exc), flush=True)
    wanted = {s.split('.')[0] for s in symbols()}
    for year in (2024, 2025, 2026):
        body = json.loads((ROOT / f'data/extended/raw/actions/twse_{year}.json').read_bytes())
        for row in body['data']:
            if row[1].strip() not in wanted or row[6] == '息':
                continue
            year, month, day = map(int, re.findall(r'\d+', row[0]))
            stamp = f'{year + 1911:04d}{month:02d}{day:02d}'
            code = row[1].strip()
            try:
                path = fetch(f'https://www.twse.com.tw/rwd/zh/exRight/TWT49UDetail?response=json&STK_NO={code}&T1={stamp}',
                             f'actions/detail_{code}_{stamp}.json')
                body = json.loads(path.read_bytes())
                if not body.get('data'):
                    raise ValueError(body)
                print('DETAIL', code, stamp, body['data'][0], flush=True)
            except Exception as exc:
                errors.append(dict(symbol=code, purpose='official_action_detail', date=stamp, error=str(exc)))
                print('DETAIL FAILED', code, stamp, str(exc), flush=True)
                if 'anti' in str(exc).lower() or '403' in str(exc):
                    break
    (OUT / 'enrichment_errors.json').write_text(json.dumps(errors, indent=2))


def evidence():
    sources = [
        ('https://www.tpex.org.tw/storage/eb_data/11309/11300087991.html', 'events/8932_20240909.html'),
        ('https://www.tpex.org.tw/storage/eb_data/11503/11500008331.html', 'events/8932_20260309.html'),
        ('https://www.twse.com.tw/exchangeReport/STOCK_DAY?response=json&date=20250701&stockNo=6919', 'events/6919_202507_official.json'),
        ('https://mopsov.twse.com.tw/nas/STR/691920260206M001.pdf', 'events/6919_20260206_issuer_presentation.pdf'),
        ('https://www.twse.com.tw/staticFiles/news/news/tsecnews/8a8216d696fb09400197160b39700036.pdf', 'active_etf/twse_20250528.pdf'),
    ]
    for url, relative in sources:
        try:
            path = fetch(url, relative)
            print('EVIDENCE', relative, path.stat().st_size, flush=True)
        except Exception as exc:
            print('EVIDENCE FAILED', relative, str(exc), flush=True)


def number(value):
    try:
        return float(str(value).replace(',', '').split()[0])
    except (ValueError, IndexError, TypeError):
        return float('nan')


def roc(value):
    year, month, day = map(int, re.findall(r'\d+', str(value)))
    return f'{year + 1911:04d}-{month:02d}-{day:02d}'


def listing_rows():
    rows = []
    for mode, suffix in [(2, '.TW'), (4, '.TWO')]:
        path = RAW / f'listing/isin_{mode}.html'
        body = path.read_bytes().decode('cp950', errors='replace')
        for row in re.findall(r'<tr[^>]*>(.*?)</tr>', body, re.S | re.I):
            values = [html.unescape(re.sub('<[^>]+>', '', x)).strip()
                      for x in re.findall(r'<td[^>]*>(.*?)</td>', row, re.S | re.I)]
            if len(values) < 7 or not values[0].split():
                continue
            code = values[0].split()[0]
            if not re.fullmatch(r'\d{4}|\d{5}A', code):
                continue
            rows.append(dict(symbol=code + suffix, code=code,
                             regular_listing_date=values[2].replace('/', '-'),
                             source_url=f'https://isin.twse.com.tw/isin/C_public.jsp?strMode={mode}',
                             raw_path=str(path.relative_to(ROOT)),
                             use='historical_listing_event_only_not_historical_industry'))
    return pd.DataFrame(rows)


def cached_official_quotes(wanted):
    rows, sources, used = {}, {}, set()
    paths = sorted((ROOT / 'data/raw/official').glob('*.json'))
    paths += sorted((ROOT / 'data/v2/raw_execution').glob('*.json'))
    for path in paths:
        try:
            body = json.loads(path.read_bytes())
            stamp = body.get('date', '')
            if not re.fullmatch(r'\d{8}', stamp):
                continue
            date = f'{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}'
            tables = body.get('tables', [])
            table = next((t for t in tables if '收盤價' in t.get('fields', [])), None)
            if table:
                suffix = '.TW'
                mapping = dict(open='開盤價', high='最高價', low='最低價', close='收盤價',
                               volume='成交股數', turnover='成交金額')
            else:
                table = next((t for t in tables if '成交金額(元)' in t.get('fields', [])), None)
                if not table:
                    continue
                suffix = '.TWO'
                mapping = dict(open='開盤', high='最高', low='最低', close='收盤',
                               volume='成交股數', turnover='成交金額(元)')
            for record in table['data']:
                symbol = str(record[0]).strip() + suffix
                if symbol not in wanted:
                    continue
                values = {key: number(record[table['fields'].index(field)]) for key, field in mapping.items()}
                if not all(math.isfinite(x) for x in values.values()) or min(values[k] for k in ['open', 'high', 'low', 'close']) <= 0:
                    continue
                rows[date, symbol] = dict(date=date, symbol=symbol, **values)
                sources[date, symbol] = str(path.relative_to(ROOT))
                used.add(path)
        except (ValueError, KeyError, TypeError):
            continue
    return rows, sources, used


def normalize():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'readiness.json').write_text(json.dumps(dict(status='NORMALIZING_DO_NOT_RUN')))
    old_daily_path = ROOT / 'data/v2/market_daily.csv'
    old_hourly_path = ROOT / 'data/extended/processed/hourly_canonical.csv'
    original = pd.read_csv(old_daily_path)
    old_hourly = pd.read_csv(old_hourly_path)
    calendar = sorted(original.date.unique())
    regular = listing_rows()
    regular.to_csv(OUT / 'listing_dates.csv', index=False)
    listing = regular.set_index('symbol').regular_listing_date.to_dict()
    wanted = set(symbols())
    assert wanted <= set(listing), wanted - set(listing)
    official, official_sources, used = cached_official_quotes(wanted)
    used |= {old_daily_path, old_hourly_path, ROOT / 'data/reference/universe_competition_20260731.csv'}
    rows, hourly, comparison, coverage, blockers = [], [], [], [], []
    no_quote_keys = {(r['symbol'], r['date']) for r in NO_QUOTES}
    for record in NO_QUOTES:
        path = ROOT / record['source']; used.add(path)
        source = json.loads(path.read_bytes())
        row = next(r for r in source['tables'][0]['data'] if r[0].strip() == record['symbol'].split('.')[0])
        assert number(row[8]) == number(row[9]) == 0 and not math.isfinite(number(row[2]))
    yahoo_events = {}
    for symbol in sorted(wanted):
        price_path = RAW / f'finmind/{symbol}.json'
        price = json.loads(price_path.read_bytes())
        assert price['status'] == 200
        for row in price['data']:
            date = row['date']
            if date < listing[symbol] or date not in calendar:
                continue
            values = dict(open=float(row['open']), high=float(row['max']), low=float(row['min']),
                          close=float(row['close']), volume=float(row['Trading_Volume']),
                          turnover=float(row['Trading_money']))
            if min(values[k] for k in ['open', 'high', 'low', 'close']) <= 0:
                if (symbol, date) not in no_quote_keys:
                    blockers.append(dict(type='nonpositive_vendor_quote', symbol=symbol, date=date))
                continue
            source = 'finmind_unadjusted_official_overlap_validated'
            key = date, symbol
            if key in official:
                verified = official[key]
                comparison.append(dict(date=date, symbol=symbol,
                    relative_close_difference=values['close'] / verified['close'] - 1,
                    relative_vwap_difference=(values['turnover'] / values['volume']) /
                    (verified['turnover'] / verified['volume']) - 1 if min(values['volume'], verified['volume']) > 0 else 0.,
                    source=official_sources[key]))
                values.update({k: verified[k] for k in values})
                source = 'official_cached_daily'
            rows.append(dict(date=date, symbol=symbol, **values, dividend=0., split=1.,
                             split_restoration_factor=1., price_source=source, source_symbol=symbol,
                             execution_volume=values['volume'], official_turnover=values['turnover'] if key in official else np.nan,
                             vwap_source=source,
                             execution_vwap=values['turnover'] / values['volume'] if values['volume'] > 0 else np.nan))
        daily_source = json.loads((RAW / f'yahoo/{symbol}_1d.json').read_bytes())['chart']['result'][0]
        events = daily_source.get('events', {})
        events_by_date = {}
        for event_type, items in events.items():
            for event in items.values():
                date = dt.datetime.fromtimestamp(event['date'], TZ).date().isoformat()
                events_by_date.setdefault(date, {})[event_type] = event
        yahoo_events[symbol] = events_by_date
        splits = {date: event['splits']['numerator'] / event['splits']['denominator']
                  for date, event in events_by_date.items() if 'splits' in event}
        source = json.loads((RAW / f'yahoo/{symbol}_60m.json').read_bytes())['chart']['result'][0]
        quotes = source['indicators']['quote'][0]
        for i, timestamp in enumerate(source.get('timestamp', [])):
            moment = dt.datetime.fromtimestamp(timestamp, TZ)
            date = moment.date().isoformat()
            if date not in calendar or date < listing[symbol] or (symbol, date) in no_quote_keys:
                continue
            values = {k: quotes[k][i] for k in ['open', 'high', 'low', 'close', 'volume']}
            if any(v is None for v in values.values()):
                continue
            factor = math.prod(ratio for event_date, ratio in splits.items() if event_date > date)
            # Paired 09:00 opens validate split-adjusted hourly prices. Hourly
            # volumes remain historical units and must NOT be divided by factor.
            values.update({k: values[k] * factor for k in ['open', 'high', 'low', 'close']})
            hourly.append(dict(date=date, symbol=symbol, **values, dividend=0., split=1.,
                               split_restoration_factor=factor, timestamp=moment.isoformat(), source_symbol=symbol))
    daily = pd.DataFrame(rows)
    hours = pd.DataFrame(hourly)
    actions = []
    for year in (2024, 2025, 2026):
        for market in ['twse', 'tpex']:
            path = ROOT / f'data/extended/raw/actions/{market}_{year}.json'
            used.add(path)
            body = json.loads(path.read_bytes())
            table = body if market == 'twse' else body['tables'][0]
            for r in table['data']:
                symbol = r[1].strip() + ('.TW' if market == 'twse' else '.TWO')
                scheduled = roc(r[0]); date = CLOSED.get(scheduled, scheduled)
                if symbol not in wanted or date < listing[symbol] or date not in calendar:
                    continue
                source = str(path.relative_to(ROOT))
                if market == 'tpex':
                    cash = number(r[table['fields'].index('現金股利')])
                    ratio = 1 + number(r[table['fields'].index('每仟股無償配股')]) / 1000
                    subscription = number(r[table['fields'].index('現金增資股數')])
                elif r[6] == '息':
                    cash, ratio, subscription = number(r[5]), 1., 0.
                else:
                    detail = RAW / f'actions/detail_{r[1].strip()}_{scheduled.replace("-", "")}.json'
                    if not detail.exists():
                        blockers.append(dict(type='missing_official_action_decomposition', symbol=symbol, date=date))
                        continue
                    dr = json.loads(detail.read_bytes())['data'][0]
                    cash, ratio, subscription = number(dr[2]), 1 + number(dr[4]) / 1000, number(dr[6])
                    source = str(detail.relative_to(ROOT))
                assert cash >= 0 and ratio > 0 and math.isfinite(subscription), (symbol, date, r)
                actions.append(dict(symbol=symbol, date=date, scheduled_date=scheduled, dividend=cash,
                                    split=ratio, subscription_shares=subscription, subscription_exercised=False,
                                    type='dividend_or_rights', source=source,
                                    cash_basis='per_pre_action_old_share'))
    for event in SPECIAL:
        actions.append(dict(symbol=event['symbol'], date=event['effective'], scheduled_date=event['halt_start'],
                            dividend=0., split=event['split'], subscription_shares=0.,
                            subscription_exercised=False, type='face_value_split',
                            source=event['source'], date_source=event.get('date_source', event['source']),
                            cash_basis='per_pre_action_old_share'))
    action_frame = pd.DataFrame(actions)
    assert not action_frame.duplicated(['symbol', 'date']).any()
    action_lookup = {(r['symbol'], r['date']): r for r in actions}
    for frame in [daily, hours]:
        for event in SPECIAL:
            mask = frame.symbol.eq(event['symbol']) & frame.date.ge(event['halt_start']) & frame.date.lt(event['effective'])
            frame.drop(frame.index[mask], inplace=True)
        for event in actions:
            mask = frame.symbol.eq(event['symbol']) & frame.date.eq(event['date'])
            frame.loc[mask, ['dividend', 'split']] = [event['dividend'], event['split']]
    for symbol, events in yahoo_events.items():
        for date, event in events.items():
            effective = CLOSED.get(date, date)
            if effective < listing[symbol] or effective not in calendar:
                continue
            if any(x['symbol'] == symbol and x['halt_start'] == date for x in SPECIAL):
                continue
            if (symbol, effective) not in action_lookup:
                blockers.append(dict(type='provider_action_without_official_event', symbol=symbol, date=date, event=event))
    keys = set(zip(daily.symbol, daily.date))
    for event in actions:
        if (event['symbol'], event['date']) not in keys:
            blockers.append(dict(type='official_action_without_price', symbol=event['symbol'], date=event['date']))
    # Fixed common market calendar; documented IPO and suspension dates alone
    # explain legitimate missing stock rows. No synthetic quotes or 4H bars.
    for symbol in sorted(wanted):
        eligible = {d for d in calendar if d >= listing[symbol]}
        eligible -= {d for s, d in no_quote_keys if s == symbol}
        for event in SPECIAL:
            if event['symbol'] == symbol:
                eligible -= {d for d in eligible if event['halt_start'] <= d < event['effective']}
        actual = set(daily.loc[daily.symbol == symbol, 'date'])
        missing = sorted(eligible - actual)
        if missing:
            blockers.append(dict(type='unexplained_daily_gap', symbol=symbol, dates=missing))
        coverage.append(dict(symbol=symbol, regular_listing_date=listing[symbol], daily_rows=len(actual),
                             evaluation_rows=sum(d >= '2025-01-01' for d in actual),
                             warmup_rows=sum(d < '2025-01-01' for d in actual), missing_dates=';'.join(missing)))
    open_rows = hours[hours.timestamp.str[11:16] == '09:00'][['date', 'symbol', 'open']]
    paired = open_rows.merge(daily[['date', 'symbol', 'open']], on=['date', 'symbol'], suffixes=('_hourly', '_daily'))
    paired['relative_difference'] = paired.open_hourly / paired.open_daily - 1
    systematic = paired[paired.relative_difference.abs() > .0003].copy()
    systematic['rounded_factor'] = (systematic.open_daily / systematic.open_hourly).round(5)
    repeated = systematic.groupby(['symbol', 'rounded_factor']).size()
    for (symbol, factor), count in repeated[repeated >= 4].items():
        if abs(factor - 1) > .002:
            blockers.append(dict(type='repeated_hourly_unit_mismatch', symbol=symbol, factor=factor, count=int(count)))
    pd.DataFrame(comparison).to_csv(OUT / 'official_vendor_overlap.csv', index=False)
    paired.to_csv(OUT / 'hourly_open_unit_checks.csv', index=False)
    pd.DataFrame(coverage).to_csv(OUT / 'new_company_coverage.csv', index=False)
    action_frame.to_csv(OUT / 'new_company_actions.csv', index=False)
    official_universe = pd.read_csv(ROOT / 'data/reference/universe_competition_20260731.csv', dtype={'ticker': str})
    official_universe['official_ticker'] = official_universe.ticker
    official_universe['symbol'] = official_universe.yahoo_symbol.replace({'3718.TWO': '5371.TWO'})
    official_universe['known_at'] = '2026-09-18T10:05:13+08:00'
    official_universe['known_at_policy'] = 'official_event_publication_raw_timestamp_Taipei_timezone_assumption'
    official_universe['universe_type'] = 'EX_POST_FIXED_OFFICIAL_POOL_NOT_PIT'
    members = set(official_universe.symbol)
    existing = members - wanted
    assert len(members) == 150 and len(wanted) == 33
    combined_daily = pd.concat([original[original.symbol.isin(existing | {'0050.TW', '^TWII'})], daily], ignore_index=True)
    combined_hourly = pd.concat([old_hourly[old_hourly.symbol.isin(existing | {'0050.TW', '^TWII'})], hours], ignore_index=True)
    assert members <= set(combined_daily.symbol)
    for name, frame, key in [('daily.csv', combined_daily, 'date'), ('hourly.csv', combined_hourly, 'timestamp')]:
        assert not frame.duplicated(['symbol', key]).any()
        frame.sort_values([key, 'symbol'], inplace=True)
        assert np.isfinite(frame[['open', 'high', 'low', 'close', 'volume', 'dividend', 'split']].to_numpy()).all()
        assert frame[['open', 'high', 'low', 'close', 'split']].gt(0).all().all()
        assert frame.volume.ge(0).all()
        assert frame.high.add(.002).ge(frame[['open', 'close', 'low']].max(axis=1)).all()
        assert frame.low.sub(.002).le(frame[['open', 'close', 'high']].min(axis=1)).all()
        # Preserve every reused textual value rather than introducing an extra
        # float-to-CSV round trip into the previously frozen inputs.
        source_path = old_daily_path if name == 'daily.csv' else old_hourly_path
        with source_path.open() as source:
            preserved = {(r['symbol'], r[key]): r for r in csv.DictReader(source)
                         if r['symbol'] in existing | {'0050.TW', '^TWII'}}
        with (OUT / name).open('w', newline='') as target:
            writer = csv.DictWriter(target, fieldnames=list(frame.columns))
            writer.writeheader()
            for record in frame.to_dict('records'):
                writer.writerow(preserved.get((record['symbol'], record[key]), record))
    official_universe.to_csv(OUT / 'universe.csv', index=False)
    test = combined_daily[combined_daily.date >= '2025-01-01']
    assert sorted(test.date.unique()) == [d for d in calendar if d >= '2025-01-01']
    assert test.date.nunique() == 417
    tradable = test[test.symbol.isin(members) & test.volume.gt(0)]
    bad_execution = tradable[~tradable.turnover.gt(0) | ~tradable.execution_volume.gt(0)]
    if len(bad_execution):
        blockers.append(dict(type='missing_execution_price', count=len(bad_execution)))
    zero_daily = daily[daily.volume.eq(0)][['date', 'symbol']].merge(hours.groupby(['date', 'symbol']).volume.sum().rename('hourly_volume'), on=['date', 'symbol'], how='left')
    if zero_daily.hourly_volume.gt(0).any():
        blockers.append(dict(type='zero_daily_positive_hourly', rows=zero_daily[zero_daily.hourly_volume.gt(0)].to_dict('records')))
    used |= set(RAW.rglob('*.json')) | set(RAW.rglob('*.html')) | set(RAW.rglob('*.pdf'))
    hashes = {str(path.relative_to(ROOT)): digest(path) for path in sorted(used)}
    outputs = {name: digest(OUT / name) for name in ['daily.csv', 'hourly.csv', 'universe.csv', 'new_company_actions.csv']}
    readiness = dict(status='BLOCKED_DATA_QA' if blockers else 'CANDIDATE_PENDING_INDEPENDENT_QA',
                     blockers=blockers, universe_members=150, new_companies=33, sessions=417,
                     daily_rows=len(combined_daily), hourly_rows=len(combined_hourly),
                     input_hashes=hashes, output_hashes=outputs,
                     source_script_sha256=digest(__file__), selection_scope='EX_POST_FIXED_OFFICIAL_POOL',
                     active_share='UNKNOWN', cash_dividend_basis='pre-action old shares, receivable until terminal',
                     subscription_policy='not exercised', new_company_actions=len(actions),
                     documented_no_quote_sessions=NO_QUOTES,
                     quote_overlap_rows=len(comparison), quote_overlap_max_close_relative_error=max(abs(r['relative_close_difference']) for r in comparison),
                     inherited_data_limitations='See frozen data/v2 provenance and data/extended/processed/canonical_audit.json',
                     generated_at_utc=dt.datetime.now(dt.timezone.utc).isoformat())
    (OUT / 'readiness.json').write_text(json.dumps(readiness, indent=2, ensure_ascii=False))
    print(json.dumps({key: readiness[key] for key in ['status', 'blockers', 'daily_rows', 'hourly_rows', 'sessions', 'output_hashes']}, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['download', 'enrich', 'evidence', 'normalize'], default='download')
    args = parser.parse_args()
    if args.mode == 'download':
        download()
    elif args.mode == 'enrich':
        enrich()
    elif args.mode == 'evidence':
        evidence()
    elif args.mode == 'normalize':
        normalize()
