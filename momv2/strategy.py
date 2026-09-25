"""Momentum-v2 strategy (docs/momentum_v2_spec.md section 1): a momv2 score -> the production portfolio layer."""
from __future__ import annotations

from dataclasses import dataclass, field, fields

from autots_strategy import portfolio
from competition.backtest import PortfolioState
from competition.data import AsOfView
from competition.rules import CompetitionRules
from momv2.signals import SignalConfig, score


@dataclass(frozen=True)
class Momv2Config:
    signal: SignalConfig = field(default_factory=SignalConfig)
    portfolio: portfolio.PortfolioConfig = field(default_factory=portfolio.PortfolioConfig)

    @classmethod
    def from_dict(cls, raw: dict) -> 'Momv2Config':
        keys = {f.name for f in fields(SignalConfig)}
        unknown = set(raw) - keys - {'portfolio'}
        if unknown:
            raise ValueError(f'Unknown momv2 keys {sorted(unknown)}')
        return cls(signal=SignalConfig(**{k: raw[k] for k in keys if k in raw}),
                   portfolio=portfolio.PortfolioConfig.from_dict(raw.get('portfolio', {})))


class Momv2Strategy:
    def __init__(self, config: Momv2Config, rules: CompetitionRules):
        self.c, self.rules, self.log = config, rules, []

    def decide(self, view: AsOfView, state: PortfolioState) -> dict:
        scores, missing = score(view, self.c.signal)
        self.log.append(dict(date=str(state.date.date()), names=int(len(scores)), missing=missing))
        return portfolio.target_weights(scores, state.weights, state.sessions_remaining, self.rules, self.c.portfolio)
