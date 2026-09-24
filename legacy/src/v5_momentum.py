"""V5 family A — cross-sectional momentum rank blend (docs/v5_spec.md §4 A).

Interface (spec §3.2)::

    score_table(panel, decision_dates, config) -> pd.DataFrame

Config keys (grid keys of spec §4 A that belong to the signal; N and rotation
belong to construction and are ignored here):

- ``blend``: ``'short'`` (R5, R10, R20) or ``'long'`` (R20, R60). Default ``'short'``.
- ``trend_filter``: bool. When true, names with ``price_ema20 <= 0`` (or NaN)
  keep a row but get ``score = NaN`` (ineligible). Default ``False``.

Score: equal-weighted mean of the cross-sectional percentile ranks
(``rank(pct=True)``, average ties) of the blend's returns, ranked among the
``feature_ready`` names of the D-1 cross-section. The trend filter is applied
after ranking (it is an eligibility mask, not a change of rank universe).

Causality: for decision date ``t`` the D-1 cross-section is the last panel
date strictly before ``t`` (``v5_features.history_at`` semantics). Only names
with ``feature_ready`` on that date receive rows. Nothing else is read.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BLENDS = {'short': ('R5', 'R10', 'R20'), 'long': ('R20', 'R60')}
DEFAULT_CONFIG = {'blend': 'short', 'trend_filter': False}
OUTPUT_COLUMNS = ['decision_date', 'symbol', 'score']


def resolve_config(config):
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(config or {})
    unknown = set(cfg) - set(DEFAULT_CONFIG)
    if unknown:
        raise ValueError(f'unknown momentum config keys: {sorted(unknown)}')
    if cfg['blend'] not in BLENDS:
        raise ValueError(f"blend must be one of {sorted(BLENDS)}")
    cfg['trend_filter'] = bool(cfg['trend_filter'])
    return cfg


def session_grid(panel, columns):
    """Pivot panel columns onto a (session date x symbol) grid.

    Row order / symbol order of the input never matters. Each grid row holds
    only that date's observations, so any per-row computation is causal.
    Returns (dates, symbols, {column: 2-D float array}, ready bool array).
    """
    frame = panel[['date', 'symbol', 'feature_ready', *columns]].copy()
    frame['date'] = pd.to_datetime(frame['date'])
    if frame.duplicated(['date', 'symbol']).any():
        raise ValueError('panel has duplicate (date, symbol) rows')
    dates = pd.DatetimeIndex(np.sort(frame['date'].unique()))
    symbols = np.sort(frame['symbol'].astype(str).unique())
    di = dates.get_indexer(frame['date'])
    si = np.searchsorted(symbols, frame['symbol'].astype(str).to_numpy())
    shape = (len(dates), len(symbols))
    grids = {}
    for column in columns:
        values = np.full(shape, np.nan)
        values[di, si] = pd.to_numeric(frame[column], errors='coerce').to_numpy(float)
        grids[column] = values
    ready = np.zeros(shape, dtype=bool)
    ready[di, si] = frame['feature_ready'].fillna(False).astype(bool).to_numpy()
    return dates, symbols, grids, ready


def prior_session_index(dates, decision_dates):
    """Index of the last session date strictly before each decision date (-1 if none)."""
    decision = pd.DatetimeIndex(pd.to_datetime(pd.Index(decision_dates)))
    return decision, np.searchsorted(dates.values, decision.values, side='left') - 1


def long_frame(decision, prior, symbols, ready, score, extra=None):
    """Emit one row per (decision date, feature_ready name on its D-1 session)."""
    valid = prior >= 0
    d_idx = np.flatnonzero(valid)
    sub_ready = ready[prior[valid]]
    di, si = np.nonzero(sub_ready)
    out = pd.DataFrame({
        'decision_date': decision[d_idx[di]],
        'symbol': symbols[si],
        'score': score[prior[valid]][di, si],
    })
    for name, grid in (extra or {}).items():
        out[name] = grid[prior[valid]][di, si] if grid.ndim == 2 else grid[d_idx[di]]
    out = out.sort_values(['decision_date', 'symbol'], kind='mergesort').reset_index(drop=True)
    return out


def cross_section_pct_rank(values, mask):
    """Row-wise pct rank (average ties) among masked, finite entries."""
    masked = np.where(mask, values, np.nan)
    return pd.DataFrame(masked).rank(axis=1, pct=True, method='average').to_numpy(float)


def score_table(panel, decision_dates, config):
    cfg = resolve_config(config)
    columns = list(BLENDS[cfg['blend']]) + ['price_ema20']
    dates, symbols, grids, ready = session_grid(panel, columns)
    ranks = [cross_section_pct_rank(grids[c], ready) for c in BLENDS[cfg['blend']]]
    stacked = np.stack(ranks)
    counts = np.isfinite(stacked).sum(axis=0)
    with np.errstate(invalid='ignore'):
        score = np.where(counts > 0, np.nansum(stacked, axis=0) / np.maximum(counts, 1), np.nan)
    if cfg['trend_filter']:
        trend_ok = grids['price_ema20'] > 0  # NaN compares False -> excluded
        score = np.where(trend_ok, score, np.nan)
    decision, prior = prior_session_index(dates, decision_dates)
    out = long_frame(decision, prior, symbols, ready, score)
    out.attrs['model'] = {'family': 'A_momentum', 'module': 'src.v5_momentum',
                          'config': cfg, 'columns': list(BLENDS[cfg['blend']])}
    return out
