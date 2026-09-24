import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from autots_strategy.strategy import AutoTSStrategyConfig
from research import tune


def write_run(root: Path, returns: dict, disqualified=()):
    for episode, value in returns.items():
        folder = root / 'episodes' / episode
        folder.mkdir(parents=True)
        (folder / 'summary.json').write_text(json.dumps(dict(
            episode_id=episode, status='COMPLETE', terminal_return=value, max_drawdown=.05, turnover=1.,
            warning_days=0, disqualified=episode in disqualified)))
    (root / 'manifest.json').write_text(json.dumps(dict(episode_ids=sorted(returns))))


class TuneSpaceTest(unittest.TestCase):
    def test_base_point_reproduces_baseline_autots(self):
        base = json.loads(tune.BASE.read_text())
        rebuilt = tune.to_config(tune.base_point(), base['name'])
        self.assertEqual(AutoTSStrategyConfig.from_dict(rebuilt['params']),
                         AutoTSStrategyConfig.from_dict(base['params']))
        for key in ('execution', 'planner', 'episodes'):
            self.assertEqual(rebuilt[key], base[key])

    def test_sampling_is_deterministic_and_valid(self):
        draw = lambda: [tune.random_point(np.random.default_rng(7), .2) for _ in range(1)]
        self.assertEqual(draw(), draw())
        rng = np.random.default_rng(1)
        points = [tune.random_point(rng, .2) for _ in range(40)]
        self.assertTrue(all(p['validation_step'] >= p['horizon'] for p in points))
        self.assertGreaterEqual(sum(tune.valid(p) for p in points), 35)

    def test_mutation_changes_one_or_two_keys(self):
        rng = np.random.default_rng(3)
        base = tune.base_point()
        for _ in range(20):
            changed = {k for k, v in tune.mutate(base, rng).items() if v != base[k]}
            self.assertTrue(1 <= len(changed) <= 2, changed)


class TuneScoreTest(unittest.TestCase):
    def test_score_covers_the_union_of_splits_but_only_requested_episodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            dev, val = Path(tmp) / 'dev', Path(tmp) / 'val'
            write_run(dev, {'dev_a': .02, 'dev_b': .00})
            write_run(val, {'val_a': .04})
            extra = dev / 'episodes' / 'dev_old'   # left over from an earlier, different subset
            extra.mkdir()
            (extra / 'summary.json').write_text(json.dumps(dict(
                episode_id='dev_old', status='COMPLETE', terminal_return=9., max_drawdown=0., turnover=0.)))
            reference = {k: dict(terminal_return=0.) for k in ('dev_a', 'dev_b', 'val_a', 'dev_old')}
            result = tune.score_runs([dev, val], reference)
            self.assertEqual(result['n'], 3)
            self.assertAlmostEqual(result['mean_return'], .02)

    def test_score_is_half_mean_half_median_of_excess(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / 'run'
            write_run(run, {'a': .03, 'b': .01, 'c': -.02})
            reference = {k: dict(terminal_return=v) for k, v in {'a': .01, 'b': .01, 'c': .01}.items()}
            result = tune.score_runs([run], reference)
            excess = np.array([.02, 0., -.03])
            self.assertAlmostEqual(result['score'], .5 * excess.mean() + .5 * np.median(excess))
            self.assertAlmostEqual(result['win_rate'], 1 / 3)

    def test_disqualified_episode_scores_minus_infinity(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / 'run'
            write_run(run, {'a': .05, 'b': .05}, disqualified={'b'})
            reference = {'a': dict(terminal_return=0.), 'b': dict(terminal_return=0.)}
            self.assertEqual(tune.score_runs([run], reference)['score'], -np.inf)


class TuneResultFilesTest(unittest.TestCase):
    def test_result_files_summarise_the_pick(self):
        with tempfile.TemporaryDirectory() as tmp:
            tuner = tune.Tuner('t', 'quick', 2, 1, runs_dir=Path(tmp) / 'runs', results_dir=Path(tmp) / 'results')
            point = tune.base_point()
            pid = tuner.add(point)
            metrics = dict(n=4, failed=0, score=.01, mean_excess=.01, median_excess=.01, win_rate=.75,
                           mean_return=.02, median_return=.02, p25_return=-.01, mean_max_drawdown=.05,
                           mean_turnover=1., warning_days=0, disqualified=0)
            tuner.state['scores'].update({pid: metrics, f'{pid}@full': metrics,
                                          f'{pid}@test': dict(metrics, mean_excess=-.004)})
            tuner.state['seed_groups'][pid] = dict(members=[pid], sensitive=False, seed_mean=.01, seed_std=0.,
                                                   n_seeds=1)
            tuner.state['pick'] = pid
            tuner.state['done'] = ['screen', 'confirm', 'seeds', 'test']
            tuner.save()
            tuner.write_outputs()
            folder = Path(tmp) / 'results' / 'tune_t'
            for name in ('summary.md', 'summary.json', 'leaderboard.csv', 'best_config.json'):
                self.assertTrue((folder / name).exists(), name)
            summary = json.loads((folder / 'summary.json').read_text())
            self.assertEqual(summary['meta']['status'], 'COMPLETE')
            self.assertEqual(summary['best']['config'], pid)
            self.assertEqual(summary['best']['verdict'], 'FAIL')
            text = (folder / 'summary.md').read_text()
            self.assertIn(pid, text)
            self.assertIn('**FAIL**', text)
            best = json.loads((folder / 'best_config.json').read_text())
            self.assertEqual(best['params'], tune.to_config(point, 'x')['params'])


if __name__ == '__main__':
    unittest.main()
