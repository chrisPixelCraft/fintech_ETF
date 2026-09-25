"""V5 planner: official lot formula, hard-rule repair, costs, and legacy odd lots."""
import math
import unittest

import numpy as np
import pandas as pd

from src.official_deep_tuning import representable_weight
from src.v5_planner import ASSUMPTION_ODD_LOT, cap_of, plan, settings

SYMBOLS = [f'{1100 + i}.TW' for i in range(40)] + ['2330.TW']


def prices(seed=0):
    rng = np.random.default_rng(seed)
    p = pd.Series(rng.uniform(15, 1800, len(SYMBOLS)).round(2), index=SYMBOLS)
    p['2330.TW'] = 1000.
    return p


def after(result, holdings, cash, p, c=None):
    """Apply orders at the sizing price with official costs."""
    c = settings(c)
    book, cash = dict(holdings), float(cash)
    for s, q in result.orders.items():
        amount = abs(q) * p[s]
        cash -= q * p[s] + amount * c['commission'] + (amount * c['sell_tax'] if q < 0 else 0.)
        book[s] = book.get(s, 0.) + q
    book = {s: q for s, q in book.items() if q > 1e-6}
    nav = cash + sum(q * p[s] for s, q in book.items())
    return book, cash, nav


class PlannerRuleTests(unittest.TestCase):
    def assert_valid(self, result, holdings, cash, p, c=None):
        c = settings(c)
        self.assertIn(result.status, ('OK', 'REPAIRED'), result.reason)
        for s, q in result.orders.items():
            self.assertEqual(q % 1000, 0, s)
            self.assertNotEqual(q, 0)
            self.assertGreaterEqual(holdings.get(s, 0.) + q, 0)
        book, cash_after, nav = after(result, holdings, cash, p, c)
        self.assertTrue(20 <= len(book) <= 30, len(book))
        self.assertGreaterEqual(cash_after, 0)
        self.assertLess(cash_after / nav, .25)
        for s, q in book.items():
            self.assertLessEqual(q * p[s] / nav, cap_of(s, c) + 1e-12, s)
        # Worst case: buys filled at +10%, sells at -10% never produce negative cash.
        self.assertGreaterEqual(result.audit['estimate']['worst_cash'], -1e-6)
        return book, cash_after, nav

    def test_first_day_equal_weight(self):
        p = prices()
        weights = pd.Series(.95 / 22, index=SYMBOLS[:21] + ['2330.TW'])
        result = plan(weights, p, 1e9, {}, 1e9)
        book, _, _ = self.assert_valid(result, {}, 1e9, p)
        self.assertEqual(len(book), 22)
        self.assertEqual(result.target_shares, book)

    def test_official_formula_when_no_repair_is_needed(self):
        p = prices(1)
        weights = pd.Series(.035, index=SYMBOLS[:24])
        c = dict(buy_price_buffer=1.0, sell_price_buffer=1.0, cash_ratio_margin=0.)
        result = plan(weights, p, 1e9, {}, 1e9, c)
        self.assertEqual(result.status, 'OK', result.reason)
        for s in weights.index:
            expected = math.floor(weights[s] * 1e9 / p[s] / 1000) * 1000
            self.assertEqual(result.orders[s], expected, s)
            # The declared D-Plan weight maps back to the same shares.
            declared = result.audit['dplan_target_weight'][s]
            self.assertEqual(math.floor(declared * 1e9 / p[s] / 1000) * 1000, expected)

    def test_caps_normal_and_tsmc(self):
        p = prices(2)
        weights = pd.Series(.03, index=SYMBOLS[:22])
        weights[SYMBOLS[0]] = .30
        weights['2330.TW'] = .40
        result = plan(weights, p, 1e9, {}, 1e9)
        book, _, nav = self.assert_valid(result, {}, 1e9, p)
        self.assertLessEqual(book[SYMBOLS[0]] * p[SYMBOLS[0]] / nav, .10)
        self.assertLessEqual(book['2330.TW'] * p['2330.TW'] / nav, .25)
        self.assertGreater(book['2330.TW'] * p['2330.TW'] / nav, .20)
        self.assertTrue(any(r.startswith('CAP_BUY_LIMIT') for r in result.audit['repairs']))

    def test_held_name_above_cap_is_trimmed(self):
        p = prices(3)
        holdings = {s: 30000. for s in SYMBOLS[:22]}
        big = SYMBOLS[1]
        holdings[big] = math.ceil(.12 * 1e9 / p[big] / 1000) * 1000
        value = sum(q * p[s] for s, q in holdings.items())
        cash = max(.05 * 1e9, 1e9 - value)
        nav = cash + value
        weights = pd.Series({s: q * p[s] / nav for s, q in holdings.items()})
        result = plan(weights, p, nav, holdings, cash)
        self.assertLess(result.orders.get(big, 0), 0)
        self.assertTrue(any(r == 'CAP_TRIM:' + big for r in result.audit['repairs']))

    def test_count_repair_above_thirty(self):
        p = prices(4)
        weights = pd.Series(.9 / 35, index=SYMBOLS[:35])
        result = plan(weights, p, 1e9, {}, 1e9)
        book, _, _ = self.assert_valid(result, {}, 1e9, p)
        self.assertEqual(len(book), 30)

    def test_too_few_names_is_not_silently_accepted(self):
        p = prices(5)
        result = plan(pd.Series(.05, index=SYMBOLS[:10]), p, 1e9, {}, 1e9)
        self.assertEqual(result.status, 'INFEASIBLE')
        self.assertIn('HOLDING_COUNT', result.reason)

    def test_cash_ceiling_topup(self):
        p = prices(6)
        weights = pd.Series(.5 / 22, index=SYMBOLS[:22])
        result = plan(weights, p, 1e9, {}, 1e9)
        self.assert_valid(result, {}, 1e9, p)
        self.assertTrue(any(r.startswith('CASH_CEILING_TOPUP') for r in result.audit['repairs']))

    def test_costs_in_estimate(self):
        p = prices(7)
        holdings = {s: 20000. for s in SYMBOLS[:22]}
        cash = 1e8
        nav = cash + sum(q * p[s] for s, q in holdings.items())
        weights = pd.Series({s: holdings[s] * p[s] / nav for s in SYMBOLS[2:22]})
        for s in SYMBOLS[22:24]:
            weights[s] = .04
        result = plan(weights, p, nav, holdings, cash, dict(rebalance_band=.02))
        c = settings()
        buys = sum(q * p[s] for s, q in result.orders.items() if q > 0)
        sells = -sum(q * p[s] for s, q in result.orders.items() if q < 0)
        self.assertGreater(sells, 0)
        self.assertGreater(buys, 0)
        expected_cash = cash - buys * (1 + .001425) + sells * (1 - .001425 - .003)
        self.assertAlmostEqual(result.audit['estimate']['cash'], expected_cash, places=4)
        self.assertAlmostEqual(result.audit['estimate']['fees'], buys * .001425 + sells * .004425, places=4)
        self.assertEqual(c['sell_tax'], .003)
        self.assert_valid(result, holdings, cash, p)

    def test_missing_held_close_holds(self):
        p = prices(8)
        holdings = {s: 10000. for s in SYMBOLS[:22]}
        p[SYMBOLS[0]] = np.nan
        result = plan(pd.Series(.04, index=SYMBOLS[:22]), p, 1e9, holdings, 1e8)
        self.assertEqual(result.orders, {})
        self.assertTrue(result.reason.startswith('MISSING_HELD_CLOSE'))


