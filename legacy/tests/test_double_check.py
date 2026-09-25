"""Counterexamples for invalid-day accounting, grace periods and fail-closed search."""
import copy
import unittest
from unittest.mock import patch
import pandas as pd
from src import double_check_ledger as ledger, double_check_tuning as tuning
from scripts.audit_double_check import audit_result
from tests.test_backtest_v2 import fixture, settings, constant_signals


class PenaltyLedgerTests(unittest.TestCase):
    def run_fixture(self, data=None, config=None, planner=None):
        daily, universe = fixture()
        with patch.object(ledger, 'make_plan_v2', planner or ledger.make_plan_v2):
            source = data if data is not None else daily
            result = ledger.run_v2(source, universe,
                config or settings(), signal_transform=constant_signals)
        audit_result(result, dict(daily=source, universe=universe))
        return result

    def test_multi_breach_day_rolls_back_every_fill_and_cost_once(self):
        def bad_plan(rows, held, cash, nav, config, *args):
            # Valid target weight but only one stock: count and cash both fail.
            return {'1000.TW': 1000}, 'TEST_BAD_PLAN', ['1000.TW']
        result = self.run_fixture(planner=bad_plan)
        self.assertEqual(result['compliance_daily'].cumulative_warnings.tolist(), [1, 2, 3])
        self.assertEqual(len(result['equity']), 3)
        self.assertTrue(result['trades'].empty)
        self.assertTrue(result['holdings'].empty)
        self.assertEqual(len(result['rejected_trades']), 3)
        self.assertTrue(result['equity'].cash.eq(10_000_000).all())
        self.assertTrue(result['equity'].costs.eq(0).all())
        self.assertTrue(result['metrics']['disqualified'])
        self.assertFalse(result['metrics']['complete_period'])

    def test_vwap_shock_rolls_back_without_resizing_prior_orders(self):
        data, _ = fixture()
        base = self.run_fixture()
        data.loc[data.date.eq('2025-01-02'), 'turnover'] *= 2
        result = self.run_fixture(data=data)
        original = base['orders'].query("signal_date == '2024-12-31'")
        actual = result['orders'].query("signal_date == '2024-12-31'")
        pd.testing.assert_frame_equal(original, actual)
        self.assertTrue(result['trades'].query("date == '2025-01-02'").empty)
        self.assertEqual(result['equity'].iloc[0].cash, 10_000_000)
        self.assertEqual(result['equity'].iloc[0].costs, 0)
        self.assertIn('NEGATIVE_CASH', result['compliance_daily'].iloc[0].warning_reasons)

    def test_passive_grace_day_six_and_active_day_one(self):
        config = settings()
        holdings = {'1000.TW': 1000}
        ages = {}
        for day in range(1, 7):
            ages, active, passive = ledger.cap_state(holdings, {'1000.TW': 100}, 200_000, ages, set(), config)
            reasons = ledger.warning_reasons({'violations': ['WEIGHT_CAP:1000.TW']},
                                            active, [s for s in passive if ages[s] > 5])
            self.assertEqual(bool(reasons), day == 6)
        _, active, passive = ledger.cap_state(holdings, {'1000.TW': 100}, 200_000, {}, {'1000.TW'}, config)
        self.assertEqual(ledger.warning_reasons({'violations': []}, active, []), ['ACTIVE_CAP:1000.TW'])

    def test_sale_fees_create_active_cap_without_buying_overweight_name(self):
        data, _ = fixture()
        initial = self.run_fixture()
        first = initial['holdings'].query("date == '2025-01-02'").set_index('symbol')
        cash = float(initial['equity'].iloc[0].cash)
        cap = settings()['max_weight']
        other_value = float((first.shares * first.close).sum() - first.at['1000.TW', 'shares'] * first.at['1000.TW', 'close'])
        boundary_price = cap * (cash + other_value) / ((1-cap) * first.at['1000.TW', 'shares'])
        mask = data.date.ge('2025-01-03') & data.symbol.eq('1000.TW')
        data.loc[mask, ['open', 'high', 'low', 'close']] = boundary_price
        data.loc[mask, 'turnover'] = boundary_price * data.loc[mask, 'execution_volume']
        base_planner = ledger.make_plan_v2
        def sell_other(rows, held, available, nav, config, *args):
            if held:
                return {'1001.TW': -1000}, 'TEST_SELL_OTHER', list(held)
            return base_planner(rows, held, available, nav, config, *args)
        result = self.run_fixture(data=data, planner=sell_other)
        day = result['compliance_daily'].query("date == '2025-01-03'").iloc[0]
        self.assertIn('ACTIVE_CAP:1000.TW', day.warning_reasons)
        self.assertTrue(day.rolled_back)
        self.assertEqual(day.passive_caps, '')
        self.assertFalse(result['trades'].date.eq('2025-01-03').any())
        rejected = result['rejected_trades'].query("date == '2025-01-03'")
        self.assertEqual(rejected.symbol.tolist(), ['1001.TW'])
        after = result['holdings'].query("date == '2025-01-03' and symbol == '1001.TW'").iloc[0]
        self.assertEqual(after.shares, first.at['1001.TW', 'shares'])

    def test_terminal_dividend_never_becomes_tradable_cash(self):
        data, _ = fixture()
        data.loc[data.date.eq('2025-01-08'), 'dividend'] = 10
        result = self.run_fixture(data=data)
        last = result['equity'].iloc[-1]
        self.assertGreater(last.terminal_dividend_credit, 0)
        self.assertAlmostEqual(last.cash, result['equity'].iloc[-2].cash)
        self.assertAlmostEqual(last.nav, result['compliance_daily'].iloc[-1].settled_nav + last.terminal_dividend_credit)
        self.assertFalse(result['compliance_daily'].warning_today.any())

    def test_rollback_preserves_dividend_and_split_entitlement(self):
        data, _ = fixture()
        base_planner = ledger.make_plan_v2
        def planner(rows, held, cash, nav, config, *args):
            if held:
                return {'1000.TW': -1000}, 'TEST_SELL', list(held)
            return base_planner(rows, held, cash, nav, config, *args)
        mask = data.date.eq('2025-01-03') & data.symbol.eq('1000.TW')
        data.loc[mask, 'split'] = 2
        data.loc[mask, 'dividend'] = 1
        # Force cash >=25% using a price discontinuity after initial purchase.
        data.loc[data.date.ge('2025-01-03'), ['open', 'high', 'low', 'close', 'turnover']] *= .1
        result = self.run_fixture(data=data, planner=planner)
        before = result['holdings'].query("date == '2025-01-02' and symbol == '1000.TW'").iloc[0].shares
        after = result['holdings'].query("date == '2025-01-03' and symbol == '1000.TW'").iloc[0].shares
        self.assertEqual(after, before * 2)
        self.assertTrue(result['compliance_daily'].query("date == '2025-01-03'").iloc[0].rolled_back)
        self.assertEqual(result['equity'].query("date == '2025-01-03'").iloc[0].dividend_receivable, before)


