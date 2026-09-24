"""Study boundaries, failure denominators, immutable groups and selection rules."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from scripts.run_24d import (aggregate, candidate_specs, digest, load_study,
                            make_registry, rank_candidates, refinement_specs, run_groups)
from scripts.audit_24d import audit_episode_registry
from src.strategy_24d import build_config, build_features, _score
from tests.test_strategy_24d import fixture


def metric(candidate, episode, value, passed=True, complete=True):
    return dict(candidate_id=candidate, episode_id=episode, measured_pass=passed,
                complete_period=complete, episode_return=value,
                episode_max_drawdown=.03, episode_turnover=1.1,
                days_to_20_holdings=1 if passed else None)


class StudyTests(unittest.TestCase):
    def test_registry_keeps_180_months_and_purges_full_intervals(self):
        calendar = pd.bdate_range('2009-01-01', '2026-09-22').strftime('%Y-%m-%d').tolist()
        registry = make_registry(calendar)
        checked = audit_episode_registry(registry, calendar)
        self.assertEqual(checked['monthly_episodes'], 180)
        monthly = registry[registry.kind.eq('monthly')]
        self.assertEqual(len(registry[registry.kind.eq('oct_nov')]), 16)
        self.assertEqual(monthly.split.value_counts().to_dict(),
                         dict(development=119, validation=35, holdout=23, purged=3))
        self.assertEqual(monthly[monthly.start.str.startswith('2019-12')].split.iloc[0], 'purged')
        self.assertTrue((monthly.prior_session_date < monthly.start).all())

    def test_split_boundaries_can_be_configured_without_start_only_leakage(self):
        study = load_study()
        study['splits']['development'][1] = '2018-12-31'
        study['splits']['validation'][0] = '2019-01-01'
        registry = make_registry(pd.bdate_range('2009', '2026'), study)
        monthly = registry[registry.kind.eq('monthly')]
        self.assertEqual(monthly[monthly.start.str.startswith('2018-12')].split.iloc[0], 'purged')
        self.assertEqual(monthly[monthly.start.str.startswith('2019-01')].split.iloc[0], 'validation')

    def test_failure_denominator_cannot_make_fragile_candidate_win(self):
        rows = pd.DataFrame([metric('stable', 'one', .01), metric('stable', 'two', .02),
                             metric('fragile', 'one', .9), metric('fragile', 'two', None, False, False)])
        summary = aggregate(rows)
        fragile = summary.set_index('candidate_id').loc['fragile']
        self.assertEqual(fragile.attempted, 2)
        self.assertEqual(fragile.compliance_pass_rate, .5)
        self.assertAlmostEqual(fragile.penalized_mean_return, -.05)
        self.assertEqual(rank_candidates(summary).iloc[0].candidate_id, 'stable')

    def test_coarse_family_is_bounded_one_factor_and_unique(self):
        specs = candidate_specs()
        self.assertLessEqual(len(specs), 30)
        self.assertGreaterEqual(len(specs), 20)
        self.assertTrue(all(len(s['params']) == 1 for s in specs[1:]))
        self.assertEqual(len({digest(s['params']) for s in specs}), len(specs))

    def test_refinement_uses_only_eligible_development_leaders(self):
        study, specs = load_study(), candidate_specs()
        rows = pd.DataFrame([metric(s['candidate_id'], 'one', .01, False) for s in specs])
        self.assertEqual(refinement_specs(aggregate(rows), specs, study), [])
        rows['measured_pass'] = True
        refined = refinement_specs(aggregate(rows), specs, study)
        self.assertLessEqual(len(refined), 6)
        self.assertTrue(all(s['source'] == 'local_refinement' for s in refined))

    def test_simple_momentum_reference_changes_score_only(self):
        daily, _, dates = fixture()
        config = build_config()
        features = build_features(daily, config)
        today = features[features.date.eq(dates[0])]
        original = _score(today, config).sort_index()
        reference = _score(today, {**config, 'research_reference': 'simple_momentum'}).sort_index()
        pd.testing.assert_frame_equal(original.drop(columns='score'), reference.drop(columns='score'))
        pd.testing.assert_series_equal(reference.score, reference.return20.rank(pct=True), check_names=False)

    def test_group_serializes_reaudits_and_resumes_without_rerun(self):
        daily, universe, dates = fixture()
        calendar = sorted(pd.to_datetime(daily.date).dt.strftime('%Y-%m-%d').unique())
        start = str(dates[0].date())
        episode = dict(episode_id='fixture', kind='monthly', start=start, end=str(dates[-1].date()),
                       split='development', session_count=24, prior_session_date=calendar[calendar.index(start)-1])
        with tempfile.TemporaryDirectory() as temporary:
            context = dict(output=Path(temporary), study=load_study(), daily=daily, universe=universe,
                           session_dates=calendar, guard_sha256='fixture')
            specs = candidate_specs()[:1]
            first = run_groups(specs, pd.DataFrame([episode]), 'test', context, workers=1)
            with patch('scripts.run_24d.run_episode', side_effect=AssertionError('Must resume')):
                resumed = run_groups(specs, pd.DataFrame([episode]), 'test', context, workers=1)
            self.assertEqual(first.episode_id.tolist(), resumed.episode_id.tolist())
            self.assertEqual(first.episode_return.iloc[0], resumed.episode_return.iloc[0])
            folder = Path(temporary)/'ledgers/test/x0352_daily_baseline'
            self.assertTrue((folder/'serialized_audit.csv').exists())
            (folder/'metrics.csv').write_text('corrupted')
            with self.assertRaisesRegex(ValueError, 'artifact changed'):
                run_groups(specs, pd.DataFrame([episode]), 'test', context, workers=1)

    def test_failed_group_keeps_planned_end_and_realized_end_distinct(self):
        daily, universe, dates = fixture(count=10)
        calendar = sorted(pd.to_datetime(daily.date).dt.strftime('%Y-%m-%d').unique())
        start = str(dates[0].date())
        episode = dict(episode_id='failed', kind='monthly', start=start, end=str(dates[-1].date()),
                       split='development', session_count=24, prior_session_date=calendar[calendar.index(start)-1])
        with tempfile.TemporaryDirectory() as temporary:
            context = dict(output=Path(temporary), study=load_study(), daily=daily, universe=universe,
                           session_dates=calendar, guard_sha256='fixture')
            rows = run_groups(candidate_specs()[:1], pd.DataFrame([episode]), 'failed', context, workers=1)
            self.assertEqual(rows.end.iloc[0], episode['end'])
            self.assertLess(rows.realized_end.iloc[0], rows.end.iloc[0])
            self.assertTrue(pd.isna(rows.episode_return.iloc[0]))


if __name__ == '__main__':
    unittest.main()
