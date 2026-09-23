"""Replay-prefix/future-shock checks using actual fixed numeric feature parameters.

``verify_selected_config`` also supports the final selected config and supplied
real inputs; it narrows evaluation dates without changing any tuning parameter.
No runtime or study dependency is modified by these tests.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
import unittest
import numpy as np
import pandas as pd
from scripts.audit_double_check import audit_result
from src import double_check_tuning
from src.tuning_a_deep import NumericFeatureCache
from src.tuning_features import FeatureCache

ROOT = Path(__file__).resolve().parents[1]


def _context(daily, universe, bars, track):
    return dict(daily=daily, universe=universe, bars=bars, track=track,
                cache=NumericFeatureCache(FeatureCache(daily, bars)))


def _prefix(frame, column, cutoff):
    return frame[pd.to_datetime(frame[column]) <= pd.Timestamp(cutoff)].reset_index(drop=True)


def _compare_before(left, right, cutoff):
    compared = 0
    for name, column in (('orders', 'signal_date'), ('trades', 'date'), ('rejected_trades', 'date'),
                         ('signals', 'date'), ('compliance_daily', 'date'), ('plan_audit', 'date')):
        pd.testing.assert_frame_equal(_prefix(left[name], column, cutoff), _prefix(right[name], column, cutoff),
                                      check_exact=True)
        compared += len(_prefix(left[name], column, cutoff))
    # A deliberately shortened period realizes its performance-only dividend
    # credit sooner. Compare its pre-credit book without erasing entitlements.
    def settlement(result):
        frame = _prefix(result['equity'], 'date', cutoff).copy()
        # Use the independently audited pre-credit value: subtracting the
        # terminal credit back from a float sum can lose one rounding bit.
        frame['nav'] = _prefix(result['compliance_daily'], 'date', cutoff).settled_nav.to_numpy()
        frame['dividend_receivable'] += frame.terminal_dividend_credit
        frame['cash_ratio'] = frame.cash/frame.nav
        frame['terminal_dividend_credit'] = 0.
        return frame
    pd.testing.assert_frame_equal(settlement(left), settlement(right), check_exact=True)
    # Position weights at the truncated terminal date use performance NAV;
    # quantities, prices and identities must still agree exactly.
    pd.testing.assert_frame_equal(_prefix(left['holdings'], 'date', cutoff).drop(columns='weight'),
                                  _prefix(right['holdings'], 'date', cutoff).drop(columns='weight'), check_exact=True)
    return compared


def verify_selected_config(config, daily, universe, bars, *, start, cutoff, end):
    """Audit baseline, rebuilt prefix cache, and independent future shocks.

    Pass the final selected config verbatim; only evaluation start/end change.
    Daily prices, dividends, splits, volumes and 4H observations strictly after
    ``cutoff`` are perturbed. Earlier decisions and settlement must be identical.
    """
    config = copy.deepcopy(config)
    parameters = copy.deepcopy(config['full_tuning_params'])
    start, cutoff, end = map(pd.Timestamp, (start, cutoff, end))
    if not start <= cutoff < end:
        raise ValueError('Require start <= cutoff < end and a nonempty future window')
    expected_symbols = set(universe.symbol.astype(str))
    available = set(daily.loc[pd.to_datetime(daily.date) <= cutoff, 'symbol'].astype(str))
    if expected_symbols - available:
        raise ValueError('Prefix would remove all source history for fixed-universe symbols: '
                         + ','.join(sorted(expected_symbols - available)))
    if not ((pd.to_datetime(daily.date) > cutoff) & (pd.to_datetime(daily.date) <= end)).any():
        raise ValueError('Future-shock window contains no market observations')
    config.update(start=str(pd.Timestamp(start).date()), end=str(pd.Timestamp(end).date()))
    daily = daily[pd.to_datetime(daily.date) <= pd.Timestamp(end)].copy()
    bars = bars[pd.to_datetime(bars.date) <= pd.Timestamp(end)].copy()
    ctx = _context(daily, universe.copy(), bars, config['universe_mode'])
    full = double_check_tuning.run_model(ctx, config)
    audit_result(full, ctx)
    short_daily = daily[pd.to_datetime(daily.date) <= pd.Timestamp(cutoff)].copy()
    short_bars = bars[pd.to_datetime(bars.date) <= pd.Timestamp(cutoff)].copy()
    short_cfg = dict(config, end=str(pd.Timestamp(cutoff).date()))
    short_ctx = _context(short_daily, universe.copy(), short_bars, config['universe_mode'])
    short = double_check_tuning.run_model(short_ctx, short_cfg)
    audit_result(short, short_ctx)
    comparison_rows = _compare_before(full, short, cutoff)
    changed = daily.copy()
    future = pd.to_datetime(changed.date) > pd.Timestamp(cutoff)
    changed.loc[future, ['open', 'high', 'low', 'close', 'turnover']] *= 1.07
    changed.loc[future, 'volume'] *= 1.31
    if 'execution_volume' in changed:
        changed.loc[future, 'execution_volume'] *= 1.17
    first_future = pd.to_datetime(changed.loc[future, 'date']).min()
    actions = pd.to_datetime(changed.date).eq(first_future)
    changed.loc[actions, 'split'] = 1.1
    changed.loc[actions, 'dividend'] = .7
    changed_bars = bars.copy()
    after = pd.to_datetime(changed_bars.date) > pd.Timestamp(cutoff)
    changed_bars.loc[after, ['open', 'high', 'low', 'close']] *= .97
    changed_bars.loc[after, 'volume'] *= 1.23
    changed_ctx = _context(changed, universe.copy(), changed_bars, config['universe_mode'])
    shock = double_check_tuning.run_model(changed_ctx, config)
    audit_result(shock, changed_ctx)
    comparison_rows += _compare_before(full, shock, cutoff)
    # Complete source frames cached up front must also match a cache built from
    # only the available prefix, including all daily/4H causal indicators.
    cached_prefix = ctx['cache'].frame(config, short_daily, short_bars)
    rebuilt_prefix = short_ctx['cache'].frame(config, short_daily, short_bars)
    pd.testing.assert_frame_equal(cached_prefix, rebuilt_prefix, check_exact=True)
    assert config['full_tuning_params'] == parameters
    return dict(status='PASS', compared_rows=comparison_rows,
                prefix_feature_rows=len(cached_prefix), start=config['start'], cutoff=str(pd.Timestamp(cutoff).date()),
                end=config['end'], pre_cutoff_fills=len(_prefix(full['trades'], 'date', cutoff)),
                universe_symbols=len(expected_symbols), universe_unchanged=True,
                hyperparameters_unchanged=True, official_compliance='UNKNOWN_BLOCK_SUBMISSION')


def synthetic_panel():
    dates = pd.bdate_range('2024-01-02', periods=236)
    t = np.arange(len(dates), dtype=float)
    frames = []
    for i in range(32):
        price = (70+i) * np.exp((.0009+i*.000003)*t + .000001*t*t + .00002*np.sin(t/9+i/4))
        volume = 2_000_000 * np.exp(.012*t)
        frames.append(pd.DataFrame(dict(date=dates, symbol=f'{1000+i}.TW', open=price,
            high=price, low=price, close=price, volume=volume, execution_volume=volume,
            turnover=price*volume, split=1., dividend=0.)))
    daily = pd.concat(frames, ignore_index=True)
    # Include a real receivable before the shortened terminal credit.
    daily.loc[daily.date.eq(dates[222]), 'dividend'] = .05
    universe = pd.DataFrame(dict(symbol=sorted(daily.symbol.unique()), known_at='2024-01-01T00:00:00+08:00'))
    bars = daily[['date','symbol','open','high','low','close','volume']].copy()
    return daily, universe, bars, dates


class CausalReplayTests(unittest.TestCase):
    def test_truncation_cannot_silently_remove_fixed_universe_member(self):
        cfg = json.loads((ROOT/'outputs/full_tuned_v2/official_ex_post/final/full_tuned_v2/config.json').read_text())
        daily, universe, bars, dates = synthetic_panel()
        daily = daily[~(daily.symbol.eq('1031.TW') & daily.date.le(dates[226]))]
        with self.assertRaisesRegex(ValueError, '1031.TW'):
            verify_selected_config(cfg, daily, universe, bars,
                                   start=dates[215], cutoff=dates[226], end=dates[-1])

    def test_fixed_incumbent_parameters_survive_rebuilt_prefix_and_future_shock(self):
        # This pre-existing immutable incumbent provides actual search parameters;
        # use verify_selected_config explicitly on the eventual winner too.
        cfg = json.loads((ROOT/'outputs/full_tuned_v2/official_ex_post/final/full_tuned_v2/config.json').read_text())
        daily, universe, bars, dates = synthetic_panel()
        result = verify_selected_config(cfg, daily, universe, bars,
                                        start=dates[215], cutoff=dates[226], end=dates[-1])
        self.assertEqual(result['status'], 'PASS')
        self.assertGreater(result['pre_cutoff_fills'], 0)
        self.assertGreater(result['prefix_feature_rows'], 7000)


if __name__ == '__main__':
    unittest.main()
