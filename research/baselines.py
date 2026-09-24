"""Mandatory simple baselines (same planner/ledger; portfolio layer shared where it applies).

- MomentumStrategy: equal-weight top-N by trailing log return, same portfolio
  construction (buffer, caps, turnover gate) as the AutoTS strategy.
- BasketStrategy: equal-weight top-N by trailing average traded value (a causal
  large-cap proxy) bought at the first session and held.
The no-signal AutoTS control is a config (LastValueNaive only), not code.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from autots_strategy import portfolio
from competition.backtest import PortfolioState
from competition.data import AsOfView
from competition.rules import CompetitionRules


def _eligible(view: AsOfView, window: int):
    """Tradable at the cutoff and fully observed over the trailing window."""
    if len(view.valid) < window:
        return view.valid.columns[:0]
    ok = view.valid.iloc[-window:].all()
    return ok.index[ok]


@dataclass(frozen=True)
class MomentumConfig:
    window: int = 20
    portfolio: portfolio.PortfolioConfig = field(default_factory=portfolio.PortfolioConfig)

    @classmethod
    def from_dict(cls, raw: dict) -> 'MomentumConfig':
        unknown = set(raw) - {'window', 'portfolio'}
        if unknown:
            raise ValueError(f'Unknown momentum keys {sorted(unknown)}')
        return cls(window=int(raw.get('window', 20)),
                   portfolio=portfolio.PortfolioConfig.from_dict(raw.get('portfolio', {})))


class MomentumStrategy:
    def __init__(self, config: MomentumConfig, rules: CompetitionRules):
        self.c, self.rules, self.log = config, rules, []

    def decide(self, view: AsOfView, state: PortfolioState) -> dict:
        names = _eligible(view, self.c.window)
        score = np.log1p(view.ret[names].iloc[-self.c.window:]).sum()
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
