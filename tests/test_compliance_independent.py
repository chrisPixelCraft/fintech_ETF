"""Independent adversarial tests for the compliance-first tuning contract."""
from pathlib import Path
import json
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.audit_tuning_2nd import (enumerate_price_corners, measured_hard_mask,
                             posttrade_price_envelope, select_zero_measured,
                             prefunded_batch_audit, MIXED_REASON, audit_model)
from src.compliance_planner import make_plan_v2, stress_violations
from src.tuning_features import FeatureCache, IsolatedEngine
from src.backtest import save_result


CONFIG = dict(initial_cash=1_000_000., min_count=20, max_count=30,
              commission=.001425, sell_tax=.003, price_buffer=1.1,
              price_lower_buffer=.9)


def equity():
    return pd.DataFrame(dict(date=['2025-01-02', '2025-01-03'],
                             nav=[1_000_000., 1_010_000.], economic_nav=[1_000_000., 1_010_000.],
                             cash=[100_000., 100_000.], cash_ratio=[.1, .0990099], holdings=[25, 25],
                             violations=['', ''], active_cap_breaches=['', ''],
                             overdue_passive_caps=['', ''], active_share_status=['UNKNOWN', 'UNKNOWN']))


class IndependentEligibilityTests(unittest.TestCase):
    def test_count_and_whitelist_cannot_hide_behind_safe_cash(self):
        for field, value in [('holdings', 19), ('holdings', 31), ('violations', 'NON_WHITELIST:9999.TW'),
                             ('cash', -1), ('cash_ratio', .25), ('active_cap_breaches', '2330.TW'),
                             ('overdue_passive_caps', '2330.TW')]:
            with self.subTest(field=field, value=value):
                frame = equity(); frame.loc[1, field] = value
                self.assertTrue(measured_hard_mask(frame, CONFIG).iloc[1])

    def test_passive_grace_is_not_formal_certification(self):
        frame = equity(); frame.loc[1, 'violations'] = 'WEIGHT_CAP:2330.TW'
        result = select_zero_measured({'p000': frame}, CONFIG)
        self.assertEqual(result['candidate_id'], 'p000')
        self.assertEqual(result['formal_compliance'], 'UNKNOWN')
        self.assertEqual(result['submission_status'], 'BLOCK_SUBMISSION')

    def test_no_least_violating_fallback(self):
        first, second = equity(), equity()
        first.loc[0, 'holdings'] = 19
        second['holdings'] = 19
        result = select_zero_measured({'best_of_bad': first, 'worse': second}, CONFIG)
        self.assertEqual(result['status'], 'NO_ELIGIBLE_WINNER')
        self.assertIsNone(result['candidate_id'])

    def test_future_return_and_future_failure_do_not_change_prefix(self):
        old = equity(); changed = old.copy()
        changed.loc[1, 'economic_nav'] *= 100
        changed.loc[1, 'cash'] = -100
        before = select_zero_measured({'p000': old}, CONFIG, '2025-01-02')
        after = select_zero_measured({'p000': changed}, CONFIG, '2025-01-02')
        self.assertEqual(before, after)

    def test_higher_return_with_any_hard_violation_loses(self):
        high = equity(); high.loc[1, 'economic_nav'] *= 10; high.loc[1, 'holdings'] = 31
        result = select_zero_measured({'high': high, 'low': equity()}, CONFIG)
        self.assertEqual(result['candidate_id'], 'low')


