"""Weighted K3 (frozen; see PREREGISTRATION.md).

Score = w_mom * pct(Mom20) + w_trust * pct(10-session trust net buy / 20-session mean volume)
        + w_rev * pct(latest revenue YoY), percentile ranks over fully valid names; components with
weight 0 are ignored (their missing values do not drop a name).
Portfolio layer = K3 layer with a 2-name buffer against the 20-name minimum: 10 core at 8% + 12 fillers
at 1.25% (= 95%). Everything else (5-session re-rank, revenue-month re-rank, keep ranks 15/32, 10%
turnover gate, saved target, >9% sell-only trim, last-3 freeze) is inherited from k3_composite.
"""
from __future__ import annotations

from pathlib import Path
import importlib.util

import pandas as pd

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('strategies_k3', HERE.parent / 'k3_composite/strategies.py')
k3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(k3)

# Simplex grid, step 0.25 (15 points) plus the equal-weight point of K3.0.
GRID = [(a / 4, b / 4, (4 - a - b) / 4) for a in range(5) for b in range(5 - a)] + [(1 / 3, 1 / 3, 1 / 3)]


class K3Weighted(k3._Composite):
    variant = 'K3w'

    def __init__(self, calendar, rules, symbols, weights):
        super().__init__(calendar, rules, symbols)
        self.weights = tuple(float(w) for w in weights)
        assert len(self.weights) == 3 and abs(sum(self.weights) - 1) < 1e-9 and min(self.weights) >= 0

    def order(self, view):
        names, mom, trust, yoy, _ = self.parts(view)
        parts = [k3._pct(x, names) * w for x, w in zip((mom, trust, yoy), self.weights) if w > 0]
        score = pd.concat(parts, axis=1).sum(axis=1, skipna=False)
        return k3.portfolio.ranked(score), dict(scored=int(score.notna().sum()))

    def plan(self, view, current):
        saved = k3.FILL_N, k3.FILL_W, k3.FILL_KEEP
        k3.FILL_N, k3.FILL_W, k3.FILL_KEEP = 12, .0125, 32
        try:
            return super().plan(view, current)
        finally:
            k3.FILL_N, k3.FILL_W, k3.FILL_KEEP = saved


class K2Buffer(k3.v4.TrustRevConc):
    """Diagnostic: production K2 with the same 2-name buffer (10 core at 8.5% + 12 fillers at 10%/12)."""

    def plan(self, view):
        v4 = k3.v4
        saved = v4.FILL_N, v4.FILL_W
        v4.FILL_N, v4.FILL_W = 12, .10 / 12
        try:
            return super().plan(view)
        finally:
            v4.FILL_N, v4.FILL_W = saved
