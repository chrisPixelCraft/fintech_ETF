"""Fail-closed pinned replay contracts independent of historical profitability."""
from pathlib import Path
import contextlib,copy,io,json,tempfile,unittest
from unittest.mock import patch
from src import v2_second_best as best


class SecondBestContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.folder=self.root/'study/historical_pit';self.folder.mkdir(parents=True)
        self.params={'target_count':25};self.selections={'historical_pit':'p005'};self.parameters={'historical_pit':self.params}
        self.cfg={'tuning_params':self.params,'cash_guard_ratio':.12,'study_policy':{},'universe_mode':'historical_pit','research_shadow':True}
        (self.folder/'final/A').mkdir(parents=True)
        self.write('final/A/config.json',self.cfg)
        self.write('selection.json',{'A':{'candidate_id':'p005','status':'SELECTED_ZERO_MEASURED_HARD'}})
        self.write('manifest.json',{'outputs_complete':True,'hashes':{},'study':{'cash_guard_ratio':.12,'candidates':[{'candidate_id':'p005','params':self.params}]}})
        self.write('audit.json',{'status':'PASS','artifact_hashes':{f:best.sha(self.folder/f) for f in ['final/A/config.json','selection.json','manifest.json']}})
        self.addCleanup(patch.stopall)
        patch.object(best,'ROOT',self.root).start();patch.object(best,'STUDY_ROOT',self.root/'study').start()

    def write(self,name,value):
        (self.folder/name).write_text(json.dumps(value))

    def config(self):return best.make_config('A',self.selections,self.parameters)

    def test_pinned_choice_is_research_only_and_independent(self):
        a=self.config();a['tuning_params']['target_count']=1;b=self.config()
        self.assertEqual(b['tuning_params']['target_count'],25)
        self.assertTrue(b['research_shadow']);self.assertFalse(b['study_policy']['parameter_search'])
        self.assertEqual(b['study_policy']['official_live_submission'],'BLOCK_IF_UNKNOWN')

    def test_changed_verified_artifact_is_rejected(self):
        self.write('final/A/config.json',{**self.cfg,'cash_guard_ratio':.24})
        with self.assertRaisesRegex(ValueError,'Verified artifact changed'):self.config()

    def test_wrong_literal_parameters_or_id_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'Pinned parameters'):
            best.make_config('A',self.selections,{'historical_pit':{'target_count':30}})
        with self.assertRaisesRegex(ValueError,'NO_ELIGIBLE_WINNER'):
            best.make_config('A',{'historical_pit':'p006'},self.parameters)

    def test_missing_or_failed_audit_cannot_run(self):
        self.write('audit.json',{'status':'FAIL'})
        with self.assertRaisesRegex(ValueError,'not PASS'):self.config()
        (self.folder/'audit.json').unlink()
        with self.assertRaisesRegex(ValueError,'no independently verified'):self.config()

    def test_show_config_does_not_run_or_create_output(self):
        with patch.object(best,'run_fixed',side_effect=AssertionError('must not run')),contextlib.redirect_stdout(io.StringIO()) as stream:
            best.single_cli('A',self.selections,self.parameters,['--show-config','--output',str(self.root/'out')])
        self.assertFalse((self.root/'out').exists());self.assertEqual(json.loads(stream.getvalue())['cash_guard_ratio'],.12)

    def test_context_mismatch_and_nonshadow_run_are_blocked(self):
        cfg=self.config()
        with self.assertRaisesRegex(ValueError,'Context universe mismatch'):
            best.run_fixed('A',cfg,{'track':'official_ex_post'})
        with self.assertRaisesRegex(ValueError,'BLOCK_SUBMISSION'):
            best.run_fixed('A',{**cfg,'research_shadow':False},{'track':'historical_pit'})


if __name__=='__main__':unittest.main()
