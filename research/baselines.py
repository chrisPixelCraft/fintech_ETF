"""Mandatory simple baselines (same planner/ledger; portfolio layer shared where it applies).

- MomentumStrategy: equal-weight top-N by trailing log return through the shared
  portfolio layer (competition/portfolio.py: buffer, caps, turnover gate). The
  production strategy (Mom20) is this class with its defaults.
- BasketStrategy: equal-weight top-N by trailing average traded value (a causal
  large-cap proxy) bought at the first session and held.
The archived AutoTS / LightGBM / hybrid strategies live in legacy/ml/.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from competition import portfolio
from competition.backtest import PortfolioState
from competition.data import AsOfView
from competition.rules import CompetitionRules


def _eligible(view: AsOfView, window: int):
    """Tradable at the cutoff and fully observed over the trailing window."""
    if len(view.valid) < window:
        return view.valid.columns[:0]
    ok = view.valid.iloc[-window:].all()
    return ok.index[ok]


QUALITY = ('near_high', 'up_ratio', 'volume_trend')
QUALITY_WINDOW = 60          # sessions for near_high and the long leg of volume_trend


@dataclass(frozen=True)
class MomentumConfig:
    """Trailing log-return momentum. Defaults are the plain 20-session signal.

    ``skip``: drop the latest k sessions from every window. ``extra_windows``:
    further lookbacks; with any extra window or quality factor the score is the
    mean cross-sectional percentile rank of the components. ``risk_adjusted``:
    divide each momentum by its daily log-return std over the same window.
    ``quality``: one of QUALITY, rank-blended as one more component.
    """
    window: int = 20
    skip: int = 0
    extra_windows: tuple[int, ...] = ()
    risk_adjusted: bool = False
    quality: str | None = None
    portfolio: portfolio.PortfolioConfig = field(default_factory=portfolio.PortfolioConfig)

    def __post_init__(self):
        if self.window < 2 or self.skip < 0 or any(w < 2 for w in self.extra_windows):
            raise ValueError('Momentum windows must be >= 2 and skip >= 0')
        if self.quality is not None and self.quality not in QUALITY:
            raise ValueError(f'Unknown quality {self.quality}')

    @property
    def history(self) -> int:
        """Sessions every eligible name must be fully observed over."""
        longest = max((self.window, *self.extra_windows)) + self.skip
        return max(longest, QUALITY_WINDOW) if self.quality in ('near_high', 'volume_trend') else longest

    @classmethod
    def from_dict(cls, raw: dict) -> 'MomentumConfig':
        unknown = set(raw) - {'window', 'skip', 'extra_windows', 'risk_adjusted', 'quality', 'portfolio'}
        if unknown:
            raise ValueError(f'Unknown momentum keys {sorted(unknown)}')
        return cls(window=int(raw.get('window', 20)), skip=int(raw.get('skip', 0)),
                   extra_windows=tuple(int(w) for w in raw.get('extra_windows', ())),
                   risk_adjusted=bool(raw.get('risk_adjusted', False)), quality=raw.get('quality'),
                   portfolio=portfolio.PortfolioConfig.from_dict(raw.get('portfolio', {})))


def _momentum(log_ret, window: int, skip: int, risk_adjusted: bool):
    rows = log_ret.iloc[len(log_ret) - skip - window:len(log_ret) - skip]
    score = rows.sum()
    return score / rows.std() if risk_adjusted else score


def _quality(view: AsOfView, names, kind: str):
    if kind == 'up_ratio':
        return (view.ret[names].iloc[-20:] > 0).mean()
    if kind == 'volume_trend':
        volume = view.volume[names]
        return volume.iloc[-20:].mean() / volume.iloc[-QUALITY_WINDOW:].mean()
    price = np.exp(np.log1p(view.ret[names].iloc[-QUALITY_WINDOW:]).cumsum())   # near_high
    return price.iloc[-1] / price.max()


def momentum_score(view: AsOfView, config: MomentumConfig):
    """Score per eligible name (higher is better); plain momentum when no blend is configured."""
    names = _eligible(view, config.history)
    log_ret = np.log1p(view.ret[names])
    parts = [_momentum(log_ret, w, config.skip, config.risk_adjusted) for w in (config.window, *config.extra_windows)]
    if config.quality is not None:
        parts.append(_quality(view, names, config.quality))
    if len(parts) == 1:
        return parts[0]
    return sum(p.rank(pct=True) for p in parts) / len(parts)


class MomentumStrategy:
    def __init__(self, config: MomentumConfig, rules: CompetitionRules):
        self.c, self.rules, self.log = config, rules, []

    def decide(self, view: AsOfView, state: PortfolioState) -> dict:
        score = momentum_score(view, self.c)
        return portfolio.target_weights(score, state.weights, state.sessions_remaining, self.rules, self.c.portfolio)


@dataclass(frozen=True)
class BasketConfig:
    n_holdings: int = 25
    window: int = 60
    invested: float = .88
    cap_scale: float = .9

    @classmethod
    def from_dict(cls, raw: dict) -> 'BasketConfig':
        unknown = set(raw) - {'n_holdings', 'window', 'invested', 'cap_scale'}
        if unknown:
            raise ValueError(f'Unknown basket keys {sorted(unknown)}')
        return cls(**raw)


class BasketStrategy:
    def __init__(self, config: BasketConfig, rules: CompetitionRules):
        self.c, self.rules, self.log = config, rules, []
        self.pcfg = portfolio.PortfolioConfig(n_holdings=config.n_holdings, keep_rank=config.n_holdings,
                                              invested=config.invested, cap_scale=config.cap_scale,
                                              rebalance_threshold=1.)  # never rebalance once established

    def decide(self, view: AsOfView, state: PortfolioState) -> dict:
        names = _eligible(view, self.c.window)
        traded_value = (view.close[names] * view.volume[names]).iloc[-self.c.window:].mean()
        return portfolio.target_weights(traded_value, state.weights, state.sessions_remaining, self.rules, self.pcfg)
