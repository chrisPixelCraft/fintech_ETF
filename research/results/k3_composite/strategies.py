"""K3 composite candidates (frozen; see PREREGISTRATION.md).

Both variants share one portfolio layer so that only the selection rule differs:
10 core names at 8% (80%) + 10 fillers at 1.5% (15%) = 95% invested. Core 8% (not K1's 8.5%) leaves
25% headroom before the 10% cap; fillers at 1.5% keep the 20-name minimum cheap.

- K3.0 Average: score = mean percentile rank of Mom20 (20-session log return), 10-session trust net buy /
  20-session mean volume, and latest revenue YoY, over names with all three defined.
- K3.1 ConfirmedMomentum: rank by Mom20 only, among names whose 10-session trust net buy > 0 and latest
  revenue YoY > 0 (momentum confirmed by flows and fundamentals; Chan, Jegadeesh & Lakonishok 1996;
  Novy-Marx 2015). If fewer than 20 names qualify, the rest are filled by the K3.0 score.

Trading (both): build on the first session; re-rank every 5 sessions and on the first decision after a
new revenue month becomes usable; core keeps held names while rank <= 15, fillers while rank <= 30; trade
only if one-way turnover >= 10%; the decided target is saved and retried on cash-band breaches.
Cap guard: a held weight above 9% is trimmed back to 8% with a sell-only order (other names unchanged),
which the planner's cash envelope can always fund. Last 3 sessions: no trades.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import importlib.util

import numpy as np
import pandas as pd

from competition import portfolio
from research.baselines import MomentumConfig, momentum_score

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('strategies_v4', HERE.parent / 'conc_tournament/strategies.py')
v4 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v4)
v3, v2 = v4.v3, v4.v2

CORE_N, CORE_W, FILL_N, FILL_W = 10, .08, 10, .015
CORE_KEEP, FILL_KEEP, EVERY, TRIM_AT = 15, 30, 5, .09


def _pct(raw: pd.Series, names) -> pd.Series:
    return raw.reindex(names).replace([np.inf, -np.inf], np.nan).rank(pct=True)


class _Composite:
    variant = None

    def __init__(self, calendar, rules, symbols):
        p = v4.panels(calendar, symbols)
        self.trust, self.rev, self.rules, self.log = p['trust'], p['rev'], rules, []
        self.target, self.month, self.last_day = None, None, None

    def parts(self, view):
        names = [s for s in view.symbols if view.valid[s].iloc[-20:].all()]
        mom = momentum_score(view, MomentumConfig(window=20)).reindex(names)
        trust = v3._flow_ratio(self.trust, view, names, 10)
        yoy = self.rev['yoy'].loc[view.date].reindex(names)
        score = pd.concat([_pct(mom, names), _pct(trust, names), _pct(yoy, names)], axis=1).mean(axis=1, skipna=False)
        return names, mom, trust, yoy, score

    def order(self, view) -> tuple[list[str], dict]:
        raise NotImplementedError

    def plan(self, view, current):
        order, info = self.order(view)
        rank = pd.Series(np.arange(len(order), 0, -1, dtype=float), index=order)     # higher = better
        held_core = [s for s, w in current.items() if w >= .05]
        held_fill = [s for s, w in current.items() if 0 < w < .05]
        core = portfolio.select(rank, held_core, SimpleNamespace(n_holdings=CORE_N, keep_rank=CORE_KEEP))
        fillers = portfolio.select(rank.drop(labels=core), held_fill,
                                   SimpleNamespace(n_holdings=FILL_N, keep_rank=FILL_KEEP - CORE_N))
        assert len(core) == CORE_N and len(fillers) == FILL_N
        target = v2._targets({**{s: CORE_W for s in core}, **{s: FILL_W for s in fillers}}, self.rules)
        return target, info

    def decide(self, view, state):
        current = dict(state.weights)
        month = self.rev['month'].loc[view.date].dropna().max()
        if self.target is None:
            self.target, info = self.plan(view, current)
            self.month, self.last_day = month, state.day_index
            self.log.append(dict(date=str(state.date.date()), action='build', **info))
            return dict(self.target)
        if state.sessions_remaining <= 3:
            return current
        if state.day_index - self.last_day >= EVERY or month != self.month:
            self.month, self.last_day = month, state.day_index
            target, info = self.plan(view, current)
            turnover = v2._turnover(target, current)
            self.log.append(dict(date=str(state.date.date()), action='rerank', turnover=turnover, **info))
            if turnover >= .10:
                self.target = target
                return dict(target)
        over = {s for s, w in current.items() if w > TRIM_AT}
        if over:
            self.log.append(dict(date=str(state.date.date()), action='trim', names=sorted(over)))
            return {s: (CORE_W if s in over else w) for s, w in current.items()}
        if state.cash < self.rules.cash_min or state.cash / state.nav >= self.rules.cash_max_exclusive:
            self.log.append(dict(date=str(state.date.date()), action='repair'))
            return dict(self.target)
        return current


class K30Average(_Composite):
    variant = 'K3.0'

    def order(self, view):
        names, mom, trust, yoy, score = self.parts(view)
        return portfolio.ranked(score), dict(scored=int(score.notna().sum()))


class K31ConfirmedMomentum(_Composite):
    variant = 'K3.1'

    def order(self, view):
        names, mom, trust, yoy, score = self.parts(view)
        ok = [s for s in names if trust.get(s, np.nan) > 0 and yoy.get(s, np.nan) > 0 and np.isfinite(mom.get(s, np.nan))]
        first = portfolio.ranked(mom[ok])
        rest = [s for s in portfolio.ranked(score) if s not in first]
        return first + rest, dict(confirmed=len(ok), filled_by_score=max(0, 20 - len(ok)))
