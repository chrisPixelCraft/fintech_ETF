"""Focused v2 ledger/causality tests, independent of downloaded market data."""
import copy
import json
from pathlib import Path
import unittest
import tempfile

import numpy as np
import pandas as pd

from src.backtest_v2 import _settings, make_plan_v2, run_buy_hold_v2, run_v2
from src.backtest import save_result
from scripts.audit_v2_study import audit_model


def settings(**kwargs):
    c = json.loads((Path(__file__).resolve().parents[1] / 'config/strategy_v1.json').read_text())
    c.update(start='2025-01-02', end='2025-01-08', initial_cash=10_000_000.,
             target_count=10, min_count=8, max_count=12, max_weight=.2,
             cash_target=0., use_4h=False, research_shadow=True)
    c.update(kwargs)
    return c


def fixture(symbols=12):
    dates = pd.to_datetime(['2024-12-31', '2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07', '2025-01-08'])
    frames = []
    for i in range(symbols):
        p = 100. + i
        frames.append(pd.DataFrame(dict(date=dates, symbol=f'{1000+i}.TW', open=p, high=p,
                                        low=p, close=p, volume=1_000_000., turnover=p*2_000_000.,
                                        execution_volume=2_000_000., split=1., dividend=0.)))
    data = pd.concat(frames, ignore_index=True)
    universe = pd.DataFrame(dict(symbol=sorted(data.symbol.unique()), known_at='2024-12-31T19:30:00+08:00'))
    return data, universe


def constant_signals(day, ranked):
    ranked['score'] = [1 - int(s.split('.')[0]) / 10000 for s in ranked.symbol]
    ranked['entry_ok'] = True
    ranked['exit'] = False
    return ranked, {'state': 'test', 'signal_day': str(day.date())}