class OddLotTests(unittest.TestCase):
    """A synthetic 5% stock dividend leaves a non-lot legacy holding."""

    def setUp(self):
        self.p = prices(9)
        self.holdings = {s: 40000. for s in SYMBOLS[:22]}
        self.odd = SYMBOLS[3]
        self.holdings[self.odd] = 21000. * 1.05  # 22,050 shares: 50 odd shares
        self.cash = 6e7
        self.nav = self.cash + sum(q * self.p[s] for s, q in self.holdings.items())

    def test_hold_mode_never_orders_odd_name_but_counts_it(self):
        # The strategy wants to exit the odd name and rotate two others.
        weights = pd.Series(.9 / 22, index=[s for s in SYMBOLS[:24] if s not in (self.odd, SYMBOLS[5])])
        result = plan(weights, self.p, self.nav, self.holdings, self.cash)
        self.assertNotIn(self.odd, result.orders)
        self.assertEqual(result.target_shares[self.odd], self.holdings[self.odd])
        self.assertIn(ASSUMPTION_ODD_LOT, result.audit['assumptions'])
        self.assertEqual(result.audit['odd_lot'][0]['symbol'], self.odd)
        self.assertEqual(result.audit['odd_lot'][0]['action'], 'HELD_UNCHANGED')
        self.assertIn(result.status, ('OK', 'REPAIRED'), result.reason)
        self.assertLess(result.orders[SYMBOLS[5]], 0)  # other names still trade
        for q in result.orders.values():
            self.assertEqual(q % 1000, 0)
        book, cash, nav = after(result, self.holdings, self.cash, self.p)
        self.assertIn(self.odd, book)  # counts toward 20-30 and NAV
        self.assertTrue(20 <= len(book) <= 30)
        # Every emitted order is accepted by the sealed ledger's D-Plan round trip.
        for s, q in result.orders.items():
            representable_weight(self.holdings.get(s, 0.) + q, self.p[s], self.nav, cap_of(s, settings()))

    def test_lots_around_mode_keeps_remainder(self):
        weights = pd.Series(.9 / 22, index=SYMBOLS[:22])
        weights[self.odd] = .02
        result = plan(weights, self.p, self.nav, self.holdings, self.cash, dict(odd_lot_mode='lots_around'))
        self.assertEqual(result.orders.get(self.odd, 0) % 1000, 0)
        self.assertAlmostEqual(result.target_shares[self.odd] % 1000, 50.)

    def test_sealed_ledger_rejects_orders_on_odd_holdings(self):
        """Documents why 'hold' is the default: v4_ledger's order audit raises."""
        with self.assertRaisesRegex(ValueError, 'DPLAN_ODD_TARGET_UNREPRESENTABLE'):
            representable_weight(self.holdings[self.odd] - 1000, self.p[self.odd], self.nav, .10)

    def test_float_residue_from_split_is_whole_lot(self):
        holdings = dict(self.holdings)
        holdings[self.odd] = 999.9999999990687 * 20
        result = plan(pd.Series(.9 / 22, index=SYMBOLS[:22]), self.p, self.nav, holdings, self.cash)
        self.assertEqual(result.audit['odd_lot'], [])


if __name__ == '__main__':
    unittest.main()
