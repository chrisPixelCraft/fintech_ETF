import unittest
import pandas as pd
from scripts.verify_24d_expansion import check_measured,check_adoption,ordered
from src.strategy_24d import build_config

class ExpansionEvidenceTests(unittest.TestCase):
    def passing_row(self):
        row=dict(measured_pass=True,complete_period=True,episode_return=.1,requested_sessions=24,observed_sessions=24,disqualified=False)
        row.update({k:0 for k in ['measured_hard_breach_days','no_valid_plan_days','unfilled_orders','simulated_warning_days',
            'stale_held_price_days','hold_without_envelope_days','execution_price_bound_breaches','raw_rule_breach_days']})
        return row

    def test_pass_flag_cannot_override_any_strict_daily_violation(self):
        good=self.passing_row();check_measured(pd.DataFrame([good]))
        for key in [k for k in good if k.endswith('days')]+['unfilled_orders','execution_price_bound_breaches']:
            with self.subTest(key=key),self.assertRaises(ValueError):
                check_measured(pd.DataFrame([{**good,key:1}]))
        for change in [dict(observed_sessions=23),dict(disqualified=True),dict(episode_return=float('inf'))]:
            with self.assertRaises(ValueError):check_measured(pd.DataFrame([{**good,**change}]))

    def test_partial_return_cannot_be_terminal_return(self):
        bad={**self.passing_row(),'measured_pass':False,'complete_period':False}
        with self.assertRaisesRegex(ValueError,'Incomplete episode'):check_measured(pd.DataFrame([bad]))
        bad['episode_return']=None;check_measured(pd.DataFrame([bad]))

    def test_adoption_requires_full_development_and_validation_compliance(self):
        base=dict(compliance_pass_rate=1.,valid_episode_rate=1.,failed=0,median_24d_return=.05,p25_24d_return=.01)
        summary=pd.DataFrame([{**base,'candidate_id':'x0352_daily_baseline'},{**base,'candidate_id':'challenger'}])
        freeze=dict(candidate_id='challenger',status='SELECTED_RESEARCH_ONLY',formal_status='BLOCK_SUBMISSION',prior_holdout_exposure=True)
        stability=dict(results=[dict(candidate_id='challenger',stable=True)])
        check_adoption(freeze,summary,summary,stability)
        for column,value in [('compliance_pass_rate',.99),('valid_episode_rate',.99),('failed',1)]:
            bad=summary.copy();bad.loc[1,column]=value
            for ds,vs in [(bad,summary),(summary,bad)]:
                with self.assertRaisesRegex(ValueError,'Ineligible'):check_adoption(freeze,ds,vs,stability)
        with self.assertRaisesRegex(ValueError,'stability'):check_adoption(freeze,summary,summary,{'results':[]})
        with self.assertRaisesRegex(ValueError,'unseen'):check_adoption({**freeze,'prior_holdout_exposure':False},summary,summary,stability)

    def test_null_selection_cannot_hide_qualified_candidate(self):
        common=dict(compliance_pass_rate=1.,valid_episode_rate=1.,failed=0,median_24d_return=.05,p25_24d_return=.01)
        summary=pd.DataFrame([{**common,'candidate_id':'x0352_daily_baseline'},{**common,'candidate_id':'challenger'}])
        freeze=dict(candidate_id=None,status='NO_ELIGIBLE_CANDIDATE',formal_status='BLOCK_SUBMISSION',prior_holdout_exposure=True)
        with self.assertRaisesRegex(ValueError,'stability evidence'):
            check_adoption(freeze,summary,summary,{'results':[]})
        with self.assertRaisesRegex(ValueError,'Final selection omits'):
            check_adoption(freeze,summary,summary,{'results':[dict(candidate_id='challenger',stable=True)]})
        check_adoption(freeze,summary,summary,{'results':[dict(candidate_id='challenger',stable=False)]})
        # A previous reference is descriptive, not a newly eligible expansion candidate.
        check_adoption(freeze,summary,summary,{'results':[]},candidate_ids=set())

    def test_recompute_rank_p10_and_baseline_distance(self):
        params=build_config()['full_tuning_params'];parameters={'a':params,'b':{**params,'target_count':22}}
        common=dict(compliance_pass_rate=1,valid_episode_rate=1,median_24d_return=.1,p25_24d_return=.05,
            median_mdd=.1,mean_24d_return=.12,median_turnover=.8,baseline_distance=999)
        summary=pd.DataFrame([{**common,'candidate_id':'a','p10_24d_return':-.05},{**common,'candidate_id':'b','p10_24d_return':-.04}])
        result=ordered(summary,parameters)
        self.assertEqual(result.candidate_id.tolist(),['b','a'])
        self.assertEqual(result.baseline_distance.tolist(),[1,0])
        summary['p10_24d_return']=0
        self.assertEqual(ordered(summary,parameters).candidate_id.tolist(),['a','b'])


class ExpansionCandidateTests(unittest.TestCase):
    def test_fixed_seed_new_candidates_and_bounds(self):
        import json
        from scripts.expand_24d import generate_candidates,PARENT
        from scripts.tune_24d import SPACE,EMA,MACD
        first,refs,membership=generate_candidates();second,refs2,membership2=generate_candidates()
        self.assertEqual(first,second);self.assertEqual(refs,refs2)
        pd.testing.assert_frame_equal(membership,membership2)
        prior={r['candidate_id'] for r in json.loads((PARENT/'candidates.json').read_text())}
        ids={r['candidate_id'] for r in first}
        self.assertEqual(len(ids),328);self.assertFalse(ids & prior)
        self.assertEqual(membership.family.value_counts().to_dict(),{'uniform':128,'availability':128,'joint_local':72})
        self.assertEqual(len(refs),2);self.assertTrue({r['candidate_id'] for r in refs}<=prior)
        for trial in first:
            p=trial['params'];self.assertLess(p['return_short'],p['return_long'])
            for key,values in SPACE.items():self.assertIn(p[key],values)
            self.assertIn((p['ema_fast'],p['ema_slow']),EMA)
            self.assertIn((p['macd_fast'],p['macd_slow'],p['macd_signal']),MACD)

if __name__=='__main__':unittest.main()
