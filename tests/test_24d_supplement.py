"""Post-freeze additions cannot fabricate full-period or successful-only evidence."""
import unittest
import numpy as np
import pandas as pd

from scripts.supplement_24d import (cold_start_metrics, extended_summary, registry,
                                    requested_split, run_long_horizon)
from scripts.audit_24d import _audit_ledger
from src.strategy_24d import build_config
from tests.test_strategy_24d import fixture


class SupplementTests(unittest.TestCase):
    def test_monthly_extension_and_full_window_boundaries(self):
        calendar=pd.bdate_range('2009-01-01','2026-09-23')
        episodes=registry(calendar)
        self.assertEqual(len(episodes),200)
        self.assertEqual(episodes.iloc[-1].start,'2026-08-03')
        rolling=registry(calendar,'2025-01-01','2026-08-31','rolling')
        dates=calendar.strftime('%Y-%m-%d').tolist()
        for row in rolling.itertuples():
            self.assertEqual(dates.index(row.end)-dates.index(row.start)+1,24)
            self.assertLess(row.prior_session_date,row.start)

    def test_continuous_book_uses_requested_length_and_preserves_disqualification(self):
        daily,universe,dates=fixture(count=10)
        more=sorted(pd.to_datetime(daily.date).unique())[220:255]
        result=run_long_horizon(daily,universe,build_config(),more)
        self.assertEqual(result['metrics']['requested_sessions'],35)
        self.assertEqual(result['metrics']['observed_sessions'],3)
        self.assertIsNone(result['metrics']['episode_return'])
        self.assertTrue(result['metrics']['disqualified'])
        checked=_audit_ledger(result,dict(daily=daily,universe=universe,session_dates=sorted(daily.date.unique())))
        self.assertTrue(checked['disqualified'])

    def test_continuous_complete_book_keeps_all_requested_sessions(self):
        daily,universe,_=fixture()
        dates=sorted(pd.to_datetime(daily.date).unique())[220:255]
        result=run_long_horizon(daily,universe,build_config(),dates)
        self.assertEqual(result['metrics']['requested_sessions'],35)
        self.assertEqual(result['metrics']['observed_sessions'],35)
        self.assertTrue(result['metrics']['complete_period'])
        self.assertAlmostEqual(result['metrics']['episode_return'],result['equity'].economic_nav.iloc[-1]/1e9-1)
        _audit_ledger(result,dict(daily=daily,universe=universe,session_dates=sorted(daily.date.unique())))

    def test_valid_portfolio_latency_checks_more_than_holding_count(self):
        equity=pd.DataFrame(dict(date=['a','b','c'],holdings=[20,20,20],cash_ratio=[.3,.1,.1],
            violations=['CASH_GE_25_PERCENT','',''],stale_count=[0,1,0],odd_residual_names=[0,0,0]))
        result=cold_start_metrics(equity)
        self.assertEqual(result['days_to_valid_portfolio'],3)
        self.assertEqual(result['day_1_holding_count'],20)
        self.assertIsNone(result['day_5_holding_count'])
        self.assertAlmostEqual(result['day_3_invested_fraction'],.9)

    def test_distribution_additions_keep_failed_denominator(self):
        rows=pd.DataFrame(dict(candidate_id=['x']*5,measured_pass=[True]*4+[False],
            complete_period=[True]*4+[False],episode_return=[.01,.02,.03,1.,None],
            episode_max_drawdown=[.1]*5,episode_turnover=[2.]*5,trades=[20]*5,
            days_to_valid_portfolio=[1,2,3,1,None]))
        result=extended_summary(rows).iloc[0]
        self.assertEqual(result.attempted,5)
        self.assertEqual(result.valid_episode_count,4)
        self.assertEqual(result.valid_portfolio_unreached_count,1)
        self.assertAlmostEqual(result.mean_return_excluding_top_5pct,.02)
        self.assertAlmostEqual(result.median_return_without_tail_extremes,.025)

    def test_requested_split_is_descriptive_and_purges_full_interval(self):
        self.assertEqual(requested_split('2018-12-03','2019-01-04'),'purged')
        self.assertEqual(requested_split('2019-01-02','2019-02-05'),'validation')
        self.assertEqual(requested_split('2025-01-02','2025-02-05'),'recent')


if __name__=='__main__':
    unittest.main()
