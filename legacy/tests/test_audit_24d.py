"""Adversarial accounting, split provenance and failure-denominator checks."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.audit_24d import (audit_episode, audit_episode_registry,
                               audit_selection_provenance, interval_split, verify_hashes,
                               audit_saved_group, audit_attempt_coverage, audit_decision_causality)
from src.strategy_24d import build_config, run_episode
from tests.test_strategy_24d import fixture


class EpisodeAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daily, cls.universe, cls.dates = fixture()
        cls.config = build_config()
        cls.base = run_episode(cls.daily, cls.universe, cls.config, cls.dates)
        cls.context = dict(daily=cls.daily, universe=cls.universe,
                           session_dates=sorted(cls.daily.date.unique()))

    def setUp(self):
        self.result = copy.deepcopy(self.base)

    def test_reconstructs_every_day_without_certifying_unknown_rules(self):
        audit = audit_episode(self.result, self.context)
        self.assertTrue(audit['measured_pass'])
        self.assertEqual(audit['independent_audit'], 'PASS_INTERNAL_ACCOUNTING')
        self.assertEqual(audit['active_share_status'], 'ACTIVE_SHARE_NOT_VERIFIED')
        self.assertEqual(audit['ready_status'], 'BLOCK_READY')
        self.assertFalse(audit['source_accuracy_verified'])

    def test_saved_group_roundtrip_reaudits_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('equity', 'trades', 'orders', 'holdings', 'compliance_daily',
                         'rejected_trades', 'warnings', 'snapshots', 'plan_audit'):
                frame = self.result[name].copy()
                frame['episode_id'] = 'fixture'
                frame.to_parquet(Path(folder)/(name+'.parquet'), index=False)
            cfg = self.result['config']
            rows = pd.DataFrame([dict(self.result['metrics'], candidate_id='baseline', episode_id='fixture')])
            episodes = pd.DataFrame([dict(episode_id='fixture', start=cfg['start'], end=cfg['end'])])
            audit = audit_saved_group(folder, rows, {'baseline':cfg}, self.context, episodes)
            self.assertEqual(audit.independent_audit.iloc[0], 'PASS_INTERNAL_ACCOUNTING')

    def test_same_day_signal_forgery_is_rejected(self):
        self.result['trades'].loc[0, 'signal_date'] = self.result['trades'].loc[0, 'date']
        with self.assertRaisesRegex(AssertionError, 'timing'):
            audit_episode(self.result, self.context)

    def test_future_shocks_and_truncation_preserve_prior_decisions(self):
        audit = audit_decision_causality(run_episode, self.daily, self.universe,
                                        self.config, self.dates, self.dates[3])
        self.assertEqual(audit['status'], 'PASS_PREFIX_INVARIANCE')

    def test_missing_warmup_failure_has_no_fabricated_performance(self):
        daily = self.daily.loc[self.daily.date >= self.dates[0]]
        result = run_episode(daily, self.universe, self.config, self.dates)
        audit = audit_episode(result, dict(self.context, daily=daily))
        self.assertEqual(audit['independent_audit'], 'NO_LEDGER_FAILURE_RETAINED')
        self.assertIsNone(audit['episode_return'])

    def test_zero_commission_cannot_hide_in_unchanged_nav(self):
        self.result['trades'].loc[0, 'fee'] = 0.
        with self.assertRaisesRegex(AssertionError, 'Fill fee'):
            audit_episode(self.result, self.context)

    def test_odd_lot_order_is_rejected(self):
        self.result['orders'].loc[0, 'shares'] += 1
        with self.assertRaisesRegex(AssertionError, 'Nonlot'):
            audit_episode(self.result, self.context)

    def test_missing_daily_record_is_rejected(self):
        self.result['equity'] = self.result['equity'].drop(index=2)
        with self.assertRaisesRegex(AssertionError, 'calendar'):
            audit_episode(self.result, self.context)

    def test_tampered_holding_is_rejected(self):
        self.result['holdings'].loc[0, 'shares'] += 1000
        with self.assertRaisesRegex(AssertionError, 'inventory'):
            audit_episode(self.result, self.context)

    def test_formal_active_share_pass_is_rejected(self):
        self.result['equity'].loc[0, 'active_share_status'] = 'PASS'
        with self.assertRaisesRegex(AssertionError, 'certification'):
            audit_episode(self.result, self.context)

    def test_drawdown_includes_initial_entry_cost(self):
        audit = audit_episode(self.result, self.context)
        self.assertGreater(audit['episode_max_drawdown'], 0.)
        self.result['metrics']['episode_max_drawdown'] = 0.
        with self.assertRaisesRegex(AssertionError, 'drawdown'):
            audit_episode(self.result, self.context)

    def test_disqualified_episode_retains_ledger_without_24d_return(self):
        dates = pd.DatetimeIndex(sorted(self.daily.date.unique()))[1:25]
        result = run_episode(self.daily, self.universe, self.config, dates)
        audit = audit_episode(result, self.context)
        self.assertFalse(audit['complete_period'])
        self.assertIsNone(audit['episode_return'])
        self.assertIsNotNone(audit['forensic_partial_return'])
        result['metrics']['episode_return'] = 0.
        with self.assertRaisesRegex(AssertionError, 'episode_return'):
            audit_episode(result, self.context)

    def test_missing_open_is_unfilled_and_not_silently_dropped(self):
        daily = self.daily.copy()
        daily.loc[daily.date.eq(self.dates[0]), 'open'] = np.nan
        result = run_episode(daily, self.universe, self.config, self.dates)
        audit = audit_episode(result, dict(self.context, daily=daily))
        self.assertGreater(audit['unfilled_orders'], 0)
        self.assertFalse(audit['measured_pass'])

    def test_missing_close_keeps_mark_and_failure_flag(self):
        daily = self.daily.copy()
        daily.loc[daily.date.eq(self.dates[2]), 'close'] = np.nan
        result = run_episode(daily, self.universe, self.config, self.dates)
        audit = audit_episode(result, dict(self.context, daily=daily))
        self.assertGreater(audit['stale_held_price_days'], 0)
        self.assertFalse(audit['measured_pass'])

    def test_dividend_credit_never_funds_an_order(self):
        daily = self.daily.copy()
        daily.loc[daily.date.eq(self.dates[-1]), 'dividend'] = 1.
        result = run_episode(daily, self.universe, self.config, self.dates)
        audit_episode(result, dict(self.context, daily=daily))
        final = result['equity'].iloc[-1]
        self.assertGreater(final.terminal_dividend_credit, 0)
        self.assertAlmostEqual(final.nav, result['compliance_daily'].iloc[-1].settled_nav
                               + final.terminal_dividend_credit)
        result['equity'].loc[result['equity'].index[-1], 'cash'] += final.terminal_dividend_credit
        with self.assertRaisesRegex(AssertionError, 'cash'):
            audit_episode(result, dict(self.context, daily=daily))


class StudyProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calendar = pd.bdate_range('2009-01-01', '2025-03-01')
        starts = pd.Series(cls.calendar, index=cls.calendar).groupby(cls.calendar.to_period('M')).first()
        rows = []
        for day in starts:
            if not 2010 <= day.year <= 2024:
                continue
            end = cls.calendar[cls.calendar.get_loc(day)+23]
            a, b = str(day.date()), str(end.date())
            rows.append(dict(episode_id='m_'+a[:7], kind='monthly', start=a, end=b,
                             split=interval_split(a,b), session_count=24, year=day.year, month=day.month))
        cls.episodes = pd.DataFrame(rows)

    def test_all_primary_episodes_and_cross_boundary_purging(self):
        audit = audit_episode_registry(self.episodes, self.calendar)
        self.assertEqual(audit['monthly_episodes'], 180)
        self.assertEqual(interval_split('2019-12-02', '2020-01-02'), 'purged')

    def test_missing_failed_month_cannot_change_denominator(self):
        with self.assertRaisesRegex(AssertionError, 'denominator'):
            audit_episode_registry(self.episodes.iloc[1:], self.calendar)

    def test_candidate_attempt_denominator_requires_failed_runs(self):
        rows = pd.DataFrame([dict(candidate_id='a', episode_id='one', measured_pass=True,
                                  complete_period=True, episode_return=.1),
                             dict(candidate_id='a', episode_id='two', measured_pass=False,
                                  complete_period=False, episode_return=None)])
        self.assertEqual(audit_attempt_coverage(rows, ['one','two'], ['a'])['failed'], 1)
        with self.assertRaisesRegex(AssertionError, 'denominator'):
            audit_attempt_coverage(rows.iloc[:1], ['one','two'], ['a'])

    def test_start_year_only_split_is_rejected(self):
        episodes = self.episodes.copy()
        episodes.loc[episodes.episode_id.eq('m_2019-12'), 'split'] = 'development'
        with self.assertRaisesRegex(AssertionError, 'Cross-boundary'):
            audit_episode_registry(episodes, self.calendar)

    def test_selection_rejects_holdout_leakage_and_false_readiness(self):
        candidates = pd.DataFrame({'candidate_id':['baseline']})
        selection = dict(candidate_id='baseline', required_measured_pass_rate=1.,
                         submission_status='BLOCK_SUBMISSION', ready_status='BLOCK_READY',
                         active_share_status='ACTIVE_SHARE_NOT_VERIFIED',
                         selection_episode_ids=['m_2010-01'], holdout_episode_ids=['m_2023-01'],
                         selection_frozen_at='2026-09-23T00:00:00Z', candidate_config_sha256='abc')
        audit_selection_provenance(selection, self.episodes, candidates,
                                   holdout_started_at='2026-09-23T00:01:00Z')
        selection['selection_episode_ids'] = ['m_2024-01']
        with self.assertRaisesRegex(AssertionError, 'holdout'):
            audit_selection_provenance(selection, self.episodes, candidates)
        selection['selection_episode_ids'] = ['m_2010-01']
        selection['ready_status'] = 'READY'
        with self.assertRaisesRegex(AssertionError, 'certified'):
            audit_selection_provenance(selection, self.episodes, candidates)

    def test_source_hash_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'config.json'
            path.write_text('{}')
            hashes = {'config.json':hashlib.sha256(path.read_bytes()).hexdigest()}
            verify_hashes(folder, hashes)
            path.write_text('{"future":true}')
            with self.assertRaisesRegex(AssertionError, 'hash mismatch'):
                verify_hashes(folder, hashes)


if __name__ == '__main__':
    unittest.main()
