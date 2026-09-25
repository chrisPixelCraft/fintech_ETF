"""Point-in-time shares issued (發行股數) for H2 (docs/momentum_v2_spec.md section 4), stored compactly.

    .venv/bin/python -m momv2.sources fetch             # first session of each month, 2014-01 .. --end
    .venv/bin/python -m momv2.sources build             # -> data/momv2/shares.csv + build_report.json

Sources, both published after the close of the report date:
- TWSE 外資及陸資投資持股統計 ``MI_QFIIS`` (column 發行股數)
- TPEx 僑外資及陸資持股比例 ``qfii`` (column 發行股數(A))

One snapshot per market on the first calendar session of each month. Only the
150-name rows are kept, with a log row (date, market, sha256 of the raw
response, row count) so downloads resume and every row stays traceable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time

import pandas as pd

from competition.data import load_calendar
from competition.rules import ROOT
from hybrid.sources import _append, _get, _number, tickers

DATA = ROOT / 'data/momv2'
URL = dict(
    twse='https://www.twse.com.tw/rwd/zh/fund/MI_QFIIS?date={d:%Y%m%d}&selectType=ALLBUT0999&response=json',
    tpex='https://www.tpex.org.tw/web/stock/3insti/qfii/qfii_result.php?l=zh-tw&d={roc}/{d:%m/%d}&o=json')
PAUSE = dict(twse=3.5, tpex=2.)
START = '2014-01-01'
ROW_COLUMNS = ['date', 'market', 'ticker', 'shares']
LOG_COLUMNS = ['date', 'market', 'rows', 'sha256', 'status']


def parse(market: str, payload: dict, universe: set[str]) -> list[dict]:
    if market == 'twse':
        fields, data = payload.get('fields') or [], payload.get('data') or []
        code, shares = 0, fields.index('發行股數') if '發行股數' in fields else None
    else:
        tables = [t for t in payload.get('tables', []) if t.get('data')]
        fields, data = (tables[0]['fields'], tables[0]['data']) if tables else ([], [])
        code, shares = (fields.index('代號'), fields.index('發行股數(A)')) if data else (None, None)
    if data and shares is None:
        raise ValueError(f'{market}: unknown layout {fields}')
    return [dict(ticker=str(r[code]).strip(), shares=_number(r[shares])) for r in data
            if str(r[code]).strip() in universe]


def snapshot_dates(end: pd.Timestamp) -> list[pd.Timestamp]:
    calendar = load_calendar()
    days = pd.Series(calendar[(calendar >= START) & (calendar < end)])
    return list(days.groupby(days.dt.to_period('M')).min())


def fetch(end: pd.Timestamp):
    DATA.mkdir(parents=True, exist_ok=True)
    universe, log_path = tickers(), DATA / 'shares_log.csv'
    done = set()
    if log_path.exists():
        log = pd.read_csv(log_path, dtype=str)
        done = set(zip(log.date[log.status == 'OK'], log.market[log.status == 'OK']))
    for day in snapshot_dates(end):
        for market in ('twse', 'tpex'):
            key = str(day.date())
            if (key, market) in done:
                continue
            url = URL[market].format(d=day, roc=day.year - 1911)
            content = _get(url)
            rows = parse(market, json.loads(content), universe)
            _append(DATA / 'shares_rows.csv', [dict(date=key, market=market, **r) for r in rows], ROW_COLUMNS)
            _append(log_path, [dict(date=key, market=market, rows=len(rows),
                                    sha256=hashlib.sha256(content).hexdigest(), status='OK' if rows else 'EMPTY')],
                    LOG_COLUMNS)
            time.sleep(PAUSE[market])
        print(f'shares {day.date()}', flush=True)


def build() -> dict:
    rows = pd.read_csv(DATA / 'shares_rows.csv', dtype={'ticker': str})
    rows = rows.drop_duplicates(['date', 'ticker'], keep='last').sort_values(['date', 'ticker'])
    rows = rows[rows.shares > 0]
    rows[['date', 'ticker', 'shares']].to_csv(DATA / 'shares.csv', index=False)
    log = pd.read_csv(DATA / 'shares_log.csv', dtype=str).drop_duplicates(['date', 'market'], keep='last')
    per_date = rows.groupby('date').ticker.nunique()
    report = dict(rows=len(rows), snapshots=int(per_date.size), first=rows.date.min(), last=rows.date.max(),
                  tickers=int(rows.ticker.nunique()), names_per_snapshot=dict(min=int(per_date.min()),
                                                                              median=float(per_date.median())),
                  empty=sorted(f'{d} {m}' for d, m in zip(log.date[log.status == 'EMPTY'],
                                                           log.market[log.status == 'EMPTY'])))
    (DATA / 'build_report.json').write_text(json.dumps(report, indent=1))
    print(report, flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('what', choices=['fetch', 'build'])
    parser.add_argument('--end', default=str(pd.Timestamp.today().date()), help='exclusive end date')
    args = parser.parse_args(argv)
    fetch(pd.Timestamp(args.end)) if args.what == 'fetch' else build()


if __name__ == '__main__':
    main()
