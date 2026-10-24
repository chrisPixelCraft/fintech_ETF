"""Updated top-15 screening, incomplete-source and post-planner regression tests."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import pandas as pd

from competition.planner import Plan
from competition.rules import load_rules
from production import active_share, engine
from production.state import SettledBook


class TopFifteenTest(unittest.TestCase):
    def setUp(self):
        self.weights = {str(1000 + i): .04 for i in range(15)}

    def etf(self, disjoint_weight=.02):
        return active_share.EtfTop10('00981A', '2026-10-23', {
            **{str(1000 + i): .04 for i in range(10)},
            **{str(2000 + i): disjoint_weight for i in range(5)}})

    def screen(self, etf):
        return active_share.check(self.weights, {'00981A': etf}, ['00981A'], '2026-10-27')

    def test_official_boundary_and_below(self):
        at = self.screen(self.etf())
        self.assertAlmostEqual(at.minimum, .15)
        self.assertEqual(at.status, active_share.CAUTION)
        below = self.screen(self.etf(.01996))
        self.assertLess(below.minimum, .15)
        self.assertEqual(below.status, active_share.INVALID)
        self.assertFalse(at.as_dict()['official_method_confirmed'])

    def test_top_ten_cannot_certify_top_fifteen(self):
        old = active_share.EtfTop10('00981A', '2026-10-23', {str(2000+i): .05 for i in range(10)})
        report = self.screen(old)
        self.assertEqual(report.status, active_share.UNVERIFIED)
        self.assertEqual(report.incomplete, ['00981A'])

    def test_future_invalid_and_missing_benchmark_are_unverified(self):
        for etf in [active_share.EtfTop10('00981A', '2026-10-28', self.etf().weights),
                    active_share.EtfTop10('00981A', '2026-10-23', {'1000': float('nan')})]:
            with self.subTest(etf=etf):
                self.assertEqual(self.screen(etf).status, active_share.UNVERIFIED)
        self.assertEqual(active_share.check(self.weights, {}, [], '2026-10-27').status,
                         active_share.UNVERIFIED)

    def test_planner_check_uses_actual_shares_and_post_cost_nav(self):
        rules = load_rules()
        inp = SimpleNamespace(rules=rules)
        book = SettledBook('2026-10-23', {}, 100000., marks={})
        plan = Plan({'2330.TW': 1000}, {'2330.TW': 1000}, 'OK', '', {})
        with patch('production.engine._check_active_share', return_value={'status': active_share.PASS}) as check:
            report = engine._check_planned_active_share(plan, book, pd.Series({'2330.TW': 50.}), inp)
        expected = 50000. / (100000. - 50000. * rules.commission_buy)
        self.assertAlmostEqual(check.call_args.args[0]['2330.TW'], expected)
        self.assertEqual(report['evaluation_basis'], 'lot_rounded_book_at_previous_close_after_costs')


if __name__ == '__main__':
    unittest.main()
