"""Point-in-time raw data for the Hybrid spec (docs/hybrid_spec.md section 4), stored compactly.

    .venv/bin/python -m hybrid.sources revenue            # MOPS t21sc03, 2013-01 .. last published month
    .venv/bin/python -m hybrid.sources twse               # TWSE T86, 2014-01-01 ..
    .venv/bin/python -m hybrid.sources tpex               # TPEx 3insti, 2014-12-01 .. (earlier dates do not exist)
    .venv/bin/python -m hybrid.sources compact            # raw files -> compact tables, verified, then raw deleted
    .venv/bin/python -m hybrid.sources build              # compact tables -> data/hybrid/{revenue,flows}.csv

Each response is parsed at once and only the 150-name rows are kept, with a
log row (request, sha256 of the raw response, row count) so downloads resume
and every table row stays traceable to a raw hash. Revenue rows carry the
report's own current, previous-month and last-year revenue (thousand TWD);
month m is usable from the 11th of month m+1. Flow rows carry net shares
bought by foreign investors (incl. foreign dealers, one definition across
report layouts) and investment trusts; a day logged OK without a stock means
zero net buying.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import shutil
import time
from pathlib import Path

import pandas as pd
import requests

from competition.data import load_calendar
from competition.rules import ROOT, UNIVERSE_PATH

DATA = ROOT / 'data/hybrid'
RAW = DATA / 'raw'                                  # legacy full responses (compacted, then deleted)
HEADERS = {'User-Agent': 'Mozilla/5.0 (fintech_ETF research data)'}
REVENUE_URL = 'https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_{roc}_{month}_{kind}.html'
TWSE_URL = 'https://www.twse.com.tw/rwd/zh/fund/T86?date={d:%Y%m%d}&selectType=ALLBUT0999&response=json'
TPEX_URL = ('https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php'
            '?l=zh-tw&se=EW&t=D&d={roc}/{d:%m/%d}&o=json')
START = dict(revenue='2013-01-01', twse='2014-01-01', tpex='2014-12-01')
PAUSE = dict(revenue=2., twse=3.5, tpex=2.)
FLOW_COLUMNS = ['date', 'ticker', 'foreign_net', 'trust_net', 'source']
REVENUE_COLUMNS = ['month', 'ticker', 'revenue', 'prev_revenue', 'last_year_revenue']


def tickers() -> set[str]:
    return set(pd.read_csv(UNIVERSE_PATH, dtype={'ticker': str}).ticker)


def _get(url: str, attempts: int = 4) -> bytes:
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=40)
            response.raise_for_status()
            return response.content
        except requests.HTTPError:
            raise
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(10 * attempt)


def _number(value) -> float:
    try:
        return float(str(value).replace(',', '').strip())
    except (TypeError, ValueError):
        return float('nan')


def _append(path: Path, rows: list[dict], columns: list[str]):
    new = not path.exists()
    with path.open('a', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        if new:
            writer.writeheader()
        writer.writerows(rows)


def _done(path: Path, key: str) -> set:
    return set(pd.read_csv(path, dtype=str)[key]) if path.exists() else set()


# ---------------------------------------------------------------- parsers

def parse_revenue(content: bytes, month: str, universe: set[str]) -> list[dict]:
    if not content:
        return []
    rows = []
    for table in pd.read_html(io.StringIO(content.decode('big5', errors='ignore'))):
        if not isinstance(table.columns, pd.MultiIndex) or table.shape[1] < 7:
            continue
        cols = [' '.join(str(c) for c in col) for col in table.columns]
        pick = lambda key: next(i for i, c in enumerate(cols) if key in c)
        try:
            code, cur, prev, last = pick('公司 代號'), pick('當月營收'), pick('上月營收'), pick('去年當月營收')
        except StopIteration:
            continue
        for values in table.itertuples(index=False):
            ticker = str(values[code]).strip()
            if ticker in universe:
                rows.append(dict(month=month, ticker=ticker, revenue=_number(values[cur]),
                                 prev_revenue=_number(values[prev]), last_year_revenue=_number(values[last])))
    return rows


def parse_twse(payload: dict, universe: set[str]) -> list[dict]:
    fields = payload.get('fields') or []
    if not payload.get('data'):
        return []
    at = {f: i for i, f in enumerate(fields)}
    if '外陸資買賣超股數(不含外資自營商)' in at:
        foreign = lambda r: _number(r[at['外陸資買賣超股數(不含外資自營商)']]) + _number(r[at['外資自營商買賣超股數']])
    elif '外資買賣超股數' in at:
        foreign = lambda r: _number(r[at['外資買賣超股數']])
    else:
        raise ValueError(f'TWSE T86: unknown layout {fields}')
    trust = at['投信買賣超股數']
    return [dict(ticker=str(r[0]).strip(), foreign_net=foreign(r), trust_net=_number(r[trust]))
            for r in payload['data'] if str(r[0]).strip() in universe]


def parse_tpex(payload: dict, universe: set[str]) -> list[dict]:
    tables = [t for t in payload.get('tables', []) if t.get('data')]
    if not tables:
        return []
    fields, rows = tables[0]['fields'], []
    for r in tables[0]['data']:
        ticker = str(r[0]).strip()
        if ticker not in universe:
            continue
        values = [_number(x) for x in r]
        if len(fields) == 16:          # 2014-12 .. 2017: foreign total at 4, trust at 7, total at 15
            foreign, trust = values[4], values[7]
            if abs(values[4] + values[7] + values[8] - values[15]) > 1:
                raise ValueError(f'TPEx 16-field sums do not reconcile for {ticker}')
        elif len(fields) == 24:        # 2018+: 7 (buy, sell, net) groups + total
            foreign, trust = values[10], values[13]
            if abs(values[4] + values[7] - values[10]) > 1 or abs(values[10] + values[13] + values[22] - values[23]) > 1:
                raise ValueError(f'TPEx 24-field sums do not reconcile for {ticker}')
        else:
            raise ValueError(f'TPEx: unknown layout with {len(fields)} fields')
        rows.append(dict(ticker=ticker, foreign_net=foreign, trust_net=trust))
    return rows


PARSE = dict(twse=parse_twse, tpex=parse_tpex)


# ---------------------------------------------------------------- compact storage

def flow_paths(source: str) -> tuple[Path, Path]:
    return DATA / f'flows_{source}.csv', DATA / f'flows_{source}_days.csv'


def record_flow_day(source: str, day: pd.Timestamp, content: bytes, universe: set[str]) -> int:
    rows = PARSE[source](json.loads(content), universe)
    table, log = flow_paths(source)
    _append(table, [dict(date=str(day.date()), source=source, **r) for r in rows], FLOW_COLUMNS)
    _append(log, [dict(date=str(day.date()), rows=len(rows), sha256=hashlib.sha256(content).hexdigest(),
                       status='OK' if rows else 'EMPTY')], ['date', 'rows', 'sha256', 'status'])
    return len(rows)


def record_revenue_page(month: str, market: str, kind: int, content: bytes, universe: set[str]) -> int:
    rows = parse_revenue(content, month, universe)
    _append(DATA / 'revenue_rows.csv', rows, REVENUE_COLUMNS)
    _append(DATA / 'revenue_pages.csv', [dict(page=f'{month}_{market}_{kind}', rows=len(rows), bytes=len(content),
                                              sha256=hashlib.sha256(content).hexdigest())],
            ['page', 'rows', 'bytes', 'sha256'])
    return len(rows)


# ---------------------------------------------------------------- downloads

def revenue_months(end: pd.Timestamp) -> list[pd.Period]:
    """Months whose report is published (by the 10th of the next month) before ``end``."""
    last = (end - pd.DateOffset(days=11)).to_period('M') - 1
    return list(pd.period_range(START['revenue'], last, freq='M'))


def fetch_revenue(end: pd.Timestamp):
    universe, done = tickers(), _done(DATA / 'revenue_pages.csv', 'page')
    for month in revenue_months(end):
        for market in ('sii', 'otc'):
            for kind in (0, 1):
                if f'{month}_{market}_{kind}' in done:
                    continue
                url = REVENUE_URL.format(market=market, roc=month.year - 1911, month=month.month, kind=kind)
                try:
                    content = _get(url)
                except requests.HTTPError:
                    content = b''                         # page does not exist for this month / kind
                record_revenue_page(str(month), market, kind, content, universe)
                time.sleep(PAUSE['revenue'])
        print(f'revenue {month}', flush=True)


def flow_dates(source: str, end: pd.Timestamp) -> list[pd.Timestamp]:
    calendar = load_calendar()
    return list(calendar[(calendar >= START[source]) & (calendar < end)])


def fetch_flows(source: str, end: pd.Timestamp, retry_empty: bool = True):
    universe = tickers()
    table, log = flow_paths(source)
    days = pd.read_csv(log, dtype=str) if log.exists() else pd.DataFrame(columns=['date', 'status'])
    ok = set(days.date[days.status == 'OK'])
    tried = set(days.date)
    for day in flow_dates(source, end):
        key = str(day.date())
        if key in ok or (key in tried and not retry_empty):
            continue
        url = TWSE_URL.format(d=day) if source == 'twse' else TPEX_URL.format(roc=day.year - 1911, d=day)
        record_flow_day(source, day, _get(url), universe)
        time.sleep(PAUSE[source])
        if day == day + pd.offsets.MonthBegin(0) or day.is_month_start:
            print(f'{source} {key}', flush=True)


# ---------------------------------------------------------------- one-off compaction of legacy raw files

def compact() -> dict:
    """Legacy raw files -> compact tables (only START onward), re-parsed and checked, then raw deleted."""
    universe, report = tickers(), {}
    for source in ('twse', 'tpex'):
        table, log = flow_paths(source)
        if table.exists() or log.exists():
            raise SystemExit(f'{table} already exists; compaction runs once on a clean state')
        files = sorted((RAW / source).glob('*.json'))
        kept = [p for p in files if pd.Timestamp(p.stem) >= pd.Timestamp(START[source])]
        for path in kept:
            record_flow_day(source, pd.Timestamp(path.stem), path.read_bytes(), universe)
        written = pd.read_csv(table, dtype={'ticker': str}) if table.exists() else pd.DataFrame(columns=FLOW_COLUMNS)
        days = pd.read_csv(log, dtype=str)
        for path in kept:                                                 # every day re-parses to the same rows
            key = str(pd.Timestamp(path.stem).date())
            expected = PARSE[source](json.loads(path.read_bytes()), universe)
            got = written[written.date == key]
            same = sorted((r['ticker'], r['foreign_net'], r['trust_net']) for r in expected) == \
                sorted(zip(got.ticker, got.foreign_net, got.trust_net))
            if not same or days.loc[days.date == key, 'sha256'].iloc[0] != hashlib.sha256(path.read_bytes()).hexdigest():
                raise SystemExit(f'{source} {key}: compact table does not match the raw file')
        report[source] = dict(raw_files=len(files), kept_days=len(kept), rows=len(written),
                              empty_days=int((days.status == 'EMPTY').sum()))
    pages = sorted((RAW / 'revenue').glob('*.html'))
    kept = [p for p in pages if pd.Period(p.stem[:7].replace('_', '-'), 'M') >= pd.Period(START['revenue'][:7], 'M')]
    if (DATA / 'revenue_pages.csv').exists():
        raise SystemExit('revenue_pages.csv already exists; compaction runs once on a clean state')
    for path in kept:
        year, month, market, kind = path.stem.split('_')
        record_revenue_page(f'{year}-{month}', market, int(kind), path.read_bytes(), universe)
    rows = pd.read_csv(DATA / 'revenue_rows.csv', dtype={'ticker': str})
    expected = sum(len(parse_revenue(p.read_bytes(), 'x', universe)) for p in kept)
    if len(rows) != expected:
        raise SystemExit(f'revenue: {len(rows)} compact rows vs {expected} parsed')
    report['revenue'] = dict(raw_pages=len(pages), kept_pages=len(kept), rows=len(rows),
                             months=int(rows.month.nunique()))
    shutil.rmtree(RAW)
    report['raw_deleted'] = str(RAW)
    (DATA / 'compaction_report.json').write_text(json.dumps(report, indent=1))
    print(report, flush=True)
    return report


# ---------------------------------------------------------------- build

def build() -> dict:
    """Final tables for the feature panel, plus a coverage report (missing and empty days listed)."""
    revenue = pd.read_csv(DATA / 'revenue_rows.csv', dtype={'ticker': str})
    revenue = revenue.drop_duplicates(['month', 'ticker'], keep='last').sort_values(['month', 'ticker'])
    revenue.to_csv(DATA / 'revenue.csv', index=False)
    frames, report = [], dict(revenue=dict(rows=len(revenue), months=int(revenue.month.nunique())))
    for source in ('twse', 'tpex'):
        table, log = flow_paths(source)
        frames.append(pd.read_csv(table, dtype={'ticker': str}))
        days = pd.read_csv(log, dtype=str).drop_duplicates('date', keep='last')
        expected = {str(d.date()) for d in flow_dates(source, pd.Timestamp(days.date.max()) + pd.Timedelta(days=1))}
        report[source] = dict(ok_days=int((days.status == 'OK').sum()),
                              empty_days=sorted(days.date[days.status == 'EMPTY']),
                              missing_days=sorted(expected - set(days.date)))
    flows = pd.concat(frames, ignore_index=True).drop_duplicates(['date', 'ticker', 'source'], keep='last')
    flows.sort_values(['date', 'ticker']).to_csv(DATA / 'flows.csv', index=False)
    report['flows'] = dict(rows=len(flows), days=int(flows.date.nunique()))
    (DATA / 'build_report.json').write_text(json.dumps(report, indent=1))
    print({k: (v if not isinstance(v, dict) else {a: (b if not isinstance(b, list) else len(b)) for a, b in v.items()})
           for k, v in report.items()}, flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('what', choices=['revenue', 'twse', 'tpex', 'compact', 'build'])
    parser.add_argument('--end', default=str(pd.Timestamp.today().date()), help='exclusive end date')
    args = parser.parse_args(argv)
    end = pd.Timestamp(args.end)
    if args.what == 'revenue':
        fetch_revenue(end)
    elif args.what in ('twse', 'tpex'):
        fetch_flows(args.what, end)
    elif args.what == 'compact':
        compact()
    else:
        build()


if __name__ == '__main__':
    main()
