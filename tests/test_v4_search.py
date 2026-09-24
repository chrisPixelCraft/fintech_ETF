import json
from collections import Counter
from pathlib import Path
import unittest
from src.v4_search import candidates, ablations, search_protocol


class SearchTests(unittest.TestCase):
    def test_fixed_fair_budget_and_unique_candidates(self):
        configs = candidates()
        self.assertEqual(Counter(c['family'] for c in configs),
                         dict(momentum=36, adaptive=36, direct=36))
        self.assertEqual(len({c['id'] for c in configs}), 108)
        serialized = {json.dumps({k:v for k,v in c.items() if k not in ('id','stage')},
                                 sort_keys=True) for c in configs}
        self.assertEqual(len(serialized),108)
        self.assertEqual(configs, candidates())

    def test_required_search_axis_coverage(self):
        m = [c for c in candidates() if c['family'] == 'momentum']
        for key, expected in [('short',{3,5,10}), ('medium',{5,10,15,20}),
                              ('long',{20,30,60}), ('target_count',{20,22,25,28,30}),
                              ('max_replacements',{0,1,2,3}),
                              ('replacement_margin',{0.,.05,.1,.15,.2})]:
            self.assertEqual({c[key] for c in m}, expected)
        a = [c for c in candidates() if c['family'] == 'adaptive']
        self.assertEqual({c['optimizer'] for c in a}, {'equal','score','continuous','de'})
        self.assertEqual({c['horizon'] for c in a}, {1,5,10,20})

    def test_config_matches_registry_and_ablation_does_not_mutate(self):
        self.assertEqual(json.loads(Path('config/v4_stage2_search.json').read_text()),search_protocol())
        chosen = {f: next(c for c in candidates() if c['family']==f)
                  for f in ('momentum','adaptive','direct')}
        before = json.dumps(chosen,sort_keys=True)
        result = ablations(chosen)
        self.assertEqual(json.dumps(chosen,sort_keys=True),before)
        self.assertTrue({'A1','A2','A3','A4','A5','A6','B1','B2'}.issubset({c['id'] for c in result}))
        self.assertEqual(search_protocol()['gate']['noninferiority_tolerance'],0)

    def test_family_configs_and_mandatory_robustness_gates(self):
        protocol=search_protocol()
        self.assertFalse(protocol['gate']['sector_robustness'])
        self.assertIn('seeds0/1/2',protocol['gate']['de_seed_stability'])
        for family in ('momentum','adaptive','direct'):
            file=json.loads(Path(f'config/v4_{family}_search.json').read_text())
            self.assertEqual(file['candidates'],[c for c in candidates() if c['family']==family])
            self.assertEqual(file['gate'],protocol['gate'])
