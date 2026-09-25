"""Momentum-v2 signals (docs/momentum_v2_spec.md sections 2-6).

Price signals (Mom20, market residual, turnover volume) are computed from the
AsOfView itself, so they only ever see rows <= D-1. The two outside panels are
point-in-time by construction and read at the view date:
- revenue: hybrid.features.revenue_features (month m from the 11th of m+1)
- shares issued: latest monthly snapshot dated <= t, times every share
  multiplier (stock dividend, capital reduction, split) after it through t
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from competition.data import AsOfView, load_calendar, load_market
from competition.rules import ROOT
from hybrid.features import revenue_features
from hybrid.walkforward import symbols_by_ticker
from research.baselines import MomentumConfig, _eligible, momentum_score

MIN_SHARE = .8
REVENUE_SCORES = {'yoy': ('rev_yoy',), 'yoy_acc': ('rev_yoy', 'rev_yoy_acc'),
                  'yoy_acc_record': ('rev_yoy', 'rev_yoy_acc', 'rev_record')}
KINDS = ('mom20', 'h1', 'h2', 'h3', 'composite', 'composite3')
_PANELS: dict = {}


@dataclass(frozen=True)
class SignalConfig:
    kind: str = 'composite'
    resid_window: int = 20
    beta_window: int = 60
    turnover_window: int = 20
    revenue_score: str = 'yoy_acc'

    def __post_init__(self):
        if self.kind not in KINDS or self.revenue_score not in REVENUE_SCORES:
            raise ValueError(f'Unknown kind {self.kind} or revenue score {self.revenue_score}')
        if not 2 <= self.resid_window <= self.beta_window or self.turnover_window < 2:
            raise ValueError('Windows must satisfy 2 <= resid_window <= beta_window and turnover_window >= 2')

    @property
    def components(self) -> tuple[str, ...]:
        return dict(mom20=('mom20',), h1=('resid',), h2=('mom20', 'turnover'), h3=('mom20', 'revenue'),
                    composite=('mom20', 'resid', 'turnover', 'revenue'),
                    composite3=('mom20', 'resid', 'revenue'))[self.kind]


def rank(values: pd.Series, names) -> pd.Series:
    """Percentile rank among ``names`` (higher is better); missing -> 0.5."""
    return values.reindex(names).replace([np.inf, -np.inf], np.nan).rank(pct=True).fillna(.5)


def _enough(count, window: int):
    return count >= math.ceil(MIN_SHARE * window)


def residual_momentum(view: AsOfView, names, resid_window: int, beta_window: int) -> pd.Series:
    lr = np.log1p(view.ret[names].iloc[-beta_window:])
    lm = np.log1p(view.benchmark_ret.iloc[-beta_window:])
    both = lr.notna() & lm.notna().to_numpy()[:, None]
    x, y = lr.where(both), pd.DataFrame(np.where(both, lm.to_numpy()[:, None], np.nan), index=lr.index,
                                        columns=lr.columns)
    n = both.sum()
    cov = (x * y).sum() / n - x.sum() / n * (y.sum() / n)
    var = (y * y).sum() / n - (y.sum() / n) ** 2
    beta = (cov / var.where(var > 0)).where(_enough(n, beta_window))
    tail = both.iloc[-resid_window:]
    resid = (x.iloc[-resid_window:] - y.iloc[-resid_window:] * beta).sum()
    return resid.where(_enough(tail.sum(), resid_window) & beta.notna())


def turnover(view: AsOfView, names, window: int, shares: pd.Series) -> pd.Series:
    volume = view.volume[names].iloc[-window:].where(view.valid[names].iloc[-window:])
    mean = volume.mean().where(_enough(volume.notna().sum(), window))
    return mean / shares.reindex(names).where(lambda s: s > 0)


# ---------------------------------------------------------------- point-in-time panels

def shares_panel(snapshots: pd.DataFrame, split: pd.DataFrame, symbols: dict) -> pd.DataFrame:
    """Daily shares issued (calendar x symbol) from monthly snapshots and the share multipliers after them."""
    s = snapshots.assign(symbol=snapshots.ticker.astype(str).map(symbols), date=pd.to_datetime(snapshots.date))
    s = s.dropna(subset=['symbol'])
    wide = s.pivot(index='date', columns='symbol', values='shares').reindex(index=split.index, columns=split.columns)
    factor = split.cumprod()                                  # product of multipliers on rows <= t
    return (wide / factor).ffill() * factor


def panels() -> dict:
    """revenue frames and daily shares, built once per process."""
    if not _PANELS:
        symbols, calendar = symbols_by_ticker(), load_calendar()
        revenue = pd.read_csv(ROOT / 'data/hybrid/revenue.csv', dtype={'ticker': str})
        _PANELS.update(revenue_features(revenue, calendar, symbols))
        shares_path = ROOT / 'data/momv2/shares.csv'
        if shares_path.exists():
            _PANELS['shares'] = shares_panel(pd.read_csv(shares_path, dtype={'ticker': str}), load_market().split,
                                             symbols)
    return _PANELS


def _row(name: str, date) -> pd.Series:
    frame = panels().get(name)
    if frame is None:
        raise RuntimeError(f'Panel {name} is missing (data/momv2/shares.csv not built?)')
    return frame.loc[date] if date in frame.index else pd.Series(dtype=float)


def revenue_score(date, names, kind: str) -> pd.Series:
    parts = [rank(_row(col, date), names) for col in REVENUE_SCORES[kind]]
    return sum(parts) / len(parts)


def score(view: AsOfView, config: SignalConfig) -> tuple[pd.Series, dict]:
    """Ranking score over Mom20's eligible names, plus per-component missing counts for the log."""
    names = _eligible(view, 20)
    parts, missing = [], {}
    for component in config.components:
        if component == 'mom20':
            raw = momentum_score(view, MomentumConfig())
        elif component == 'resid':
            raw = residual_momentum(view, names, config.resid_window, config.beta_window)
        elif component == 'turnover':
            raw = -turnover(view, names, config.turnover_window, _row('shares', view.date))
        else:
            raw = None
            parts.append(revenue_score(view.date, names, config.revenue_score))
            missing['revenue'] = int(_row('rev_yoy', view.date).reindex(names).isna().sum())
        if raw is not None:
            missing[component] = int(raw.reindex(names).replace([np.inf, -np.inf], np.nan).isna().sum())
            parts.append(rank(raw, names))
    return sum(parts) / len(parts), missing
