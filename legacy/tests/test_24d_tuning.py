import unittest
import pandas as pd
from scripts.tune_24d import BASE, SPACE, spec, phase_specs, screen_registry, rank, eligible
from scripts.run_24d import interval_split, load_study

class TuningProtocolTests(unittest.TestCase):
    def test_new_horizon_extremes_cannot_use_future_prices(self):
        from tests.test_strategy_24d import fixture
        from src.strategy_24d import build_config, build_features
        daily,_,dates=fixture(count=2)
        cutoff=dates[3]
        for p in [dict(return_short=3,return_long=15,ema_fast=5,ema_slow=15,macd_fast=5,macd_slow=13,macd_signal=4),
                  dict(return_short=20,return_long=60,ema_fast=20,ema_slow=50,macd_fast=16,macd_slow=35,macd_signal=9)]:
            cfg=build_config(p)
            expected=build_features(daily[daily.date.le(cutoff)],cfg)
            changed=daily.copy()
            changed.loc[changed.date.gt(cutoff),['open','high','low','close','volume']]*=3
            actual=build_features(changed,cfg)
            pd.testing.assert_frame_equal(expected,actual[actual.date.le(cutoff)].reset_index(drop=True),check_exact=True)

    def test_full_structural_grid_and_conditional_deduplication(self):
        a=phase_specs('A',[spec()]); c=phase_specs('C',[spec()])
        self.assertEqual(len(a),20);self.assertEqual(len(c),19)
        self.assertEqual(sum(s['params']['max_replacements_per_day']==0 for s in c),1)
        self.assertEqual(spec({'replacement_margin':0.})['candidate_id'],spec()['candidate_id'])

    def test_search_grids_respect_bounds_and_weights(self):
        from src.strategy_24d import build_config
        for phase in 'ABCDEFG':
            for trial in phase_specs(phase,[spec()]*5):
                p=trial['params'];self.assertLess(p['return_short'],p['return_long'])
                for key,values in SPACE.items():
                    self.assertGreaterEqual(p[key],min(values));self.assertLessEqual(p[key],max(values))
                cfg=build_config(p)
                self.assertAlmostEqual(sum(cfg['score_weights'].values()),1.)
                self.assertFalse(cfg['use_4h']);self.assertEqual(cfg['commission'],.001425)
                self.assertEqual(cfg['sell_tax'],.003);self.assertEqual(cfg['initial_cash'],1e9)
        self.assertEqual(len(phase_specs('D',[spec()])),42)
        self.assertEqual(phase_specs('G',[spec()]),phase_specs('G',[spec()]))

    def test_split_purges_crossing_episodes(self):
        study=load_study('config/24d_tuning_study.json')
        self.assertEqual(interval_split('2018-12-01','2019-01-08',study),'purged')
        self.assertEqual(interval_split('2019-01-02','2019-02-01',study),'validation')
        self.assertEqual(interval_split('2022-12-01','2023-01-05',study),'purged')
        self.assertEqual(interval_split('2023-01-03','2023-02-08',study),'holdout')

    def test_screen_is_shared_chronological_and_uses_endpoints(self):
        d=pd.DataFrame({'episode_id':range(107)})
        s=screen_registry(d)
        self.assertEqual(len(s),12);self.assertEqual(s.episode_id.iloc[0],0)
        self.assertEqual(s.episode_id.iloc[-1],106);self.assertTrue(s.episode_id.is_monotonic_increasing)

    def test_p10_breaks_median_p25_tie_and_failed_candidate_cannot_win(self):
        candidates=[spec(),spec({'target_count':22}),spec({'target_count':25})]
        # Same median/P25; second has a better P10, despite a larger MDD.
        rows=[]
        for i,returns in enumerate([[-.2,0,.1,.2,.3],[-.1,0,.1,.2,.3],[1,1,1,1,1]]):
            for r in returns:
                rows.append(dict(candidate_id=candidates[i]['candidate_id'],measured_pass=i!=2,
                    complete_period=True,episode_return=r,episode_max_drawdown=.1+i*.01,episode_turnover=.2))
        ordered=rank(pd.DataFrame(rows),candidates)
        self.assertEqual(ordered.candidate_id.iloc[0],candidates[1]['candidate_id'])
        self.assertEqual(len(eligible(ordered)),2)
        self.assertEqual(ordered.iloc[-1].failed,5)


class TuningVerifierTests(unittest.TestCase):
    def test_failed_episodes_remain_in_denominator_and_partial_returns_not_metrics(self):
        from scripts.run_24d import aggregate
        from scripts.verify_24d_tuning import check_summary
        rows=pd.DataFrame([dict(candidate_id='x',measured_pass=True,complete_period=True,
            episode_return=.10,episode_max_drawdown=.02,episode_turnover=.5,days_to_20_holdings=1),
            dict(candidate_id='x',measured_pass=False,complete_period=False,
            episode_return=None,episode_max_drawdown=.99,episode_turnover=20.,days_to_20_holdings=None)])
        summary=aggregate(rows);check_summary(rows,summary)
        self.assertEqual(summary.iloc[0].passed,1)
        self.assertEqual(summary.iloc[0].compliance_pass_rate,.5)
        self.assertEqual(summary.iloc[0].median_24d_return,.10)
        corrupt=summary.copy();corrupt.loc[0,'attempted']=1
        with self.assertRaisesRegex(ValueError,'Summary mismatch'):
            check_summary(rows,corrupt)
        corrupt=summary.copy();corrupt.loc[0,'median_24d_return']=.11
        with self.assertRaisesRegex(ValueError,'Summary mismatch'):
            check_summary(rows,corrupt)

if __name__=='__main__':unittest.main()
