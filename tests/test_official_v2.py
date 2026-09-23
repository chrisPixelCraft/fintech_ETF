"""Independent literal D-Plan and corporate-action edge cases."""
from pathlib import Path
import json
import unittest
import numpy as np
import pandas as pd
from scripts.audit_official_v2 import derive_order,nav_metrics,primary_months,document_inventory
from src.official_v2_review import isolated_planner,representable_weight

ROOT=Path(__file__).resolve().parents[1]


class OfficialFormulaTests(unittest.TestCase):
    def test_literal_floor_is_not_round_nearest(self):
        target,quantity=derive_order(.059,10_000_000,100,1000)
        self.assertEqual(target,5000);self.assertEqual(quantity,4000)

    def test_boundary_diagnostic_depends_on_numeric_backend(self):
        weight,nav,price=.045705001258850096,1e9,55.400001525878906
        decimal_target,_=derive_order(weight,nav,price,0)
        float_target=np.floor(weight*nav/price/1000)*1000
        self.assertEqual(decimal_target,824000)
        self.assertEqual(float_target,825000)
        interior=representable_weight(825000,price,nav,.1)
        self.assertEqual(derive_order(interior,nav,price,0)[0],825000)
        self.assertEqual(np.floor(interior*nav/price/1000)*1000,825000)

    def test_odd_held_quantity_cannot_produce_whole_lot_order(self):
        for weight in [0,.01,.05,.1]:
            target,quantity=derive_order(weight,10_000_000,100,2150)
            self.assertEqual(target%1000,0);self.assertNotEqual(quantity%1000,0)

    def test_serialization_uses_same_bin_under_cap(self):
        for target in (1000,17000,999000):
            for price in (13.15,100.1,1045.):
                nav=target*price/.073
                weight=representable_weight(target,price,nav,.1)
                self.assertLessEqual(weight,.1)
                self.assertEqual(derive_order(weight,nav,price,0)[0],target)

    def test_midpoint_cap_clips_inside_target_bin(self):
        weight=representable_weight(1000,100,1_050_000,.1)
        self.assertLessEqual(weight,.1)
        self.assertEqual(derive_order(weight,1_050_000,100,0)[0],1000)

    def test_nonrepresentable_target_is_rejected(self):
        with self.assertRaises(ValueError):representable_weight(2150,100,1_000_000,.25)
        with self.assertRaises(ValueError):representable_weight(1000,100,1_000_000,.1)

    def test_sell_all_zero_weight(self):
        self.assertEqual(representable_weight(0,100,1_000_000,.1),0)
        self.assertEqual(derive_order(0,1_000_000,100,3000)[1],-3000)

    def test_split_affects_inventory_not_fixed_pending_order(self):
        held,pending,split,cash_per_old=2000,1000,1.1,10
        self.assertEqual(held*split+pending,3200)
        self.assertEqual(held*cash_per_old,20000)
        self.assertNotEqual((held+pending)*split,held*split+pending)

    def test_book_mdd_differs_from_receivable_adjusted_nav(self):
        self.assertAlmostEqual(nav_metrics([90,100],100)['max_drawdown'],.1)
        self.assertEqual(nav_metrics([100,100],100)['max_drawdown'],0)

    def test_terminal_payout_changes_book_month_not_economic(self):
        eq=pd.DataFrame(dict(date=['2025-01-31','2025-02-28'],nav=[90,100],economic_nav=[100,100]))
        rows=primary_months(eq,100)
        self.assertAlmostEqual(rows[1]['book_return'],1/9)
        self.assertEqual(rows[1]['economic_return'],0)

    def test_supplied_inventory_and_etf_count(self):
        inventory=document_inventory()
        self.assertEqual(len(inventory['files']),10)
        self.assertEqual(len(inventory['etfs']),30)
        self.assertEqual(len(inventory['official_tickers']),150)


class OddHoldPlannerTests(unittest.TestCase):
    def setUp(self):
        self.module=isolated_planner()
        cfg=json.loads((ROOT/'tests/fixtures/anchor_historical_pit.json').read_text())
        self.config=cfg
        self.names=[str(1000+i)+'.TW' for i in range(35)]
        self.ranked=pd.DataFrame(dict(symbol=self.names,close=100.,score=np.arange(35)/35,entry_ok=True,exit=False))
        self.holdings={s:420000. for s in self.names[:20]}
        self.odd=self.names[0];self.holdings[self.odd]=421150.
        self.nav=1e9;self.cash=self.nav-sum(self.holdings.values())*100

    def test_forced_exit_and_replacement_never_trade_odd_name(self):
        for forced in (False,True):
            frame=self.ranked.copy();frame.loc[0,'exit']=forced
            for cash in (80e6,self.cash,230e6):
                with self.subTest(forced=forced,cash=cash):
                    before=frame.copy(deep=True)
                    orders,reason,selected=self.module.make_plan_v2(frame,self.holdings,cash,self.nav,self.config)
                    self.assertNotIn(self.odd,orders)
                    pd.testing.assert_frame_equal(frame,before)
                    for q in orders.values():self.assertEqual(q%1000,0)

    def test_odd_name_cap_violation_remains_visible(self):
        held=dict(self.holdings);held[self.odd]=1_200_150
        orders,reason,_=self.module.make_plan_v2(self.ranked,held,80e6,self.nav,self.config)
        self.assertNotIn(self.odd,orders)
        failures=self.module.stress_violations(orders,held,80e6,self.ranked,self.config,allow_mixed=True)
        self.assertIn('WEIGHT_CAP:'+self.odd,failures)
        self.assertTrue('INFEASIBLE' in reason or not orders)


if __name__=='__main__':unittest.main()
