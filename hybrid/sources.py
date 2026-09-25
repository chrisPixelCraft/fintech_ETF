"""Point-in-time raw data for the Hybrid spec (docs/hybrid_spec.md section 4): monthly revenue and daily
institutional net buying, resumable, one raw file per request under data/hybrid/raw/.

    .venv/bin/python -m hybrid.sources revenue            # MOPS t21sc03, 2010-01 .. last published month
    .venv/bin/python -m hybrid.sources twse               # TWSE T86, 2012-05-02 .. (earlier dates do not exist)
    .venv/bin/python -m hybrid.sources tpex               # TPEx 3insti, 2014-12-01 .. (earlier dates do not exist)
    .venv/bin/python -m hybrid.sources build              # parse raw files -> data/hybrid/{revenue,flows}.csv

Revenue: one row per (month, ticker) with the report's own current, previous-month and last-year revenue
(thousand TWD); a month is usable from the 11th of the next month. Flows: net shares bought per day by
foreign investors (incl. foreign dealers, for one definition across report layouts) and investment trusts.
"""
from __future__ import annotations

import argparse
import io
import json
import time
from pathlib import Path

import pandas as pd
import requests

from competition.data import load_calendar
from competition.rules import ROOT, UNIVERSE_PATH

DATA = ROOT / 'data/hybrid'
RAW = DATA / 'raw'
HEADERS = {'User-Agent': 'Mozilla/5.0 (fintech_ETF research data)'}
REVENUE_URL = 'https://mopsov.twse.com.tw/nas/t21/{market}/t21sc03_{roc}_{month}_{kind}.html'
TWSE_URL = 'https://www.twse.com.tw/rwd/zh/fund/T86?date={d:%Y%m%d}&selectType=ALLBUT0999&response=json'
TPEX_URL = ('https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php'
            '?l=zh-tw&se=EW&t=D&d={roc}/{d:%m/%d}&o=json')
START = dict(revenue='2010-01-01', twse='2012-05-02', tpex='2014-12-01')
PAUSE = dict(revenue=2., twse=3.5, tpex=2.)


def tickers() -> set[str]:
    return set(pd.read_csv(UNIVERSE_PATH, dtype={'ticker': str}).ticker)


def _get(url: str, attempts: int = 4) -> bytes:
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=40)
            response.raise_for_status()
            return response.content
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(10 * attempt)


def _number(value) -> float:
    try:
        return float(str(value).replace(',', '').strip())
    except (TypeError, ValueError):
        return float('nan')


# ---------------------------------------------------------------- revenue

def revenue_months(end: pd.Timestamp) -> list[pd.Period]:
    """Months whose report is published (by the 10th of the next month) before ``end``."""
    last = (end - pd.DateOffset(days=11)).to_period('M') - 1
    return list(pd.period_range(START['revenue'], last, freq='M'))


def fetch_revenue(end: pd.Timestamp):
    for month in revenue_months(end):
        for market in ('sii', 'otc'):
            for kind in (0, 1):
                path = RAW / 'revenue' / f'{month.year}_{month.month:02d}_{market}_{kind}.html'
                if path.exists():
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                url = REVENUE_URL.format(market=market, roc=month.year - 1911, month=month.month, kind=kind)
                try:
                    path.write_bytes(_get(url))
                except requests.HTTPError:
                    path.write_bytes(b'')                 # page does not exist for this month / kind
                time.sleep(PAUSE['revenue'])
        print(f'revenue {month}', flush=True)


def parse_revenue(content: bytes, month: pd.Period, universe: set[str]) -> list[dict]:
    if not content:
        return []
    text = content.decode('big5', errors='ignore')
    rows = []
    for table in pd.read_html(io.StringIO(text)):
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
                rows.append(dict(month=str(month), ticker=ticker, revenue=_number(values[cur]),
                                 prev_revenue=_number(values[prev]), last_year_revenue=_number(values[last])))
    return rows


# ---------------------------------------------------------------- institutional flows

def flow_dates(source: str, end: pd.Timestamp) -> list[pd.Timestamp]:
    calendar = load_calendar()
    days = calendar[(calendar >= START[source]) & (calendar < end)]
    return list(days)


def fetch_flows(source: str, end: pd.Timestamp):
    for day in flow_dates(source, end):
        path = RAW / source / f'{day:%Y%m%d}.json'
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        url = TWSE_URL.format(d=day) if source == 'twse' else TPEX_URL.format(roc=day.year - 1911, d=day)
        path.write_bytes(_get(url))
        time.sleep(PAUSE[source])
        if day.day == 1 or day == day + pd.offsets.MonthBegin(0):
            print(f'{source} {day.date()}', flush=True)


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
    table = tables[0]
    fields, rows = table['fields'], []
    for r in table['data']:
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


# ---------------------------------------------------------------- build

def build() -> dict:
    universe, report = tickers(), {}
    rows = []
    for path in sorted((RAW / 'revenue').glob('*.html')):
        year, month = map(int, path.stem.split('_')[:2])
        rows += parse_revenue(path.read_bytes(), pd.Period(f'{year}-{month:02d}', 'M'), universe)
    revenue = pd.DataFrame(rows).drop_duplicates(['month', 'ticker'], keep='last').sort_values(['month', 'ticker'])
    revenue.to_csv(DATA / 'revenue.csv', index=False)
    report['revenue'] = dict(rows=len(revenue), months=int(revenue.month.nunique()),
                             tickers=int(revenue.ticker.nunique()))
    frames = []
    for source, parse in (('twse', parse_twse), ('tpex', parse_tpex)):
        for path in sorted((RAW / source).glob('*.json')):
            parsed = parse(json.loads(path.read_bytes()), universe)
            frames.append(pd.DataFrame(parsed).assign(date=pd.Timestamp(path.stem).date(), source=source))
        report[source] = len(list((RAW / source).glob('*.json')))
    flows = pd.concat(frames, ignore_index=True)[['date', 'ticker', 'foreign_net', 'trust_net', 'source']]
    flows.sort_values(['date', 'ticker']).to_csv(DATA / 'flows.csv', index=False)
    report['flows'] = dict(rows=len(flows), days=int(flows.date.nunique()), tickers=int(flows.ticker.nunique()))
    print(report, flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('what', choices=['revenue', 'twse', 'tpex', 'build'])
    parser.add_argument('--end', default=str(pd.Timestamp.today().date()), help='exclusive end date')
    args = parser.parse_args(argv)
    end = pd.Timestamp(args.end)
    if args.what == 'revenue':
        fetch_revenue(end)
    elif args.what in ('twse', 'tpex'):
        fetch_flows(args.what, end)
    else:
        build()


if __name__ == '__main__':
    main()
