"""Panic gate (docs/hybrid_spec.md section 2), from 0050 action-neutral daily returns only.

For a session t (information through the close of t, used by the next decision day):
    R20(t)       = prod(1 + r) over the 20 sessions ending t, minus 1
    vol20(t)     = sample std of log(1 + r) over the same 20 sessions (all 20 observed)
    threshold(t) = median of vol20 over every session from the data start through t
    PANIC(t)     = R20(t) < 0 and vol20(t) > threshold(t); False with fewer than 250 vol20 values
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WINDOW, MIN_HISTORY = 20, 250


def panic_table(benchmark_ret: pd.Series) -> pd.DataFrame:
    """Gate inputs and state per session; row t uses rows <= t only."""
    log_ret = np.log1p(benchmark_ret)
    r20 = np.expm1(log_ret.rolling(WINDOW, min_periods=WINDOW).sum())
    vol20 = log_ret.rolling(WINDOW, min_periods=WINDOW).std()
    threshold = vol20.expanding(min_periods=MIN_HISTORY).median()
    panic = (r20 < 0) & (vol20 > threshold) & threshold.notna()
    return pd.DataFrame(dict(r20=r20, vol20=vol20, threshold=threshold, panic=panic.astype(bool)))


def panic_at(benchmark_ret: pd.Series) -> bool:
    """Gate state at the last row (a view's cutoff D-1)."""
    return bool(panic_table(benchmark_ret).panic.iloc[-1])
