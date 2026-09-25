"""HybridStrategy (docs/hybrid_spec.md section 1): Mom20 ranking, or the LightGBM-v2 ranking on panic days.

The gate is evaluated on the view (0050 returns through D-1). Normal days call
the same momentum_score as production Mom20; panic days read the walk-forward
prediction for D (hybrid.walkforward). A panic day without a prediction raises
instead of silently using Mom20. The portfolio layer is the production one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from autots_strategy import portfolio
from competition.backtest import PortfolioState
from competition.data import AsOfView
from competition.rules import ROOT, CompetitionRules
from hybrid.gate import panic_at
from research.baselines import MomentumConfig, _eligible, momentum_score

KEYS = {'predictions', 'window', 'portfolio'}
_CACHE: dict = {}


def load_predictions(path: str) -> dict:
    """decision date -> Series(symbol -> score), cached per process."""
    if path not in _CACHE:
        frame = pd.read_parquet(ROOT / path if not Path(path).is_absolute() else path)
        frame['decision_date'] = pd.to_datetime(frame.decision_date)
        _CACHE[path] = {d: g.set_index('symbol').score for d, g in frame.groupby('decision_date')}
    return _CACHE[path]


@dataclass(frozen=True)
class HybridConfig:
    predictions: str                                  # parquet from hybrid.walkforward
    momentum: MomentumConfig = field(default_factory=MomentumConfig)

    @classmethod
    def from_dict(cls, raw: dict) -> 'HybridConfig':
        unknown = set(raw) - KEYS
        if unknown or 'predictions' not in raw:
            raise ValueError(f'Hybrid needs predictions; unknown keys {sorted(unknown)}')
        momentum = MomentumConfig.from_dict({k: raw[k] for k in ('window', 'portfolio') if k in raw})
        return cls(predictions=raw['predictions'], momentum=momentum)


class HybridStrategy:
    def __init__(self, config: HybridConfig, rules: CompetitionRules):
        self.c, self.rules, self.log = config, rules, []

    def decide(self, view: AsOfView, state: PortfolioState) -> dict:
        panic = panic_at(view.benchmark_ret)
        if panic:
            predictions = load_predictions(self.c.predictions)
            if state.date not in predictions:
                raise RuntimeError(f'No LightGBM-v2 prediction for panic day {state.date.date()}')
            scores = predictions[state.date]
            scores = scores[scores.index.intersection(_eligible(view, self.c.momentum.window))]   # Mom20's names
        else:
            scores = momentum_score(view, self.c.momentum)
        self.log.append(dict(date=str(state.date.date()), panic=panic, expert='lgbm_v2' if panic else 'mom20',
                             names=int(len(scores))))
        return portfolio.target_weights(scores, state.weights, state.sessions_remaining, self.rules,
                                        self.c.momentum.portfolio)
