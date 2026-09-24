"""V5 strategy object called by ``src.v5_episode.run_episode`` once per decision.

The signal is a precomputed, causal score table (spec §3.2); this class only
looks up the decision date's cross-section, reads the D-1 panel rows and the
D-1 regime, and delegates to ``src.v5_portfolio.construct``.
"""
from __future__ import annotations

import copy
import importlib

import numpy as np
import pandas as pd

from src.v5_portfolio import construct, regime_at, settings

FAMILY_MODULES = {'A': 'src.v5_momentum', 'B': 'src.v5_ensemble', 'C': 'src.v5_rank', 'E': 'src.v5_direct'}
FAMILY_NAMES = {'A': 'momentum', 'B': 'ensemble', 'C': 'rank', 'E': 'direct'}
SIMPLICITY = {'A': 0, 'E': 1, 'C': 2, 'B': 3, 'V3': 9}
SCORE_COLUMNS = ('score', 'expected_return', 'lower', 'upper', 'confidence', 'p_topk')
_PANEL_INDEX = {}


def signal_module(family):
    """Lazy import so the harness runs before and after the signal modules land."""
    return importlib.import_module(FAMILY_MODULES[family])


def _panel_index(panel):
    """date -> cross-section, cached per panel object (the panel is read-only)."""
    key = (id(panel), len(panel))
    cached = _PANEL_INDEX.get(key)
    if cached is None:
        dates = pd.to_datetime(panel.date)
        order = pd.DatetimeIndex(sorted(dates.unique()))
        groups = {d: g for d, g in panel.groupby(dates, sort=True)}
        cached = (order, groups)
        _PANEL_INDEX.clear()
        _PANEL_INDEX[key] = cached
    return cached


def previous_rows(panel, decision_date):
    """Same contract as src.v5_features.history_at: last cross-section strictly before t."""
    order, groups = _panel_index(panel)
    t = pd.Timestamp(decision_date).normalize()
    position = order.searchsorted(t, side='left')
    if position == 0:
        return panel.iloc[:0].copy()
    return groups[order[position - 1]].copy()


class Strategy:
    def __init__(self, candidate, scores, regime=None):
        self.candidate = copy.deepcopy(candidate)
        self.family = str(candidate['family'])
        self.construction = settings(candidate.get('construction', {}))
        if self.construction['regime'] and regime is None:
            raise ValueError('Regime overlay requires a causal regime table')
        self.regime = regime
        frame = scores.copy()
        frame['decision_date'] = pd.to_datetime(frame.decision_date).dt.normalize()
        frame['symbol'] = frame.symbol.astype(str)
        if frame.duplicated(['decision_date', 'symbol']).any():
            raise ValueError('Duplicate score rows for a decision date and symbol')
        self.by_date = {d: g.set_index('symbol', drop=False) for d, g in frame.groupby('decision_date', sort=True)}
        self.identity = f"V5_{self.family}_{FAMILY_NAMES.get(self.family, self.family)}:{candidate['id']}"
        self.log = []

    def _failed(self, decision_date, reason, **audit):
        frame = pd.DataFrame(columns=['target_weight', 'score'], index=pd.Index([], name='symbol'), dtype=float)
        frame.attrs.update(status='NO_VALID_PLAN', reason='INFEASIBLE_V5:' + reason,
                           identity=self.identity, decision_date=str(pd.Timestamp(decision_date).date()), **audit)
        self.log.append(dict(decision_date=frame.attrs['decision_date'], observed_date=audit.get('observed_date'),
                             reason=frame.attrs['reason'], names=0, regime=audit.get('regime')))
        return frame

    def generate_weights(self, decision_date, panel, portfolio_state, competition_state=None):
        t = pd.Timestamp(decision_date).normalize()
        rows = previous_rows(panel, t)
        if rows.empty:
            return self._failed(t, 'NO_PRIOR_OBSERVATIONS')
        observed = pd.Timestamp(rows.date.max())
        if not observed < t:
            raise AssertionError('Observed rows are not strictly before the decision date')
        audit = dict(observed_date=str(observed.date()))
        scores = self.by_date.get(t)
        if scores is None or scores.empty:
            return self._failed(t, 'MISSING_SCORES', **audit)
        state = dict(portfolio_state or {})
        if self.construction['regime']:
            state['regime'] = regime_at(self.regime, t)
            audit['regime'] = state['regime']
        weights = construct(scores, rows, state, self.construction)
        if weights.empty:
            return self._failed(t, weights.attrs.get('reason', 'EMPTY_TARGET'), **audit)
        names = weights.index
        out = pd.DataFrame(dict(target_weight=weights), index=names)
        for column in SCORE_COLUMNS:
            if column in scores:
                out[column] = pd.to_numeric(scores[column], errors='coerce').reindex(names)
        out.index.name = 'symbol'
        out['decision_date'] = str(t.date())
        out['observed_date'] = audit['observed_date']
        attrs = {k: v for k, v in weights.attrs.items()}
        out.attrs.update(attrs, status='TARGET', identity=self.identity, candidate=self.candidate['id'],
                         family=self.family, decision_date=str(t.date()), **audit)
        self.log.append(dict(decision_date=str(t.date()), observed_date=audit['observed_date'],
                             reason=attrs['reason'], names=len(names), regime=attrs.get('regime'),
                             kept=len(attrs.get('kept', [])), added=len(attrs.get('added', [])),
                             forced_drops=len(attrs.get('forced_drops', [])),
                             voluntary_drops=len(attrs.get('voluntary_drops', [])),
                             cash_target=attrs.get('cash_target')))
        return out

    # Alias for adapters written against the V4 name.
    generate_target = generate_weights

    def predictions(self):
        return pd.DataFrame(self.log)


def build_score_table(family, panel, decision_dates, signal_config):
    """Call a family's score_table and check the declared contract shape."""
    table = signal_module(family).score_table(panel, pd.DatetimeIndex(decision_dates), copy.deepcopy(signal_config))
    required = {'decision_date', 'symbol', 'score'}
    if not required <= set(table.columns):
        raise ValueError(f'{family} score_table lacks {sorted(required - set(table.columns))}')
    table = table.copy()
    table['decision_date'] = pd.to_datetime(table.decision_date).dt.normalize()
    table['symbol'] = table.symbol.astype(str)
    keep = ['decision_date', 'symbol', *[c for c in SCORE_COLUMNS if c in table.columns]]
    table = table[keep].sort_values(['decision_date', 'symbol']).reset_index(drop=True)
    if table.duplicated(['decision_date', 'symbol']).any():
        raise ValueError(f'{family} score_table has duplicate rows')
    for column in keep[2:]:
        table[column] = pd.to_numeric(table[column], errors='coerce').astype(float)
    if np.isinf(table.score).any():
        raise ValueError(f'{family} score_table has infinite scores')
    return table
