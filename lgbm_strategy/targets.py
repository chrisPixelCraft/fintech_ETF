"""Training targets on the action-neutral price (uses the future by design; only matured rows are trained on).

- ``raw_return``: P(t+h) / P(t) - 1; NaN unless all h daily returns are observed.
- ``relative_alpha``: raw_return minus the equal-weight mean of the finite raw
  returns of the whole universe on the same origin date (no feature-readiness
  filter, no 0050). Dates with fewer than ``MIN_MARKET_LABELS`` finite labels
  are invalid (all NaN), so each valid date's alpha averages to zero.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from autots_strategy.targets import forward_log_return

TARGET_MODES = ('raw_return', 'relative_alpha')
MIN_MARKET_LABELS = 20


def raw_forward_return(ret: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Label at t: P(t+h) / P(t) - 1 on the action-neutral price; NaN unless all h returns are observed."""
    observed = ret.notna().rolling(horizon).sum().shift(-horizon) == horizon
    return np.expm1(forward_log_return(ret, horizon)).where(observed)


def cross_section_market_return(raw: pd.DataFrame, min_labels: int = MIN_MARKET_LABELS) -> pd.Series:
    """Per-date mean of the finite labels; NaN where fewer than ``min_labels`` are finite."""
    finite = raw.where(np.isfinite(raw))
    return finite.mean(axis=1).where(finite.notna().sum(axis=1) >= min_labels)


def relative_alpha(raw: pd.DataFrame, min_labels: int = MIN_MARKET_LABELS) -> pd.DataFrame:
    return raw.sub(cross_section_market_return(raw, min_labels), axis=0)


def build_target(returns: pd.DataFrame, horizon: int, mode: str) -> pd.DataFrame:
    """Wide (date x symbol) target of ``mode`` from daily action-neutral returns."""
    if mode not in TARGET_MODES:
        raise ValueError(f'Unknown target_mode {mode}; expected one of {TARGET_MODES}')
    raw = raw_forward_return(returns, horizon)
    return raw if mode == 'raw_return' else relative_alpha(raw)
