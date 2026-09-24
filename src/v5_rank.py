"""V5 Family C -- rank-frequency top-quintile classifier (M6 ATA-inspired), spec §4-C.

Public API
----------
``score_table(panel, decision_dates, config=None) -> pd.DataFrame``

Long frame sorted by (decision_date, symbol) with columns
``decision_date, symbol, score, p_topk, agreement``. ``p_topk`` is the mean over the
three window-variant models of P(forward h-session return in the top cross-sectional
quintile); ``agreement`` (0..3) counts variants placing the name in their own top N.
``attrs``: ``model_identity``, ``config``, ``audit`` (per decision date: observed date,
refit anchor date, latest label end, training rows per variant).

Config keys (defaults in ``DEFAULT_CONFIG``)
-------------------------------------------
- ``horizon`` (20): h, label horizon in sessions (spec grid {10, 20}).
- ``train_window`` (750): T, trailing origins (sessions) of matured labels per fit.
- ``score_mode`` ('p'): ``'p'`` -> score = mean p; ``'p_agree'`` -> mean p x agreement / 3.
- ``agree_topn`` (22): N for ``agreement``.
- ``windows`` ((10, 20, 60)): rank-frequency windows, one logistic model each.
- ``l2`` (1.0): L2 penalty on slopes (intercept unpenalized).
- ``refit_every`` (5): sessions between refits.
- ``min_coverage`` (0.5): a window feature needs >= ceil(min_coverage * w) valid days.
- ``min_names`` (10): minimum names for a daily quintile / label cross-section.
- ``min_train_rows`` (500), ``irls_max_iter`` (50), ``irls_tol`` (1e-10).

Method
------
Daily return = panel ``R1`` (corporate-action-neutral ``signal_price`` change within a
signal segment). Each date, names with a finite R1 are ranked (pct, average ties) and
bucketed into quintiles 1..5. Feature for window w: share of the last w grid sessions
(ending at the row date, inclusive) spent in each quintile, among days with a bucket;
features are centred by subtracting 0.2. Label at origin s: forward h-session
signal_price return (no segment crossing) ranks in the top quintile (pct > 0.8) among
names with a finite forward return. Model: logistic regression [1, 5 shares] fitted by
deterministic IRLS from zero.

Causality: decision t uses c = last grid date < t and anchor a = (c // refit_every) *
refit_every <= c; training origins s satisfy s + h <= a. Only names with
``feature_ready`` at c and finite features in all three variants are scored. Feature
windows use integer cumulative counts (exact), and all fits use compacted valid rows.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.v5_ensemble import panel_grid, forward_return

MODEL_IDENTITY = 'V5_C_RANKFREQ_QUINTILE_LOGIT3_v1'
DEFAULT_CONFIG = dict(horizon=20, train_window=750, score_mode='p', agree_topn=22,
                      windows=(10, 20, 60), l2=1.0, refit_every=5, min_coverage=0.5,
                      min_names=10, min_train_rows=500, irls_max_iter=50, irls_tol=1e-10)
OUTPUT_COLUMNS = ['decision_date', 'symbol', 'score', 'p_topk', 'agreement']


def _row_pct_rank(values, min_names):
    ranks = pd.DataFrame(values).rank(axis=1, pct=True).to_numpy(float)
    ranks[np.isfinite(values).sum(axis=1) < min_names] = np.nan
    return ranks


def logistic_irls(x, y, l2=1., max_iter=50, tol=1e-10):
    """L2-penalized logistic regression; column 0 of x is the unpenalized intercept."""
    beta = np.zeros(x.shape[1])
    penalty = np.full(x.shape[1], float(l2))
    penalty[0] = 0.
    for _ in range(max_iter):
        eta = np.clip(x @ beta, -30, 30)
        p = 1. / (1. + np.exp(-eta))
        w = np.maximum(p * (1. - p), 1e-10)
        grad = x.T @ (y - p) - penalty * beta
        hess = x.T @ (x * w[:, None]) + np.diag(penalty)
        step = np.linalg.solve(hess, grad)
        beta = beta + step
        if np.max(np.abs(step)) < tol:
            break
    return beta


class RankModel:
    def __init__(self, panel, config):
        self.cfg = cfg = {**DEFAULT_CONFIG, **(config or {})}
        self.h = int(cfg['horizon'])
        self.dates, self.symbols, g, _ = panel_grid(
            panel, ['signal_price', 'signal_history_segment', 'signal_available',
                    'feature_ready', 'R1'])
        avail = (g['signal_available'] == 1) & np.isfinite(g['signal_price'])
        self.ready = g['feature_ready'] == 1
        r1 = np.where(avail, g['R1'], np.nan)
        pct = _row_pct_rank(r1, cfg['min_names'])
        bucket = np.where(np.isfinite(pct), np.clip(np.ceil(pct * 5), 1, 5), 0).astype(np.int64)
        onehot = np.stack([(bucket == q) for q in range(1, 6)], axis=2).astype(np.int64)
        cum = np.concatenate([np.zeros((1, *onehot.shape[1:]), np.int64), np.cumsum(onehot, axis=0)])
        self.features = []
        for w in cfg['windows']:
            counts = cum[w:] - cum[:-w]                       # rows w-1..n-1
            counts = np.concatenate([np.full((w - 1, *counts.shape[1:]), 0, np.int64), counts]) \
                if w > 1 else counts
            total = counts.sum(axis=2)
            ok = (total >= int(np.ceil(cfg['min_coverage'] * w))) & (bucket > 0)
            ok[:w - 1] = False
            with np.errstate(invalid='ignore', divide='ignore'):
                share = counts / total[:, :, None] - .2
            share[~ok] = np.nan
            self.features.append(share)
        fwd = forward_return(g['signal_price'], avail, g['signal_history_segment'], self.h)
        fpct = _row_pct_rank(fwd, cfg['min_names'])
        self.label = np.where(np.isfinite(fpct), (fpct > .8).astype(float), np.nan)
        self.n = len(self.dates)
        self._models = {}

    def _fit(self, a):
        if a in self._models:
            return self._models[a]
        cfg, h = self.cfg, self.h
        stop = a - h
        result = None
        if stop >= 0:
            origins = np.arange(max(0, stop - cfg['train_window'] + 1), stop + 1)
            betas, rows, label_end = [], [], -1
            for feat in self.features:
                x = feat[origins]
                y = self.label[origins]
                m = np.isfinite(x).all(axis=2) & np.isfinite(y)
                if m.sum() < cfg['min_train_rows']:
                    betas = None
                    break
                design = np.column_stack([np.ones(m.sum()), x[m]])
                betas.append(logistic_irls(design, y[m], cfg['l2'], cfg['irls_max_iter'], cfg['irls_tol']))
                rows.append(int(m.sum()))
                label_end = max(label_end, int(origins[m.any(axis=1)].max()) + h)
            if betas is not None:
                result = dict(betas=betas, rows=rows, label_end=label_end)
        self._models[a] = result
        return result

    def predict(self, decision_date):
        cfg = self.cfg
        c = int(self.dates.searchsorted(pd.Timestamp(decision_date), side='left')) - 1
        if c < 0:
            return None
        a = (c // cfg['refit_every']) * cfg['refit_every']
        model = self._fit(a)
        if model is None:
            return None
        x = [feat[c] for feat in self.features]
        ok = self.ready[c] & np.all([np.isfinite(v).all(axis=1) for v in x], axis=0)
        if not ok.any():
            return None
        probs = np.column_stack([
            1. / (1. + np.exp(-np.clip(np.column_stack([np.ones(ok.sum()), v[ok]]) @ b, -30, 30)))
            for v, b in zip(x, model['betas'])])
        agreement = np.zeros(ok.sum(), np.int64)
        topn = min(cfg['agree_topn'], ok.sum())
        for j in range(probs.shape[1]):
            order = np.argsort(-probs[:, j], kind='stable')
            agreement[order[:topn]] += 1
        p = probs.mean(axis=1)
        k = probs.shape[1]
        score = p if cfg['score_mode'] == 'p' else p * agreement / k
        frame = pd.DataFrame(dict(symbol=self.symbols[ok], score=score, p_topk=p,
                                  agreement=agreement))
        audit = dict(observed_date=self.dates[c], anchor_date=self.dates[a],
                     latest_label_end=self.dates[model['label_end']], train_rows=model['rows'])
        return frame, audit


def score_table(panel, decision_dates, config=None):
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    if cfg['score_mode'] not in ('p', 'p_agree'):
        raise ValueError("score_mode must be 'p' or 'p_agree'")
    cfg['windows'] = tuple(int(w) for w in cfg['windows'])
    dates = pd.DatetimeIndex(sorted(set(pd.to_datetime(list(decision_dates)))))
    model = RankModel(panel, cfg)
    frames, audit = [], []
    for d in dates:
        result = model.predict(d)
        if result is None:
            continue
        frame, info = result
        frame.insert(0, 'decision_date', d)
        frames.append(frame)
        audit.append(dict(decision_date=d, **info))
    out = (pd.concat(frames, ignore_index=True) if frames else
           pd.DataFrame({c: pd.Series(dtype='float64') for c in OUTPUT_COLUMNS}))
    out = out.loc[:, OUTPUT_COLUMNS].sort_values(['decision_date', 'symbol'], kind='stable').reset_index(drop=True)
    out.attrs.update(model_identity=MODEL_IDENTITY, config={k: cfg[k] for k in sorted(cfg)},
                     audit=audit)
    return out
