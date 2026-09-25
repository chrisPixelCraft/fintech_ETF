"""Daily market data for production (docs/production_spec.md section 4).

Sources, in order of trust:
- Official T-1 daily quotes: TWSE MI_INDEX (listed) and TPEx dailyQuotes (OTC).
  They give the exchange close used by the official lot formula (C2) and the
  official average price (trading value / volume) used to settle the ledger.
  Parsing is copied from legacy/src/v4_execution.py: tables are found by column
  names and a response for another date is rejected.
- Yahoo daily history, refreshed with the same downloader that built the
  research snapshot (legacy/scripts/download_yahoo_daily.py), so the
  production signal uses the same action-neutral returns as the backtest.

Every raw official response is stored with its sha256 under the run folder.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from competition.data import CALENDAR_PATH, OFFICIAL_PATH, YAHOO_PATH, MarketData, load_market
from competition.rules import ROOT, UNIVERSE_PATH

OFFICIAL_COLUMNS = ['date', 'symbol', 'open', 'high', 'low', 'close', 'volume', 'dividend', 'split', 'execution_vwap']
CLOSE_TOLERANCE = .005          # Yahoo vs exchange close: relative gap counted as a disagreement
MAX_DISAGREEMENTS = 5           # more disagreeing names than this fails the data gate
URLS = {
    'TWSE': 'https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={d:%Y%m%d}&type=ALLBUT0999',
    'TPEx': 'https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={d:%Y/%m/%d}&id=&response=json',
}
FIELDS = {
    'TWSE': dict(symbol='證券代號', open='開盤價', high='最高價', low='最低價', close='收盤價', volume='成交股數',
                 trading_value='成交金額'),
    'TPEx': dict(symbol='代號', open='開盤', high='最高', low='最低', close='收盤', volume='成交股數',
                 trading_value='成交金額(元)'),
}
HEADERS = {'User-Agent': 'Mozilla/5.0 (fintech_ETF production data check)'}


def _number(value) -> float:
    try:
        return float(str(value).replace(',', '').strip())
    except (TypeError, ValueError):
        return np.nan


def _date(value) -> pd.Timestamp:
    text = str(value).strip().replace('-', '/')
    if '/' in text:
        parts = text.split('/')
        if int(parts[0]) < 1911:                  # ROC year
            parts[0] = str(int(parts[0]) + 1911)
        text = '/'.join(parts)
    return pd.Timestamp(text).normalize()


def parse_official_day(payload: dict, market: str, date, symbols: set) -> pd.DataFrame:
    """Rows of the exact-unit daily table for ``symbols``; another date or an ambiguous table raises."""
    if 'date' not in payload or _date(payload['date']) != _date(date):
        raise ValueError(f'{market}: response date does not match {pd.Timestamp(date).date()}')
    names = FIELDS[market]
    tables = [t for t in payload.get('tables', []) if all(v in t.get('fields', []) for v in names.values())
              and (market != 'TPEx' or t.get('title', '上櫃股票行情') == '上櫃股票行情')]
    if len(tables) != 1:
        raise ValueError(f'{market}: missing or ambiguous daily table')
    fields, rows = tables[0]['fields'], []
    suffix = '.TW' if market == 'TWSE' else '.TWO'
    for values in tables[0]['data']:
        row = {k: values[fields.index(v)] for k, v in names.items()}
        symbol = str(row['symbol']).strip() + suffix
        if symbol not in symbols:
            continue
        row = {k: _number(v) for k, v in row.items() if k != 'symbol'}
        valid = all(np.isfinite(row[k]) and row[k] > 0 for k in ('volume', 'trading_value', 'close'))
        rows.append(dict(symbol=symbol, **row, execution_vwap=row['trading_value'] / row['volume'] if valid else np.nan,
                         market=market))
    return pd.DataFrame(rows)


def fetch_official_day(date, raw_dir: Path | None = None, attempts: int = 3) -> tuple[pd.DataFrame, list[dict]]:
    """Official T-1 quotes for the 150 names plus a provenance record per exchange."""
    universe = set(pd.read_csv(UNIVERSE_PATH).yahoo_symbol)
    day, frames, provenance = pd.Timestamp(date), [], []
    for market, template in URLS.items():
        url = template.format(d=day)
        for attempt in range(1, attempts + 1):
            try:
                response = requests.get(url, timeout=30, headers=HEADERS)
                response.raise_for_status()
                content = response.content
                frame = parse_official_day(json.loads(content), market, day, universe)
                break
            except Exception as error:              # retried, then reported by the caller's data gate
                if attempt == attempts:
                    provenance.append(dict(market=market, url=url, status='FAILED', error=repr(error)))
                    frame, content = None, None
                else:
                    time.sleep(2 ** attempt)
        if frame is None:
            continue
        digest = hashlib.sha256(content).hexdigest()
        if raw_dir is not None:
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f'{market}_{day:%Y%m%d}.json').write_bytes(content)
        provenance.append(dict(market=market, url=url, status='OK', rows=len(frame), sha256=digest))
        frames.append(frame)
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=['symbol', 'close'])
    out['date'] = day
    return out, provenance


def update_official_store(store: Path, dates, raw_dir: Path) -> list[dict]:
    """Append official rows for ``dates`` missing from ``store`` (CSV in load_market's official format)."""
    have = set(pd.read_csv(store, usecols=['date']).date) if store.exists() else set()
    report = []
    for day in dates:
        key = str(pd.Timestamp(day).date())
        if key in have:
            continue
        frame, provenance = fetch_official_day(day, raw_dir)
        report.append(dict(date=key, provenance=provenance, rows=len(frame)))
        if frame.empty:
            continue
        rows = frame.assign(date=key, dividend=0., split=1.)[OFFICIAL_COLUMNS]
        rows.to_csv(store, mode='a', header=not store.exists(), index=False)
    return report


def combined_official(store: Path, out: Path) -> Path:
    """Research official table + production-fetched days (fetched rows win on overlap)."""
    base = pd.read_csv(OFFICIAL_PATH, usecols=OFFICIAL_COLUMNS)
    if store.exists():
        extra = pd.read_csv(store)
        base = pd.concat([base[~base.date.isin(set(extra.date))], extra], ignore_index=True)
    base.sort_values(['date', 'symbol']).to_csv(out, index=False)
    return out


def refresh_yahoo(end, cache_root: Path = ROOT / 'data/yahoo_daily') -> Path:
    """Download a fresh immutable Yahoo cache through ``end`` (exclusive); reused when already complete."""
    folder = cache_root / f'v3_{pd.Timestamp(end):%Y%m%d}'
    if not (folder / 'calendar_v2.json').exists():
        subprocess.run([sys.executable, str(ROOT / 'legacy/scripts/download_yahoo_daily.py'), '--cache-dir',
                        str(folder), '--end', str(pd.Timestamp(end).date())], check=True, cwd=ROOT)
    return folder


def load_live_market(yahoo_dir: Path | None, official_csv: Path | None) -> MarketData:
    return load_market(yahoo_path=(yahoo_dir / 'nominal_daily.parquet') if yahoo_dir else YAHOO_PATH,
                       official_path=official_csv or OFFICIAL_PATH,
                       calendar_path=(yahoo_dir / 'calendar_v2.json') if yahoo_dir else CALENDAR_PATH)


def cross_check(market: MarketData, prev, official: pd.DataFrame) -> dict:
    """Yahoo T-1 close vs exchange close; problems empty when the two sources agree."""
    prev = pd.Timestamp(prev)
    if official.empty:
        return dict(problems=['OFFICIAL_T-1_MISSING'], disagreements={}, covered=0)
    exchange = official.set_index('symbol').close
    if prev not in market.close.index:
        return dict(problems=['YAHOO_T-1_MISSING'], disagreements={}, covered=int(exchange.notna().sum()))
    vendor = market.close.loc[prev].reindex(exchange.index)
    gap = (vendor / exchange - 1).abs()
    bad = gap[(gap > CLOSE_TOLERANCE) | (vendor.isna() & exchange.notna())]
    problems = [f'CLOSE_DISAGREEMENT:{len(bad)}'] if len(bad) > MAX_DISAGREEMENTS else []
    return dict(problems=problems, disagreements={s: float(g) for s, g in bad.items()},
                covered=int(exchange.notna().sum()))


def with_exchange_close(market: MarketData, prev, official: pd.DataFrame) -> MarketData:
    """Market whose T-1 close is the exchange close where one exists (the C2 sizing price)."""
    if official.empty:
        return market
    close = market.close.copy()
    exchange = official.set_index('symbol').close.reindex(close.columns)
    close.loc[pd.Timestamp(prev)] = exchange.where(np.isfinite(exchange), close.loc[pd.Timestamp(prev)])
    return market.with_frames(close=close)


def append_official_row(market: MarketData, prev, official: pd.DataFrame) -> MarketData:
    """Backup when Yahoo lacks T-1: add the exchange row for T-1 (return unknown, so the normal path is barred)."""
    prev = pd.Timestamp(prev)
    if prev in market.calendar:
        return market
    row = official.set_index('symbol').reindex(market.symbols)
    calendar = market.calendar.append(pd.DatetimeIndex([prev]))

    def extend(frame, values):
        return pd.concat([frame, pd.DataFrame([values], index=[prev], columns=frame.columns)])

    close = row.close.where(row.close > 0)
    valid = close.notna() & row.volume.gt(0)
    frames = {name: extend(getattr(market, name), row[name].where(close.notna()).to_numpy())
              for name in ('open', 'high', 'low', 'close')}
    frames.update(volume=extend(market.volume, row.volume.to_numpy()),
                  ret=extend(market.ret, np.full(len(market.symbols), np.nan)),
                  valid=extend(market.valid, valid.to_numpy()),
                  split=extend(market.split, np.ones(len(market.symbols))),
                  dividend=extend(market.dividend, np.zeros(len(market.symbols))),
                  quality_flag=extend(market.quality_flag, np.zeros(len(market.symbols), dtype=bool)),
                  official_vwap=extend(market.official_vwap, row.execution_vwap.to_numpy()))
    bench = pd.concat([market.benchmark_ret, pd.Series([np.nan], index=[prev])])
    return market.with_frames(calendar=calendar, benchmark_ret=bench, **frames)