class IndependentEnvelopeTests(unittest.TestCase):
    def test_closed_form_agrees_with_exhaustive_small_corner_oracle(self):
        prices = {'x': 100., 'y': 130., 'z': 80.}
        holdings = {'x': 1000., 'y': 1000., 'z': 1000.}
        for orders, cash in [({}, 90_000.), ({'x': -1000}, 90_000.), ({'x': 1000}, 150_000.)]:
            with self.subTest(orders=orders):
                envelope = posttrade_price_envelope(holdings, cash, prices, orders, CONFIG)
                corners = enumerate_price_corners(holdings, cash, prices, orders, CONFIG)
                self.assertAlmostEqual(envelope['cash_min'], min(x['cash'] for x in corners), places=7)
                self.assertAlmostEqual(envelope['cash_max'], max(x['cash'] for x in corners), places=7)
                self.assertAlmostEqual(envelope['cash_ratio_max'], max(x['cash_ratio'] for x in corners), places=12)
                for symbol, bound in envelope['max_weights'].items():
                    self.assertAlmostEqual(bound, max(x['weights'].get(symbol, 0) for x in corners), places=12)

    def test_old_projected_cash_can_pass_while_next_close_fails(self):
        holdings = {f's{i}': 1000 for i in range(25)}
        prices = {s: 30. for s in holdings}
        # Known close: 24.24%; all held prices down10%: 26.23%.
        cash = 240_000.
        self.assertLess(cash / (cash + 750_000.), .25)
        envelope = posttrade_price_envelope(holdings, cash, prices, {}, CONFIG)
        self.assertGreaterEqual(envelope['cash_ratio_max'], .25)

    def test_production_checker_matches_independent_price_corner_failures(self):
        prices = {'x': 100., 'y': 130., 'z': 80.}
        holdings = {'x': 1000., 'y': 1000., 'z': 1000.}
        ranked = pd.DataFrame(dict(symbol=list(prices), close=list(prices.values()),
                                   score=[3., 2., 1.], entry_ok=True, exit=False))
        config = {**CONFIG, 'lot_size': 1000, 'min_count': 1, 'max_count': 3,
                  'max_weight': .5, 'tsmc_max_weight': .25,
                  'cash_guard_ratio': .16, 'cash_guard_headroom': .02}
        for orders in ({}, {'x': -1000}, {'x': 1000}, {'x': -1000, 'y': 1000}):
            for cash in (50_000., 150_000., 300_000.):
                with self.subTest(orders=orders, cash=cash):
                    corners = enumerate_price_corners(holdings, cash, prices, orders, config)
                    expected = set()
                    if min(row['cash'] for row in corners) < -1e-6:
                        expected.add('NEGATIVE_CASH')
                    if max(row['cash_ratio'] for row in corners) >= .23 - 1e-12:
                        expected.add('CASH_GUARD')
                    for name in prices:
                        if max(row['weights'].get(name, 0) for row in corners) > .5 + 1e-10:
                            expected.add('WEIGHT_CAP:' + name)
                    actual = set(stress_violations(orders, holdings, cash, ranked, config, allow_mixed=True))
                    self.assertEqual(actual, expected)

    def test_untradable_31_residual_names_are_not_disappeared(self):
        names = [f'{1000+i}.TW' for i in range(31)]
        ranked = pd.DataFrame(dict(symbol=names, close=100., score=np.arange(31.), entry_ok=False, exit=True))
        config = {**CONFIG, 'lot_size': 1000, 'target_count': 25,
                  'max_weight': .1, 'tsmc_max_weight': .25, 'cash_guard_ratio': .16,
                  'cash_guard_headroom': .02, 'max_replacements_per_day': 0,
                  'replacement_margin': .1, 'allocation_mode': 'local', 'cash_target': 0.}
        orders, reason, _ = make_plan_v2(ranked, dict.fromkeys(names, 500.), 200_000., 1_750_000., config)
        self.assertEqual(orders, {})
        self.assertTrue('INFEASIBLE' in reason or 'ODD' in reason)
        self.assertIn('HOLDING_COUNT_MAX', stress_violations(orders, dict.fromkeys(names, 500.), 200_000., ranked, config))

    def test_actual_mixed_planner_passes_independent_budget_and_envelope(self):
        names = [f'{1000+i}.TW' for i in range(35)]
        ranked = pd.DataFrame(dict(symbol=names, close=100., score=np.linspace(1., 0., len(names)),
                                   entry_ok=True, exit=False))
        holdings = dict.fromkeys(names[:25], 25_000.)
        holdings[names[0]] = 100_000.
        cash = 20_000_000.
        config = {**CONFIG, 'lot_size': 1000, 'target_count': 25,
                  'max_weight': .1, 'tsmc_max_weight': .25, 'cash_guard_ratio': .12,
                  'cash_guard_headroom': .02, 'max_replacements_per_day': 0,
                  'replacement_margin': .1, 'allocation_mode': 'local', 'cash_target': 0.}
        orders, reason, _ = make_plan_v2(ranked, holdings, cash, cash + sum(holdings.values()) * 100, config)
        self.assertEqual(reason, MIXED_REASON)
        batch = pd.DataFrame(dict(symbol=list(orders), shares=list(orders.values()), sizing_price=100., reason=reason))
        self.assertTrue(prefunded_batch_audit(batch, config, cash, holdings))
        envelope = posttrade_price_envelope(holdings, cash, dict.fromkeys(names, 100.), orders, config)
        self.assertGreaterEqual(envelope['cash_min'], 0.)
        self.assertLess(envelope['cash_ratio_max'], .23)
        self.assertTrue(all(weight <= .1 + 1e-10 for weight in envelope['max_weights'].values()))
        self.assertTrue(20 <= len(envelope['posttrade_shares']) <= 30)


