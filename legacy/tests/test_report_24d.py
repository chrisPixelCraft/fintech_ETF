"""Prevent report survivorship, denominator and partial-return mistakes."""
import tempfile
import json
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from scripts.report_24d import (BASELINE, canonical_episode_rows, date_range_label, episodes,
                               local_snapshot, render, selection_id, stats, truth, verified_diagnostics,
                               verified_supplement, write_canonical_aliases)


def sample():
    return pd.DataFrame({
        'candidate_id': ['a'] * 4, 'episode_id': ['e1', 'e2', 'e3', 'e4'],
        'start': ['2020-01-02', '2020-02-03', '2020-03-02', '2020-04-01'],
        'end': ['2020-02-05', '2020-03-06', '2020-04-06', '2020-05-06'],
        'complete_period': [True, True, True, False],
        'measured_pass': [True, True, False, False],
        'episode_return': [.1, -.2, .9, np.nan],
        'episode_max_drawdown': [.02, .2, .4, .1],
        'episode_turnover': [1., 2., 3., 0.],
    })


class Report24DTests(unittest.TestCase):
    def test_failed_and_incomplete_episodes_stay_in_denominator(self):
        result = stats(sample())
        self.assertEqual((result['requested'], result['valid'], result['complete']), (4, 2, 3))
        self.assertEqual(result['measured_pass_rate'], .5)
        self.assertEqual(result['incomplete'], 1)
        self.assertEqual(result['failed'], 2)
        self.assertAlmostEqual(result['median'], -.05)
        self.assertEqual(result['all_complete_median'], .1)
        self.assertEqual(result['formal_confirmed'], 0)
        self.assertEqual(result['formal_unknown'], 4)

    def test_wins_and_losses_do_not_use_failed_returns(self):
        result = stats(sample())
        self.assertEqual(result['positive'], .5)
        self.assertEqual(result['loss'], .5)
        self.assertAlmostEqual(result['median_mdd'], .11)
        self.assertEqual(result['turnover'], 1.5)

    def test_no_valid_returns_are_unknown_not_zero(self):
        frame = sample()
        frame['measured_pass'] = False
        result = stats(frame)
        self.assertIsNone(result['median'])
        self.assertIsNone(result['worst'])
        self.assertEqual(result['measured_pass_rate'], 0.)

    def test_reject_partial_returns_and_duplicated_episodes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'episodes.csv'
            frame = sample()
            frame.loc[3, 'episode_return'] = .5
            frame.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                episodes(path)
            frame = pd.concat([sample(), sample().iloc[[0]]], ignore_index=True)
            frame.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                episodes(path)

    def test_string_false_does_not_become_truthy(self):
        self.assertEqual(truth(pd.Series(['False', 'True', False, True])).tolist(), [False, True, False, True])
        with self.assertRaises(ValueError):
            truth(pd.Series(['UNKNOWN']))

    def test_periods_are_read_from_actual_evidence(self):
        self.assertEqual(date_range_label(sample()), '2020-01-02–2020-04-01 starts; last episode ends 2020-05-06')
        self.assertEqual(selection_id({'candidate_id': None, 'diagnostic_candidate_id': 'fallback'}), 'fallback')

    def test_canonical_alias_preserves_native_partial_but_nulls_full_horizon(self):
        frame = sample().assign(terminal_NAV=[1.1e9, .8e9, 1.9e9, .5e9],
                                episode_status=['PASS', 'PASS', 'FAIL_CASH', 'FAIL_OTHER'])
        result = canonical_episode_rows(frame, 'source.csv', 'hash')
        self.assertEqual(result.terminal_NAV.iloc[0], 1.1e9)
        self.assertTrue(pd.isna(result.terminal_NAV.iloc[3]))
        self.assertEqual(result.native_terminal_NAV.iloc[3], .5e9)
        self.assertTrue(pd.isna(result['return'].iloc[3]))
        self.assertTrue(pd.isna(result.max_drawdown.iloc[3]))
        self.assertEqual(result.episode_max_drawdown.iloc[3], .1)
        self.assertTrue(result.execution_model.eq('DAILY_OPEN_RESEARCH_PROXY').all())
        self.assertTrue(result.formal_compliance_status.str.startswith('UNKNOWN').all())

    def test_supplement_outputs_must_match_the_seal(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            main, supplement = parent / '24d', parent / '24d_supplement'
            main.mkdir(); supplement.mkdir()
            (main / 'run_manifest.json').write_text('{}')
            hashes = {}
            for filename in ['enriched_episodes.csv', 'extended_summary.csv',
                             'monthly_comparison_2010_2026.csv', 'rolling_comparison_2010_2026.csv']:
                sample().to_csv(supplement / filename, index=False)
                hashes[filename] = hashlib.sha256((supplement / filename).read_bytes()).hexdigest()
            (supplement / 'status.json').write_text('{"status":"COMPLETE"}')
            (supplement / 'supplement_audit.json').write_text(json.dumps({
                'status': 'PASS_INTERNAL_ACCOUNTING',
                'parent_run_manifest_sha256': hashlib.sha256((main / 'run_manifest.json').read_bytes()).hexdigest(),
                'output_sha256': hashes,
            }))
            self.assertEqual(len(verified_supplement(main)['monthly']), 4)
            (supplement / 'monthly_comparison_2010_2026.csv').write_text('corrupted')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                verified_supplement(main)

    def test_nine_reports_do_not_adopt_ineligible_diagnostic(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            output, reports = folder / 'output', folder / 'reports'
            output.mkdir()
            baseline = sample().assign(candidate_id=BASELINE)
            rows = pd.concat([baseline, sample()], ignore_index=True)
            for name in ['development', 'validation', 'holdout', 'oct_nov']:
                rows.to_csv(output / f'{name}.csv', index=False)
            pd.DataFrame({'candidate_id': [BASELINE, 'a']}).to_csv(output / 'candidates.csv', index=False)
            (output / 'configs').mkdir()
            candidate_config = output / 'configs' / f'{BASELINE}.json'
            candidate_config.write_text(json.dumps({
                'full_tuning_params': {'four_hour_mode': 'coverage_only'},
                'feature_spec': {'four_hour_mode': 'disabled'},
            }))
            (output / 'final_selection.json').write_text(json.dumps({
                'candidate_id': BASELINE, 'research_best_candidate_id': 'a',
                'decision': 'NO_ELIGIBLE_CANDIDATE', 'ready_status': 'BLOCK_READY',
                'number_of_candidates_evaluated': 2, 'required_measured_pass_rate': 1.,
                'candidate_config_sha256': hashlib.sha256(candidate_config.read_bytes()).hexdigest(),
            }))
            (output / 'audit.json').write_text(json.dumps({'status': 'PASS'}))
            metadata = {'downloaded_count': 151, 'requested_count': 151,
                        'invalid_price_rows': 3, 'quality_alert_rows': 4, 'calendar': ['2026-09-23']}
            with patch('scripts.report_24d.local_snapshot', return_value=folder), \
                 patch('src.yahoo_daily.load_metadata', return_value=metadata), \
                 patch('src.yahoo_daily.calendar_amendment', return_value={'added_dates': ['2024-01-15']}):
                result = render(output, reports)
                before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in reports.iterdir()}
                verified = render(output, reports, verify=True)
                self.assertEqual(verified['verification'], 'PASS')
                self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in reports.iterdir()})
            self.assertEqual(result['reports'], 9)
            summary = (reports / '24d_strategy_summary.md').read_text()
            self.assertTrue(summary.startswith(f'SELECTED STRATEGY: `{BASELINE}`'))
            self.assertIn('NO_ELIGIBLE_CANDIDATE', summary.splitlines()[0])
            self.assertIn('diagnostic fallback', summary.splitlines()[0])
            self.assertIn('a (diagnostic; not adopted)', summary)
            self.assertIn('0/4 formally confirmed; UNKNOWN', summary)
            self.assertIn('24D MEDIAN RETURN: -5.00%', summary)
            expected = {'strategy_summary', 'x0352_baseline', 'parameter_search', 'validation',
                        'holdout', 'recent_regime', 'oct_nov_analogs', 'failure_analysis', 'final_candidate'}
            self.assertEqual({p.name for p in reports.glob('24d_*.md')}, {f'24d_{n}.md' for n in expected})
            final = (reports / '24d_final_candidate.md').read_text()
            self.assertIn('| four_hour_mode | disabled |', final)
            self.assertNotIn('| four_hour_mode | coverage_only |', final)
            self.assertIn('competition_24d_final_metadata.json', final)
            self.assertIn('Base candidate configuration SHA256', final)

    def test_family_panel_rejects_partial_continuous_return_even_with_matching_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            main, supplement, family = [root / name for name in ['24d', '24d_supplement', '24d_diagnostics']]
            for folder in [main, supplement, family]:
                folder.mkdir()
            (main / 'run_manifest.json').write_text('{}')
            (supplement / 'supplement_audit.json').write_text('{}')
            sample().assign(candidate_id=BASELINE).to_csv(family / 'monthly_all_candidates.csv', index=False)
            pd.DataFrame([{'candidate_id': BASELINE}]).to_csv(family / 'full_period_summary.csv', index=False)
            long_row = pd.DataFrame([dict(candidate_id=BASELINE, complete_period=False, disqualified=True,
                                         measured_pass=False, long_horizon_return=np.nan)])
            long_row.to_csv(family / 'long_horizon.csv', index=False)
            seal = dict(analysis_status='POST_FREEZE_DIAGNOSTIC_NEVER_SELECTION_INPUT', adoption_allowed=False,
                        main_run_manifest_sha256=hashlib.sha256((main / 'run_manifest.json').read_bytes()).hexdigest(),
                        supplement_audit_sha256=hashlib.sha256((supplement / 'supplement_audit.json').read_bytes()).hexdigest(),
                        monthly_attempts=4, monthly_per_candidate=4, candidate_count=1,
                        parameter_set_count=1, sanity_reference_count=0, long_horizon_attempts=1)
            def seal_outputs():
                seal['output_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in family.glob('*.csv')}
                (family / 'diagnostics_audit.json').write_text(json.dumps(seal))
            seal_outputs()
            (family / 'status.json').write_text('{"status":"COMPLETE"}')
            self.assertEqual(len(verified_diagnostics(main, {'folder': supplement})['monthly']), 4)
            long_row['long_horizon_return'] = .9
            long_row.to_csv(family / 'long_horizon.csv', index=False)
            seal_outputs()
            with self.assertRaisesRegex(ValueError, 'partial book'):
                verified_diagnostics(main, {'folder': supplement})

    def test_alias_verify_does_not_create_missing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            main, supplement = Path(temporary) / '24d', Path(temporary) / '24d_supplement'
            main.mkdir(); supplement.mkdir()
            (main / 'run_manifest.json').write_text('{}')
            rows = sample().assign(candidate_id=BASELINE, phase='recent_stress', source_study='original')
            for filename in ['monthly_comparison_2010_2026.csv', 'rolling_comparison_2010_2026.csv', 'enriched_episodes.csv']:
                rows.to_csv(supplement / filename, index=False)
            with self.assertRaisesRegex(FileNotFoundError, 'Canonical alias is missing'):
                write_canonical_aliases(main, {'folder': supplement, 'monthly': rows, 'rolling': rows, 'enriched': rows}, verify=True)
            self.assertEqual([p.name for p in main.iterdir()], ['run_manifest.json'])

    def test_snapshot_uses_this_checkout_and_never_old_absolute_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'new_checkout'
            old = Path(temporary) / 'old_checkout/data/yahoo_daily/v3_snapshot'
            local = root / 'data/yahoo_daily/v3_snapshot'
            old.mkdir(parents=True)
            local.mkdir(parents=True)
            payload = b'{"snapshot":"expected"}'
            (old / 'metadata.json').write_bytes(payload)
            (local / 'metadata.json').write_bytes(payload)
            study = {'cache': str(old), 'guard': {'metadata_sha256': hashlib.sha256(payload).hexdigest()}}
            self.assertEqual(local_snapshot({}, study, root), local)
            (local / 'metadata.json').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'hash differs'):
                local_snapshot({}, study, root)
            (local / 'metadata.json').unlink()
            with self.assertRaisesRegex(FileNotFoundError, 'this checkout'):
                local_snapshot({}, study, root)


if __name__ == '__main__':
    unittest.main()
