"""Prediction targets built only from as-of data, plus evaluation labels.

Input series (what AutoTS forecasts), all causal:
- ``log_price``: log of the cumulative action-neutral return index (dividend-
  and split-neutral, built from past returns only; ``adj_close`` is never used).
- ``relative_log_price``: ``log_price`` minus a market log index (equal-weight
  universe or 0050), so the forecast change is an excess return.
- ``log_return``: daily log returns (the forecast h-day return is their sum).

Evaluation labels (never used for decisions):
- ``forward_log_return``: sum of log returns over t+1..t+h.
- ``fill_aligned_return``: close(t+h-1) / fill(t) - 1, the return of a trade
  filled at day t's average price and valued at later closes (docs/task.md §11).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from competition.data import AsOfView

SERIES_KINDS = ('log_price', 'relative_log_price', 'log_return')
MARKETS = ('ew', '0050')


def log_returns(ret: pd.DataFrame) -> pd.DataFrame:
    return np.log1p(ret)


def log_price_index(ret: pd.DataFrame) -> pd.DataFrame:
    """Cumulative log return index; NaN before a name's first observation, flat over gaps."""
    started = ret.notna().cummax()
    return log_returns(ret).fillna(0.).cumsum().where(started)


def market_log_index(view: AsOfView, market: str = 'ew') -> pd.Series:
    if market == 'ew':
        return log_returns(view.ret).mean(axis=1).fillna(0.).cumsum()
    if market == '0050':
        return np.log1p(view.benchmark_ret).fillna(0.).cumsum()
    raise ValueError(f'Unknown market {market}')


def build_series(view: AsOfView, kind: str = 'relative_log_price', market: str = 'ew') -> pd.DataFrame:
    """Wide (date x symbol) input series for AutoTS as of ``view.date``."""
    if kind == 'log_price':
        return log_price_index(view.ret)
    if kind == 'relative_log_price':
        return log_price_index(view.ret).sub(market_log_index(view, market), axis=0)
    if kind == 'log_return':
        return log_returns(view.ret).where(view.ret.notna().cummax())
    raise ValueError(f'Unknown series kind {kind}')


def forward_log_return(ret: pd.DataFrame, h: int) -> pd.DataFrame:
    """Label at t: sum of log returns over t+1..t+h (uses the future by design)."""
    return log_returns(ret).fillna(0.).rolling(h).sum().shift(-h)


def excess(frame: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional demeaning (relative-to-equal-weight label)."""
    return frame.sub(frame.mean(axis=1), axis=0)


def fill_aligned_return(close: pd.DataFrame, fill: pd.DataFrame, h: int) -> pd.DataFrame:
    """Label at t: close(t+h-1) / fill(t) - 1 (uses the future by design)."""
    return close.shift(-(h - 1)) / fill - 1


def cross_sectional_rank(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.rank(axis=1, pct=True)
