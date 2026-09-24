"""Scientific launch gates fail before partial availability can select windows."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.v4_stage2_run import require_official_cache


class LaunchGateTests(unittest.TestCase):
    def test_incomplete_acquisition_never_launches_search(self):
        with tempfile.TemporaryDirectory() as d:
            table=Path(d)/'execution.csv'
            table.with_suffix('.manifest.json').write_text(json.dumps(dict(attempts=[
                dict(date='2020-01-01',market='TWSE',status='FAILED'),
                dict(date='2020-01-01',market='TPEx',status='OK')])))
            with patch('scripts.v4_verify.verify_execution_provenance') as verify:
                with self.assertRaisesRegex(ValueError,'BLOCK_CANONICAL_V4'):
                    require_official_cache(dict(execution_data=str(table)),[
                        dict(prior_session_date='2020-01-01',sessions=[])])
                verify.assert_not_called()

    def test_complete_requests_still_require_independent_raw_audit(self):
        with tempfile.TemporaryDirectory() as d:
            table=Path(d)/'execution.csv'
            table.with_suffix('.manifest.json').write_text(json.dumps(dict(attempts=[
                dict(date='2020-01-01',market=m,status='OK') for m in ('TWSE','TPEx')])))
            with patch('scripts.v4_verify.verify_execution_provenance',side_effect=ValueError('bad raw')):
                with self.assertRaisesRegex(ValueError,'bad raw'):
                    require_official_cache(dict(execution_data=str(table)),[
                        dict(prior_session_date='2020-01-01',sessions=[])])
