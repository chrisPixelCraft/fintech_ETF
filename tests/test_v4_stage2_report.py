import unittest
import pandas as pd
from scripts.v4_stage2_report import paired, neighborhood
from src.v4_search import candidates


class ReportGateTests(unittest.TestCase):
    def test_missing_pair_cannot_improve_gate(self):
        f=pd.DataFrame([dict(candidate=c,split='validation',episode=e,episode_return=r)
                        for c,e,r in [('X','a',.2),('X','b',None),('A0_V3','a',.1),('A0_V3','b',.1)]])
        p=paired(f,'X','validation',['a','b'])
        self.assertFalse(p['pass'])
        self.assertEqual(p['paired'],1)
        self.assertIsNone(p['median'])

    def test_lower_tail_loss_blocks_positive_median(self):
        f=pd.DataFrame([dict(candidate=c,split='validation',episode=str(i),episode_return=r)
                        for c,returns in [('X',[-.2,.2,.2,.2]),('A0_V3',[0.,0.,0.,0.])]
                        for i,r in enumerate(returns)])
        p=paired(f,'X','validation',[str(i) for i in range(4)])
        self.assertGreater(p['median'],0)
        self.assertLess(p['p10'],0)
        self.assertFalse(p['pass'])

    def test_neighbors_require_one_axis(self):
        all_configs=candidates()
        base=next(c for c in all_configs if c['id']=='M04')
        near=neighborhood(base,all_configs)
        self.assertIn('M06',near)
        self.assertNotIn('M13',near)
        self.assertNotIn('M07',near) # short10 farther than short3 from5

    def test_report_generation_with_failed_stability_never_freezes(self):
        import tempfile,json,hashlib
        from pathlib import Path
        from scripts.v4_stage2_report import generate
        configs=[next(c for c in candidates() if c['family']==f)
                 for f in ('momentum','adaptive','direct')]
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); output=root/'study'; output.mkdir()
            rows=[]
            registry=[dict(episode_id=s+'1',split=s) for s in ('development','validation','historical_holdout')]
            for cid,family in [('A0_V3','v3'),*[(c['id'],c['family']) for c in configs]]:
                for e in registry:
                    rows.append(dict(candidate=cid,family=family,episode=e['episode_id'],split=e['split'],
                                     episode_return=.01,canonical_status='AVAILABLE',measured_pass=True,
                                     episode_max_drawdown=.01,episode_turnover=.9,transaction_cost=1e6))
            pd.DataFrame(rows).to_csv(output/'all_episodes.csv',index=False)
            summary=pd.DataFrame([dict(candidate=c['id'],family=c['family']) for c in configs])
            for name in ('development_summary.csv','validation_summary.csv'):
                summary.to_csv(output/name,index=False)
            (output/'freeze.json').write_text(json.dumps(dict(selected={c['family']:c for c in configs},preferred_family='momentum')))
            (output/'verification.json').write_text(json.dumps(dict(status='PASS_INDEPENDENT_STAGE2_AUDIT')))
            (output/'candidates.json').write_text(json.dumps(configs))
            (output/'episodes.json').write_text(json.dumps(registry))
            names=('all_episodes.csv','development_summary.csv','validation_summary.csv','freeze.json','candidates.json','episodes.json')
            (output/'manifest.json').write_text(json.dumps(dict(outputs={n:hashlib.sha256((output/n).read_bytes()).hexdigest() for n in names})))
            (output/'verification.json').write_text(json.dumps(dict(status='PASS_INDEPENDENT_STAGE2_AUDIT',manifest_sha256=hashlib.sha256((output/'manifest.json').read_bytes()).hexdigest())))
            result=generate(output,root/'reports',root/'final.json')
            self.assertEqual(result['decision'],'NO_V4_WINNER')
            self.assertTrue(all(not g['sector_robustness'] for g in result['gates'].values()))
            self.assertFalse((root/'final.json').exists())
            self.assertEqual(len(list((root/'reports').glob('*.md'))),6)

    def test_direct_and_momentum_architectures_have_real_neighbors(self):
        configs=candidates()
        for c in configs:
            if c['family']=='direct' or c['id'] in ('M00','M01','M02','M03'):
                near=neighborhood(c,configs)
                self.assertTrue(near,c['id'])
                if c['family']=='momentum':
                    neighbor=next(x for x in configs if x['id']==near[0])
                    self.assertEqual(neighbor['score_family'],c['score_family'])
                    self.assertNotEqual(neighbor['cash_target'],c['cash_target'])

    def test_blocked_mode_is_honest_and_hash_bound(self):
        import tempfile,json,hashlib
        from pathlib import Path
        from scripts.v4_stage2_report import generate_blocked
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); d=root/'data'; d.mkdir()
            (d/'execution_data.csv').write_text('date,symbol\n')
            manifest=dict(status='BLOCK_CANONICAL_V4_INCOMPLETE_COVERAGE',rows=10,available_rows=5,
                normalized_sha256=hashlib.sha256((d/'execution_data.csv').read_bytes()).hexdigest(),
                attempts=[dict(status='OK'),dict(status='FAILED',error='HTTP428')])
            (d/'execution_data.manifest.json').write_text(json.dumps(manifest))
            (d/'download_status.json').write_text(json.dumps(dict(status='BLOCKED_HTTP428',reason='Challenge')))
            (d/'episodes.json').write_text(json.dumps([dict(split='development',episode_id='D1')]))
            audit=dict(status='BLOCKED_OFFICIAL_ACQUISITION',manifest_sha256=hashlib.sha256((d/'execution_data.manifest.json').read_bytes()).hexdigest())
            (d/'verification.json').write_text(json.dumps(audit))
            outcome=generate_blocked(d,root/'reports',root/'final.json',d/'verification.json')
            self.assertEqual(outcome['empirical_status'],'BLOCKED_NOT_RUN')
            self.assertFalse((root/'final.json').exists())
            self.assertIn('NOT_RUN',(root/'reports/v4_final.md').read_text())
            manifest['available_rows']=6
            (d/'execution_data.manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError,'exact execution-data manifest'):
                generate_blocked(d,root/'reports',root/'final.json',d/'verification.json')
