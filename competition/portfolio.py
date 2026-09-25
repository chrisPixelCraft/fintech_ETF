"""Scores -> target weights for the competition planner.

selection (top-N with a hold buffer) -> weighting -> capped water-filling ->
turnover gate. Hard rules are enforced again, fail-closed, by
competition/planner.py; this layer only aims inside them.
"""
from __future__ import annotations

from dataclasses import dataclass, fields

import numpy as np
import pandas as pd

from competition.rules import CompetitionRules

WEIGHTINGS = ('equal', 'score', 'inverse_vol')


@dataclass(frozen=True)
class PortfolioConfig:
    n_holdings: int = 25              # 20..30
    keep_rank: int = 35               # a held name stays while its score rank <= keep_rank
    weighting: str = 'equal'          # equal | score | inverse_vol
    invested: float = .88             # stock weight target; the planner needs a cash buffer for its +/-10% envelope
    cap_scale: float = .9             # aim at cap * cap_scale (planner buys at +10% worst case)
    rebalance_threshold: float = .10  # skip rebalancing if one-way turnover would be below this
    freeze_last_days: int = 3         # hold (no discretionary trades) in the last k sessions

    def __post_init__(self):
        if not 20 <= self.n_holdings <= 30 or self.keep_rank < self.n_holdings:
            raise ValueError('n_holdings must be in 20..30 and keep_rank >= n_holdings')
        if self.weighting not in WEIGHTINGS or not .75 < self.invested <= 1 or not 0 < self.cap_scale <= 1:
            raise ValueError('Invalid portfolio config')

    @classmethod
    def from_dict(cls, raw: dict) -> 'PortfolioConfig':
        unknown = set(raw) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f'Unknown portfolio keys {sorted(unknown)}')
        return cls(**raw)


def cap_fill(preference: pd.Series, budget: float, caps: pd.Series) -> pd.Series:
    """Proportional allocation of ``budget`` with per-name caps (water-filling).

    Adapted from legacy/src/v5_portfolio.py cap_fill.
    """
    p = preference.astype(float).clip(lower=0)
    caps = caps.reindex(p.index).astype(float)
    if caps.sum() < budget - 1e-12:
        raise ValueError('Caps cannot hold the invested budget')
    w = pd.Series(0., index=p.index)
    free = pd.Series(True, index=p.index)
    remaining = budget
    for _ in range(len(p) + 1):
        mass = p[free].sum()
        share = remaining * p[free] / mass if mass > 0 else pd.Series(remaining / free.sum(), index=p.index[free])
        over = share > caps[free] + 1e-15
        if not over.any():
            w[free] = share
            break
        hit = share.index[over]
        w[hit] = caps[hit]
        free[hit] = False
        remaining = budget - w[~free].sum()
    return w


def ranked(scores: pd.Series) -> list[str]:
    """Descending score; ties broken by symbol (deterministic)."""
    s = scores.dropna()
    return sorted(s.index, key=lambda x: (-s[x], x))


def select(scores: pd.Series, held: list[str], cfg: PortfolioConfig) -> list[str]:
    order = ranked(scores)
    rank = {s: i for i, s in enumerate(order)}
    keep = sorted((s for s in held if rank.get(s, np.inf) < cfg.keep_rank), key=rank.get)[:cfg.n_holdings]
    new = [s for s in order if s not in keep][:cfg.n_holdings - len(keep)]
    return keep + new


def target_weights(scores: pd.Series, current: dict, sessions_remaining: int, rules: CompetitionRules,
                   cfg: PortfolioConfig, vol: pd.Series | None = None) -> dict:
    """Target weights (sum = ``cfg.invested``) or the current book when a trade is not worth it."""
    held = [s for s, w in current.items() if w > 0]
    established = rules.min_positions <= len(held) <= rules.max_positions
    if established and sessions_remaining <= cfg.freeze_last_days:
        return dict(current)
    chosen = select(scores, held, cfg)
    if len(chosen) < rules.min_positions:
        return dict(current) if established else {}
    s = scores.reindex(chosen)
    if cfg.weighting == 'equal':
        pref = pd.Series(1., index=chosen)
    elif cfg.weighting == 'score':
        pref = s - s.min() + (s.max() - s.min()) / len(s) if s.max() > s.min() else pd.Series(1., index=chosen)
    else:
        inv = 1 / vol.reindex(chosen).where(vol.reindex(chosen) > 0) if vol is not None else pd.Series(np.nan, index=chosen)
        pref = inv.fillna(inv.median()).fillna(1.)
    caps = pd.Series({x: rules.cap(x) * cfg.cap_scale for x in chosen})
    weights = cap_fill(pref, cfg.invested, caps)
    if established:
        names = set(weights.index) | set(held)
        turnover = .5 * sum(abs(weights.get(x, 0.) - current.get(x, 0.)) for x in names)
        if turnover < cfg.rebalance_threshold:
            return dict(current)
    return {x: float(w) for x, w in weights.items() if w > 0}
