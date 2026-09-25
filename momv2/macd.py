"""Multi-scale normalized MACD rerank (docs/macd_momentum_spec.md), after Baz et al. (2015).

Panels are built once per process from the market since DATA_START with
recursive (adjust=False) and rolling operations only, so row t uses rows <= t.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, fields

import numpy as np
import pandas as pd

from autots_strategy import portfolio
from competition.backtest import PortfolioState
from competition.data import AsOfView, MarketData, load_market
from competition.rules import CompetitionRules
from lgbm_strategy.features import signal_price
from research.baselines import MomentumConfig, momentum_score

DATA_START = '2013-01-01'
SCALES = {'fast': (8, 24), 'medium': (16, 48), 'slow': (32, 96)}
PRICE_STD, SIGNAL_STD = 63, 252
SHORTLIST = 40
MIN_SHARE = .8
VARIANTS = ('combined', *(f'scale:{s}' for s in SCALES))
_PANELS: dict = {}


def _roll(frame: pd.DataFrame, window: int):
    return frame.rolling(window, min_periods=math.ceil(MIN_SHARE * window))


def normalized_macd(market: MarketData) -> dict[str, pd.DataFrame]:
    p = signal_price(market.ret).where(market.close.notna())
    ewm = lambda s: p.ewm(alpha=1 / s, adjust=False).mean()
    price_std = _roll(p, PRICE_STD).std()
    out = {}
    for name, (s, l) in SCALES.items():
        q = (ewm(s) - ewm(l)) / price_std.where(price_std > 0)
        q_std = _roll(q, SIGNAL_STD).std()
        out[name] = (q / q_std.where(q_std > 0)).replace([np.inf, -np.inf], np.nan)
    return out


def panels() -> dict[str, pd.DataFrame]:
    if not _PANELS:
        _PANELS.update(normalized_macd(load_market().since(DATA_START)))
    return _PANELS


def macd_score(date, names, variant: str, frames: dict | None = None) -> pd.Series:
    frames = frames if frames is not None else panels()
    rank = lambda s: (frames[s].loc[date] if date in frames[s].index else pd.Series(dtype=float)) \
        .reindex(names).rank(pct=True).fillna(.5)
    if variant == 'combined':
        return sum(rank(s) for s in SCALES) / len(SCALES)
    return rank(variant.split(':')[1])


def rerank(view: AsOfView, variant: str, frames: dict | None = None) -> tuple[pd.Series, list]:
    """Mom20's top 40 ordered by the MACD score first, the rest in Mom20 order."""
    mom = momentum_score(view, MomentumConfig())
    top = portfolio.ranked(mom)[:SHORTLIST]
    mom_pct = mom.rank(pct=True)
    score = mom_pct.copy()
    score[top] = 1 + macd_score(view.date, top, variant, frames) + 1e-6 * mom_pct[top]
    return score, top


@dataclass(frozen=True)
class MacdConfig:
    variant: str = 'combined'
    portfolio: portfolio.PortfolioConfig = field(default_factory=portfolio.PortfolioConfig)

    def __post_init__(self):
        if self.variant not in VARIANTS:
            raise ValueError(f'Unknown MACD variant {self.variant}')

    @classmethod
    def from_dict(cls, raw: dict) -> 'MacdConfig':
        unknown = set(raw) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f'Unknown MACD keys {sorted(unknown)}')
        return cls(variant=raw.get('variant', 'combined'),
                   portfolio=portfolio.PortfolioConfig.from_dict(raw.get('portfolio', {})))


class MacdStrategy:
    def __init__(self, config: MacdConfig, rules: CompetitionRules):
        self.c, self.rules, self.log = config, rules, []

    def decide(self, view: AsOfView, state: PortfolioState) -> dict:
        score, top = rerank(view, self.c.variant)
        missing = {s: int(panels()[s].loc[view.date].reindex(top).isna().sum()) for s in SCALES}
        self.log.append(dict(date=str(state.date.date()), missing={k: v for k, v in missing.items() if v}))
        return portfolio.target_weights(score, state.weights, state.sessions_remaining, self.rules,
                                        self.c.portfolio)
