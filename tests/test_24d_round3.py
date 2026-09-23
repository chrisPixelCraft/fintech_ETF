"""Third-round exclusions, structural coverage and fail-closed adoption evidence."""
import itertools
import json
import unittest
import pandas as pd
from scripts.expand_24d_round3 import generate_candidates, ORIGINAL, PARENT
from scripts.tune_24d import SPACE, EMA, MACD
from scripts.verify_24d_round3 import check_adoption
from scripts.verify_24d import require_receipt_sealed


class Round3EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidates, cls.refs, cls.members = generate_candidates()

    def test_new_population_is_deterministic_bounded_and_excludes_both_prior_rounds(self):
        candidates, refs, members = generate_candidates()
        self.assertEqual(candidates, self.candidates)
        self.assertEqual(refs, self.refs)
        pd.testing.assert_frame_equal(members, self.members)
        old = {r['candidate_id'] for folder in [ORIGINAL, PARENT]
               for r in json.loads((folder/'candidates.json').read_text())}
        ids = {r['candidate_id'] for r in candidates}
        self.assertEqual(len(old), 575)
        self.assertEqual(len(ids), 512)
        self.assertFalse(ids & old)
        self.assertTrue({r['candidate_id'] for r in refs} <= old)
        for trial in candidates:
            p = trial['params']
            self.assertLess(p['return_short'], p['return_long'])
            for key, values in SPACE.items():
                self.assertIn(p[key], values)
            self.assertIn((p['ema_fast'], p['ema_slow']), EMA)
            self.assertIn((p['macd_fast'], p['macd_slow'], p['macd_signal']), MACD)

    def test_each_structural_cell_covers_all_entry_levels(self):
        self.assertEqual(self.members.family.value_counts().to_dict(),
                         {'uniform': 256, 'stratified_entry_replacement': 256})
        ids = set(self.members.loc[self.members.family.eq('stratified_entry_replacement'), 'candidate_id'])
        records = [r['params'] for r in self.candidates if r['candidate_id'] in ids]
        cells = list(itertools.product(SPACE['target_count'], SPACE['max_replacements_per_day']))
        for index, (target, replacements) in enumerate(cells):
            rows = [r for r in records if r['target_count'] == target and r['max_replacements_per_day'] == replacements]
            self.assertEqual(len(rows), 13 if index < 16 else 12)
            for key in ['volume_low', 'volume_high', 'one_day_chase_return', 'volatility_spike_ratio', 'cash_guard_ratio']:
                self.assertEqual({r[key] for r in rows}, set(SPACE[key]))

    def test_null_selection_requires_all_eligible_stability_evidence_in_rank_order(self):
        common = dict(compliance_pass_rate=1., valid_episode_rate=1., failed=0,
                      median_24d_return=.05, p25_24d_return=.01)
        rows = pd.DataFrame([{**common, 'candidate_id': c} for c in ['x0352_daily_baseline', 'a', 'b']])
        freeze = dict(candidate_id=None, status='NO_ELIGIBLE_CANDIDATE', formal_status='BLOCK_SUBMISSION', prior_holdout_exposure=True)
        evidence = [{'candidate_id': c, 'stable': False} for c in ['a', 'b']]
        check_adoption(freeze, rows, rows, {'results': evidence}, {'a', 'b'})
        with self.assertRaisesRegex(ValueError, 'Missing or duplicate'):
            check_adoption(freeze, rows, rows, {'results': evidence[:1]}, {'a', 'b'})
        with self.assertRaisesRegex(ValueError, 'ranked qualified'):
            check_adoption(freeze, rows, rows, {'results': evidence[::-1]}, {'a', 'b'})
        with self.assertRaisesRegex(ValueError, 'Final selection omits'):
            check_adoption(freeze, rows, rows, {'results': [evidence[0], {**evidence[1], 'stable': True}]}, {'a', 'b'})

    def test_receipt_seal_must_cover_raw_ledger_evidence(self):
        relative = 'ledgers/development/a/receipt.json'
        receipt = {'artifact_sha256': {'holdings.parquet': 'hash'}}
        with self.assertRaisesRegex(ValueError, 'omits receipt or its evidence'):
            require_receipt_sealed(relative, receipt, {relative: 'hash'})
        require_receipt_sealed(relative, receipt, {relative: 'hash', 'ledgers/development/a/holdings.parquet': 'hash'})


if __name__ == '__main__':
    unittest.main()
