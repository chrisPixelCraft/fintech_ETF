"""Forecast -> per-stock score (higher is better)."""
from __future__ import annotations

import pandas as pd

FORMS = ('mu', 'z', 'rank_blend')


def score(forecast: pd.DataFrame, form: str = 'z') -> pd.Series:
    """``mu``: expected h-day (excess) log return; ``z``: mu / calibrated sigma
    (risk-adjusted strength); ``rank_blend``: mean of the percentile ranks of both."""
    if form == 'mu':
        return forecast.mu
    if form == 'z':
        return forecast.z
    if form == 'rank_blend':
        return (forecast.mu.rank(pct=True) + forecast.z.rank(pct=True)) / 2
    raise ValueError(f'Unknown score form {form}')
