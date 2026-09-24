"""Fail-closed checks for the compact release and independent comparison audit."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import verify_best_v2 as release


class CompactReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return path

    def test_payload_tampering_is_detected(self):
        path = self.write('equity.json', {'nav': 100})
        hashes = {'equity.json': release.sha(path)}
        release.verify_hashes(self.root, hashes, {'equity.json'})
        path.write_text('{"nav": 200}')
        with self.assertRaisesRegex(ValueError, 'Hash mismatch'):
            release.verify_hashes(self.root, hashes, {'equity.json'})

    def test_omitted_required_payload_is_rejected_even_when_other_hashes_match(self):
        path = self.write('config.json', {})
        with self.assertRaisesRegex(ValueError, 'Incomplete hash manifest'):
            release.verify_hashes(self.root, {'config.json': release.sha(path)},
                                  {'config.json', 'equity.csv'})

    def test_missing_file_fails_with_validation_error(self):
        with self.assertRaisesRegex(ValueError, 'Hash mismatch'):
            release.verify_hashes(self.root, {'missing.csv': 'a' * 64})

    def test_parent_absolute_and_symlink_paths_cannot_escape_release(self):
        for name in ('../outside.csv', '/tmp/outside.csv', 'a/../outside.csv'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Unsafe manifest path'):
                release.safe_path(self.root, name)
        (self.root / 'escape').symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Escaping manifest path'):
            release.safe_path(self.root, 'escape/outside.csv')

    def test_receipt_requires_every_payload_and_rejects_failed_runs(self):
        path = self.write('config.json', {})
        self.write('receipt.json', {'config.json': release.sha(path)})
        with self.assertRaisesRegex(ValueError, 'Incomplete result receipt'):
            release.verify_receipt(self.root, {'config.json', 'equity.csv'})
        release.verify_receipt(self.root, {'config.json'})
        self.write('failure.json', {'error': 'incomplete'})
        with self.assertRaisesRegex(ValueError, 'Failed artifact'):
            release.verify_receipt(self.root, {'config.json'})

    def selected(self):
        return dict(tuning_candidate_id='x0352', universe_mode='official_ex_post',
                    research_shadow=True, official_review_policy={'active_share': 'UNKNOWN_BLOCK_SUBMISSION'},
                    full_tuning_params=copy.deepcopy(release.PARAMS), start='2025-01-01', end='2026-09-21')

    def test_changed_selected_parameters_are_rejected(self):
        config = self.selected()
        release.selected_config(config, 'official_ex_post')
        config['full_tuning_params']['max_replacements_per_day'] = 1
        with self.assertRaisesRegex(ValueError, 'Selected x0352 parameters'):
            release.selected_config(config, 'official_ex_post')

    def test_relaxed_submission_gate_is_rejected(self):
        config = self.selected()
        config['official_review_policy']['active_share'] = 'PASS'
        with self.assertRaisesRegex(ValueError, 'submission gate'):
            release.selected_config(config, 'official_ex_post')

    def test_manifest_identity_gate_cannot_be_rehashed_away(self):
        manifest = dict(schema_version=1, candidate_id='x0352',
                        strategy_id='best_finetuned_double_check_v2', scope=release.SCOPE,
                        submission_status='ALLOW_SUBMISSION', source_commit='a' * 40, files={})
        self.write(release.MANIFEST, manifest)
        with self.assertRaisesRegex(ValueError, 'submission gate'):
            release.verify_files(self.root)

    def test_incomplete_manifest_is_rejected_before_receipts(self):
        manifest = dict(schema_version=1, candidate_id='x0352',
                        strategy_id='best_finetuned_double_check_v2', scope=release.SCOPE,
                        submission_status='BLOCK_SUBMISSION', source_commit='a' * 40, files={})
        self.write(release.MANIFEST, manifest)
        with self.assertRaisesRegex(ValueError, 'Incomplete hash manifest'):
            release.verify_files(self.root)

    def test_audit_summary_tampering_is_detected(self):
        audit = dict(status='PASS', selected_count=2, monthly_count=132,
                     submission_status='BLOCK_SUBMISSION')
        self.write(release.AUDIT, {**audit, 'monthly_count': 131})
        with patch.object(release, 'verify_files'), patch.object(release, '_summary', return_value=audit):
            with self.assertRaisesRegex(ValueError, 'monthly_count'):
                release.verify_release(self.root)

    def test_disqualified_and_missing_counts_never_become_research_eligible(self):
        audit = dict(independent_audit='PASS', complete_period=True, disqualified=False,
                     total_return=.1, max_drawdown=.2, turnover_two_way=2.,
                     official_compliance='UNKNOWN_BLOCK_SUBMISSION', **dict.fromkeys(release.ZERO, 0))
        self.assertTrue(release.measured_eligible(audit))
        for changes in ({'disqualified': True}, {'complete_period': False},
                        {'raw_rule_breach_days': True}, {'total_return': float('nan')},
                        {'official_compliance': 'PASS'}):
            self.assertFalse(release.measured_eligible({**audit, **changes}))
        del audit['unfilled_orders']
        self.assertFalse(release.measured_eligible(audit))

    def test_false_boolean_and_nonfinite_metrics_are_rejected(self):
        for actual, expected in ((1, True), (True, 1), (float('nan'), 1.), (float('inf'), 1.)):
            with self.subTest(actual=actual), self.assertRaises(ValueError):
                release.same(actual, expected, 'metric')


if __name__ == '__main__':
    unittest.main()