class EligibilityTests(unittest.TestCase):
    def test_unknown_missing_nonfinite_and_failed_counts_cannot_win(self):
        row = dict(status='COMPLETE', complete_period=True, disqualified=False,
                   total_return=1., max_drawdown=.2, turnover_two_way=3., candidate_id='x',
                   **{key: 0 for key in tuning.ZERO_COUNTS})
        self.assertTrue(tuning.eligible(row))
        for key in tuning.ZERO_COUNTS:
            self.assertFalse(tuning.eligible({**row, key: 1}))
            missing = copy.deepcopy(row); del missing[key]
            self.assertFalse(tuning.eligible(missing))
        self.assertFalse(tuning.eligible({**row, 'total_return': float('nan')}))
        self.assertFalse(tuning.eligible({**row, 'complete_period': False}))
        for value in (None, '0', False, float('nan')):
            self.assertFalse(tuning.eligible({**row, tuning.ZERO_COUNTS[0]: value}))
        for value in (None, 'high', False):
            self.assertFalse(tuning.eligible({**row, 'total_return': value}))


class IndependentAuditTests(unittest.TestCase):
    def setUp(self):
        daily, universe = fixture()
        self.ctx = dict(daily=daily, universe=universe)
        self.result = ledger.run_v2(daily, universe, settings(), signal_transform=constant_signals)

    def test_missing_fill_and_forged_settlement_are_detected(self):
        audit_result(self.result, self.ctx)
        for table, column in [('equity', 'cash'), ('holdings', 'shares'), ('trades', 'fee'),
                              ('compliance_daily', 'settled_nav'), ('orders', 'target_weight')]:
            altered = copy.deepcopy(self.result)
            altered[table].loc[0, column] += 1
            with self.subTest(table=table), self.assertRaises(AssertionError):
                audit_result(altered, self.ctx)
        altered = copy.deepcopy(self.result)
        altered['trades'] = altered['trades'].iloc[1:]
        with self.assertRaises(AssertionError):
            audit_result(altered, self.ctx)

    def test_unfilled_source_is_not_fabricated(self):
        daily = self.ctx['daily'].copy()
        daily.loc[daily.date.eq('2025-01-02') & daily.symbol.eq('1000.TW'), 'turnover'] = float('nan')
        result = ledger.run_v2(daily, self.ctx['universe'], settings(), signal_transform=constant_signals)
        checked = audit_result(result, dict(daily=daily, universe=self.ctx['universe']))
        self.assertGreater(checked['unfilled_orders'], 0)
        altered = copy.deepcopy(result)
        altered['warnings'] = altered['warnings'][~altered['warnings'].issue.str.startswith('UNFILLED')]
        with self.assertRaises(AssertionError):
            audit_result(altered, dict(daily=daily, universe=self.ctx['universe']))


if __name__ == '__main__':
    unittest.main()
