"""Independent guards for full-v2 study selection and prefunded repair orders."""
import unittest
from decimal import Decimal,ROUND_FLOOR
import pandas as pd
from scripts.audit_official_deep import prefunded,qualifies,winner,REPAIR
from src.official_deep_tuning import representable_weight


class FullStudyAuditGuards(unittest.TestCase):
    def orders(self,reason=REPAIR):
        return pd.DataFrame(dict(symbol=['1000.TW','2000.TW'],shares=[-1000,1000],sizing_price=[100.,100.],reason=reason))

    def config(self):return dict(price_buffer=1.1,commission=.001425)

    def row(self,cid='x',ret=1.,**changes):
        return dict(candidate_id=cid,status='COMPLETE',total_return=ret,max_drawdown=.2,turnover_two_way=10.,
                    measured_hard_breach_days=0,no_valid_plan_days=0,unfilled_orders=0,**changes)

    def test_funded_repair_accepts_distinct_prefunded_symbols(self):
        self.assertTrue(prefunded(self.orders(),self.config(),120000.,{'1000.TW':1000}))

    def test_repair_buy_cannot_consume_same_day_sell_proceeds(self):
        with self.assertRaises(AssertionError):prefunded(self.orders(),self.config(),110000.,{'1000.TW':1000})

    def test_repair_cannot_oversell_existing_inventory(self):
        with self.assertRaises(AssertionError):prefunded(self.orders(),self.config(),120000.,{'1000.TW':500})

    def test_mixed_unknown_reason_and_same_security_are_rejected(self):
        with self.assertRaises(AssertionError):prefunded(self.orders('renamed_hold'),self.config(),120000.,{'1000.TW':1000})
        orders=self.orders();orders.symbol='1000.TW'
        with self.assertRaises(AssertionError):prefunded(orders,self.config(),120000.,{'1000.TW':1000})

    def test_each_gate_disqualifies_higher_return_without_fallback(self):
        for field in ['measured_hard_breach_days','no_valid_plan_days','unfilled_orders']:
            high=self.row('high',10.);high[field]=1
            self.assertFalse(qualifies(high));self.assertIsNone(winner([high]))
            self.assertEqual(winner([high,self.row('low',.1)])['candidate_id'],'low')

    def test_book_return_then_mdd_then_turnover_then_id(self):
        a=self.row('a',2.);b=self.row('b',1.);b['economic_total_return']=100.
        self.assertEqual(winner([b,a])['candidate_id'],'a')
        b['total_return']=2.;b['max_drawdown']=.1
        self.assertEqual(winner([a,b])['candidate_id'],'b')
        a['max_drawdown']=.1;a['turnover_two_way']=9.
        self.assertEqual(winner([a,b])['candidate_id'],'a')
        b['turnover_two_way']=9.
        self.assertEqual(winner([b,a])['candidate_id'],'a')

    def test_only_submicroshare_boundary_noise_is_normalized(self):
        q=999.9999999990687;p=24.;n=3514043008.4873714
        weight=representable_weight(q,p,n,.1)
        result=(Decimal(str(weight))*Decimal(str(n))/Decimal(str(p))/1000).to_integral_value(rounding=ROUND_FLOOR)*1000
        self.assertEqual(result,1000)
        with self.assertRaises(ValueError):representable_weight(1000.001,p,n,.1)


if __name__=='__main__':unittest.main()
