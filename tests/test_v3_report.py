"""Report regressions: empty baseline intervention and independent metric units."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

import numpy as np
import pandas as pd

from scripts.report_v3 import intervention, pct, pp
from src.v3_study import evaluation_tables


ROOT = Path(__file__).resolve().parents[1]


def equity(values, dates):
    return pd.DataFrame(dict(date=dates, economic_nav=np.asarray(values)*1e6,
        cash_ratio=.1, cash=1e8, active_cap_breaches='', overdue_passive_caps='',
        executed_plan='HOLD', violations='', traded_notional=0., costs=0., turnover=0.))


class V3ReportTests(unittest.TestCase):
    def test_empty_a_intervention_and_last_signal_exclusion(self):
        with TemporaryDirectory(prefix='v3_report_test_') as tmp:
            folder = Path(tmp)
            for model in ('A', 'B', 'C', 'D'):
                path = folder/'final'/model; path.mkdir(parents=True)
                signals = []
                for date, regime in [('2024-12-31','NORMAL'), ('2025-01-02','WEAK'),
                                     ('2026-09-21','WEAK')]:
                    apply = model != 'A' and regime == 'WEAK'
                    for i in range(3):
                        signals.append(dict(date=date, symbol=f'S{i}', v3_regime=regime,
                            v3_rank_applied=apply, v3_factor_valid=i<2, entry_ok=True,
                            audit_base_score=1-i*.1, score=i*.1 if apply else 1-i*.1))
                pd.DataFrame(signals).to_csv(path/'signals.csv',index=False)
                pd.DataFrame([dict(signal_date='2025-01-02',symbol='S2',shares=1000),
                              dict(signal_date='2026-09-21',symbol='S2',shares=1000)]).to_csv(path/'orders.csv',index=False)
            result = intervention(folder,pd.DataFrame()).set_index('model')
            self.assertEqual(result.loc['A','applied_days'],0)
            self.assertEqual(result.loc['A','applied_days_with_fewer_than_20_valid_entries'],0)
            self.assertEqual(result.loc['A','weak_invalid_factor_buy_orders'],0)
            self.assertTrue(result.executed_signal_days.eq(2).all())
            self.assertTrue(result.weak_decision_days.eq(1).all())
            for model in ('B','C','D'):
                self.assertEqual(result.loc[model,'applied_days'],1)
                self.assertEqual(result.loc[model,'changed_eligible_top20_days'],1)
                self.assertEqual(result.loc[model,'weak_invalid_factor_buy_orders'],1)

    def test_six_model_monthly_labels_returns_and_percentage_point_units(self):
        dates = ['2025-01-02','2025-01-31','2025-02-03','2025-02-28','2026-09-01','2026-09-21']
        curves = {'A':[980,970,990,1040,1060,1070], 'B':[985,975,998,1045,1065,1075],
                  'C':[979,969,991,1041,1061,1071], 'D':[984,974,994,1044,1064,1074],
                  'v1_matched':[970,960,980,1030,1050,1060], '0050':[925,954,970,990,1000,1015]}
        results = {}
        for model, values in curves.items():
            nav = np.r_[1000.,values]
            results[model] = dict(equity=equity(values,dates), metrics=dict(
                economic_total_return=nav[-1]/nav[0]-1,
                economic_max_drawdown=-np.min(nav/np.maximum.accumulate(nav)-1),
                measured_hard_breach_days=0,transaction_costs=0.,turnover_two_way=0.))
        daily_dates = ['2024-12-31',*dates]
        close = np.array([100.,90.,46.,47.,48.,51.,50.])
        daily = pd.DataFrame(dict(date=daily_dates,symbol='0050.TW',open=close,high=close,
            low=close,close=close,volume=1000.,split=[1,1,2,1,1,1,1],dividend=[0,0,2,0,1,0,0]))
        monthly, summary = evaluation_tables(results,daily)
        self.assertEqual(len(monthly),18)
        self.assertEqual(set(summary.model),set(curves))
        # Independent old-share event arithmetic: Jan31 instrument=(46*2+2),
        # Feb28 month growth=(48+1)/46. No strategy costed ledger in these labels.
        expected_instrument = {'2025-01':94/100-1,'2025-02':49/46-1,'2026-09':50/48-1}
        np.testing.assert_allclose(monthly.instrument_return,monthly.month.map(expected_instrument),atol=1e-12,rtol=0)
        self.assertTrue(monthly.weak_month.eq(monthly.month.eq('2025-01')).all())
        self.assertEqual(int(monthly.partial_month.sum()),6)
        a_months = dict(zip(['2025-01','2025-02','2026-09'],[970/1000-1,1040/970-1,1070/1040-1]))
        b_months = dict(zip(['2025-01','2025-02','2026-09'],[954/1000-1,990/954-1,1015/990-1]))
        for model, values in curves.items():
            previous = 1000.
            for i, month in enumerate(('2025-01','2025-02','2026-09')):
                row = monthly[monthly.model.eq(model)&monthly.month.eq(month)].iloc[0]
                local = np.r_[previous,values[2*i:2*i+2]]
                expected = local[-1]/local[0]-1
                self.assertAlmostEqual(row.economic_return,expected,places=12)
                self.assertAlmostEqual(row.economic_max_drawdown,-np.min(local/np.maximum.accumulate(local)-1),places=12)
                self.assertAlmostEqual(row.difference_vs_A,expected-a_months[month],places=12)
                self.assertAlmostEqual(row.excess_vs_0050,expected-b_months[month],places=12)
                previous = local[-1]
        january_b = monthly[monthly.model.eq('B')&monthly.month.eq('2025-01')].iloc[0]
        self.assertEqual(pct(january_b.economic_return),'-2.50%')
        self.assertEqual(pp(january_b.difference_vs_A),'+0.50')
        self.assertFalse(summary.set_index('model').loc['B','weak_positive_months'])
        self.assertEqual(summary.set_index('model').loc['B','weak_outperform_0050_months'],1)

    def test_existing_raw_sources_match_instrument_month_labels(self):
        """Read-only validation on both frozen real sources; no new backtest."""
        sources = {'historical_pit':'data/v2/market_daily.csv',
                   'official_ex_post':'data/tuning_2nd/official_universe/processed/daily.csv'}
        for track, relative in sources.items():
            with self.subTest(track=track):
                folder = ROOT/'outputs/tuning_report_2nd_try'/track/'final'
                if not (ROOT/relative).exists() or not folder.exists():
                    self.skipTest('Frozen local source snapshot unavailable')
                results = {model:dict(equity=pd.read_csv(folder/model/'equity.csv'),
                    metrics=json.loads((folder/model/'metrics.json').read_text())) for model in ('A','0050')}
                daily = pd.read_csv(ROOT/relative)
                monthly,_ = evaluation_tables(results,daily)
                prices = daily[daily.symbol.eq('0050.TW')].sort_values('date')
                marks = {}; value = previous_close = None
                for r in prices.itertuples():
                    value = r.close if previous_close is None else value*(r.close*r.split+r.dividend)/previous_close
                    if r.volume>0:marks[r.date] = value
                    previous_close = r.close
                calendar = sorted(daily.date.unique()); filled = {}; last = None
                for day in calendar:
                    last = marks.get(day,last);filled[day] = last
                labels = {}
                for month,g in results['A']['equity'].groupby(results['A']['equity'].date.str[:7]):
                    first,end = g.date.iloc[0],g.date.iloc[-1]
                    before = calendar[calendar.index(first)-1]
                    labels[month] = filled[end]/filled[before]-1
                np.testing.assert_allclose(monthly.instrument_return,monthly.month.map(labels),rtol=0,atol=1e-12)
                self.assertEqual(monthly.month.nunique(),21)
                self.assertTrue(monthly.weak_month.eq(monthly.instrument_return.lt(0)).all())
                self.assertEqual(monthly[monthly.partial_month].month.unique().tolist(),['2026-09'])


if __name__ == '__main__':
    unittest.main()
