"""V5 portfolio construction (spec §3.3, §6 S2).

``construct`` turns one decision date's score cross-section into target weights
before planning. It never sees execution-day data: ``scores_t`` is dated at the
decision date but was produced from rows dated strictly before it, ``rows_prev``
is the D-1 cross-section, and the regime overlay reads only D-1 aggregates.

Constraints enforced here: long only, 20–30 names, normal cap 10%, 2330 cap 25%,
cash target in [0, 25%). Board-lot rounding and cash repair belong to the planner.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

DEFAULTS = dict(
    method='topn_equal',          # topn_equal | score_bounded | risk_aware
    n=22,
    rotation='buffer_2N',         # never | buffer_2N
    cash_target=0.05,
    min_names=20,
    max_names=30,
    max_weight=0.10,
    tsmc_max_weight=0.25,
    cap_headroom=0.01,            # construction caps 9% / 24%: execution-day drift must not breach 10% / 25%
    risk_lambda=1.0,              # spec §6: λ fixed at 1
    variance_horizon=20,          # vol20 is daily; scale variance to the forecast horizon
    min_weight=0.005,             # risk_aware floor, so the optimizer keeps every selected name
    regime=False,
    risk_off_cash=0.10,
)
METHODS = ('topn_equal', 'score_bounded', 'risk_aware')
ROTATIONS = ('never', 'buffer_2N')
REGIME_DEFAULTS = dict(breadth_threshold=0.40, vol_pct_threshold=0.70,
                       vol_window=20, pct_window=250, min_history=60)


def settings(config):
    c = dict(DEFAULTS)
    c.update(config or {})
    if c['method'] not in METHODS:
        raise ValueError('Unknown construction method: ' + str(c['method']))
    if c['rotation'] not in ROTATIONS:
        raise ValueError('Unknown rotation rule: ' + str(c['rotation']))
    n = int(c['n'])
    if not int(c['min_names']) <= n <= int(c['max_names']):
        raise ValueError('N outside the 20–30 holding range')
    if not 0 <= float(c['cash_target']) < 0.25 or not 0 <= float(c['risk_off_cash']) < 0.25:
        raise ValueError('Cash target outside [0, 25%)')
    c['n'] = n
    return c


def weight_cap(symbol, c):
    """Construction cap = rule cap − headroom (the rule caps themselves are enforced by planner and ledger)."""
    rule = float(c['tsmc_max_weight']) if str(symbol).split('.')[0] == '2330' else float(c['max_weight'])
    return rule - float(c.get('cap_headroom', 0.))


def _as_frame(scores_t):
    frame = scores_t.copy()
    if 'symbol' in frame.columns:
        frame = frame.set_index('symbol', drop=False)
    frame.index = frame.index.astype(str)
    if frame.index.duplicated().any():
        raise ValueError('Duplicate symbols in score cross-section')
    return frame


def _shares(holdings):
    if holdings is None:
        return {}
    items = holdings.items() if hasattr(holdings, 'items') else dict(holdings).items()
    return {str(s): float(q) for s, q in items if float(q) > 1e-9}


def rank_order(scores):
    """Descending score, symbol ascending on ties: fully deterministic."""
    frame = pd.DataFrame(dict(score=scores.astype(float).to_numpy(), symbol=scores.index.astype(str)))
    frame = frame.sort_values(['score', 'symbol'], ascending=[False, True], kind='mergesort')
    return list(frame.symbol)


def eligible_scores(scores_t, rows_prev, portfolio_state):
    """Holdable names: D-1 feature_ready and a finite positive D-1 price.

    A holdable name may have a missing score (e.g. the momentum trend filter);
    it can then be kept by a no-rotation rule but never newly entered.
    """
    frame = _as_frame(scores_t)
    rows = rows_prev.set_index('symbol', drop=False) if 'symbol' in rows_prev.columns else rows_prev
    rows.index = rows.index.astype(str)
    ready = rows['feature_ready'].astype(bool) if 'feature_ready' in rows else pd.Series(True, index=rows.index)
    prices = (portfolio_state or {}).get('previous_close')
    if prices is None:
        prices = rows['close'] if 'close' in rows else pd.Series(np.nan, index=rows.index)
    prices = pd.Series(prices, dtype=float)
    prices.index = prices.index.astype(str)
    held = _shares((portfolio_state or {}).get('holdings'))
    names = sorted(set(frame.index) | set(held))
    frame = frame.reindex(names)
    frame['symbol'] = frame.index
    frame['score'] = pd.to_numeric(frame['score'], errors='coerce')
    ok = [s for s in names if bool(ready.get(s, False))
          and math.isfinite(prices.get(s, np.nan)) and prices.get(s, np.nan) > 0]
    return frame.loc[ok]


def select_names(ranked, held, c, rotate=True, holdable=None):
    """Apply the rotation rule to a ranked (finite-score) eligible list.

    ``held``: names currently held. A held name that is no longer holdable
    (feature_ready / price lost) is a forced drop. ``never`` (and the risk_off
    budget ``rotate=False``) keeps every holdable holding, scored or not;
    ``buffer_2N`` keeps a holding only while its rank is <= 2N. Free slots are
    filled with the best-ranked names not already chosen.
    """
    n = c['n']
    position = {s: i + 1 for i, s in enumerate(ranked)}
    holdable = set(position) if holdable is None else set(holdable) | set(position)
    order = lambda s: (position.get(s, math.inf), s)
    held_ok = [s for s in held if s in holdable]
    forced = sorted(set(held) - set(held_ok))
    if not held:
        keep = []
    elif c['rotation'] == 'never' or not rotate:
        keep = sorted(held_ok, key=order)[:int(c['max_names'])]
    else:
        keep = sorted([s for s in held_ok if position.get(s, math.inf) <= 2 * n], key=order)[:n]
    voluntary = sorted(set(held_ok) - set(keep))
    chosen = list(keep)
    for s in ranked:
        if len(chosen) >= n:
            break
        if s not in chosen:
            chosen.append(s)
    added = [s for s in chosen if s not in held]
    chosen = sorted(chosen, key=order)
    return chosen, dict(kept=sorted(keep), added=sorted(added), forced_drops=forced,
                        voluntary_drops=voluntary, unscored_kept=sorted(s for s in keep if s not in position))


def cap_fill(preference, budget, caps):
    """Proportional allocation of ``budget`` with per-name caps (water-filling)."""
    p = pd.Series(preference, dtype=float).clip(lower=0)
    caps = pd.Series(caps, dtype=float).reindex(p.index)
    if caps.sum() < budget - 1e-12:
        raise ValueError('Caps cannot hold the invested budget')
    w = pd.Series(0., index=p.index)
    free = pd.Series(True, index=p.index)
    remaining = budget
    for _ in range(len(p) + 1):
        mass = p[free].sum()
        if mass <= 0:
            share = pd.Series(remaining / free.sum(), index=p.index[free])
        else:
            share = remaining * p[free] / mass
        over = share > caps[free] + 1e-15
        if not over.any():
            w[free] = share
            break
        hit = share.index[over]
        w[hit] = caps[hit]
        free[hit] = False
        remaining = budget - w[~free].sum()
    return w


def risk_aware_weights(mu, variance, budget, caps, floor, lam):
    """argmax mu·w − λ Σ var_i w_i², Σw = budget, floor ≤ w ≤ cap (separable KKT)."""
    mu = np.asarray(mu, float)
    var = np.asarray(variance, float)
    caps = np.asarray(caps, float)
    lo = np.minimum(floor, caps)
    if lo.sum() > budget + 1e-12 or caps.sum() < budget - 1e-12:
        raise ValueError('Risk-aware box constraints infeasible')

    def weights(nu):
        return np.clip((mu - nu) / (2 * lam * var), lo, caps)
    low, high = mu.min() - 2 * lam * (var * caps).max() - 1, mu.max() + 1
    for _ in range(200):
        mid = (low + high) / 2
        if weights(mid).sum() > budget:
            low = mid
        else:
            high = mid
    w = weights((low + high) / 2)
    # Remove bisection residue exactly on the free coordinates.
    free = (w > lo + 1e-12) & (w < caps - 1e-12)
    gap = budget - w.sum()
    if free.any():
        w[free] += gap / free.sum()
    else:
        w *= budget / w.sum()
    return w


def construct(scores_t, rows_prev, portfolio_state, config):
    """Target weights by symbol (sum = 1 − cash target) for one decision date.

    Returns an empty Series with attrs['reason'] starting 'INFEASIBLE' when fewer
    than ``min_names`` names are eligible.
    """
    c = settings(config)
    state = portfolio_state or {}
    regime = state.get('regime') or {}
    risk_off = bool(c['regime'] and regime.get('risk_off', False))
    cash = max(float(c['cash_target']), float(c['risk_off_cash'])) if risk_off else float(c['cash_target'])
    budget = 1. - cash
    eligible = eligible_scores(scores_t, rows_prev, state)
    scored = eligible['score'].dropna()
    ranked = rank_order(scored)
    held = sorted(_shares(state.get('holdings')))
    attrs = dict(method=c['method'], rotation=c['rotation'], n=c['n'], cash_target=cash,
                 regime='risk_off' if risk_off else ('risk_on' if c['regime'] else 'disabled'),
                 eligible=len(ranked))
    chosen, rotation = select_names(ranked, held, c, rotate=not risk_off, holdable=eligible.index)
    if len(chosen) < int(c['min_names']):
        out = pd.Series(dtype=float, name='target_weight')
        out.attrs.update(attrs, reason='INFEASIBLE_TOO_FEW_ELIGIBLE')
        return out
    caps = pd.Series({s: weight_cap(s, c) for s in chosen})
    if c['method'] == 'topn_equal':
        w = cap_fill(pd.Series(1., index=chosen), budget, caps)
    elif c['method'] == 'score_bounded':
        k = len(chosen)
        w = cap_fill(pd.Series(np.arange(k, 0, -1, dtype=float), index=chosen), budget, caps)
    else:
        if 'expected_return' in eligible and np.isfinite(pd.to_numeric(eligible.loc[chosen, 'expected_return'], errors='coerce').to_numpy(float)).all():
            mu = pd.to_numeric(eligible.loc[chosen, 'expected_return']).to_numpy(float)
            mu_source = 'expected_return'
        else:
            pct = pd.Series(np.linspace(1., 0., len(ranked)) if len(ranked) > 1 else [1.], index=ranked)
            mu = (0.1 * (pct - 0.5)).reindex(chosen).fillna(-0.05).to_numpy(float)
            mu_source = 'rank_scaled_score'
        rows = rows_prev.set_index('symbol', drop=False) if 'symbol' in rows_prev.columns else rows_prev
        rows.index = rows.index.astype(str)
        vol = pd.to_numeric(rows['vol20'], errors='coerce') if 'vol20' in rows else pd.Series(dtype=float)
        vol = vol.reindex(chosen)
        fallback = float(np.nanmedian(vol)) if np.isfinite(vol).any() else 0.02
        vol = vol.where(np.isfinite(vol), fallback).clip(lower=1e-3)
        variance = (vol ** 2 * float(c['variance_horizon'])).to_numpy(float)
        w = pd.Series(risk_aware_weights(mu, variance, budget, caps.to_numpy(float),
                                         float(c['min_weight']), float(c['risk_lambda'])), index=chosen)
        attrs['mu_source'] = mu_source
    w = w.astype(float).rename('target_weight')
    w.index.name = 'symbol'
    if not (w >= 0).all() or (w > caps.reindex(w.index) + 1e-12).any() or abs(w.sum() - budget) > 1e-9:
        raise AssertionError('Construction violated its own constraints')
    w.attrs.update(attrs, reason='TARGET_' + c['method'].upper(), **rotation)
    return w


def regime_table(panel, config=None):
    """Per-date market state computed from rows on or before that date only.

    breadth: share of feature_ready names with price_ema20 > 0.
    market_vol: 20-session std of the equal-weight mean R1 of feature_ready names.
    vol_pct: percentile of today's market_vol within its trailing 250 values.
    risk_off: breadth < threshold and vol_pct ≥ threshold (both, predeclared).
    Use ``regime_at`` to read the last row strictly before a decision date.
    """
    r = dict(REGIME_DEFAULTS)
    r.update(config or {})
    rows = panel.loc[panel.feature_ready.astype(bool)]
    dates = pd.DatetimeIndex(sorted(pd.to_datetime(panel.date).unique()))
    grouped = rows.groupby(pd.to_datetime(rows.date))
    breadth = grouped.price_ema20.apply(lambda x: float((x > 0).mean()) if len(x) else np.nan).reindex(dates)
    market = grouped.R1.mean().reindex(dates)
    vol = market.rolling(int(r['vol_window']), min_periods=int(r['vol_window'])).std()

    def pct(x):
        x = x[np.isfinite(x)]
        return float((x <= x[-1]).mean()) if len(x) >= int(r['min_history']) else np.nan
    vol_pct = vol.rolling(int(r['pct_window']), min_periods=1).apply(pct, raw=True)
    table = pd.DataFrame(dict(breadth=breadth, market_vol=vol, vol_pct=vol_pct), index=dates)
    table['risk_off'] = (table.breadth < float(r['breadth_threshold'])) & (table.vol_pct >= float(r['vol_pct_threshold']))
    table.index.name = 'date'
    return table


def regime_at(table, decision_date):
    """Regime observed on the last session strictly before ``decision_date``."""
    past = table.loc[table.index < pd.Timestamp(decision_date)]
    if past.empty:
        return dict(observed_date=None, breadth=None, market_vol=None, vol_pct=None, risk_off=False)
    row = past.iloc[-1]
    clean = lambda v: None if not np.isfinite(v) else float(v)
    return dict(observed_date=str(past.index[-1].date()), breadth=clean(row.breadth),
                market_vol=clean(row.market_vol), vol_pct=clean(row.vol_pct), risk_off=bool(row.risk_off))
