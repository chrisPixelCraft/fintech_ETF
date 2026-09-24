"""Causal per-symbol features of the JPX #2 family, computed from an AsOfView only.

Every frame is wide (date x symbol) and every rolling window runs down one
symbol's column, so no value at row t reads a row after t or another symbol.

- raw: open, high, low, close, volume as observed (NaN where the price is invalid).
- return_h: P(t) / P(t-h) - 1 on the signal price P.
- volatility_h: std of daily log returns over the last h sessions (all h observed).
- ma_gap_h: P(t) / SMA_h(P)(t), as in JPX #2 (not minus one).

The signal price P is the action-neutral price index (dividend- and
split-neutral, built from past returns only; ``adj_close`` is never used).
Nothing is back-filled and nothing is normalised over the full sample.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autots_strategy.targets import log_price_index, log_returns
from competition.data import AsOfView

WINDOWS = (20, 40, 60)
RAW = ('open', 'high', 'low', 'close', 'volume')
COLUMNS = (*RAW, *(f'return_{h}' for h in WINDOWS), *(f'volatility_{h}' for h in WINDOWS),
           *(f'ma_gap_{h}' for h in WINDOWS))


def signal_price(ret: pd.DataFrame) -> pd.DataFrame:
    """Action-neutral price index: NaN before a name's first observed return, flat over missing returns."""
    return np.exp(log_price_index(ret))


def trailing_return(price: pd.DataFrame, h: int) -> pd.DataFrame:
    return price / price.shift(h) - 1


def trailing_volatility(ret: pd.DataFrame, h: int) -> pd.DataFrame:
    """NaN unless all of the last ``h`` daily returns are observed."""
    return log_returns(ret).rolling(h, min_periods=h).std()


def ma_gap(price: pd.DataFrame, h: int) -> pd.DataFrame:
    return price / price.rolling(h, min_periods=h).mean()


def feature_frames(view: AsOfView) -> dict[str, pd.DataFrame]:
    """Every feature as a wide frame over the whole view (rows <= view.date)."""
    price = signal_price(view.ret)
    frames = {name: getattr(view, name).astype(float) for name in RAW}
    for h in WINDOWS:
        frames[f'return_{h}'] = trailing_return(price, h)
        frames[f'volatility_{h}'] = trailing_volatility(view.ret, h)
        frames[f'ma_gap_{h}'] = ma_gap(price, h)
    return frames


def stack(frames: dict[str, pd.DataFrame], valid: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Long table (date, symbol, features..., feature_ready) for ``dates``, sorted by date then symbol.

    ``feature_ready``: the name is valid (tradable, research-clean) on that date and every feature is finite.
    """
    symbols = sorted(valid.columns)
    index = pd.MultiIndex.from_product([dates, symbols], names=['date', 'symbol'])
    table = pd.DataFrame({name: frames[name].reindex(index=dates, columns=symbols).stack(future_stack=True)
                          for name in COLUMNS}, index=index)
    finite = np.isfinite(table.to_numpy(float)).all(axis=1)
    ready = valid.reindex(index=dates, columns=symbols).fillna(False).astype(bool).stack(future_stack=True)
    table['feature_ready'] = finite & ready.to_numpy()
    return table.reset_index()


def build_features(view: AsOfView, dates: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    """Feature table for ``dates`` (default: every date of the view); all inputs are rows <= view.date."""
    dates = view.close.index if dates is None else pd.DatetimeIndex(dates)
    if len(dates) and dates.max() > view.date:
        raise ValueError(f'Feature dates run past the view cutoff {view.date.date()}')
    return stack(feature_frames(view), view.valid, dates)
