"""Batch-4 concentrated candidates for a top-6 tournament (frozen; see PREREGISTRATION.md).

Concentrated layout inside the rules (20–30 names, 10% cap): 10 core names at 8.5% (85%) plus 10 filler
names at 1% (10%) = 95% invested. 2330 is treated like any other name (8.5% < its 25% cap).

- K1 MomConc: core = top 10 by Mom20 score (keep held core while rank <= 14); fillers = next names by the
  same score (keep held fillers while rank <= 30). Daily decision, trade only if one-way turnover >= 10%.
- K2 TrustRevConc: FinLab filter (10-session trust net buy > 0, 3-month average revenue at its 12-month
  high, latest YoY > 20%), ranked by 10-session trust net buy / 20-session mean volume. Core = top 10 of the
  filtered list; fillers = filtered ranks 11–20, then large-cap rank. Built on the first session and
  re-screened once when a new revenue month becomes usable (trade if turnover >= 10%).
- T2c: batch-3 LargeTrust with panels built once per process (identical selection logic).
All: last-3-session freeze; a held weight above cap - 0.5pp or a cash breach re-targets to the current plan.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
import importlib.util

import pandas as pd

from competition import portfolio
from research.baselines import MomentumConfig, momentum_score

HERE = Path(__file__).resolve().parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v3 = _load('strategies_v3', HERE.parent / 'recent5_v3/strategies.py')
v2 = v3.v2
CORE_N, CORE_W, FILL_N, FILL_W = 10, .085, 10, .01
_PANELS = {}


def panels(calendar, symbols) -> dict:
    """Trust and revenue panels, built once per (calendar, symbols)."""
    key = (calendar[0], calendar[-1], len(calendar), tuple(symbols))
    if key not in _PANELS:
        _PANELS.clear()
        _PANELS[key] = dict(trust=v3.trust_panel(calendar, symbols), rev=v3.revenue_high_panel(calendar))
    return _PANELS[key]


def _concentrated(core: list[str], fillers: list[str], rules) -> dict:
    assert len(core) == CORE_N and len(fillers) == FILL_N and not set(core) & set(fillers)
    return v2._targets({**{s: CORE_W for s in core}, **{s: FILL_W for s in fillers}}, rules)


def _breach(state, rules) -> bool:
    return (any(w > rules.cap(s) - .005 for s, w in state.weights.items())
            or state.cash < rules.cash_min or state.cash / state.nav >= rules.cash_max_exclusive)


class MomConc:
    def __init__(self, rules):
        self.rules, self.log = rules, []

    def decide(self, view, state):
        if state.sessions_remaining <= 3 and state.weights:
            return dict(state.weights)
        scores = momentum_score(view, MomentumConfig(window=20))
        held_core = [s for s, w in state.weights.items() if w >= .05]
        held_fill = [s for s, w in state.weights.items() if 0 < w < .05]
        core = portfolio.select(scores, held_core, SimpleNamespace(n_holdings=CORE_N, keep_rank=14))
        rest = scores.drop(labels=core, errors='ignore')
        fillers = portfolio.select(rest, held_fill, SimpleNamespace(n_holdings=FILL_N, keep_rank=30 - CORE_N))
        target = _concentrated(core, fillers, self.rules)
        turnover = v2._turnover(target, state.weights)
        trade = not state.weights or turnover >= .10 or _breach(state, self.rules)
        self.log.append(dict(date=str(state.date.date()), action='trade' if trade else 'hold', turnover=turnover))
        return target if trade else dict(state.weights)


class TrustRevConc:
    def __init__(self, calendar, rules, symbols):
        p = panels(calendar, symbols)
        self.trust, self.rev, self.rules, self.log = p['trust'], p['rev'], rules, []
        self.current, self.month = None, None

    def plan(self, view):
        large = v3.large_order(view)
        names = [s for s in view.symbols if view.valid[s].iloc[-20:].all()]
        ratio = v3._flow_ratio(self.trust, view, names, 10)
        yoy, high = self.rev['yoy'].loc[view.date], self.rev['high3'].loc[view.date]
        ok = portfolio.ranked(ratio[[s for s in names if ratio.get(s, float('nan')) > 0
                                     and high.get(s) == 1 and yoy.get(s, float('nan')) > .20]])
        ordered = ok + [s for s in large if s not in ok]
        core, fillers = ordered[:CORE_N], ordered[CORE_N:CORE_N + FILL_N]
        info = dict(passed_filter=len(ok), core_from_filter=min(len(ok), CORE_N))
        return _concentrated(core, fillers, self.rules), info

    def decide(self, view, state):
        month = self.rev['month'].loc[view.date].dropna().max()
        if self.current is None:
            self.current, info = self.plan(view)
            self.month = month
            self.log.append(dict(date=str(state.date.date()), action='build', **info))
            return self.current.copy()
        if state.sessions_remaining <= 3:
            return dict(state.weights)
        if month != self.month:
            self.month = month
            target, info = self.plan(view)
            turnover = v2._turnover(target, state.weights)
            self.log.append(dict(date=str(state.date.date()), action='rescreen', turnover=turnover, **info))
            if turnover >= .10:
                self.current = target
                return target.copy()
        if _breach(state, self.rules):
            self.log.append(dict(date=str(state.date.date()), action='repair'))
            return self.current.copy()
        return dict(state.weights)


class LargeTrustCached(v3.LargeTrust):
    """Batch-3 T2 with shared panels; selection and trading logic inherited unchanged."""

    def __init__(self, calendar, rules, symbols):
        p = panels(calendar, symbols)
        self.rules, self.log = rules, []
        self.trust, self.rev = p['trust'], p['rev']
        self.current, self.month = None, None
