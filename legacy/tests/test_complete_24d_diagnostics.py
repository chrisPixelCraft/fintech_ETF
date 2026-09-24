"""Exact monthly reuse and immutable continuous-book artifact diagnostics."""
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from scripts.complete_24d_diagnostics import (canonical_rows,continuous_job,_initialize,missing_groups)
from scripts.supplement_24d import registry
from scripts.run_24d import load_study
from src.strategy_24d import build_config
from tests.test_strategy_24d import fixture


class CompleteDiagnosticsTests(unittest.TestCase):
    def test_recent_aliases_reuse_same_monthly_identity(self):
        rows=pd.DataFrame([dict(candidate_id='baseline',episode_id='recent_2025-01-02',
            phase='recent_stress',start='2025-01-02',end='2025-02-06')])
        canonical=canonical_rows(rows)
        self.assertEqual(canonical.episode_id.iloc[0],'monthly_2025-01-02')
        self.assertEqual(canonical.source_episode_id.iloc[0],'recent_2025-01-02')
        self.assertEqual(canonical.source_phase.iloc[0],'recent_stress')

    def test_only_missing_cells_are_scheduled(self):
        episodes=registry(pd.bdate_range('2009-01-01','2026-09-23'))
        reused=pd.concat([episodes.iloc[:180].assign(candidate_id='first'),
                          episodes.assign(candidate_id='complete')],ignore_index=True)
        specs=[dict(candidate_id='first'),dict(candidate_id='complete')]
        groups=missing_groups(episodes,reused,specs)
        self.assertEqual(len(groups),1)
        self.assertEqual(groups[0][0],[dict(candidate_id='first')])
        self.assertEqual(len(groups[0][1]),20)
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            missing_groups(episodes,pd.concat([reused,reused.iloc[:1]]),specs)

    def test_continuous_receipt_resumes_and_rejects_tampering(self):
        daily,universe,_=fixture(count=10)
        daily['date']=pd.to_datetime(daily.date)+pd.DateOffset(years=1)
        calendar=sorted(pd.to_datetime(daily.date).dt.strftime('%Y-%m-%d').unique())
        # Retain a genuine prior observation before the requested 2010 start.
        warmup=daily.copy();warmup['date']-=pd.DateOffset(years=1)
        source=pd.concat([warmup,daily],ignore_index=True)
        calendar=sorted(pd.to_datetime(source.date).dt.strftime('%Y-%m-%d').unique())
        config=build_config();spec=dict(candidate_id='fixture',source='TEST')
        with tempfile.TemporaryDirectory() as temporary:
            ctx=dict(output=Path(temporary),daily=source,universe=universe,session_dates=calendar,
                     study=load_study(),guard_sha256='test')
            _initialize(ctx)
            result=continuous_job(spec,config)
            self.assertTrue(result['disqualified'])
            self.assertIsNone(result['long_horizon_return'])
            self.assertEqual(continuous_job(spec,config),result)
            path=Path(temporary)/'continuous/fixture/metrics.json'
            path.write_text('{}')
            with self.assertRaisesRegex(ValueError,'artifact changed'):
                continuous_job(spec,config)


if __name__=='__main__':
    unittest.main()
