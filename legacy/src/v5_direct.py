"""V5 family E — direct (single-stage) portfolio-utility linear score (spec §4 E).

Interface (spec §3.2)::

    score_table(panel, decision_dates, config) -> pd.DataFrame

Config keys (defaults in ``DEFAULT_CONFIG``):

- ``train_window`` (T, default 250): number of most recent *matured* training
  sessions used per refit (grid {250, 750}).
- ``l2`` (lambda, default 1.0): L2 penalty on ``w`` (grid {0.1, 1.0}).
- ``horizon`` (h, default 10): label horizon in sessions (grid {10, 20}).
- ``refit_every`` (default 20): refit cadence in panel sessions.
- ``top_n`` (default 22): N of the soft top-N portfolio in the objective.
- ``entry_lag`` (default 1): label starts ``entry_lag`` sessions after the
  feature date (feature on D-1, trade on D), so the label is
  ``P[j+lag+h] / P[j+lag] - 1``.
- ``temperature`` (tau, default 1.0), ``steps`` (default 200),
  ``step_size`` (default 0.25), ``min_train_dates`` (default 60),
  ``winsor_z`` (default 3.0): fixed optimizer / preprocessing constants.
- ``seed`` (default 0): recorded for identity; the optimizer is fully
  deterministic (zero initialisation, fixed step count), so no RNG is drawn.

Model
-----
Features (``FEATURES``) are z-scored cross-sectionally per session among
``feature_ready`` names (population std), clipped to +-``winsor_z``; a missing
value of a ready name becomes 0 (neutral). ``score = x . w``.

Objective for a refit: mean over training sessions ``j`` of the soft top-N
portfolio return ``R_j = sum_i p_ij y_ij`` minus ``l2 * ||w||^2``, where
``g_ij = sigmoid((x_ij.w - theta_j) / tau)``, ``theta_j`` is the N-th largest
score of session ``j`` (treated as a constant in the gradient) and
``p_ij = g_ij / sum_i g_ij``. ``y`` is the h-session forward return in
**percentage points**, computed from the corporate-action-neutral
``signal_price`` inside one ``signal_history_segment`` (labels that cross a
quarantine reset, or with a missing/unavailable endpoint, are dropped).
Optimisation: ``w0 = 0``; ``steps`` iterations of
``w += step_size * (grad mean R - 2 l2 w)``.

Causality
---------
Session index ``k`` = last panel date strictly before the decision date
(``history_at`` semantics). The model used at ``k`` is the refit at
``r = (k // refit_every) * refit_every`` (sessions counted from the panel's
first date). That refit uses training sessions ``j`` with
``j + entry_lag + horizon <= r``, i.e. every label ended on or before session
``r <= k`` (< decision date). Per-session reductions are computed on the
compacted (date-sorted, symbol-sorted) set of valid names, so symbols that
exist only in the future cannot perturb floating-point results.

Audit: ``frame.attrs['refits']`` lists every refit (dates, window, counts,
objective, ``w`` by feature).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.special import expit

from src.v5_momentum import session_grid, prior_session_index, long_frame

FEATURES = ('R5', 'R20', 'R60', 'price_ema20', 'volume_ratio', 'vol20',
            'momentum_quality', 'drawdown20')
DEFAULT_CONFIG = {
    'train_window': 250, 'l2': 1.0, 'horizon': 10, 'refit_every': 20,
    'top_n': 22, 'entry_lag': 1, 'temperature': 1.0, 'steps': 200,
    'step_size': 0.25, 'min_train_dates': 60, 'winsor_z': 3.0, 'seed': 0,
}


def resolve_config(config):
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(config or {})
    unknown = set(cfg) - set(DEFAULT_CONFIG)
    if unknown:
        raise ValueError(f'unknown direct config keys: {sorted(unknown)}')
    for key in ('train_window', 'horizon', 'refit_every', 'top_n', 'entry_lag',
                'steps', 'min_train_dates', 'seed'):
        cfg[key] = int(cfg[key])
    for key in ('l2', 'temperature', 'step_size', 'winsor_z'):
        cfg[key] = float(cfg[key])
    if cfg['refit_every'] < 5:
        raise ValueError('spec §3.2: refit at most every 5 sessions')
    if min(cfg['train_window'], cfg['horizon'], cfg['top_n'], cfg['steps']) < 1 or cfg['entry_lag'] < 0:
        raise ValueError('invalid direct config')
    return cfg


def standardized_features(panel, dates, symbols, winsor_z):
    """(session, symbol, feature) z-scores among feature_ready names per session."""
    frame = panel.loc[panel['feature_ready'].fillna(False).astype(bool),
                      ['date', 'symbol', *FEATURES]].copy()
    frame['date'] = pd.to_datetime(frame['date'])
    frame['symbol'] = frame['symbol'].astype(str)
    frame = frame.sort_values(['date', 'symbol'], kind='mergesort').reset_index(drop=True)
    grid = np.zeros((len(dates), len(symbols), len(FEATURES)))
    di = dates.get_indexer(frame['date'])
    si = np.searchsorted(symbols, frame['symbol'].to_numpy())
    for f, name in enumerate(FEATURES):
        x = pd.to_numeric(frame[name], errors='coerce').astype(float)
        x = x.where(np.isfinite(x))
        grouped = x.groupby(frame['date'], sort=True)
        mean = grouped.transform('mean')
        std = grouped.transform('std', ddof=0)
        z = ((x - mean) / std.where(std > 0)).clip(-winsor_z, winsor_z).fillna(0.0)
        grid[di, si, f] = z.to_numpy(float)
    return grid


def forward_labels(grids, horizon, lag):
    """Forward h-session return (pp) starting `lag` sessions after row date."""
    price = grids['signal_price']
    avail = (grids['signal_available'] > 0) & np.isfinite(price) & (price > 0)
    seg = grids['signal_history_segment']
    n = price.shape[0]
    label = np.full(price.shape, np.nan)
    a, b = lag, lag + horizon
    if n > b:
        start, end = slice(a, n - horizon), slice(b, n)
        ok = avail[start] & avail[end] & (seg[start] == seg[end])
        with np.errstate(invalid='ignore', divide='ignore'):
            value = 100.0 * (price[end] / price[start] - 1.0)
        label[:n - b] = np.where(ok, value, np.nan)
    return label


def _compact(x, y, valid):
    """Left-align valid names per session (symbol order kept); drop padding width."""
    order = np.argsort(~valid, axis=1, kind='stable')
    counts = valid.sum(axis=1)
    width = int(counts.max())
    order = order[:, :width]
    xc = np.take_along_axis(x, order[:, :, None], axis=1)
    yc = np.take_along_axis(y, order, axis=1)
    mc = np.arange(width)[None, :] < counts[:, None]
    xc = np.where(mc[:, :, None], xc, 0.0)
    yc = np.where(mc, yc, 0.0)
    return xc, yc, mc, counts


def fit_weights(x, y, mask, counts, cfg):
    """Deterministic gradient ascent on mean soft top-N return - l2 ||w||^2."""
    d, width, nf = x.shape
    w = np.zeros(nf)
    tau, lam, eta = cfg['temperature'], cfg['l2'], cfg['step_size']
    n = cfg['top_n']
    few = counts <= n  # every valid name is in the top N: theta = min score
    flat_x = x.reshape(d * width, nf)

    def evaluate(w):
        s = (flat_x @ w).reshape(d, width)
        theta = np.where(mask, s, np.inf).min(axis=1)
        if n < width and not few.all():
            nth = -np.partition(np.where(mask, -s, np.inf), n - 1, axis=1)[:, n - 1]
            theta = np.where(few, theta, nth)
        g = np.where(mask, expit((s - theta[:, None]) / tau), 0.0)
        total = g.sum(axis=1)
        ret = (g * y).sum(axis=1) / total
        return g, total, ret

    for _ in range(cfg['steps']):
        g, total, ret = evaluate(w)
        dr_ds = (y - ret[:, None]) * g * (1.0 - g) / (tau * total[:, None])
        grad = (dr_ds.reshape(-1) @ flat_x) / d
        w = w + eta * (grad - 2.0 * lam * w)
    _, _, ret = evaluate(w)
    return w, float(ret.mean() - lam * w @ w), float(ret.mean())


def score_table(panel, decision_dates, config):
    cfg = resolve_config(config)
    extra = ['signal_price', 'signal_available', 'signal_history_segment']
    dates, symbols, grids, ready = session_grid(panel, extra)
    z = standardized_features(panel, dates, symbols, cfg['winsor_z'])
    label = forward_labels(grids, cfg['horizon'], cfg['entry_lag'])
    decision, prior = prior_session_index(dates, decision_dates)

    step = cfg['refit_every']
    refit_of = np.where(prior >= 0, (prior // step) * step, -1)
    score = np.full(ready.shape, np.nan)
    refits = []
    maturity = cfg['entry_lag'] + cfg['horizon']
    for r in np.unique(refit_of[refit_of >= 0]):
        j_end = int(r) - maturity
        j_start = max(0, j_end - cfg['train_window'] + 1)
        record = {'refit_session': int(r), 'refit_date': str(dates[r].date()),
                  'train_start': None, 'train_end': None, 'n_dates': 0,
                  'n_obs': 0, 'objective': None, 'train_return_pp': None, 'w': None}
        w = None
        if j_end >= 0:
            idx = np.arange(j_start, j_end + 1)
            valid = ready[idx] & np.isfinite(label[idx])
            keep = valid.sum(axis=1) >= 2
            idx, valid = idx[keep], valid[keep]
            if len(idx) >= cfg['min_train_dates']:
                xc, yc, mc, counts = _compact(z[idx], np.nan_to_num(label[idx]), valid)
                w, objective, train_ret = fit_weights(xc, yc, mc, counts, cfg)
                record.update(train_start=str(dates[idx[0]].date()),
                              train_end=str(dates[idx[-1]].date()),
                              label_end=str(dates[idx[-1] + maturity].date()),
                              n_dates=int(len(idx)), n_obs=int(counts.sum()),
                              objective=objective, train_return_pp=train_ret,
                              w={f: float(v) for f, v in zip(FEATURES, w)})
        refits.append(record)
        if w is None:
            continue
        sessions = np.unique(prior[refit_of == r])
        zs = z[sessions]
        s = np.zeros(zs.shape[:2])
        for f in range(len(FEATURES)):  # elementwise: bit-exact per name
            s = s + zs[:, :, f] * w[f]
        score[sessions] = np.where(ready[sessions], s, np.nan)

    out = long_frame(decision, prior, symbols, ready, score)
    out.attrs['model'] = {'family': 'E_direct', 'module': 'src.v5_direct',
                          'config': cfg, 'features': list(FEATURES)}
    out.attrs['refits'] = refits
    return out
