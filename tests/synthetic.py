"""Small deterministic synthetic market for fast tests."""
from __future__ import annotations

import numpy as np
import pandas as pd

from competition.data import MarketData


def synthetic_market(n_days: int = 320, n_symbols: int = 32, seed: int = 0, official_from: int | None = None,
                     start: str = '2015-01-05') -> MarketData:
    """Random-walk panel with 2330.TW plus S000.TW..; official VWAP from row ``official_from``."""
    rng = np.random.default_rng(seed)
    calendar = pd.bdate_range(start, periods=n_days)
    symbols = ['2330.TW'] + [f'S{i:03d}.TW' for i in range(n_symbols - 1)]
    drift = rng.normal(0, .0005, n_symbols)
    ret = pd.DataFrame(rng.normal(drift, .015, (n_days, n_symbols)), index=calendar, columns=symbols)
    ret.iloc[0] = np.nan
    base = rng.uniform(20, 400, n_symbols)
    close = base * (1 + ret.fillna(0.)).cumprod()
    spread = close * rng.uniform(.002, .02, (n_days, n_symbols))
    open_ = close.shift().fillna(close) * (1 + rng.normal(0, .004, (n_days, n_symbols)))
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    vwap = (high + low + close) / 3 * (1 + rng.normal(0, .001, (n_days, n_symbols)))
    if official_from is None:
        vwap[:] = np.nan
    else:
        vwap.iloc[:official_from] = np.nan
    ones = pd.DataFrame(1., index=calendar, columns=symbols)
    return MarketData(
        calendar=calendar, open=open_, high=high, low=low, close=close,
        volume=pd.DataFrame(rng.uniform(1e6, 1e7, (n_days, n_symbols)), index=calendar, columns=symbols),
        ret=ret, valid=ones.astype(bool), split=ones, dividend=ones * 0., quality_flag=~ones.astype(bool),
        official_vwap=vwap, benchmark_ret=ret.mean(axis=1), provenance={'synthetic': str(seed)})
