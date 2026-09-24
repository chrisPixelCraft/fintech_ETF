"""AutoTSStrategy: the Strategy protocol implementation of the AutoTS-first method.

Per episode: select a model with AutoTS on the episode-style validation windows
and calibrate sigma at the first session (and every ``refit_every`` sessions);
on each decision day refit the frozen template on data through D-1, forecast,
score, and build target weights. Every input comes from the AsOfView.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from autots_strategy import portfolio, scoring, targets
from autots_strategy.forecaster import AutoTSForecaster, ForecastConfig, prepare_panel
from competition.backtest import PortfolioState
from competition.data import AsOfView
from competition.rules import CompetitionRules

STRATEGY_KEYS = {'target', 'forecaster', 'score', 'portfolio', 'refit_every', 'predict_every',
                 'vol_window'}


@dataclass(frozen=True)
class AutoTSStrategyConfig:
    series: str = 'relative_log_price'     # targets.SERIES_KINDS
    market: str = 'ew'                     # ew | 0050, for relative series
    forecaster: ForecastConfig = field(default_factory=ForecastConfig)
    score: str = 'z'                       # scoring.FORMS
    portfolio: portfolio.PortfolioConfig = field(default_factory=portfolio.PortfolioConfig)
    refit_every: int = 24                  # sessions between AutoTS model selections (24 = episode start only)
    predict_every: int = 1                 # sessions between forecasts (scores reused in between)
    vol_window: int = 60                   # trailing sessions for inverse-vol weights

    @classmethod
    def from_dict(cls, raw: dict) -> 'AutoTSStrategyConfig':
        unknown = set(raw) - STRATEGY_KEYS
        if unknown:
            raise ValueError(f'Unknown strategy keys {sorted(unknown)}')
        target = dict(raw.get('target', {}))
        if set(target) - {'series', 'market'}:
            raise ValueError(f'Unknown target keys {sorted(set(target) - {"series", "market"})}')
        cfg = cls(series=target.get('series', cls.series), market=target.get('market', cls.market),
                  forecaster=ForecastConfig.from_dict(raw.get('forecaster', {})), score=raw.get('score', cls.score),
                  portfolio=portfolio.PortfolioConfig.from_dict(raw.get('portfolio', {})),
                  refit_every=int(raw.get('refit_every', cls.refit_every)),
                  predict_every=int(raw.get('predict_every', cls.predict_every)),
                  vol_window=int(raw.get('vol_window', cls.vol_window)))
        if cfg.series not in targets.SERIES_KINDS or cfg.market not in targets.MARKETS \
                or cfg.score not in scoring.FORMS or cfg.refit_every < 1 or cfg.predict_every < 1:
            raise ValueError('Invalid AutoTS strategy config')
        return cfg


class AutoTSStrategy:
    def __init__(self, config: AutoTSStrategyConfig, rules: CompetitionRules):
        self.c, self.rules = config, rules
        self.forecaster = AutoTSForecaster(config.forecaster, level=config.series != 'log_return')
        self.template = self.sigma = self.scores = None
        self.log: list[dict] = []

    def decide(self, view: AsOfView, state: PortfolioState) -> dict:
        c = self.c
        entry = dict(date=str(state.date.date()), asof=str(view.date.date()))
        if state.day_index % c.predict_every == 0 or self.scores is None:
            panel = prepare_panel(targets.build_series(view, c.series, c.market), c.forecaster.lookback)
            if panel.shape[1] < self.rules.min_positions:
                self.log.append(dict(entry, status='INSUFFICIENT_HISTORY', names=int(panel.shape[1])))
                return dict(state.weights)
            if state.day_index % c.refit_every == 0 or self.template is None:
                self.template, info = self.forecaster.select(panel)
                self.sigma, ics = self.forecaster.calibrate(panel, self.template)
                entry.update(refit=True, calibration_rank_ic=[round(x, 4) for x in ics], **info)
            forecast = self.forecaster.forecast(panel, self.template, self.sigma)
            self.scores = scoring.score(forecast, c.score)
            entry.update(model=str(self.template.Model.iloc[0]), names=int(len(forecast)),
                         mu_dispersion=float(forecast.mu.std()), median_sigma=float(forecast.sigma.median()))
        eligible = view.valid.iloc[-1]
        scores = self.scores[self.scores.index.intersection(eligible.index[eligible])]
        vol = np.log1p(view.ret.iloc[-c.vol_window:]).std()
        weights = portfolio.target_weights(scores, state.weights, state.sessions_remaining, self.rules,
                                           c.portfolio, vol)
        entry.update(eligible=int(len(scores)), traded=weights != state.weights)
        self.log.append(entry)
        return weights
