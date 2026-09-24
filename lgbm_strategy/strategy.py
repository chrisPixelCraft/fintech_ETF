"""LightGBMStrategy: AsOfView -> features -> LightGBM prediction -> portfolio.target_weights().

Refits every ``refit_every`` sessions of the episode on matured labels through
D-1 and reuses the model in between; features and predictions are refreshed
every decision day. Scores are the predictions (of the ``target_mode`` target)
for the names that are feature-ready at D-1. The log also records rank
stability and turnover per day; those diagnostics never feed a decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from autots_strategy import portfolio
from competition.backtest import PortfolioState
from competition.data import AsOfView
from competition.rules import CompetitionRules
from lgbm_strategy import model
from lgbm_strategy.dataset import build_training_set
from lgbm_strategy.features import COLUMNS, build_features
from lgbm_strategy.targets import TARGET_MODES

STRATEGY_KEYS = {'horizon', 'lookback', 'refit_every', 'target_mode', 'portfolio'}
OVERLAP_SIZES = (25, 35)


@dataclass(frozen=True)
class LightGBMStrategyConfig:
    horizon: int = 10                  # label: target over t+1..t+horizon
    lookback: int = 750                # latest matured dates in the training window
    refit_every: int = 5               # sessions between refits within an episode
    target_mode: str = 'raw_return'    # lgbm_strategy.targets.TARGET_MODES
    portfolio: portfolio.PortfolioConfig = field(default_factory=portfolio.PortfolioConfig)

    def __post_init__(self):
        if self.horizon < 1 or self.lookback < 1 or self.refit_every < 1:
            raise ValueError('horizon, lookback and refit_every must be positive')
        if self.target_mode not in TARGET_MODES:
            raise ValueError(f'Unknown target_mode {self.target_mode}')

    @classmethod
    def from_dict(cls, raw: dict) -> 'LightGBMStrategyConfig':
        unknown = set(raw) - STRATEGY_KEYS
        if unknown:
            raise ValueError(f'Unknown lgbm keys {sorted(unknown)}')
        return cls(horizon=int(raw.get('horizon', cls.horizon)), lookback=int(raw.get('lookback', cls.lookback)),
                   refit_every=int(raw.get('refit_every', cls.refit_every)),
                   target_mode=str(raw.get('target_mode', cls.target_mode)),
                   portfolio=portfolio.PortfolioConfig.from_dict(raw.get('portfolio', {})))


def predict_scores(fitted: model.FittedModel, view: AsOfView) -> pd.Series:
    """Prediction per feature-ready symbol at ``view.date``, sorted by symbol."""
    table = build_features(view, view.close.index[-1:])
    table = table[table.feature_ready]
    return pd.Series(model.predict(fitted, table).to_numpy(), index=table.symbol.to_numpy(), name='score')


def rank_turnover(order: list[str], previous: list[str] | None, weights: dict, current: dict) -> dict:
    """Top-k overlap with the previous decision's ranking, entries / exits and one-way turnover of the target."""
    out = {f'top{k}_overlap': (len(set(order[:k]) & set(previous[:k])) / k if previous else None)
           for k in OVERLAP_SIZES}
    held, target = {s for s, w in current.items() if w > 0}, {s for s, w in weights.items() if w > 0}
    names = held | target
    out.update(entries=len(target - held), exits=len(held - target),
               one_way_turnover=.5 * sum(abs(weights.get(s, 0.) - current.get(s, 0.)) for s in names))
    return out


class LightGBMStrategy:
    def __init__(self, config: LightGBMStrategyConfig, rules: CompetitionRules):
        self.c, self.rules = config, rules
        self.fitted: model.FittedModel | None = None
        self.previous_order: list[str] | None = None
        self.log: list[dict] = []

    def decide(self, view: AsOfView, state: PortfolioState) -> dict:
        c = self.c
        entry = dict(date=str(state.date.date()), asof=str(view.date.date()))
        if self.fitted is None or state.day_index % c.refit_every == 0:
            data = build_training_set(view, c.horizon, c.lookback, c.target_mode)
            self.fitted = model.fit(data.train, data.validation, COLUMNS)
            entry.update(refit=True, **data.audit, **self.fitted.metadata)
        scores = predict_scores(self.fitted, view)
        weights = portfolio.target_weights(scores, state.weights, state.sessions_remaining, self.rules, c.portfolio)
        order = portfolio.ranked(scores)
        entry.update(eligible=int(len(scores)), score_dispersion=float(scores.std()), traded=weights != state.weights,
                     **rank_turnover(order, self.previous_order, weights, state.weights))
        self.previous_order = order
        self.log.append(entry)
        return weights
