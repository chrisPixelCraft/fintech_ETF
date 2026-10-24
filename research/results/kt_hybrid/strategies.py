"""KT: one portfolio holding a K2 sleeve and a T2 sleeve (frozen; see PREREGISTRATION.md).

alpha = K2 share of the 95% invested; 1 - alpha goes to the T2 sleeve.
- K2 sleeve: K2's ranking (trust buy + revenue at a 12-month high + YoY > 20%, large-cap fill); its top 10
  names share alpha * 95% equally (K2's 1% fillers are dropped so the merged book stays <= 30 names).
- T2 sleeve: 2330 + top 18 of the 40 largest (ex-2330) by 20-session trust buy / mean volume;
  2330 takes 23.5/95 of the sleeve, the 18 names share the rest equally.
Overlapping names add their weights. Caps are enforced by cap_fill (2330 25%, others 10%), budget 95%.
If the merged book has fewer than 22 names, the next K2 ranks are added at 1% each before cap_fill
re-scales, keeping a 2-name buffer above the 20-name minimum.
Trading: build on the first session; the T2 sleeve names stay fixed for the window; the K2 sleeve is
re-ranked on the first decision after a new revenue month and the merged target is traded only if one-way
turnover >= 10%; the decided target is saved and retried on cap/cash breaches (cap - 0.5pp); last 3
sessions: no trades.
"""
from __future__ import annotations

from pathlib import Path
import importlib.util

import numpy as np
import pandas as pd

from competition import portfolio

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('strategies_v4', HERE.parent / 'conc_tournament/strategies.py')
v4 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v4)
v3, v2 = v4.v3, v4.v2

TSMC, INVESTED, K2_CORE, T2_NAMES, POOL, MIN_NAMES = '2330.TW', .95, 10, 18, 40, 22
ALPHAS = (.25, .5, .75)


class KTHybrid:
    def __init__(self, calendar, rules, symbols, alpha):
        p = v4.panels(calendar, symbols)
        self.trust, self.rev, self.rules, self.alpha, self.log = p['trust'], p['rev'], rules, float(alpha), []
        self.target, self.month, self.t2 = None, None, None
        assert 0 < self.alpha < 1

    def k2_order(self, view) -> tuple[list[str], int]:
        large = v3.large_order(view)
        names = [s for s in view.symbols if view.valid[s].iloc[-20:].all()]
        ratio = v3._flow_ratio(self.trust, view, names, 10)
        yoy, high = self.rev['yoy'].loc[view.date], self.rev['high3'].loc[view.date]
        ok = portfolio.ranked(ratio[[s for s in names if ratio.get(s, np.nan) > 0
                                     and high.get(s) == 1 and yoy.get(s, np.nan) > .20]])
        return ok + [s for s in large if s not in ok], len(ok)

    def t2_names(self, view) -> list[str]:
        pool = [s for s in v3.large_order(view) if s != TSMC][:POOL]
        return portfolio.ranked(v3._flow_ratio(self.trust, view, pool, 20))[:T2_NAMES]

    def plan(self, view) -> tuple[dict, dict]:
        order, passed = self.k2_order(view)
        if self.t2 is None:
            self.t2 = self.t2_names(view)
        w = {}
        for s in order[:K2_CORE]:
            w[s] = w.get(s, 0.) + self.alpha * INVESTED / K2_CORE
        t2_budget = (1 - self.alpha) * INVESTED
        w[TSMC] = w.get(TSMC, 0.) + t2_budget * .235 / INVESTED
        for s in self.t2:
            w[s] = w.get(s, 0.) + t2_budget * (1 - .235 / INVESTED) / T2_NAMES
        extra = [s for s in order[K2_CORE:] if s not in w]
        while len(w) < MIN_NAMES and extra:
            w[extra.pop(0)] = .01
        assert self.rules.min_positions <= len(w) <= self.rules.max_positions
        caps = pd.Series({s: self.rules.cap(s) for s in w})
        target = portfolio.cap_fill(pd.Series(w), INVESTED, caps).to_dict()
        return target, dict(k2_passed=passed, names=len(target))

    def decide(self, view, state):
        current = dict(state.weights)
        month = self.rev['month'].loc[view.date].dropna().max()
        if self.target is None:
            self.target, info = self.plan(view)
            self.month = month
            self.log.append(dict(date=str(state.date.date()), action='build', **info))
            return dict(self.target)
        if state.sessions_remaining <= 3:
            return current
        if month != self.month:
            self.month = month
            target, info = self.plan(view)
            turnover = v2._turnover(target, current)
            self.log.append(dict(date=str(state.date.date()), action='rescreen', turnover=turnover, **info))
            if turnover >= .10:
                self.target = target
                return dict(target)
        breach = (any(x > self.rules.cap(s) - .005 for s, x in current.items())
                  or state.cash < self.rules.cash_min or state.cash / state.nav >= self.rules.cash_max_exclusive)
        if breach:
            self.log.append(dict(date=str(state.date.date()), action='repair'))
            return dict(self.target)
        return current
