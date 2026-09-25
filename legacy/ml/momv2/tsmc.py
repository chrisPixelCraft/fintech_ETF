"""TSMC core with score / inverse-vol weights (docs/tsmc_core_spec.md).

Selection stays Mom20; only the weights change. ``formal``: 2330 at 22.5%
plus 24 Mom20 names weighted 0.85 score + 0.15 inverse 60-day vol.
``core_equal`` (diagnostic A): the same core, the 24 names equal weight.
``blend_only`` (diagnostic B): no core, 25 Mom20 names with the blend.
A 2330 weight above 24% at the D-1 close is trimmed back to 22.5% the same
day, regardless of the turnover gate and the last-days freeze.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace

import numpy as np
import pandas as pd

from autots_strategy import portfolio
from competition.backtest import PortfolioState
from competition.data import AsOfView
from competition.rules import CompetitionRules
from research.baselines import MomentumConfig, momentum_score

TSMC = '2330.TW'
CORE, TRIM_AT = .225, .24
W_SCORE, W_INV_VOL = .85, .15
VOL_WINDOW, VOL_MIN = 60, 48
VARIANTS = ('formal', 'core_equal', 'blend_only')


def blend_preference(scores: pd.Series, view: AsOfView) -> pd.Series:
    """0.85 x normalised shifted-score weight + 0.15 x normalised inverse 60-day vol weight."""
    s = scores
    shifted = s - s.min() + (s.max() - s.min()) / len(s) if s.max() > s.min() else pd.Series(1., index=s.index)
    lr = np.log1p(view.ret[s.index].iloc[-VOL_WINDOW:])
    vol = lr.std().where(lr.notna().sum() >= VOL_MIN)
    inv = (1 / vol.where(vol > 0))
    inv = inv.fillna(inv.median()).fillna(1.)
    return W_SCORE * shifted / shifted.sum() + W_INV_VOL * inv / inv.sum()


def trimmed(current: dict) -> dict:
    out = dict(current)
    out[TSMC] = CORE
    return out


@dataclass(frozen=True)
class TsmcConfig:
    variant: str = 'formal'
    portfolio: portfolio.PortfolioConfig = field(default_factory=portfolio.PortfolioConfig)

    def __post_init__(self):
        if self.variant not in VARIANTS:
            raise ValueError(f'Unknown TSMC variant {self.variant}')

    @property
    def core(self) -> bool:
        return self.variant != 'blend_only'

    @classmethod
    def from_dict(cls, raw: dict) -> 'TsmcConfig':
        unknown = set(raw) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f'Unknown TSMC keys {sorted(unknown)}')
        return cls(variant=raw.get('variant', 'formal'),
                   portfolio=portfolio.PortfolioConfig.from_dict(raw.get('portfolio', {})))


class TsmcStrategy:
    def __init__(self, config: TsmcConfig, rules: CompetitionRules):
        self.c, self.rules, self.log = config, rules, []

    def _record(self, state, action: str, weights: dict) -> dict:
        self.log.append(dict(date=str(state.date.date()), action=action, tsmc_before=state.weights.get(TSMC, 0.),
                             tsmc_target=weights.get(TSMC, 0.), names=len(weights)))
        return weights

    def decide(self, view: AsOfView, state: PortfolioState) -> dict:
        cfg, rules, current = self.c.portfolio, self.rules, state.weights
        held = [s for s, w in current.items() if w > 0]
        established = rules.min_positions <= len(held) <= rules.max_positions
        over = self.c.core and current.get(TSMC, 0.) > TRIM_AT
        if established and state.sessions_remaining <= cfg.freeze_last_days:
            return self._record(state, 'trim' if over else 'freeze', trimmed(current) if over else dict(current))
        mom = momentum_score(view, MomentumConfig())
        if self.c.core:
            tradable = TSMC in view.valid.columns and bool(view.valid[TSMC].iloc[-1])
            tsmc_w = CORE if tradable else current.get(TSMC, 0.)
            pool, slots, budget = mom.drop(TSMC, errors='ignore'), cfg.n_holdings - 1, cfg.invested - tsmc_w
            held_pool = [s for s in held if s != TSMC]
        else:
            tsmc_w, pool, slots, budget, held_pool = 0., mom, cfg.n_holdings, cfg.invested, held
        chosen = portfolio.select(pool, held_pool, replace(cfg, n_holdings=slots, keep_rank=max(cfg.keep_rank, slots)))
        if len(chosen) + self.c.core < rules.min_positions:
            return self._record(state, 'hold', dict(current) if established else {})
        pref = pd.Series(1., index=chosen) if self.c.variant == 'core_equal' else blend_preference(pool[chosen], view)
        caps = pd.Series({x: rules.cap(x) * cfg.cap_scale for x in chosen})
        weights = {x: float(w) for x, w in portfolio.cap_fill(pref, budget, caps).items() if w > 0}
        if self.c.core and tsmc_w > 0:
            weights[TSMC] = tsmc_w
        if established:
            names = set(weights) | set(held)
            turnover = .5 * sum(abs(weights.get(x, 0.) - current.get(x, 0.)) for x in names)
            if turnover < cfg.rebalance_threshold:
                return self._record(state, 'trim' if over else 'skip', trimmed(current) if over else dict(current))
        return self._record(state, 'rebalance', weights)
