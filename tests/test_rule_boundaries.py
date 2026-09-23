"""Independent checks of sourced contest boundaries and initial-capital accounting."""
import json
from pathlib import Path
import unittest

import pandas as pd

from src.backtest import rule_check
from src.benchmarks import run_buy_hold


ROOT = Path(__file__).resolve().parents[1]


class RuleBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'tests/fixtures/strategy_v1.json').read_text())

    def portfolio(self, count=20, cash=150_000_000., special=None):
        """Construct exact NAV=1e9, independently of the production allocator."""
        names = [f'{1000 + i}.TW' for i in range(count)]
        if special is not None:
            symbol, weight = special
            names[0] = symbol
            values = [weight * 1_000_000_000.]
            values += [(1_000_000_000. - cash - values[0]) / (count - 1)] * (count - 1)
        else:
            values = [(1_000_000_000. - cash) / count] * count
        holdings = dict(zip(names, [1000] * count))
        prices = dict(zip(names, [v / 1000 for v in values]))
        return holdings, cash, prices, set(names)

    def check(self, portfolio):
        holdings, cash, prices, whitelist = portfolio
        return rule_check(holdings, cash, prices, self.config, whitelist)

    def test_cash_exactly_25_percent_is_rejected(self):
        for cash, rejected in [(249_000_000., False), (250_000_000., True), (251_000_000., True)]:
            with self.subTest(cash=cash):
                result = self.check(self.portfolio(cash=cash))
                self.assertEqual('CASH_GE_25_PERCENT' in result['violations'], rejected)

    def test_position_count_closed_interval_20_to_30(self):
        for count, rejected in [(19, True), (20, False), (30, False), (31, True)]:
            with self.subTest(count=count):
                result = self.check(self.portfolio(count=count))
                self.assertEqual('HOLDING_COUNT' in result['violations'], rejected)

    def test_generic_stock_limit_is_10_percent_inclusive(self):
        for weight, rejected in [(.0999, False), (.10, False), (.1001, True)]:
            with self.subTest(weight=weight):
                result = self.check(self.portfolio(special=('2454.TW', weight)))
                self.assertEqual('WEIGHT_CAP:2454.TW' in result['violations'], rejected)

    def test_tsmc_limit_is_25_percent_inclusive(self):
        for weight, rejected in [(.2499, False), (.25, False), (.2501, True)]:
            with self.subTest(weight=weight):
                result = self.check(self.portfolio(special=('2330.TW', weight)))
                self.assertEqual('WEIGHT_CAP:2330.TW' in result['violations'], rejected)

    def test_holding_outside_whitelist_is_rejected(self):
        holdings, cash, prices, whitelist = self.portfolio()
        outsider = next(iter(holdings))
        whitelist.remove(outsider)
        result = self.check((holdings, cash, prices, whitelist))
        self.assertIn('NON_WHITELIST:' + outsider, result['violations'])

    def test_negative_cash_is_rejected_and_zero_is_allowed(self):
        for cash, rejected in [(-1., True), (0., False), (1., False)]:
            with self.subTest(cash=cash):
                result = self.check(self.portfolio(cash=cash))
                self.assertEqual('NEGATIVE_CASH' in result['violations'], rejected)

    def test_missing_active_share_evidence_never_becomes_pass(self):
        result = self.check(self.portfolio())
        self.assertEqual(result['active_share_status'], 'UNKNOWN_MISSING_HISTORICAL_ETF_HOLDINGS')
        self.assertEqual(result['active_share'], {})

    def test_annual_start_is_one_billion_with_initial_purchase_fee(self):
        rules = json.loads((ROOT / 'data/reference/competition_rules.json').read_text())
        self.assertEqual(self.config['initial_cash'], 1_000_000_000)
        self.assertEqual(self.config['initial_cash'], rules['initial_capital_twd'])
        symbols = [f'{1000 + i}.TW' for i in range(20)]
        daily = pd.DataFrame([
            dict(date=date, symbol=symbol, open=100., high=100., low=100., close=100.,
                 volume=1_000_000., turnover=100_000_000., dividend=0., split=1.)
            for date in ['2025-12-31', '2026-01-02', '2026-01-05'] for symbol in symbols
        ])
        config = dict(self.config, start='2026-01-01', end='2026-01-05', execution='vwap')
        result = run_buy_hold(daily, symbols, config)
        # Each position is 425,000 shares; total capital deployed is exactly 850m.
        expected_fee = 850_000_000. * .001425
        self.assertAlmostEqual(result['metrics']['transaction_costs'], expected_fee)
        self.assertAlmostEqual(result['metrics']['final_nav'], 1_000_000_000. - expected_fee)
        self.assertAlmostEqual(result['equity'].iloc[0].cash, 150_000_000. - expected_fee)
        self.assertAlmostEqual(result['metrics']['total_return'], -expected_fee / 1_000_000_000.)


if __name__ == '__main__':
    unittest.main()
