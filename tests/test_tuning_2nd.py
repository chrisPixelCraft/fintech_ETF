"""Second-round gate and explicit scenario boundary contracts."""
import copy
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from src import tuning_2nd as t
from src.tuning_features import FeatureCache
from scripts.audit_tuning_2nd import measured_hard_mask


def equity():
    return pd.DataFrame([dict(date='2025-01-02',economic_nav=1e9,nav=1e9,cash=1e8,cash_ratio=.1,holdings=25,
        active_cap_breaches='',overdue_passive_caps='',violations='',executed_plan='HOLD',traded_notional=0,costs=0,turnover=0)])


class SecondRoundTests(unittest.TestCase):
    def test_gate_rejects_breach_even_when_return_is_highest(self):
        good=dict(status='COMPLETE',candidate_id='low',measured_hard_breach_days=0,economic_total_return=.1,economic_max_drawdown=.05)
        bad={**good,'candidate_id':'high','measured_hard_breach_days':1,'economic_total_return':9.}
        self.assertEqual(t.choose([good,bad])['candidate_id'],'low')
        self.assertIsNone(t.choose([bad]))
        self.assertIsNone(t.choose([]))

    def test_count_and_whitelist_are_hard_rules(self):
        for change in [dict(holdings=31),dict(holdings=19),dict(violations='NON_WHITELIST:9999.TW'),dict(cash=-1)]:
            frame=equity()
            for k,v in change.items():frame.loc[0,k]=v
            self.assertEqual(t.metrics(frame)['measured_hard_breach_days'],1)
            self.assertTrue(measured_hard_mask(frame,dict(min_count=20,max_count=30)).all())

    def test_unknown_active_share_is_not_official_pass(self):
        m=t.metrics(equity());self.assertTrue(m['observed_rule_eligible'])
        self.assertIn('UNKNOWN',m['official_compliance']);self.assertIn('BLOCK_SUBMISSION',m['official_compliance'])

    def test_economic_volatility_does_not_overwrite_book_metric_name(self):
        m=t.metrics(equity());self.assertIn('economic_sharpe_zero_rf',m)
        self.assertNotIn('sharpe_zero_rf',m);self.assertNotIn('annualized_volatility',m)

    def test_universe_override_is_explicit_and_does_not_mutate_known_at(self):
        # Obtain only isolated namespaces; no market cache evaluation needed.
        class Cache:
            def frame(self,*args):raise AssertionError('Universe guard should precede features')
        dates=pd.to_datetime(['2024-12-31','2025-01-02'])
        daily=pd.DataFrame(dict(date=dates,symbol='2330.TW',open=100.,high=100.,low=100.,close=100.,volume=1e6,turnover=1e8,dividend=0.,split=1.))
        universe=pd.DataFrame([dict(symbol='2330.TW',known_at='2026-09-18T00:00:00+08:00')])
        before=universe.copy(deep=True)
        cfg=dict(start='2025-01-01',end='2025-01-02',initial_cash=1e9,research_shadow=True)
        for track in t.TRACKS:
            engine=t.adapter(dict(cache=Cache(),track=track),use_new_planner=False)
            with self.assertRaisesRegex(ValueError,'LOOKAHEAD_UNIVERSE'):
                engine.module.run_v2(daily,universe,cfg)
            if track=='official_ex_post':
                with self.assertRaisesRegex(AssertionError,'Universe guard should precede features'):
                    engine.module.run_v2(daily,universe,{**cfg,'ex_post_fixed_universe':True})
        pd.testing.assert_frame_equal(universe,before)


if __name__=='__main__':unittest.main()
