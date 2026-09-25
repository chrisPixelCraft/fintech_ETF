import math
import unittest

from competition.planner import PlannerPolicy, plan, representable_weight
from competition.rules import load_rules

RULES = load_rules()
NAV = 1e9


def names(n, start=0):
    return [f'S{i:03d}.TW' for i in range(start, start + n)]


def prices(symbols, value=100.):
    return {s: value for s in symbols}


class RulesTest(unittest.TestCase):
    def test_official_limits(self):
        r = RULES
        self.assertEqual((r.min_positions, r.max_positions, r.lot_size), (20, 30, 1000))
        self.assertEqual((r.cap('2330.TW'), r.cap('2317.TW')), (.25, .10))
        self.assertEqual((r.cash_min, r.cash_max_exclusive, r.initial_capital), (0., .25, 1e9))
        self.assertAlmostEqual(r.round_trip_cost, .00585)
        self.assertEqual(r.episode_sessions, 24)


class PlannerTest(unittest.TestCase):
    def check_orders(self, p, held, close, nav):
        for s, q in p.orders.items():
            self.assertEqual(q % 1000, 0, s)
            target = held.get(s, 0.) + q
            w = p.audit['dplan_target_weight'][s]
            self.assertEqual(math.floor(w * nav / close[s] / 1000) * 1000, target, s)  # official formula round-trip

    def test_initial_build_obeys_rules(self):
        syms = names(25)
        p = plan({s: .88 / 25 for s in syms}, prices(syms), NAV, {}, NAV, RULES)
        self.assertEqual(p.status, 'OK')
        self.check_orders(p, {}, prices(syms), NAV)
        est = p.audit['estimate']
        self.assertEqual(est['count'], 25)
        self.assertLess(est['cash_ratio'], .25)
        self.assertGreaterEqual(est['worst_cash'], 0)
        self.assertLess(est['best_case_cash_ratio'], .25 - PlannerPolicy().cash_ratio_margin)
        for s in syms:
            self.assertEqual(p.orders[s], math.floor(.88 / 25 * NAV / 100 / 1000) * 1000)

    def test_single_name_cap_and_2330_cap(self):
        syms = ['2330.TW'] + names(24)
        w = {s: .7 / 24 for s in syms[1:]}
        w['2330.TW'], w[syms[1]] = .2, .2
        p = plan(w, prices(syms), NAV, {}, NAV, RULES)
        self.assertIn(p.status, ('OK', 'REPAIRED'))
        worst = PlannerPolicy().buy_price_buffer
        self.assertLessEqual(p.target_shares[syms[1]] * 100 * worst, .10 * NAV)
        self.assertGreater(p.target_shares['2330.TW'] * 100, .10 * NAV)      # 2330 may exceed 10%
        self.assertLessEqual(p.target_shares['2330.TW'] * 100 * worst, .25 * NAV)
        self.assertTrue(any(x.startswith('CAP_BUY_LIMIT:' + syms[1]) for x in p.audit['repairs']))

    def test_holding_count_upper_bound(self):
        syms = names(35)
        p = plan({s: .88 / 35 for s in syms}, prices(syms), NAV, {}, NAV, RULES)
        self.assertLessEqual(len(p.target_shares), 30)
        self.assertGreaterEqual(len(p.target_shares), 20)
        self.assertIn(p.status, ('OK', 'REPAIRED'))

    def test_too_few_names_fails_closed(self):
        syms = names(15)
        p = plan({s: .88 / 15 for s in syms}, prices(syms), NAV, {}, NAV, RULES)
        self.assertEqual(p.status, 'INFEASIBLE')
        self.assertIn('HOLDING_COUNT', p.reason)

    def test_cash_band_top_up(self):
        syms = names(25)
        p = plan({s: .5 / 25 for s in syms}, prices(syms), NAV, {}, NAV, RULES)
        est = p.audit['estimate']
        self.assertLess(est['best_case_cash_ratio'], .25 - PlannerPolicy().cash_ratio_margin)
        self.assertGreaterEqual(est['worst_cash'], 0)
        self.assertTrue(any(x.startswith('CASH_CEILING_TOPUP') for x in p.audit['repairs']))
        self.check_orders(p, {}, prices(syms), NAV)

    def test_overfunded_buys_are_scaled(self):
        syms = names(25)
        p = plan({s: .099 for s in syms}, prices(syms), NAV, {}, NAV, RULES)
        self.assertGreaterEqual(p.audit['estimate']['worst_cash'], 0)
        self.assertTrue(any(x.startswith('FUNDING_SCALE') for x in p.audit['repairs']))

    def test_missing_held_close_fails_closed(self):
        syms = names(25)
        held = {s: 30000. for s in syms}
        close = prices(syms[1:])
        p = plan({s: .035 for s in syms}, close, NAV, held, 1e8, RULES)
        self.assertEqual((p.status, p.orders), ('INFEASIBLE', {}))
        self.assertTrue(p.reason.startswith('MISSING_HELD_CLOSE'))

    def test_odd_lot_holding_held_unchanged(self):
        syms = names(25)
        held = {s: 30000. for s in syms}
        held[syms[0]] = 30500.
        p = plan({s: .03 for s in syms[1:]}, prices(syms), NAV, held, 1e8, RULES)
        self.assertNotIn(syms[0], p.orders)
        self.assertEqual(p.target_shares[syms[0]], 30500.)
        self.assertIn('ASSUMPTION_ODD_LOT', p.audit['assumptions'])

    def test_rebalance_band_skips_small_changes(self):
        syms = names(25)
        held = {s: 350000. for s in syms}
        target = {s: 350000 * 100 / NAV for s in syms}
        target[syms[0]] += .002      # 0.2% NAV < 0.5% band
        p = plan(target, prices(syms), NAV, held, NAV - 25 * 350000 * 100, RULES)
        self.assertNotIn(syms[0], p.orders)

    def test_representable_weight(self):
        w = representable_weight(12000, 101.5, 1e9, .10)
        self.assertEqual(math.floor(w * 1e9 / 101.5 / 1000) * 1000, 12000)
        self.assertEqual(representable_weight(11999.9999999, 101.5, 1e9, .10), w)   # float residue only
        with self.assertRaises(ValueError):
            representable_weight(12500, 101.5, 1e9, .10)


if __name__ == '__main__':
    unittest.main()
