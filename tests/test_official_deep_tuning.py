import copy
import unittest
import pandas as pd
from src.official_deep_tuning import OfficialPlanner, eligible, validate_params, representable_weight
from src.tuning_a_deep import from_old
from pathlib import Path
import json

class ReplanningContract(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]
        self.cfg=json.loads((root/'outputs/official_v2_reaudit/official_ex_post/final/A_dplan_guard/config.json').read_text())
        self.rows=pd.DataFrame([dict(date='2026-09-21',symbol=str(2000+i),close=10.,score=1-i/100,
            entry_ok=True,exit=False) for i in range(30)])
        self.holdings={str(2000+i):4_000_000. for i in range(20)}
    def test_failed_replacement_can_be_valid_hold(self):
        p=OfficialPlanner();p.base.make_plan_v2=lambda *a:({},'INFEASIBLE_TRANSITION',list(self.holdings))
        p.base._buy_plan=lambda *a,**k:{}
        p.base._prefunded_mixed_plan=lambda *a,**k:{}
        orders,reason,_=p(self.rows,self.holdings,150_000_000.,950_000_000.,self.cfg)
        self.assertEqual(orders,{})
        self.assertTrue(reason.startswith('HOLD_REVALIDATED'))
        self.assertGreater(p.audit[-1]['attempts'],1)
    def test_cash_violation_cannot_be_renamed_hold(self):
        p=OfficialPlanner();p.base.make_plan_v2=lambda *a:({},'INFEASIBLE_TRANSITION',[])
        p.base._buy_plan=lambda *a,**k:{}
        p.base._prefunded_mixed_plan=lambda *a,**k:{}
        _,reason,_=p(self.rows,self.holdings,300_000_000.,1_100_000_000.,self.cfg)
        self.assertTrue(reason.startswith('INFEASIBLE'))
    def test_missing_quote_blocks_even_hold(self):
        p=OfficialPlanner();_,reason,_=p(self.rows.iloc[1:],self.holdings,150_000_000.,950_000_000.,self.cfg)
        self.assertIn('MISSING_HELD_QUOTE',reason)
    def test_odd_trade_cannot_escape(self):
        p=OfficialPlanner();h={**self.holdings,'2000':4_000_001.}
        p.base.make_plan_v2=lambda *a:({'2000':1000},'BUY',list(h))
        with self.assertRaisesRegex(ValueError,'odd'):p(self.rows,h,150_000_000.,950_000_000.,self.cfg)
    def test_eligibility_is_hard_gate(self):
        base=dict(status='COMPLETE',measured_hard_breach_days=0,no_valid_plan_days=0,unfilled_orders=0)
        self.assertTrue(eligible(base))
        for k in ['measured_hard_breach_days','no_valid_plan_days','unfilled_orders']:
            self.assertFalse(eligible({**base,k:1}))
    def test_cash_tuning_does_not_relax_hard_limit(self):
        p={**from_old(self.cfg),'cash_guard_ratio':.18};validate_params(p)
        with self.assertRaises(ValueError):validate_params({**p,'cash_guard_ratio':.25})
    def test_corporate_float_residue_is_not_a_real_odd_lot(self):
        from decimal import Decimal, ROUND_FLOOR
        nav=3514043008.4873714
        w=representable_weight(999.9999999990687,24.,nav,.1)
        target=(Decimal(str(w))*Decimal(str(nav))/Decimal('24')/1000).to_integral_value(rounding=ROUND_FLOOR)*1000
        self.assertEqual(target,1000)
        for odd in [999.,1000.001,1000.1,1001.]:
            with self.assertRaises(ValueError):representable_weight(odd,24.,nav,.1)

if __name__=='__main__':unittest.main()
