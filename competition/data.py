"""Market data panel and the as-of accessor that enforces the information cutoff.

Sources (read-only):
- Yahoo nominal daily panel ``data/yahoo_daily/v3_20260923/nominal_daily.parquet``
  (prices, volume, splits, cash dividends, ``action_neutral_return``).
  ``adj_close`` embeds future dividends and is never loaded.
- Official universe daily table ``data/tuning_2nd/official_universe/processed/daily.csv``
  (``execution_vwap`` = official turnover / volume, 2024-2026; 5371 history).
- Research calendar ``calendar_v2.json`` (observed-session proxy, not official).
- Universe CSV (150 names, 2026-07-31 list; back-applied = survivorship bias).

Every frame is wide (calendar x symbol). Strategies only ever see an
``AsOfView`` holding rows dated <= the view date.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np
import pandas as pd

from competition.rules import ROOT, UNIVERSE_PATH

YAHOO_PATH = ROOT / 'data/yahoo_daily/v3_20260923/nominal_daily.parquet'
CALENDAR_PATH = ROOT / 'data/yahoo_daily/v3_20260923/calendar_v2.json'
OFFICIAL_PATH = ROOT / 'data/tuning_2nd/official_universe/processed/daily.csv'
BENCHMARK = '0050.TW'
# 5371.TWO (中光電) became 3718.TWO (中光電投控) on 2026-09-03; Yahoo has only the new code.
RENAMES = {'5371.TWO': '3718.TWO'}

HISTORY_FIELDS = ('open', 'high', 'low', 'close', 'volume', 'ret', 'valid', 'official_vwap')


def sha256(path: Path | str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_universe(path: Path | str = UNIVERSE_PATH) -> list[str]:
    frame = pd.read_csv(path, dtype={'ticker': str})
    symbols = sorted(frame.yahoo_symbol)
    if len(symbols) != 150 or len(set(symbols)) != 150 or BENCHMARK in symbols:
        raise ValueError('Expected exactly 150 unique universe symbols without 0050')
    return symbols


def load_calendar(path: Path | str = CALENDAR_PATH) -> pd.DatetimeIndex:
    dates = pd.DatetimeIndex(pd.to_datetime(json.loads(Path(path).read_text())['calendar']))
    if not dates.is_unique or not dates.is_monotonic_increasing:
        raise ValueError('Calendar must be unique and increasing')
    return dates


@dataclass(frozen=True, eq=False)
class AsOfView:
    """Everything observable after the close of ``date`` (rows <= date only)."""
    date: pd.Timestamp
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    ret: pd.DataFrame          # action-neutral simple return, NaN where flagged
    valid: pd.DataFrame        # valid price, positive volume, not flagged
    official_vwap: pd.DataFrame  # official average price (published after each close), NaN if absent
    benchmark_ret: pd.Series   # 0050 action-neutral return

    @property
    def symbols(self) -> list[str]:
        return list(self.close.columns)


@dataclass(frozen=True, eq=False)
class MarketData:
    calendar: pd.DatetimeIndex
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    ret: pd.DataFrame
    valid: pd.DataFrame
    split: pd.DataFrame        # share multiplier effective on the row date (1 = none)
    dividend: pd.DataFrame     # cash dividend per pre-action share on the ex date
    quality_flag: pd.DataFrame  # True where the vendor row is flagged
    official_vwap: pd.DataFrame  # official average price in panel share units, NaN if absent
    benchmark_ret: pd.Series
    provenance: dict

    @property
    def symbols(self) -> list[str]:
        return list(self.close.columns)

    def position(self, date) -> int:
        """Index of ``date`` in the calendar; raises if it is not a session."""
        i = int(self.calendar.searchsorted(pd.Timestamp(date)))
        if i >= len(self.calendar) or self.calendar[i] != pd.Timestamp(date):
            raise KeyError(f'{date} is not a calendar session')
        return i

    def asof(self, date) -> AsOfView:
        stop = self.position(date) + 1
        cut = {f: getattr(self, f).iloc[:stop] for f in HISTORY_FIELDS}
        return AsOfView(date=self.calendar[stop - 1], benchmark_ret=self.benchmark_ret.iloc[:stop], **cut)

    def with_frames(self, **frames) -> 'MarketData':
        values = {f.name: getattr(self, f.name) for f in fields(self)}
        values.update(frames)
        return MarketData(**values)


def _wide(long: pd.DataFrame, column: str, calendar, symbols) -> pd.DataFrame:
    return long.pivot(index='date', columns='symbol', values=column).reindex(index=calendar, columns=symbols)


def load_market(yahoo_path=YAHOO_PATH, official_path=OFFICIAL_PATH, calendar_path=CALENDAR_PATH,
                universe_path=UNIVERSE_PATH) -> MarketData:
    symbols = load_universe(universe_path)
    calendar = load_calendar(calendar_path)
    cols = ['date', 'symbol', 'open', 'high', 'low', 'close', 'volume', 'dividend', 'split',
            'valid_price', 'tradable', 'action_neutral_return', 'quality_flags', 'valid_for_research']
    yahoo = pd.read_parquet(yahoo_path, columns=cols)
    yahoo['date'] = pd.to_datetime(yahoo.date)
    official = pd.read_csv(official_path, usecols=['date', 'symbol', 'open', 'high', 'low', 'close', 'volume',
                                                     'dividend', 'split', 'execution_vwap'])
    official['date'] = pd.to_datetime(official.date)
    official['symbol'] = official.symbol.replace(RENAMES)

    # Backfill renamed names (3718 <- 5371) from the official table before their Yahoo start.
    extra = []
    for new in RENAMES.values():
        first = yahoo.loc[yahoo.symbol == new, 'date'].min()
        rows = official[(official.symbol == new) & (official.date < first)].sort_values('date').copy()
        prices = rows[['open', 'high', 'low', 'close']]
        rows['valid_price'] = np.isfinite(prices).all(axis=1) & (prices > 0).all(axis=1)
        rows['tradable'] = rows.valid_price & rows.volume.gt(0)
        rows['action_neutral_return'] = (rows.close * rows.split + rows.dividend) / rows.close.shift() - 1
        rows['quality_flags'] = ''
        rows['valid_for_research'] = rows.valid_price
        extra.append(rows[cols])
    yahoo = pd.concat([yahoo, *extra], ignore_index=True)
    stocks = yahoo[yahoo.symbol.isin(symbols)]

    valid_price = _wide(stocks, 'valid_price', calendar, symbols).eq(True)
    frames = {c: _wide(stocks, c, calendar, symbols).where(valid_price) for c in ('open', 'high', 'low', 'close')}
    research_ok = _wide(stocks, 'valid_for_research', calendar, symbols).eq(True)
    frames['volume'] = _wide(stocks, 'volume', calendar, symbols)
    frames['ret'] = _wide(stocks, 'action_neutral_return', calendar, symbols).where(research_ok)
    frames['valid'] = research_ok & _wide(stocks, 'tradable', calendar, symbols).eq(True)
    frames['split'] = _wide(stocks, 'split', calendar, symbols).fillna(1.)
    frames['dividend'] = _wide(stocks, 'dividend', calendar, symbols).fillna(0.)
    frames['quality_flag'] = _wide(stocks, 'quality_flags', calendar, symbols).fillna('').ne('')

    # Official average price, converted to the panel's share units via the same-day close ratio.
    off = official[official.symbol.isin(symbols)]
    vwap = _wide(off, 'execution_vwap', calendar, symbols)
    ratio = frames['close'] / _wide(off, 'close', calendar, symbols)
    frames['official_vwap'] = (vwap * ratio).where(vwap.gt(0) & np.isfinite(ratio))

    bench = yahoo[yahoo.symbol == BENCHMARK].set_index('date')
    benchmark_ret = bench.action_neutral_return.where(bench.valid_for_research).reindex(calendar)
    provenance = {str(Path(p).relative_to(ROOT)) if Path(p).is_relative_to(ROOT) else str(p): sha256(p)
                  for p in (yahoo_path, official_path, calendar_path, universe_path)}
    return MarketData(calendar=calendar, benchmark_ret=benchmark_ret, provenance=provenance, **frames)
