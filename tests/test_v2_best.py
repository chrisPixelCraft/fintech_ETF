"""Independent contract tests for the pinned A/B/C research entrypoints.

These test a fixed replay, not the validity of choosing it on development data.
No frozen market data, tuning configurations, or prior outputs are modified.
"""
import contextlib
import copy
import hashlib
import importlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from src import v2_best as best


EXPECTED_PARAMS = {
    'target_count': 25,
    'replacement_margin': 0.1,
    'max_replacements_per_day': 0,
    'volatility_spike_ratio': 2.0,
    'one_day_chase_return': 0.07,
    'volume_low': 0.5,
    'volume_high': 3.0,
    'four_hour_mode': 'strict',
    'returns': 'base',
    'ema': 'base',
    'macd': 'base',
    'score_profile': 'base',
    'sector_top_fraction': 0.5,
    'sector_short_weight': 8 / 15,
    'sector_fallback_mode': 'baseline',
    'c_alpha': 0.08,
}
EXPECTED_IDS = {'A': 'p005', 'B': 'p005', 'C': 'p006'}


def fixture_root(root, manifest):
    """A tiny self-consistent input tree for destructive *fixture* checks."""
    frozen = {
        'config/strategy_v2.json': manifest['config'],
        'config/v2_tuning_study.json': manifest['study'],
    }
    hashes = {}
    for relative, value in frozen.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True))
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    local = {**copy.deepcopy(manifest), 'hashes': hashes}
    location = root / 'outputs/v2_abc_tuning_20260922/tuning_manifest.json'
    location.parent.mkdir(parents=True, exist_ok=True)
    location.write_text(json.dumps(local))
    return location, local


class PinnedBestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = best.verify_frozen_inputs()
        # Keep the first-round contract against its frozen delivered entrypoints.
        cls.entries = {}
        snapshot = best.ROOT / 'outputs/v2_best_final_20260922/input_snapshot'
        for strategy in EXPECTED_IDS:
            spec = importlib.util.spec_from_file_location(f'legacy_v2_{strategy}_best', snapshot / f'v2_{strategy}_best.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls.entries[strategy] = module

    def build(self, strategy):
        with patch.object(best, 'verify_frozen_inputs', return_value=copy.deepcopy(self.manifest)):
            return self.entries[strategy].build_config()

    def test_all_three_literals_match_the_requested_fixed_candidates(self):
        self.assertEqual(best.SELECTED_CANDIDATES, EXPECTED_IDS)
        for strategy, entry in self.entries.items():
            with self.subTest(strategy=strategy):
                expected = {**EXPECTED_PARAMS, 'max_replacements_per_day': int(strategy == 'C')}
                self.assertEqual(entry.STRATEGY, strategy)
                self.assertEqual(entry.CANDIDATE_ID, EXPECTED_IDS[strategy])
                self.assertEqual(entry.PARAMETERS, expected)
                frozen = next(t for t in self.manifest['study']['candidates']
                              if t['candidate_id'] == EXPECTED_IDS[strategy])
                self.assertEqual(entry.PARAMETERS, frozen['params'])
                config = self.build(strategy)
                self.assertEqual(config['tuning_params'], expected)
                self.assertEqual(config['tuning_candidate_id'], EXPECTED_IDS[strategy])
                self.assertEqual(config['max_replacements_per_day'], int(strategy == 'C'))
                self.assertEqual(config['feature_presets'], dict(returns='base', ema='base', macd='base'))
                self.assertEqual(config['score_weights'], dict(return20=.4, return50=.35,
                                 volume=.1, macd=.05, trend=.05, long_trend=.05))
                self.assertTrue(config['use_4h'])
                self.assertFalse(config['match_4h_coverage'])
                self.assertTrue(config['study_policy']['historical_period_is_development'])
                self.assertFalse(config['study_policy']['parameter_search'])

    def test_wrong_candidate_or_any_parameter_change_is_rejected(self):
        with patch.object(best, 'verify_frozen_inputs', return_value=copy.deepcopy(self.manifest)):
            for strategy, entry in self.entries.items():
                with self.subTest(strategy=strategy):
                    with self.assertRaises(ValueError):
                        best.make_config(strategy, 'p000', entry.PARAMETERS)
                    for key in entry.PARAMETERS:
                        altered = copy.deepcopy(entry.PARAMETERS)
                        altered[key] = 'UNAPPROVED_VALUE'
                        with self.subTest(parameter=key), self.assertRaises(ValueError):
                            best.make_config(strategy, entry.CANDIDATE_ID, altered)
                    with self.assertRaises(ValueError):
                        best.make_config(strategy, entry.CANDIDATE_ID,
                                         {**entry.PARAMETERS, 'future_winner': 'p063'})
            with self.assertRaises(ValueError):
                best.make_config('D', 'p005', EXPECTED_PARAMS)

    def test_runtime_never_reselects_from_results_or_future_winner_metadata(self):
        manifest = copy.deepcopy(self.manifest)
        manifest['ex_post_winners'] = {s: 'p063' for s in EXPECTED_IDS}
        manifest['future_schedule'] = [{'effective_signal_date': '2099-01-01', 'candidate_id': 'p063'}]
        manifest['study']['candidates'].reverse()
        with patch.object(best, 'verify_frozen_inputs', return_value=manifest), \
             patch.object(best.tuning, 'selection', side_effect=AssertionError('Runtime reselection')), \
             patch.object(best.tuning, 'run_walk_forward', side_effect=AssertionError('Runtime schedule')), \
             patch.object(pd, 'read_csv', side_effect=AssertionError('Trial metric/table read')):
            for strategy, entry in self.entries.items():
                self.assertEqual(entry.build_config()['tuning_candidate_id'], EXPECTED_IDS[strategy])

    def test_configs_are_independent_and_entrypoint_literals_remain_unchanged(self):
        for strategy, entry in self.entries.items():
            a = self.build(strategy)
            a['score_weights']['return20'] = 999
            a['tuning_params']['target_count'] = 999
            b = self.build(strategy)
            self.assertEqual(b['score_weights']['return20'], .4)
            self.assertEqual(b['tuning_params']['target_count'], 25)
            self.assertEqual(entry.PARAMETERS['target_count'], 25)

    def test_entrypoints_delegate_to_the_same_fixed_runner_and_cli(self):
        for strategy, entry in self.entries.items():
            config, context, result = object(), object(), object()
            with patch.object(entry, 'build_config', return_value=config), \
                 patch.object(entry, 'run_fixed', return_value=result) as run:
                self.assertIs(entry.run_backtest(context), result)
                run.assert_called_once_with(strategy, config, context)
            with patch.object(entry, 'single_cli') as cli:
                entry.main(['--show-config'])
                cli.assert_called_once_with(strategy, entry.CANDIDATE_ID,
                                            entry.PARAMETERS, ['--show-config'])
            self.assertIs(entry.run_fixed, best.run_fixed)
            self.assertIs(entry.make_config, best.make_config)

    def test_fixed_runner_uses_one_shared_engine_call_and_correct_transform(self):
        for strategy in EXPECTED_IDS:
            config = self.build(strategy)
            before = copy.deepcopy(config)
            context = {key: object() for key in ['cache', 'daily', 'universe', 'bars']}
            callback = object()
            context['signals'] = Mock()
            context['signals'].make_callback.return_value = callback
            result = {'holdings': pd.DataFrame(columns=['symbol', 'date']), 'metrics': {}}
            with patch.object(best, 'IsolatedEngine') as factory, \
                 patch.object(best, 'enrich_metrics'):
                factory.return_value.run_v2.return_value = result
                self.assertIs(best.run_fixed(strategy, config, context), result)
                factory.assert_called_once_with(context['cache'])
                call = factory.return_value.run_v2.call_args
                factory.return_value.run_v2.assert_called_once()
                self.assertIs(call.args[0], context['daily'])
                self.assertIs(call.args[1], context['universe'])
                self.assertEqual(call.args[2], config)
                self.assertIsNot(call.args[2], config)
                self.assertIs(call.args[3], context['bars'])
                self.assertIs(call.kwargs['signal_transform'], None if strategy == 'A' else callback)
                if strategy == 'A':
                    context['signals'].make_callback.assert_not_called()
                else:
                    context['signals'].make_callback.assert_called_once_with(strategy, dict(
                        target_count=25, min_count=config['min_count'],
                        sector_top_fraction=.5, sector_short_weight=8 / 15,
                        sector_fallback_mode='baseline', c_alpha=.08))
                self.assertEqual(config, before)
                self.assertEqual(result['metrics']['selection_scope'], 'EX_POST_DEVELOPMENT')

    def test_unsupported_merger_result_is_rejected(self):
        context = {key: object() for key in ['cache', 'daily', 'universe', 'bars']}
        bad_result = {'holdings': pd.DataFrame([dict(symbol='2888.TW', date='2025-07-24')])}
        with patch.object(best, 'IsolatedEngine') as factory:
            factory.return_value.run_v2.return_value = bad_result
            with self.assertRaisesRegex(ValueError, 'Unsupported multi-security merger'):
                best.run_fixed('A', self.build('A'), context)

    def test_frozen_byte_changes_or_missing_dependencies_are_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fixture_root(root, self.manifest)
            best.verify_frozen_inputs(root)
            target = root / 'config/strategy_v2.json'
            original = target.read_bytes()
            target.write_bytes(original + b'\n')
            with self.assertRaisesRegex(ValueError, 'Frozen dependency changed'):
                best.verify_frozen_inputs(root)
            target.unlink()
            with self.assertRaises(FileNotFoundError):
                best.verify_frozen_inputs(root)

    def test_embedded_manifest_config_and_study_must_match_verified_files(self):
        for field in ['config', 'study']:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                path, manifest = fixture_root(root, self.manifest)
                manifest[field]['UNAPPROVED_MANIFEST_CHANGE'] = True
                path.write_text(json.dumps(manifest))
                with self.assertRaises(ValueError):
                    best.verify_frozen_inputs(root)

    def test_incomplete_tuning_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, manifest = fixture_root(root, self.manifest)
            manifest['outputs_complete'] = False
            path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                best.verify_frozen_inputs(root)

    def test_output_guard_preserves_existing_files_and_nested_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            new = best.create_output(root / 'new')
            self.assertTrue(new.is_dir())
            self.assertEqual(best.create_output(new), new)
            sentinel = new / 'do_not_overwrite.txt'
            sentinel.write_text('original trusted output')
            with self.assertRaises(FileExistsError):
                best.create_output(new)
            self.assertEqual(sentinel.read_text(), 'original trusted output')
            with self.assertRaises(FileExistsError):
                best.create_output(sentinel)
            nested = root / 'nested'
            (nested / 'prior_run').mkdir(parents=True)
            with self.assertRaises(FileExistsError):
                best.create_output(nested)

    def test_show_config_has_no_run_or_output_side_effects(self):
        with patch.object(best, 'verify_frozen_inputs', return_value=copy.deepcopy(self.manifest)), \
             patch.object(best, 'create_output') as create, \
             patch.object(best, 'run_fixed') as run, contextlib.redirect_stdout(io.StringIO()) as out:
            best.single_cli('C', 'p006', self.entries['C'].PARAMETERS, ['--show-config'])
        self.assertEqual(json.loads(out.getvalue())['tuning_candidate_id'], 'p006')
        create.assert_not_called()
        run.assert_not_called()

    def test_failed_preflight_stops_cli_before_output_creation_or_execution(self):
        with patch.object(best, 'verify_frozen_inputs', side_effect=ValueError('Frozen dependency changed')), \
             patch.object(best, 'create_output') as create, patch.object(best, 'run_fixed') as run:
            with self.assertRaisesRegex(ValueError, 'Frozen dependency changed'):
                best.single_cli('A', 'p005', self.entries['A'].PARAMETERS, [])
        create.assert_not_called()
        run.assert_not_called()

    def test_diagnostics_never_overwrite_book_or_economic_performance_fields(self):
        original = dict(total_return=.25, economic_total_return=.3, max_drawdown=.1,
                        economic_max_drawdown=.09, negative_cash_days=0)
        result = {'metrics': copy.deepcopy(original), 'equity': object()}
        values = dict(hard_breach_days=1, infeasible_executed_days=2, trade_days=3,
                      costs=4., final_economic_nav=5., **{key: -999 for key in original})
        with patch.object(best.tuning, 'period_metrics', return_value=values):
            best.enrich_metrics(result, 1e9)
        for key, value in original.items():
            self.assertEqual(result['metrics'][key], value)
        self.assertEqual(result['metrics']['trade_days'], 3)


if __name__ == '__main__':
    unittest.main()
