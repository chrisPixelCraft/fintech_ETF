"""Defensive candidate (frozen; see PREREGISTRATION.md).

D: the 22 lowest-volatility names (60-session daily return std, names fully valid over 60 sessions), equal weight,
targeting INVESTED of NAV. Built on the first session; re-screened once when a new revenue month becomes usable
(same calendar as K2; trade if turnover >= 10%); last-3 freeze; cap or cash breach re-targets the current plan.
"""
from __future__ import annotations

from pathlib import Path
import importlib.util

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('strategies_conc', HERE.parent / 'conc_tournament/strategies.py')
conc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conc)
N, WINDOW, INVESTED = 22, 60, .80


class LowVol:
    variant = 'D'

    def __init__(self, calendar, rules, symbols, invested=INVESTED):
        self.rev, self.rules, self.log = conc.panels(calendar, symbols)['rev'], rules, []
        self.invested, self.current, self.month = invested, None, None

    def plan(self, view):
        valid = view.valid.iloc[-WINDOW:]
        names = list(valid.columns[valid.all()])
        vol = view.ret[names].iloc[-WINDOW:].std()
        chosen = conc.portfolio.ranked(-vol)[:N]
        assert len(chosen) == N
        return {s: self.invested / N for s in chosen}, dict(scored=int(vol.notna().sum()))

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
            turnover = conc.v2._turnover(target, state.weights)
            self.log.append(dict(date=str(state.date.date()), action='rescreen', turnover=turnover, **info))
            if turnover >= .10:
                self.current = target
                return target.copy()
        if conc._breach(state, self.rules):
            self.log.append(dict(date=str(state.date.date()), action='repair'))
            return self.current.copy()
        return dict(state.weights)