class V2LedgerTests(unittest.TestCase):
    def test_local_retained_shares_do_not_rebalance(self):
        daily, u = fixture()
        result = run_v2(daily, u, settings(), signal_transform=constant_signals)
        self.assertEqual(len(result['trades']), 10)
        self.assertEqual(result['trades'].date.nunique(), 1)
        self.assertTrue((result['equity'].cash >= 0).all())
        self.assertTrue(result['equity'].violations.eq('').all())
        for _, rows in result['holdings'].groupby('symbol'):
            self.assertEqual(rows.shares.nunique(), 1)
        self.assertTrue(result['snapshots'].submission_status.eq('BLOCK_SUBMISSION').all())

    def test_official_volume_pair_not_signal_volume(self):
        daily, u = fixture()
        result = run_v2(daily, u, settings(), signal_transform=constant_signals)
        first = result['trades'].iloc[0]
        self.assertAlmostEqual(first.price, 100.)
        self.assertAlmostEqual(first.volume_participation, first.shares / 2_000_000.)

    def test_missing_official_vwap_never_substitutes_open(self):
        daily, u = fixture()
        daily.loc[(daily.date == '2025-01-02') & (daily.symbol == '1000.TW'), 'turnover'] = np.nan
        result = run_v2(daily, u, settings(), signal_transform=constant_signals)
        self.assertFalse(((result['trades'].date == '2025-01-02') & (result['trades'].symbol == '1000.TW')).any())
        self.assertTrue(result['warnings'].issue.eq('UNFILLED_MISSING_OFFICIAL_VWAP').any())

    def test_fixed_quantity_and_prefix_invariance(self):
        daily, u = fixture()
        a = run_v2(daily, u, settings(), signal_transform=constant_signals)
        shock = daily.copy()
        shock.loc[shock.date >= '2025-01-06', ['open', 'high', 'low', 'close', 'turnover']] *= 1.05
        b = run_v2(shock, u, settings(), signal_transform=constant_signals)
        pd.testing.assert_frame_equal(a['orders'][a['orders'].signal_date <= '2025-01-03'].reset_index(drop=True),
                                      b['orders'][b['orders'].signal_date <= '2025-01-03'].reset_index(drop=True))
        short = run_v2(daily[daily.date <= '2025-01-06'], u, settings(end='2025-01-06'), signal_transform=constant_signals)
        for key, col in [('orders', 'signal_date'), ('signals', 'date'), ('trades', 'date'), ('holdings', 'date')]:
            pd.testing.assert_frame_equal(a[key][a[key][col] <= '2025-01-06'].reset_index(drop=True), short[key].reset_index(drop=True))
        self.assertAlmostEqual(float(a['equity'].query("date == '2025-01-06'").economic_nav.iloc[0]), float(short['equity'].economic_nav.iloc[-1]))

    def test_execution_price_change_does_not_change_initial_quantities(self):
        daily, u = fixture()
        a = run_v2(daily, u, settings(), signal_transform=constant_signals)
        changed = daily.copy()
        changed.loc[changed.date == '2025-01-02', 'turnover'] *= 1.05
        b = run_v2(changed, u, settings(), signal_transform=constant_signals)
        pd.testing.assert_series_equal(a['trades'].query("date == '2025-01-02'").shares.reset_index(drop=True),
                                       b['trades'].query("date == '2025-01-02'").shares.reset_index(drop=True))

    def test_sell_settles_before_next_session_buy(self):
        daily, u = fixture()
        def rotated(day, ranked):
            ranked, meta = constant_signals(day, ranked)
            if day >= pd.Timestamp('2025-01-02'):
                ranked.loc['1011.TW', 'score'] = 2.
            return ranked, meta
        result = run_v2(daily, u, settings(max_replacements_per_day=1), signal_transform=rotated)
        later = result['trades'].query("date > '2025-01-02'")
        self.assertEqual(list(later.date), ['2025-01-03', '2025-01-06'])
        self.assertLess(later.iloc[0].shares, 0)
        self.assertGreater(later.iloc[1].shares, 0)
        self.assertEqual(later.iloc[1].symbol, '1011.TW')
        for date, group in later.groupby('date'):
            self.assertFalse((group.shares < 0).any() and (group.shares > 0).any())
        self.assertTrue(result['equity'].violations.eq('').all())

    def test_full_control_completes_same_two_stage_rotation(self):
        daily, u = fixture()
        def rotated(day, ranked):
            ranked, meta = constant_signals(day, ranked)
            if day >= pd.Timestamp('2025-01-02'):
                ranked.loc['1011.TW', 'score'] = 2.
            return ranked, meta
        result = run_v2(daily, u, settings(allocation_mode='full', max_replacements_per_day=1), signal_transform=rotated)
        bought = result['trades'].query("symbol == '1011.TW'")
        self.assertEqual(bought.date.tolist(), ['2025-01-06'])
        self.assertGreater(bought.shares.iloc[0] * bought.price.iloc[0] / 10_000_000., .08)
        self.assertTrue(result['equity'].violations.eq('').all())

    def test_cap_fix_only_touches_breaching_position_and_keeps_odd_shares(self):
        names = [f'{1000+i}.TW' for i in range(10)]
        ranked = pd.DataFrame(dict(symbol=names, close=[300.] + [100.] * 9,
                                    score=np.linspace(1, .1, 10), entry_ok=True, exit=False))
        holdings = {s: 9000. for s in names}
        holdings[names[0]] = 9500.
        nav = 1_000_000. + sum(holdings[s] * p for s, p in zip(names, ranked.close))
        orders, reason, selected = make_plan_v2(ranked, holdings, 1_000_000., nav, _settings(settings()))
        self.assertEqual(orders, {'1000.TW': -3000})
        self.assertEqual((holdings['1000.TW'] + orders['1000.TW']) % 1000, 500)
        self.assertEqual(reason, 'SELL_THEN_WAIT_SETTLEMENT')

    def test_unexpected_price_bound_breach_cannot_be_hidden_by_resizing(self):
        daily, u = fixture()
        daily.loc[daily.date == '2025-01-02', 'turnover'] *= 1.3
        result = run_v2(daily, u, settings(), signal_transform=constant_signals)
        self.assertGreater(result['metrics']['negative_cash_days'], 0)
        self.assertEqual(result['metrics']['accounting_status'], 'INVALID_NEGATIVE_CASH')
        self.assertGreater(result['metrics']['execution_price_bound_breaches'], 0)
        self.assertTrue(result['warnings'].issue.eq('EXECUTION_OUTSIDE_PREDECLARED_PRICE_BOUND').any())

    def test_simultaneous_cash_distribution_and_split_use_old_shares(self):
        daily, _ = fixture(1)
        daily['symbol'] = '0050.TW'
        mask = daily.date >= '2025-01-03'
        daily.loc[mask, ['open', 'high', 'low', 'close']] = (100. - 10.) / 1.1
        daily.loc[mask, 'turnover'] = (100. - 10.) / 1.1 * 2_000_000.
        daily.loc[daily.date == '2025-01-03', ['split', 'dividend']] = [1.1, 10.]
        result = run_buy_hold_v2(daily, settings())
        q = result['holdings'].query("date == '2025-01-02'").shares.iloc[0]
        self.assertAlmostEqual(result['holdings'].query("date == '2025-01-03'").shares.iloc[0], q * 1.1)
        self.assertAlmostEqual(result['equity'].query("date == '2025-01-03'").dividend_receivable.iloc[0], q * 10.)
        np.testing.assert_allclose(result['equity'].economic_nav, result['equity'].economic_nav.iloc[0])
        self.assertAlmostEqual(result['equity'].nav.iloc[-1], result['equity'].economic_nav.iloc[-1])
        self.assertEqual(result['equity'].dividend_receivable.iloc[-1], 0)
        self.assertEqual(len(result['trades']), 1)

    def test_benchmark_keeps_calendar_during_suspension(self):
        daily, _ = fixture(2)
        daily.loc[daily.symbol == '1000.TW', 'symbol'] = '0050.TW'
        daily = daily[~((daily.symbol == '0050.TW') & daily.date.isin(pd.to_datetime(['2025-01-03', '2025-01-06'])))]
        result = run_buy_hold_v2(daily, settings())
        self.assertEqual(len(result['equity']), 5)
        self.assertEqual(result['metrics']['stale_held_price_days'], 2)

    def test_future_universe_and_implicit_live_mode_rejected(self):
        daily, u = fixture()
        with self.assertRaisesRegex(ValueError, 'LOOKAHEAD_UNIVERSE'):
            run_v2(daily, u.assign(known_at='2025-01-02T08:55:01+08:00'), settings(), signal_transform=constant_signals)
        with self.assertRaisesRegex(ValueError, 'BLOCK_SUBMISSION'):
            run_v2(daily, u, settings(research_shadow=False))

    def test_transform_cannot_change_price_or_invent_members(self):
        daily, u = fixture()
        def bad(day, ranked):
            ranked['close'] *= 2
            return ranked, {}
        with self.assertRaises(AssertionError):
            run_v2(daily, u, settings(), signal_transform=bad)

    def test_snapshot_cutoff_is_next_session_0855_not_prior_close(self):
        daily, u = fixture()
        result = run_v2(daily, u, settings(end='2025-01-03'), signal_transform=constant_signals)
        first = result['snapshots'].iloc[0]
        self.assertEqual(first.date, '2024-12-31')
        self.assertEqual(first.decision_date, '2025-01-02')
        self.assertEqual(pd.Timestamp(first.cutoff_at), pd.Timestamp('2025-01-02T08:55:00+08:00'))
        self.assertEqual(result['snapshots'].iloc[-1].decision_date, '2025-01-06')

    def test_many_exits_are_shrunk_to_feasible_count_and_cash(self):
        daily, u = fixture(10)
        def exits(day, ranked):
            ranked, meta = constant_signals(day, ranked)
            if day >= pd.Timestamp('2025-01-02'):
                ranked.loc[['1000.TW', '1001.TW', '1002.TW'], 'exit'] = True
            return ranked, meta
        result = run_v2(daily, u, settings(), signal_transform=exits)
        self.assertGreater(len(result['trades']), 10)
        self.assertTrue(result['equity'].holdings.ge(8).all())
        self.assertTrue(result['equity'].cash_ratio.lt(.25).all())
        self.assertTrue(result['equity'].violations.eq('').all())

    def test_25_mass_exits_progress_in_sell_buy_batches(self):
        daily, u = fixture(50)
        original = {f'{1000+i}.TW' for i in range(25)}
        def exits(day, ranked):
            ranked, meta = constant_signals(day, ranked)
            if day >= pd.Timestamp('2025-01-02'):
                ranked.loc[ranked.symbol.isin(original), 'exit'] = True
                ranked.loc[ranked.symbol.isin(original), 'entry_ok'] = False
            return ranked, meta
        c = settings(initial_cash=1e9, target_count=25, min_count=20, max_count=30, max_weight=.1)
        for mode in ['local', 'full']:
            result = run_v2(daily, u, {**c, 'allocation_mode': mode}, signal_transform=exits)
            later = result['trades'].query("date > '2025-01-02'")
            self.assertTrue((later.shares > 0).any())
            self.assertTrue((later.shares < 0).any())
            self.assertTrue(result['equity'].holdings.ge(20).all())
            self.assertTrue(result['equity'].cash_ratio.lt(.25).all())
            self.assertTrue(result['equity'].violations.eq('').all())
            first_buy = later[later.shares > 0].iloc[0].date
            first_sell = later[later.shares < 0].iloc[0].date
            self.assertLess(first_sell, first_buy)
            self.assertTrue(set(later[later.shares > 0].symbol).isdisjoint(original))

    def test_odd_residual_does_not_block_other_rotation(self):
        names = [f'{1000+i}.TW' for i in range(12)]
        ranked = pd.DataFrame(dict(symbol=names, close=100., score=np.linspace(.9, .1, 12), entry_ok=True, exit=False))
        ranked.loc[ranked.symbol == '1000.TW', 'exit'] = True
        ranked.loc[ranked.symbol == '1011.TW', 'score'] = 2.
        holdings = {s: 9000. for s in names[:10]}
        holdings['1000.TW'] = 500.
        cash = 1_000_000.
        nav = cash + sum(holdings.values()) * 100
        orders, reason, selected = make_plan_v2(ranked, holdings, cash, nav, _settings(settings()))
        self.assertTrue(orders)
        self.assertNotIn('1000.TW', orders)
        self.assertEqual(reason, 'SELL_THEN_WAIT_SETTLEMENT')

    def test_independent_auditor_reconstructs_and_rejects_phantom_share(self):
        daily, u = fixture()
        result = run_v2(daily, u, settings(), signal_transform=constant_signals)
        calendar = list(pd.to_datetime(result['equity'].date))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            save_result(result, path / 'A')
            rebuilt, status = audit_model(path, 'A', daily, calendar, result['config'])
            self.assertEqual(len(rebuilt), 5)
            bad = pd.read_csv(path / 'A/holdings.csv')
            bad.loc[0, 'shares'] += 1
            bad.to_csv(path / 'A/holdings.csv', index=False)
            with self.assertRaisesRegex(AssertionError, 'action/fill shares'):
                audit_model(path, 'A', daily, calendar, result['config'])


if __name__ == '__main__':
    unittest.main()
