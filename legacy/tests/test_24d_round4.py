"""Independent fourth-round population and fail-closed pruning checks."""
import copy
import unittest
import pandas as pd
from scripts.expand_24d_round4 import generate_candidates
from scripts.verify_24d_round4 import check_population,check_pruning
from scripts.verify_24d import require_receipt_sealed


class Round4EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidates,cls.refs,cls.members=generate_candidates()

    def test_population_bounds_exclusions_and_determinism(self):
        candidates,refs,members=generate_candidates()
        self.assertEqual(candidates,self.candidates);self.assertEqual(refs,self.refs)
        pd.testing.assert_frame_equal(members,self.members)
        check_population(candidates,refs,members)

    def test_changed_fixed_parameter_and_unequal_parent_budget_rejected(self):
        candidates=copy.deepcopy(self.candidates)
        candidates[0]['params']['cash_guard_ratio']=.12345
        with self.assertRaises(ValueError):check_population(candidates,self.refs,self.members)
        members=self.members.copy()
        members.loc[0,'parent_id']=members.iloc[-1].parent_id
        with self.assertRaisesRegex(ValueError,'Unbalanced'):check_population(self.candidates,self.refs,members)

    def test_cannot_prune_missing_or_passing_required_episode(self):
        freeze=dict(candidate_id=None,status='NO_ELIGIBLE_CANDIDATE',formal_status='BLOCK_SUBMISSION',
            stop_reason='ALL_NEW_FAILED_REQUIRED_DEVELOPMENT_EPISODE',downstream_evaluation='NOT_RUN')
        row=dict(candidate_id='a',measured_pass=False,complete_period=False,episode_return=float('nan'))
        rows=pd.DataFrame([row]);check_pruning(freeze,rows,{'a'})
        with self.assertRaisesRegex(ValueError,'Missing'):check_pruning(freeze,rows,{'a','b'})
        passed={**row,'measured_pass':True,'complete_period':True,'episode_return':.01,
            'requested_sessions':24,'observed_sessions':24,'disqualified':False}
        for key in ['measured_hard_breach_days','no_valid_plan_days','unfilled_orders','simulated_warning_days',
                    'stale_held_price_days','hold_without_envelope_days','execution_price_bound_breaches','raw_rule_breach_days']:
            passed[key]=0
        with self.assertRaisesRegex(ValueError,'Passing new candidate'):check_pruning(freeze,pd.DataFrame([passed]),{'a'})

    def test_pruning_cannot_claim_adoption_or_downstream_results(self):
        freeze=dict(candidate_id=None,status='NO_ELIGIBLE_CANDIDATE',formal_status='BLOCK_SUBMISSION',
            stop_reason='ALL_NEW_FAILED_REQUIRED_DEVELOPMENT_EPISODE',downstream_evaluation='NOT_RUN')
        rows=pd.DataFrame([dict(candidate_id='a',measured_pass=False,complete_period=True,episode_return=-.1)])
        for mutation in [{'candidate_id':'a'},{'downstream_evaluation':'COMPLETED'},{'formal_status':'PASS'}]:
            with self.assertRaises(ValueError):check_pruning({**freeze,**mutation},rows,{'a'})

    def test_unsealed_raw_holdings_are_rejected(self):
        path='ledgers/bottleneck/a/receipt.json'
        receipt={'artifact_sha256':{'holdings.parquet':'abc'}}
        with self.assertRaisesRegex(ValueError,'omits receipt or its evidence'):
            require_receipt_sealed(path,receipt,{path:'def'})
        require_receipt_sealed(path,receipt,{path:'def','ledgers/bottleneck/a/holdings.parquet':'abc'})

if __name__=='__main__':unittest.main()
