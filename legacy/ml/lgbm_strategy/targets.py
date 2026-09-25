"""Training targets (use the future by design; only matured rows are trained on).

For a feature origin t (the decision cutoff D-1 of a later day):

- ``raw_return``: P(t+h) / P(t) - 1 on the action-neutral price P; label end t+h.
- ``relative_alpha``: raw_return minus the equal-weight mean of the finite raw
  returns of the whole universe on the same origin (no feature-readiness
  filter, no 0050); label end t+h.
- ``execution_alpha``: relative alpha of the execution-aligned return, entered
  at the fill price of the next session (entry_session = t+1) and exited at
  the fill price of exit_session = t+1+h; label end t+1+h. Fill prices come from
  competition.execution with the backtest's default execution settings, so the
  label and the simulator price trades identically. Nominal fill prices are
  made split/dividend-neutral as (X/C)(exit) / (X/C)(entry) * P(exit) / P(entry),
  with C the same-day close; without corporate actions this is X(exit)/X(entry).

A label is NaN unless every input it needs is observed. Alpha dates with fewer
than ``MIN_MARKET_LABELS`` finite labels are invalid (all NaN), so each valid
date's alpha averages to zero.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from autots_strategy.targets import forward_log_return
from competition import execution
from competition.backtest import ExecutionConfig
from competition.data import AsOfView

MIN_MARKET_LABELS = 20
EXECUTION = ExecutionConfig()          # the backtest default: auto (official VWAP, else HLC3 proxy)


def raw_forward_return(ret: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Label at t: P(t+h) / P(t) - 1 on the action-neutral price; NaN unless all h returns are observed."""
    observed = ret.notna().rolling(horizon).sum().shift(-horizon) == horizon
    return np.expm1(forward_log_return(ret, horizon)).where(observed)


def market_forward_return(raw: pd.DataFrame, min_labels: int = MIN_MARKET_LABELS) -> pd.Series:
    """Per-origin mean of the finite labels; NaN where fewer than ``min_labels`` are finite."""
    finite = raw.where(np.isfinite(raw))
    return finite.mean(axis=1).where(finite.notna().sum(axis=1) >= min_labels)


def relative_alpha(raw: pd.DataFrame, min_labels: int = MIN_MARKET_LABELS) -> pd.DataFrame:
    return raw.sub(market_forward_return(raw, min_labels), axis=0)


def execution_price(view: AsOfView) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Backtest fill price and its source for every session of the view."""
    return execution.execution_prices(view, EXECUTION.mode, EXECUTION.proxy)


def execution_forward_return(view: AsOfView, horizon: int) -> pd.DataFrame:
    """Label at t: fill(t+1+h) / fill(t+1) - 1, split/dividend-neutral; NaN unless every input is observed."""
    price, _ = execution_price(view)
    to_close = price / view.close                                  # same-day, same share units
    entry, exit_ = to_close.shift(-1), to_close.shift(-(1 + horizon))
    bridge = 1 + raw_forward_return(view.ret, horizon).shift(-1)   # P(t+1+h) / P(t+1)
    return exit_ / entry * bridge - 1


def execution_source_shares(view: AsOfView, origins: pd.DatetimeIndex, label: pd.DataFrame,
                            horizon: int) -> dict:
    """Share of official-VWAP vs proxy fill prices among the entry and exit prices of finite labels."""
    _, source = execution_price(view)
    finite = label.reindex(origins).notna().to_numpy()
    pos = view.close.index.get_indexer(origins)
    used = np.concatenate([source.to_numpy()[pos + 1][finite], source.to_numpy()[pos + 1 + horizon][finite]])
    n = max(len(used), 1)
    return dict(official_vwap_share=float((used == execution.OFFICIAL).sum() / n),
                proxy_hlc3_share=float((used == 'proxy_' + EXECUTION.proxy).sum() / n))


@dataclass(frozen=True)
class RawReturnTarget:
    mode: str = 'raw_return'
    alpha: bool = False

    def label_span(self, horizon: int) -> int:
        """Sessions from the origin to the label end."""
        return horizon

    def build(self, view: AsOfView, horizon: int) -> pd.DataFrame:
        return raw_forward_return(view.ret, horizon)

    def price_sources(self, view: AsOfView, origins: pd.DatetimeIndex, label: pd.DataFrame, horizon: int) -> dict:
        """Execution-price sources behind the labels (close-based targets use none)."""
        return dict(official_vwap_share=None, proxy_hlc3_share=None)


@dataclass(frozen=True)
class RelativeAlphaTarget(RawReturnTarget):
    mode: str = 'relative_alpha'
    alpha: bool = True

    def build(self, view: AsOfView, horizon: int) -> pd.DataFrame:
        return relative_alpha(raw_forward_return(view.ret, horizon))


@dataclass(frozen=True)
class ExecutionAlphaTarget(RawReturnTarget):
    mode: str = 'execution_alpha'
    alpha: bool = True

    def label_span(self, horizon: int) -> int:
        return 1 + horizon                  # entry_session = t+1, exit_session = t+1+h

    def build(self, view: AsOfView, horizon: int) -> pd.DataFrame:
        return relative_alpha(execution_forward_return(view, horizon))

    def price_sources(self, view: AsOfView, origins: pd.DatetimeIndex, label: pd.DataFrame, horizon: int) -> dict:
        return execution_source_shares(view, origins, label, horizon)


TARGETS = {t.mode: t for t in (RawReturnTarget(), RelativeAlphaTarget(), ExecutionAlphaTarget())}
TARGET_MODES = tuple(TARGETS)


def target_for(mode: str) -> RawReturnTarget:
    if mode not in TARGETS:
        raise ValueError(f'Unknown target_mode {mode}; expected one of {TARGET_MODES}')
    return TARGETS[mode]


def build_target(view: AsOfView, horizon: int, mode: str) -> pd.DataFrame:
    """Wide (origin date x symbol) target of ``mode``."""
    return target_for(mode).build(view, horizon)