class IndependentPrefundingTests(unittest.TestCase):
    def setUp(self):
        self.orders = pd.DataFrame(dict(symbol=['x', 'y'], shares=[1000, -1000],
                                        sizing_price=[100., 100.], reason=MIXED_REASON))

    def test_different_stock_mixed_is_valid_when_entire_buy_is_prefunded(self):
        required = 1000 * 100. * 1.1 * 1.001425
        self.assertTrue(prefunded_batch_audit(self.orders, CONFIG, required, {'y': 1000.}))

    def test_sale_proceeds_cannot_rescue_underfunded_fixed_buys(self):
        with self.assertRaisesRegex(AssertionError, 'sale proceeds'):
            prefunded_batch_audit(self.orders, CONFIG, 100_000., {'y': 1000.})

    def test_same_stock_roundtrip_and_oversale_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'duplicate symbol'):
            prefunded_batch_audit(self.orders.assign(symbol=['x', 'x']), CONFIG, 200_000., {'x': 1000.})
        with self.assertRaisesRegex(AssertionError, 'existing inventory'):
            prefunded_batch_audit(self.orders, CONFIG, 200_000., {'y': 500.})

    def test_mixed_requires_explicit_correction_reason(self):
        with self.assertRaisesRegex(AssertionError, 'explicit pre-funded reason'):
            prefunded_batch_audit(self.orders.assign(reason='BUY_WITH_SETTLED_CASH'), CONFIG, 200_000., {'y': 1000.})


