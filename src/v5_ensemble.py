"""V5 Family B -- walk-forward expert ensemble (M6 AutoTS-inspired), spec §4-B.

Public API
----------
``score_table(panel, decision_dates, config=None, horizon_by_date=None) -> pd.DataFrame``

Long frame sorted by (decision_date, symbol) with columns
``decision_date, symbol, score, expected_return, lower, upper, confidence, horizon``.
``expected_return`` is the hinge point ``P = (U + L) / 2`` (h-session return units).
``attrs``: ``model_identity``, ``config``, ``audit`` (per decision date: observed
date, refit anchor date, latest label end, expert weights).

Config keys (defaults in ``DEFAULT_CONFIG``)
-------------------------------------------
- ``horizon`` (20): int h in 1..60 or ``'remaining'``. For ``'remaining'`` the caller
  must pass ``horizon_by_date`` ({decision_date: int h}); each distinct h is fitted
  lazily by its own ``EnsembleModel`` (ablation only; ``horizon`` column records h).
- ``window`` (60): W, number of trailing matured origins (sessions) whose rank-IC
  and residuals define expert weights and the L/U interval.
- ``score_mode`` ('point'): ``'point'`` -> score = P; ``'point_conf'`` -> P x confidence.
- ``fit_lookback`` (250): trailing origins used to fit calibrations and the ridge.
- ``refit_every`` (5): sessions between refits (anchors = multiples of this index).
- ``ridge_alpha`` (1.0): ridge penalty = ridge_alpha * rows / 12 on slopes
  (features are cross-sectional percentile ranks - 0.5, variance ~1/12).
- ``weight_cap`` (0.5): per-expert weight bound.
- ``min_train_origins`` (10), ``min_weight_origins`` (5), ``min_names`` (10).

Method
------
Experts: momentum R5/R20/R60, reversal -R3, trend price_ema20 (heuristics) and a
ridge on standardized ``RIDGE_FEATURES``. Every feature is standardized as its
per-date percentile rank among ``feature_ready`` names minus 0.5. Each heuristic is
mapped to h-session return units by a univariate OLS (y = a + b x) fitted on matured
labels; the ridge predicts y directly. Labels are forward h-session returns of the
corporate-action-neutral ``signal_price`` that never cross a
``signal_history_segment`` reset.

Confidence: share of {L, P, U} and the 6 experts whose cross-sectional EXCESS forecast
(value minus the decision date's median over scored names) has the sign of P's excess.
Because L and U are P shifted by constants, their excess signs equal P's, so
confidence = (3 + #experts agreeing) / 9 for names off the median, and 0 for a name
exactly at the median. ``expected_return``/``lower``/``upper`` stay in raw return units.
(Design repair before any study result: raw signs were dominated by the market-level
intercept, giving confidence ~0.889 almost everywhere.)

Causality: sessions are indexed on the panel's date grid. For a decision date t the
observed index is c = last grid date < t and the refit anchor is a = (c // refit_every)
* refit_every <= c. Models at anchor a use origins s with s + h <= a (label end
observed by a). Out-of-sample forecasts F[s] use the model of anchor(s). Weights and
residual quantiles at anchor a use origins s in [a-h-W+1, a-h], i.e. matured labels.
All cross-sectional operations act on compacted arrays of valid names only, so
symbols that appear only in the future cannot change past arithmetic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import rankdata

MODEL_IDENTITY = 'V5_B_ENSEMBLE_6EXPERTS_RANKIC_HINGE_v1'
HEURISTICS = (('mom_R5', 'R5', 1.), ('mom_R20', 'R20', 1.), ('mom_R60', 'R60', 1.),
              ('rev_R3', 'R3', -1.), ('trend_ema20', 'price_ema20', 1.))
RIDGE_FEATURES = ('R3', 'R5', 'R10', 'R20', 'R60', 'price_ema20', 'volume_ratio',
                  'vol20', 'momentum_quality', 'drawdown20')
EXPERT_NAMES = tuple(h[0] for h in HEURISTICS) + ('ridge',)
DEFAULT_CONFIG = dict(horizon=20, window=60, score_mode='point', fit_lookback=250,
                      refit_every=5, ridge_alpha=1.0, weight_cap=0.5,
                      min_train_origins=10, min_weight_origins=5, min_names=10)
OUTPUT_COLUMNS = ['decision_date', 'symbol', 'score', 'expected_return', 'lower', 'upper',
                  'confidence', 'horizon']


def panel_grid(panel, columns):
    """Scatter long panel columns onto a (date, symbol) grid; missing cells are NaN."""
    dates = pd.DatetimeIndex(np.sort(pd.to_datetime(panel.date).unique()))
    symbols = np.sort(panel.symbol.astype(str).unique())
    di = dates.get_indexer(pd.to_datetime(panel.date))
    si = np.searchsorted(symbols, panel.symbol.astype(str).to_numpy())
    if len(np.unique(di.astype(np.int64) * len(symbols) + si)) != len(di):
        raise ValueError('panel has duplicate (date, symbol) rows')
    out = {}
    for col in columns:
        arr = np.full((len(dates), len(symbols)), np.nan)
        arr[di, si] = panel[col].to_numpy(float)
        out[col] = arr
    return dates, symbols, out, (di, si)


def forward_return(price, available, segment, horizon):
    """y[s] = p[s+h]/p[s] - 1 on the grid; NaN across segment resets or gaps."""
    y = np.full_like(price, np.nan)
    if horizon < len(price):
        ok = available[:-horizon] & available[horizon:] & (segment[:-horizon] == segment[horizon:])
        with np.errstate(invalid='ignore', divide='ignore'):
            y[:-horizon] = np.where(ok, price[horizon:] / price[:-horizon] - 1, np.nan)
    return y


def _pct_rank_features(panel, columns, grid_idx, shape):
    """Per-date percentile rank among feature_ready names with a finite value, minus 0.5."""
    frame = panel.loc[:, ['date', *columns]].copy()
    ready = panel.feature_ready.fillna(False).astype(bool).to_numpy()
    for col in columns:
        frame.loc[~ready | ~np.isfinite(frame[col].to_numpy(float)), col] = np.nan
    ranks = frame.groupby('date')[list(columns)].rank(pct=True) - .5
    out = np.full((*shape, len(columns)), np.nan)
    out[grid_idx[0], grid_idx[1], :] = ranks.to_numpy(float)
    return out


def bounded_weights(ic, cap=.5):
    """max(IC, 0) normalized, then capped at ``cap`` with proportional redistribution.

    Mass freed by capping goes to uncapped experts in proportion to their positive IC;
    if none has positive IC it is spread equally over the uncapped experts. No positive
    IC at all gives equal weights. Result lies in [0, cap] and sums to 1 (k*cap >= 1).
    """
    k = len(ic)
    q = np.maximum(np.nan_to_num(np.asarray(ic, float), nan=0.), 0.)
    if q.sum() <= 0:
        return np.full(k, 1. / k)
    capped = np.zeros(k, bool)
    while True:
        w = np.where(capped, cap, 0.)
        rest, free = 1. - cap * capped.sum(), ~capped
        base = q[free] if q[free].sum() > 0 else np.ones(free.sum())
        w[free] = rest * base / base.sum()
        over = w > cap
        if not over.any():
            return w
        capped |= over


def _spearman_columns(x, y):
    rx = rankdata(x, axis=0)
    ry = rankdata(y)
    cx = rx - rx.mean(axis=0)
    cy = ry - ry.mean()
    den = np.sqrt((cx ** 2).sum(axis=0) * (cy ** 2).sum())
    return np.divide(cx.T @ cy, den, out=np.full(x.shape[1], np.nan), where=den > 0)


class EnsembleModel:
    """All walk-forward state for one horizon; anchors are fitted on demand."""

    def __init__(self, panel, config, horizon):
        self.cfg = {**DEFAULT_CONFIG, **(config or {})}
        self.h = int(horizon)
        if not 1 <= self.h <= 60:
            raise ValueError('horizon must be in 1..60')
        heur_cols = [h[1] for h in HEURISTICS]
        feats = list(dict.fromkeys(heur_cols + list(RIDGE_FEATURES)))
        self.dates, self.symbols, g, idx = panel_grid(
            panel, ['signal_price', 'signal_history_segment', 'signal_available', 'feature_ready'])
        shape = g['signal_price'].shape
        self.ready = g['feature_ready'] == 1
        avail = (g['signal_available'] == 1) & np.isfinite(g['signal_price'])
        self.y = forward_return(g['signal_price'], avail, g['signal_history_segment'], self.h)
        z = _pct_rank_features(panel, feats, idx, shape)
        sign = np.array([h[2] for h in HEURISTICS])
        self.zh = z[:, :, [feats.index(c) for c in heur_cols]] * sign
        self.zr = z[:, :, [feats.index(c) for c in RIDGE_FEATURES]]
        self.xmask = self.ready & np.isfinite(self.zh).all(axis=2) & np.isfinite(self.zr).all(axis=2)
        self.n, self.k = shape[0], len(EXPERT_NAMES)
        self.F = np.full((*shape, self.k), np.nan)
        self.model_label_end = np.full(self.n, -1)
        self._fitted = set()
        self.ic = np.full((self.n, self.k), np.nan)
        self._ic_done = np.zeros(self.n, bool)
        self._anchor_cache = {}

    def _fit(self, a):
        """Fit calibrations + ridge at anchor a and fill F for origins [a, a+refit)."""
        if a in self._fitted:
            return
        self._fitted.add(a)
        cfg, h = self.cfg, self.h
        stop = a - h
        if stop < 0:
            return
        origins = np.arange(max(0, stop - cfg['fit_lookback'] + 1), stop + 1)
        m = self.xmask[origins] & np.isfinite(self.y[origins])
        if (m.sum(axis=1) >= cfg['min_names']).sum() < cfg['min_train_origins']:
            return
        xh, xr, y = self.zh[origins][m], self.zr[origins][m], self.y[origins][m]
        coef_h = np.empty((xh.shape[1], 2))
        for j in range(xh.shape[1]):
            x = xh[:, j]
            xc = x - x.mean()
            var = (xc * xc).mean()
            b = (xc * (y - y.mean())).mean() / var if var > 0 else 0.
            coef_h[j] = (y.mean() - b * x.mean(), b)
        design = np.column_stack([np.ones(len(y)), xr])
        penalty = np.eye(design.shape[1]) * (cfg['ridge_alpha'] * len(y) / 12.)
        penalty[0, 0] = 0.
        beta = np.linalg.solve(design.T @ design + penalty, design.T @ y)
        self.model_label_end[a] = int(origins[m.any(axis=1)].max()) + h
        for s in range(a, min(a + cfg['refit_every'], self.n)):
            f = np.full((len(self.symbols), self.k), np.nan)
            ok = self.xmask[s]
            f[ok, :-1] = coef_h[:, 0] + coef_h[:, 1] * self.zh[s][ok]
            f[ok, -1] = np.column_stack([np.ones(ok.sum()), self.zr[s][ok]]) @ beta
            self.F[s] = f

    def _forecast(self, s):
        self._fit((s // self.cfg['refit_every']) * self.cfg['refit_every'])
        return self.F[s]

    def _ic(self, s):
        if not self._ic_done[s]:
            self._ic_done[s] = True
            f = self._forecast(s)
            ok = np.isfinite(f).all(axis=1) & np.isfinite(self.y[s])
            if ok.sum() >= self.cfg['min_names']:
                self.ic[s] = _spearman_columns(f[ok], self.y[s][ok])
        return self.ic[s]

    def anchor_state(self, a):
        """Expert weights and residual quantiles at anchor a (matured origins only)."""
        if a in self._anchor_cache:
            return self._anchor_cache[a]
        cfg, h = self.cfg, self.h
        stop = a - h
        state = None
        if stop >= 0:
            origins = [s for s in range(max(0, stop - cfg['window'] + 1), stop + 1)
                       if np.isfinite(self._ic(s)).all()]
            if len(origins) >= cfg['min_weight_origins']:
                origins = np.array(origins)
                w = bounded_weights(self.ic[origins].mean(axis=0), cfg['weight_cap'])
                f, y = self.F[origins], self.y[origins]
                ok = np.isfinite(f).all(axis=2) & np.isfinite(y)
                resid = y[ok] - f[ok] @ w
                q10, q90 = np.quantile(resid, [.1, .9])
                state = dict(weights=w, q10=float(q10), q90=float(q90),
                             label_end=int(origins.max()) + h)
        self._anchor_cache[a] = state
        return state

    def predict(self, decision_date):
        """Cross-section for one decision date, or None when no causal model exists."""
        c = int(self.dates.searchsorted(pd.Timestamp(decision_date), side='left')) - 1
        if c < 0:
            return None
        a = (c // self.cfg['refit_every']) * self.cfg['refit_every']
        f = self._forecast(c)
        state = self.anchor_state(a)
        if state is None or self.model_label_end[a] < 0:
            return None
        ok = self.ready[c] & np.isfinite(f).all(axis=1)
        if not ok.any():
            return None
        f = f[ok]
        e = f @ state['weights']
        lower, upper = e + state['q10'], e + state['q90']
        point = (upper + lower) / 2.
        # Sign agreement on cross-sectional excess forecasts (minus the date median),
        # so the market-level intercept cannot make agreement degenerate.
        xs = lambda v: v - np.median(v, axis=0)
        sp = np.sign(xs(point))
        agree = (np.sign(xs(lower)) == sp).astype(float) + (np.sign(xs(upper)) == sp) + 1. \
            + (np.sign(xs(f)) == sp[:, None]).sum(axis=1)
        conf = np.where(sp == 0, 0., agree / (3. + self.k))
        score = point if self.cfg['score_mode'] == 'point' else point * conf
        frame = pd.DataFrame(dict(symbol=self.symbols[ok], score=score, expected_return=point,
                                  lower=lower, upper=upper, confidence=conf,
                                  horizon=np.full(ok.sum(), self.h, dtype=np.int64)))
        audit = dict(observed_date=self.dates[c], anchor_date=self.dates[a],
                     latest_label_end=self.dates[max(self.model_label_end[a], state['label_end'])],
                     weights=dict(zip(EXPERT_NAMES, state['weights'].tolist())),
                     q10=state['q10'], q90=state['q90'])
        return frame, audit


def score_table(panel, decision_dates, config=None, horizon_by_date=None):
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    if cfg['score_mode'] not in ('point', 'point_conf'):
        raise ValueError("score_mode must be 'point' or 'point_conf'")
    dates = pd.DatetimeIndex(sorted(set(pd.to_datetime(list(decision_dates)))))
    if cfg['horizon'] == 'remaining':
        if horizon_by_date is None:
            raise ValueError("horizon='remaining' requires horizon_by_date")
        hmap = {pd.Timestamp(k): int(v) for k, v in dict(horizon_by_date).items()}
        horizons = [hmap[d] for d in dates]
    else:
        horizons = [int(cfg['horizon'])] * len(dates)
    models, frames, audit = {}, [], []
    for d, h in zip(dates, horizons):
        if h not in models:
            models[h] = EnsembleModel(panel, cfg, h)
        result = models[h].predict(d)
        if result is None:
            continue
        frame, info = result
        frame.insert(0, 'decision_date', d)
        frames.append(frame)
        audit.append(dict(decision_date=d, horizon=h, **info))
    out = (pd.concat(frames, ignore_index=True) if frames else
           pd.DataFrame({c: pd.Series(dtype='float64') for c in OUTPUT_COLUMNS}))
    out = out.loc[:, OUTPUT_COLUMNS].sort_values(['decision_date', 'symbol'], kind='stable').reset_index(drop=True)
    out.attrs.update(model_identity=MODEL_IDENTITY, experts=list(EXPERT_NAMES),
                     config={k: cfg[k] for k in sorted(cfg)}, audit=audit)
    return out
