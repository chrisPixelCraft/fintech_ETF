"""A-only numeric indicator tuning over the frozen second-round execution model.

This adapter does not alter a frozen source file or the competition constraints.
The entire observed history remains development data. Formal submission blocks.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src import tuning_2nd as second
from src.tuning_features import normalized_config

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ('target_count', 'replacement_margin', 'max_replacements_per_day',
          'volatility_spike_ratio', 'one_day_chase_return', 'volume_low', 'volume_high',
          'four_hour_mode', 'return_short', 'return_long', 'ema_fast', 'ema_slow',
          'macd_fast', 'macd_slow', 'macd_signal', 'momentum_weight', 'long_return_fraction')
FIXED = dict(min_count=20, max_count=30, cash_target=0, max_weight=.1,
             tsmc_max_weight=.25, commission=.001425, sell_tax=.003,
             lot_size=1000, price_buffer=1.1, price_lower_buffer=.9,
             cash_guard_ratio=.12, cash_guard_headroom=.02, warmup_sessions=200,
             execution='vwap', dividend_cash_policy='end_of_period',
             allocation_mode='local', research_shadow=True)


def validate_params(params):
    if set(params) != set(FIELDS):
        raise ValueError('Unexpected or missing tunable parameters')
    p = params
    integer = ['target_count', 'max_replacements_per_day', 'return_short', 'return_long',
               'ema_fast', 'ema_slow', 'macd_fast', 'macd_slow', 'macd_signal']
    for key in integer:
        if isinstance(p[key], bool) or not isinstance(p[key], (int, np.integer)):
            raise ValueError('Expected integer: ' + key)
    if not 20 <= p['target_count'] <= 30 or not 0 <= p['max_replacements_per_day'] <= 12:
        raise ValueError('Invalid holdings/replacement target')
    for fast, slow in [('return_short', 'return_long'), ('ema_fast', 'ema_slow'), ('macd_fast', 'macd_slow')]:
        if not 1 <= p[fast] < p[slow] <= 200:
            raise ValueError('Invalid causal indicator window')
    if not 1 <= p['macd_signal'] <= 50 or p['four_hour_mode'] not in ('strict', 'coverage_only'):
        raise ValueError('Invalid MACD or 4H setting')
    for key in set(FIELDS) - set(integer) - {'four_hour_mode'}:
        if not np.isfinite(float(p[key])):
            raise ValueError('Nonfinite parameter')
    if not 0 <= p['replacement_margin'] <= 1 or not 1 <= p['volatility_spike_ratio'] <= 10:
        raise ValueError('Invalid risk/ranking threshold')
    if not 0 < p['one_day_chase_return'] <= .1 or not 0 < p['volume_low'] < p['volume_high']:
        raise ValueError('Invalid price/volume gate')
    if not 0 <= p['momentum_weight'] <= 1 or not 0 <= p['long_return_fraction'] <= 1:
        raise ValueError('Invalid score mixture')


def from_old(config):
    from src.tuning_features import feature_spec
    spec = feature_spec(config)
    p = {k: config[k] for k in FIELDS[:8]}
    p.update(zip(('return_short', 'return_long'), spec['return_lookbacks']))
    p.update(zip(('ema_fast', 'ema_slow'), spec['ema_spans']))
    p.update(zip(('macd_fast', 'macd_slow', 'macd_signal'), spec['macd_spans']))
    w = config['score_weights']
    p['momentum_weight'] = round(w['return20'] + w['return50'], 12)
    p['long_return_fraction'] = w['return50'] / p['momentum_weight']
    validate_params(p)
    return p


def config_for(ctx, trial):
    p = copy.deepcopy(trial['params'])
    validate_params(p)
    c = copy.deepcopy(ctx['base'])
    c.update(FIXED)
    c.update({key: p[key] for key in FIELDS[:8]})
    c.update(strategy_id='A_deep_' + trial['candidate_id'], tuning_candidate_id=trial['candidate_id'],
             tuning_params=p, deep_feature_params=p, feature_presets=dict(returns='base', ema='base', macd='base'),
             universe_mode=ctx['track'], ex_post_fixed_universe=ctx['track'] == 'official_ex_post')
    m, q = p['momentum_weight'], p['long_return_fraction']
    weights = [m * (1-q), m*q, (1-m)*.4, (1-m)*.2, (1-m)*.2, (1-m)*.2]
    c['score_weights'] = dict(zip(('return20', 'return50', 'volume', 'macd', 'trend', 'long_trend'),
                                [round(w, 12) for w in weights]))
    c = normalized_config(c)
    c['feature_spec'].update(return_lookbacks=[p['return_short'], p['return_long']],
                             ema_spans=[p['ema_fast'], p['ema_slow']],
                             macd_spans=[p['macd_fast'], p['macd_slow'], p['macd_signal']],
                             presets={'returns': 'explicit_numeric', 'ema': 'explicit_numeric', 'macd': 'explicit_numeric'})
    c['study_policy'] = dict(parameter_search=True, historical_period_is_development=True,
        selection_scope='EX_POST_DEVELOPMENT', official_live_submission='BLOCK_IF_UNKNOWN',
        selection='ZERO_MEASURED_HARD_THEN_NET_RETURN_THEN_MDD',
        risk_controlled_alternative='ZERO_MEASURED_HARD_AND_MDD_NOT_ABOVE_INCUMBENT_THEN_RETURN')
    return c


class NumericFeatureCache:
    """Share frozen source checks; cache only backward-looking numeric indicators."""
    def __init__(self, source):
        self.source = source
        self.values = {}

    def vector(self, kind, windows):
        key = (kind, *windows)
        if key not in self.values:
            out = np.full(len(self.source._baseline), np.nan)
            for _, rows in self.source._baseline.groupby('symbol', sort=True):
                price = rows.signal_price.reset_index(drop=True)
                if kind == 'return':
                    v = price.pct_change(windows[0], fill_method=None)
                elif kind == 'ema':
                    v = price.ewm(span=windows[0], adjust=False, min_periods=windows[0]).mean()
                else:
                    fast, slow, signal = windows
                    line = price.ewm(span=fast, adjust=False).mean() - price.ewm(span=slow, adjust=False).mean()
                    v = line - line.ewm(span=signal, adjust=False).mean()
                out[rows.index.to_numpy()] = v.to_numpy()
            out.flags.writeable = False
            self.values[key] = out
        return self.values[key]

    def prewarm(self, trials):
        for trial in trials:
            p = trial['params']
            for kind, keys in [('return', ('return_short', 'return_long')), ('ema', ('ema_fast', 'ema_slow'))]:
                for key in keys:
                    self.vector(kind, (p[key],))
            self.vector('macd', (p['macd_fast'], p['macd_slow'], p['macd_signal']))

    def frame(self, config, daily=None, four_hour=None):
        p = config['deep_feature_params']
        validate_params(p)
        pos = self.source._positions(daily, four_hour)
        frame = self.source._baseline.iloc[pos].copy(deep=True)
        for column, kind, key in [('return20', 'return', 'return_short'), ('return50', 'return', 'return_long'),
                                  ('ema20', 'ema', 'ema_fast'), ('ema50', 'ema', 'ema_slow')]:
            frame[column] = self.vector(kind, (p[key],))[pos]
        frame['macd_hist'] = self.vector('macd', (p['macd_fast'], p['macd_slow'], p['macd_signal']))[pos]
        frame['trend'] = ((frame.signal_price > frame.ema20) & (frame.ema20 > frame.ema50)).astype(float)
        frame['ready'] = self.source._ordinal[pos] >= config['warmup_sessions']
        return frame.reset_index(drop=True)


def context(track):
    ctx = second.context(track)
    ctx['cache'] = NumericFeatureCache(ctx['cache'])
    return ctx


def run_model(ctx, config):
    for key, value in FIXED.items():
        if config.get(key) != value:
            raise ValueError('Frozen execution/competition setting changed: ' + key)
    if config['universe_mode'] != ctx['track']:
        raise ValueError('Universe context mismatch')
    engine = second.adapter(ctx)
    result = engine.module.run_v2(ctx['daily'], ctx['universe'], copy.deepcopy(config), ctx['bars'])
    if len(result['holdings'].query("symbol == '2888.TW' and date >= '2025-07-24'")):
        raise ValueError('Unsupported multi-security merger held')
    eq = result['equity']
    if not np.isfinite(eq.economic_nav.astype(float)).all():
        raise ValueError('Nonfinite accounting')
    result['metrics'].update(second.metrics(eq))
    result['metrics'].update(trade_count=len(result['trades']), universe_track=ctx['track'],
        planner='COMPLIANCE_GUARD_V2', candidate_id=config['tuning_candidate_id'],
        official_compliance='UNKNOWN_BLOCK_SUBMISSION')
    result['snapshots']['universe_mode'] = ctx['track']
    return result


def choose(rows, max_mdd=None):
    eligible = [r for r in rows if r.get('status') == 'COMPLETE' and r['measured_hard_breach_days'] == 0
                and (max_mdd is None or r['economic_max_drawdown'] <= max_mdd + 1e-12)]
    return min(eligible, key=lambda r: (-r['economic_total_return'], r['economic_max_drawdown'], r['candidate_id'])) if eligible else None