class IndependentProductionTimingTests(unittest.TestCase):
    @staticmethod
    def fixture():
        dates = pd.to_datetime(['2024-12-31', '2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07', '2025-01-08'])
        frames = [pd.DataFrame(dict(date=dates, symbol=f'{1000+i}.TW', open=100., high=100., low=100., close=100.,
                                    volume=1_000_000., turnover=100_000_000., execution_volume=1_000_000.,
                                    split=1., dividend=0.)) for i in range(30)]
        daily = pd.concat(frames, ignore_index=True)
        universe = pd.DataFrame(dict(symbol=sorted(daily.symbol.unique()), known_at='2024-12-31T19:30:00+08:00'))
        return daily, universe

    @staticmethod
    def run_case(daily, universe, end='2025-01-08'):
        config = json.loads((Path(__file__).resolve().parents[1] / 'config/strategy_v2.json').read_text())
        config.update(start='2025-01-02', end=end, initial_cash=100_000_000., cash_guard_ratio=.12,
                      cash_guard_headroom=.02, four_hour_mode='strict', max_replacements_per_day=1)
        engine = IsolatedEngine(FeatureCache(daily))
        engine.module.make_plan_v2 = make_plan_v2
        def signals(day, rows):
            rows['entry_ok'] = True
            rows['exit'] = False
            rows['score'] = [1. - int(s.split('.')[0]) / 10000 for s in rows.symbol]
            if day >= pd.Timestamp('2025-01-03'):
                rows.loc[rows.symbol == '1029.TW', 'score'] = 2.
            return rows, dict(state='INDEPENDENT_FIXED_TEST_SIGNAL')
        return engine.run_v2(daily, universe, config, signal_transform=signals)

    def test_future_perturbation_and_physical_prefix_keep_prior_orders(self):
        daily, universe = self.fixture()
        full = self.run_case(daily, universe)
        changed = daily.copy()
        changed.loc[changed.date >= '2025-01-06', ['open', 'high', 'low', 'close', 'turnover']] *= 1.05
        future = self.run_case(changed, universe)
        before = '2025-01-03'
        pd.testing.assert_frame_equal(full['orders'].query('signal_date <= @before').reset_index(drop=True),
                                      future['orders'].query('signal_date <= @before').reset_index(drop=True))
        prefix = self.run_case(daily[daily.date <= '2025-01-06'], universe, '2025-01-06')
        for table in ('trades', 'holdings'):
            pd.testing.assert_frame_equal(full[table].query("date <= '2025-01-06'").reset_index(drop=True),
                                          prefix[table].reset_index(drop=True))
        np.testing.assert_allclose(full['equity'].query("date <= '2025-01-06'").economic_nav,
                                   prefix['equity'].economic_nav, rtol=0, atol=1e-6)
        self.assertTrue((full['equity'].cash >= 0).all())
        self.assertTrue(full['equity'].holdings.between(20, 30).all())
        for _, fills in full['trades'].groupby('date'):
            self.assertEqual(len(set(np.sign(fills.shares))), 1)

    def test_realized_execution_price_cannot_resize_existing_order(self):
        daily, universe = self.fixture()
        full = self.run_case(daily, universe)
        changed = daily.copy()
        changed.loc[changed.date == '2025-01-02', 'turnover'] *= 1.05
        repriced = self.run_case(changed, universe)
        first, other = (result['trades'].query("date == '2025-01-02'").set_index('symbol').shares
                        for result in (full, repriced))
        self.assertGreater(len(first), 0)
        pd.testing.assert_series_equal(first, other)

    def test_full_rowwise_auditor_accepts_prefunding_and_rejects_hidden_credit(self):
        daily, universe = self.fixture()
        config = json.loads((Path(__file__).resolve().parents[1] / 'config/strategy_v2.json').read_text())
        config.update(start='2025-01-02', end='2025-01-08', initial_cash=100_000_000.,
                      cash_guard_ratio=.12, cash_guard_headroom=.02, four_hour_mode='strict',
                      max_replacements_per_day=0)
        engine = IsolatedEngine(FeatureCache(daily))
        def controlled_mixed(rows, holdings, cash, nav, cfg, *args):
            if not holdings:
                return make_plan_v2(rows, holdings, cash, nav, cfg, *args)
            if '1029.TW' not in holdings:
                return {'1000.TW': -1000, '1029.TW': 1000}, MIXED_REASON, [*holdings, '1029.TW']
            return {}, 'HOLD', list(holdings)
        def signals(day, rows):
            rows['entry_ok'] = True; rows['exit'] = False
            rows['score'] = [1. - int(s.split('.')[0]) / 10000 for s in rows.symbol]
            return rows, {}
        engine.module.make_plan_v2 = controlled_mixed
        result = engine.run_v2(daily, universe, config, signal_transform=signals)
        calendar = sorted(pd.Timestamp(x) for x in daily.date.unique() if x >= pd.Timestamp(config['start']))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            save_result(result, output / 'A')
            audit_model(output, 'A', daily, calendar, config)
            path = output / 'A/orders.csv'
            orders = pd.read_csv(path)
            orders.loc[orders.reason.eq(MIXED_REASON) & orders.shares.gt(0), 'sizing_price'] *= 1000
            orders.to_csv(path, index=False)
            with self.assertRaisesRegex(AssertionError, 'sale proceeds'):
                audit_model(output, 'A', daily, calendar, config)


if __name__ == '__main__':
    unittest.main()
