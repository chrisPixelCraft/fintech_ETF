import contextlib
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from research import compare, run_experiment
from research.run_experiment import HOLDOUT_LOG, ROOT

MOMENTUM = ROOT / 'research/configs/baselines/momentum_20d.json'
BASKET = ROOT / 'research/configs/baselines/largecap_basket.json'


def quiet(fn, *args):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*args)


class RunExperimentTest(unittest.TestCase):
    def test_holdout_requires_flag(self):
        before = HOLDOUT_LOG.read_text() if HOLDOUT_LOG.exists() else None
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            run_experiment.main(['--config', str(MOMENTUM), '--split', 'holdout'])
        after = HOLDOUT_LOG.read_text() if HOLDOUT_LOG.exists() else None
        self.assertEqual(before, after)

    def test_manifest_resume_registry_compare(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, reg = Path(tmp) / 'run', Path(tmp) / 'registry.csv'
            args = ['--config', str(MOMENTUM), '--split', 'dev', '--episodes', '2', '--out', str(out),
                    '--registry', str(reg)]
            manifest = quiet(run_experiment.main, args)
            for key in ('run_id', 'status', 'config_hash', 'git', 'autots', 'data', 'split', 'runtime_seconds',
                        'episode_ids', 'environment'):
                self.assertIn(key, manifest)
            self.assertEqual(manifest['status'], 'COMPLETE')
            self.assertEqual(len(manifest['git']['commit']), 40)
            self.assertIn('dirty', manifest['git'])
            self.assertEqual(manifest['autots']['upstream_commit'], 'd35f3189e0d2bab84732e957ca3f4c737da08bf0')
            self.assertTrue(manifest['autots']['local_patches'][0].startswith('P1'))
            self.assertEqual(len(manifest['data']['files']), 4)
            self.assertTrue((out / 'config.json').exists())
            for e in manifest['episode_ids']:
                folder = out / 'episodes' / e
                for name in ('ledger.csv', 'trades.csv', 'orders.csv', 'holdings.csv', 'summary.json'):
                    self.assertTrue((folder / name).exists(), name)
                summary = json.loads((folder / 'summary.json').read_text())
                self.assertEqual(summary['verification_problems'], [])
            log = io.StringIO()
            with contextlib.redirect_stdout(log):
                run_experiment.main(args)                     # resume: nothing left to run
            self.assertIn('2 already complete, running 0', log.getvalue())
            rows = list(csv.DictReader(reg.open()))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]['n_episodes'], '2')
            with self.assertRaises(SystemExit):              # different config into the same folder
                quiet(run_experiment.main, ['--config', str(BASKET), '--split', 'dev', '--episodes', '2',
                                            '--out', str(out), '--registry', str(reg)])
            other = Path(tmp) / 'basket'
            quiet(run_experiment.main, ['--config', str(BASKET), '--split', 'dev', '--episodes', '2',
                                        '--out', str(other), '--registry', str(reg)])
            result = compare.compare(out, other, n_boot=200)
            self.assertEqual(result['n_common'], 2)
            self.assertLessEqual(result['ci95'][0], result['mean_diff'])


if __name__ == '__main__':
    unittest.main()
