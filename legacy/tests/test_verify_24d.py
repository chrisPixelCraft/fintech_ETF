"""Small adversarial fixtures for the offline postrun verifier."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from scripts.verify_24d import (ARTIFACTS, DELIVERY_REQUIRED, digest, local_cache, ranking, sha256, verify_freeze,
                               verify_group, verify_hash_map, verify_receipt_set,
                               verify_study, verify_summary, verify_delivery,
                               final_metadata, verify_final_metadata)
from scripts.verify_24d import diagnostic_registry, verify_cold_rows, verify_extended_summary
from scripts.verify_24d import require_receipt_sealed


class OfflineVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    def test_hash_tampering_missing_and_path_escape(self):
        path = self.root / 'evidence.csv'
        path.write_text('original')
        hashes = {'evidence.csv': sha256(path)}
        verify_hash_map(self.root, hashes)
        path.write_text('tampered')
        with self.assertRaisesRegex(ValueError, 'Hash mismatch'):
            verify_hash_map(self.root, hashes)
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing artifact'):
            verify_hash_map(self.root, hashes)
        with self.assertRaisesRegex(ValueError, 'Unsafe'):
            verify_hash_map(self.root, {'../outside': 'abc'})

    def test_relocated_checkout_never_reads_original_absolute_cache(self):
        original = self.root / 'original/data/yahoo_daily/snapshot'
        copied_root = self.root / 'copied'
        copied = copied_root / 'data/yahoo_daily/snapshot'
        original.mkdir(parents=True)
        copied.mkdir(parents=True)
        (original / 'data').write_text('old checkout')
        (copied / 'data').write_text('copied checkout')
        self.assertEqual(local_cache(copied_root, str(original)), copied)
        self.assertEqual((local_cache(copied_root, str(original)) / 'data').read_text(), 'copied checkout')
        (copied / 'data').unlink()
        copied.rmdir()
        with self.assertRaisesRegex(ValueError, 'Local immutable data snapshot'):
            local_cache(copied_root, str(original))

    def test_omitted_group_cannot_reduce_denominator(self):
        relative = 'ledgers/development/a/receipt.json'
        self.write(self.root / relative, {})
        expected = {relative: None, 'ledgers/development/b/receipt.json': None}
        with self.assertRaisesRegex(ValueError, 'Missing or unexpected'):
            verify_receipt_set(self.root, {relative: {}}, expected)
        with self.assertRaisesRegex(ValueError, 'on disk'):
            verify_receipt_set(self.root, {name: {} for name in expected}, expected)

    def test_final_seal_cannot_omit_a_group_receipt(self):
        receipt = {'artifact_sha256': {'metrics.csv': 'hash'}}
        with self.assertRaisesRegex(ValueError, 'omits receipt'):
            require_receipt_sealed('ledgers/phase/a/receipt.json', receipt,
                                   {'ledgers/phase/a/metrics.csv': 'hash'})
        require_receipt_sealed('ledgers/phase/a/receipt.json', receipt,
            {'ledgers/phase/a/metrics.csv': 'hash', 'ledgers/phase/a/receipt.json': 'receipt_hash'})

    def test_optional_delivery_seal_detects_report_and_run_changes(self):
        output = self.root / 'outputs/24d'
        self.write(output / 'run_manifest.json', {'study': 'complete'})
        verify_delivery(self.root, output)
        report = self.root / 'report.md'
        report.write_text('verified report')
        for name in DELIVERY_REQUIRED:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('sealed fixture')
        files = {name: sha256(self.root / name) for name in DELIVERY_REQUIRED | {'report.md'}}
        seal = dict(schema_version=1, files=files,
                    run_manifest_sha256=sha256(output / 'run_manifest.json'))
        self.write(output / 'delivery_manifest.json', seal)
        verify_delivery(self.root, output)
        report.write_text('changed report')
        with self.assertRaisesRegex(ValueError, 'Hash mismatch'):
            verify_delivery(self.root, output)
        report.write_text('verified report')
        self.write(output / 'run_manifest.json', {'study': 'different'})
        with self.assertRaisesRegex(ValueError, 'different completed study'):
            verify_delivery(self.root, output)
        self.write(output / 'run_manifest.json', {'study': 'complete'})
        seal['files'].pop('reports/24d_validation.md')
        self.write(output / 'delivery_manifest.json', seal)
        with self.assertRaisesRegex(ValueError, 'omits required reports'):
            verify_delivery(self.root, output)

    def test_final_metadata_preserves_config_and_exposes_inactive_legacy_fields(self):
        output = self.root / 'outputs/24d'
        snapshot = self.root / 'data/yahoo_daily/snapshot'
        selection = dict(candidate_id='baseline', decision='NO_ELIGIBLE_CANDIDATE',
            selection_frozen_at='2026-09-23T12:00:00Z', active_share_status='ACTIVE_SHARE_NOT_VERIFIED',
            ready_status='BLOCK_READY', submission_status='BLOCK_SUBMISSION')
        self.write(output / 'run_manifest.json', {'finished': True})
        self.write(output / 'final_selection.json', selection)
        self.write(output / 'study_manifest.json', dict(cache='/original/snapshot', guard_sha256='guard',
            guard={'study': {'ranking': ['median DESC'], 'splits': {'development': ['2010', '2019']}}}))
        self.write(snapshot / 'metadata.json', dict(derived_file='nominal.parquet',
            artifact_sha256={'nominal.parquet': 'nominal_hash'}, request={'end': '2026-09-24'}))
        self.write(snapshot / 'calendar_v2.json', {'calendar': ['2026-09-23']})
        config = dict(selection_provenance=selection, start='2025-01-01', end='2026-09-21',
            study_policy={'selection': 'LEGACY_BOOK_RETURN'}, use_4h=False, match_4h_coverage=False,
            four_hour_mode='disabled', feature_spec={'four_hour_mode': 'disabled'},
            tuning_params={'four_hour_mode': 'coverage_only'}, execution='open_proxy',
            execution_assumption='DAILY_OPEN_RESEARCH_PROXY', initial_cash=1e9,
            commission=.001425, sell_tax=.003, slippage_bps=0, dividend_cash_policy='end_of_period')
        path = self.root / 'configs/competition_24d_final.json'
        self.write(path, config)
        original_hash = sha256(path)
        companion = final_metadata(self.root, output)
        self.assertEqual(sha256(path), original_hash)
        self.assertEqual(companion['actual_data_cutoff'], '2026-09-23')
        self.assertIsNone(companion['adopted_candidate_id'])
        self.assertEqual(companion['four_hour_policy']['active_feature_mode'], 'disabled')
        metadata_path = self.root / 'configs/competition_24d_final_metadata.json'
        self.write(metadata_path, companion)
        verify_final_metadata(self.root, output)
        companion['actual_data_cutoff'] = '2026-09-21'
        self.write(metadata_path, companion)
        with self.assertRaisesRegex(ValueError, 'differs from frozen evidence'):
            verify_final_metadata(self.root, output)

    def test_failed_attempts_control_ranking(self):
        rows = pd.DataFrame([
            dict(candidate_id='a', measured_pass=True, complete_period=True, episode_return=.5,
                 episode_max_drawdown=.1, episode_turnover=1),
            dict(candidate_id='a', measured_pass=False, complete_period=False, episode_return=None,
                 episode_max_drawdown=.1, episode_turnover=1),
            dict(candidate_id='b', measured_pass=True, complete_period=True, episode_return=.01,
                 episode_max_drawdown=.1, episode_turnover=1)])
        result = ranking(rows)
        self.assertEqual(result.iloc[0].candidate_id, 'b')
        self.assertEqual(result.iloc[1].compliance_pass_rate, .5)

    def test_summary_omitted_failure_is_rejected(self):
        rows = pd.DataFrame([dict(candidate_id='a', measured_pass=False, complete_period=False,
            episode_return=None, episode_max_drawdown=.1, episode_turnover=1.)])
        summary = ranking(rows).assign(attempted=0, passed=0, completed=0, failed=1,
            failed_episode_penalty_return=-1., penalized_mean_return=-1.,
            penalized_median_return=-1., penalized_p25_return=-1.)
        with self.assertRaisesRegex(ValueError, 'Summary denominator differs'):
            verify_summary(summary, rows)

    def test_supplement_calendar_uses_complete_windows_only(self):
        calendar = pd.bdate_range('2009-01-01', '2026-09-23').strftime('%Y-%m-%d').tolist()
        monthly = diagnostic_registry(calendar, 'monthly', '2010-01-01', '2026-08-31')
        self.assertEqual(len(monthly), 200)
        rolling = diagnostic_registry(calendar, 'rolling', '2025-01-01', '2026-08-31')
        for row in rolling.itertuples():
            self.assertEqual(calendar.index(row.end)-calendar.index(row.start), 23)
        self.assertLessEqual(rolling.end.max(), '2026-09-23')

    def test_cold_start_rejects_holding_count_only_validity(self):
        book = pd.DataFrame(dict(episode_id=['e']*3, date=['a','b','c'], holdings=[20]*3,
            cash_ratio=[.3,.1,.1], violations=['CASH_GE_25_PERCENT','WEIGHT_CAP:x',''],
            stale_count=[0]*3, odd_residual_names=[0]*3))
        check = pd.DataFrame(dict(episode_id=['e']*3, date=['a','b','c'], warning_today=[False]*3))
        book.to_parquet(self.root / 'equity.parquet', index=False)
        check.to_parquet(self.root / 'compliance_daily.parquet', index=False)
        enriched = pd.DataFrame([dict(episode_id='e', day_1_invested_fraction=.7,
            day_1_holding_count=20, day_3_invested_fraction=.9, day_3_holding_count=20,
            day_5_invested_fraction=None, day_5_holding_count=None,
            days_to_valid_portfolio=3, valid_portfolio_reached=True)])
        verify_cold_rows(self.root, enriched)
        enriched['days_to_valid_portfolio'] = 1
        with self.assertRaisesRegex(ValueError, 'Cold-start evidence'):
            verify_cold_rows(self.root, enriched)

    def test_extended_diagnostics_reject_hash_consistent_tail_metric_change(self):
        from scripts.supplement_24d import extended_summary
        rows = pd.DataFrame(dict(candidate_id=['a']*5, measured_pass=[True]*4+[False],
            complete_period=[True]*4+[False], episode_return=[.01,.02,.03,1.,None],
            episode_max_drawdown=[.1]*5, episode_turnover=[2.]*5, trades=[20]*5,
            days_to_valid_portfolio=[1,2,3,1,None]))
        for day in [1,3,5]:
            rows[f'day_{day}_invested_fraction'] = [.9]*4+[None]
            rows[f'day_{day}_holding_count'] = [20]*4+[None]
        summary = extended_summary(rows)
        verify_extended_summary(summary, rows)
        summary['mean_return_excluding_top_5pct'] = .265
        with self.assertRaisesRegex(ValueError, 'Extended summary'):
            verify_extended_summary(summary, rows)

    def group_fixture(self):
        relative = 'ledgers/development/a/receipt.json'
        folder = (self.root / relative).parent
        config = {'candidate_id': 'a'}
        episodes = pd.DataFrame([dict(episode_id='monthly_2010-01-04', kind='monthly',
            start='2010-01-04', end='2010-02-04', split='development', session_count=24,
            prior_session_date='2009-12-31')])
        rows = episodes.assign(candidate_id='a', phase='development', episode_return=.01,
            forensic_partial_return=float('nan'), episode_max_drawdown=.02, episode_turnover=1.,
            measured_pass=True, complete_period=True, independent_audit='PASS_INTERNAL_ACCOUNTING')
        audited = rows.drop(columns=['candidate_id', 'phase']).assign(
            active_share_status='ACTIVE_SHARE_NOT_VERIFIED', ready_status='BLOCK_READY',
            official_compliance='UNKNOWN_BLOCK_SUBMISSION')
        self.write(folder / 'config.json', config)
        self.write(self.root / 'configs/a.json', config)
        rows.to_csv(folder / 'metrics.csv', index=False)
        audited.to_csv(folder / 'audit.csv', index=False)
        audited.to_csv(folder / 'serialized_audit.csv', index=False)
        for name in ARTIFACTS:
            if name.endswith('.parquet'):
                (folder / name).write_bytes(b'opaque frozen parquet bytes; quick mode hashes only')
        receipt = dict(candidate_id='a', phase='development', episodes=1,
            input_sha256=digest(dict(study='guard', phase='development', config=config,
                                    episodes=episodes.to_dict('records'))),
            artifact_sha256={name: sha256(folder / name) for name in ARTIFACTS})
        self.write(folder / 'receipt.json', receipt)
        return relative, folder, receipt, episodes

    def test_quick_mode_verifies_saved_receipt_without_reading_parquet(self):
        relative, _, receipt, episodes = self.group_fixture()
        rows, _ = verify_group(self.root, relative, receipt, episodes, 'a', 'development', 'guard')
        self.assertEqual(len(rows), 1)

    def test_receipt_json_and_central_config_tampering(self):
        relative, folder, receipt, episodes = self.group_fixture()
        altered = copy.deepcopy(receipt)
        altered['episodes'] = 2
        self.write(folder / 'receipt.json', altered)
        with self.assertRaisesRegex(ValueError, 'Manifest receipt differs'):
            verify_group(self.root, relative, receipt, episodes, 'a', 'development', 'guard')
        self.write(folder / 'receipt.json', receipt)
        self.write(self.root / 'configs/a.json', {'candidate_id': 'other'})
        with self.assertRaisesRegex(ValueError, 'Central/group config differs'):
            verify_group(self.root, relative, receipt, episodes, 'a', 'development', 'guard')

    def test_hash_consistent_audit_omission_is_rejected(self):
        relative, folder, receipt, episodes = self.group_fixture()
        path = folder / 'serialized_audit.csv'
        pd.read_csv(path).iloc[:0].to_csv(path, index=False)
        receipt['artifact_sha256']['serialized_audit.csv'] = sha256(path)
        self.write(folder / 'receipt.json', receipt)
        with self.assertRaisesRegex(ValueError, 'omits or duplicates'):
            verify_group(self.root, relative, receipt, episodes, 'a', 'development', 'guard')

    def test_incomplete_run_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'unfinished'):
            verify_study(self.root, self.root)

    def test_freeze_must_precede_holdout(self):
        selection = dict(candidate_id='a', required_measured_pass_rate=1.,
            submission_status='BLOCK_SUBMISSION', ready_status='BLOCK_READY',
            active_share_status='ACTIVE_SHARE_NOT_VERIFIED', selection_episode_ids=['dev'],
            holdout_episode_ids=['test'], selection_frozen_at='2026-09-23T12:00:01Z',
            candidate_config_sha256='unused')
        self.write(self.root / 'final_selection.json', selection)
        self.write(self.root / 'holdout_started.json', dict(started_at='2026-09-23T12:00:00Z',
            frozen_selection_sha256=sha256(self.root / 'final_selection.json')))
        registry = pd.DataFrame([dict(episode_id='dev', kind='monthly', split='development'),
                                 dict(episode_id='test', kind='monthly', split='holdout')])
        with self.assertRaisesRegex(AssertionError, 'frozen after holdout'):
            verify_freeze(self.root, self.root, selection, registry,
                          pd.DataFrame({'candidate_id': ['a']}), pd.DataFrame(), pd.DataFrame())


if __name__ == '__main__':
    unittest.main()
