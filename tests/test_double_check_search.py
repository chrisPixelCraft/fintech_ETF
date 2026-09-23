"""Coverage and provenance checks for the declared bounded search."""
import itertools
import json
from pathlib import Path
import tempfile
import unittest
from scripts.prepare_double_check import design, local_grid
from scripts.prepare_official_deep import canonical
from scripts.run_double_check import dump, sha, verify_hashes

ROOT = Path(__file__).resolve().parents[1]


class SearchCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.study = design()

    def test_frozen_design_is_reproducible_and_includes_all_old_candidates(self):
        frozen = json.loads((ROOT / 'config/v2_double_check_study.json').read_text())
        self.assertEqual(self.study, frozen)
        old = json.loads((ROOT / 'config/official_deep_study.json').read_text())['candidates']
        old += json.loads((ROOT / 'outputs/full_tuned_v2/local_candidates.json').read_text())['candidates']
        keys = [canonical(t['params']) for t in self.study['candidates']]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue({canonical(t['params']) for t in old}.issubset(keys))
        self.assertEqual(self.study['raw_cartesian_combinations'], 36_303_120_000)
        self.assertFalse(self.study['exhaustive_global'])

    def test_local_grid_covers_every_tuple_and_reuses_only_identical_candidates(self):
        parent = self.study['candidates'][0]['params']
        grid = local_grid(self.study, parent)
        self.assertEqual(grid['raw_combinations'], 128)
        expected = set(itertools.product(*[map(json.dumps, v) for v in grid['choices'].values()]))
        actual = {tuple(json.dumps(row['values'][key]) for key in grid['choices']) for row in grid['coverage']}
        self.assertEqual(actual, expected)
        candidates = {t['candidate_id']: t for t in self.study['candidates'] + grid['candidates']}
        from scripts.prepare_double_check import PAIR_KEYS
        for item in grid['coverage']:
            params = candidates[item['candidate_id']]['params']
            for axis, expected_value in item['values'].items():
                got = [params[k] for k in PAIR_KEYS[axis]] if axis in PAIR_KEYS else params[axis]
                self.assertEqual(got, expected_value)

    def test_resume_rejects_changed_receipt_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            dump(root / 'metrics.json', {'status': 'COMPLETE'})
            hashes = {'metrics.json': sha(root / 'metrics.json')}
            verify_hashes(root, hashes)
            dump(root / 'metrics.json', {'status': 'COMPLETE', 'total_return': 100})
            with self.assertRaisesRegex(ValueError, 'Frozen file changed'):
                verify_hashes(root, hashes)


if __name__ == '__main__':
    unittest.main()
