import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd

import scripts.v5_report as report
import scripts.v5_run as run
import scripts.v5_verify as verify

REPO = Path(__file__).resolve().parents[1]


def rec(candidate, episode, value, split='validation', family='A', complete=True, measured=True, start=None):
    return dict(candidate=candidate, episode=episode, split=split, family=family, complete=complete,
                episode_return=value if complete else None, measured_pass=measured, mdd=.05, turnover=1.,
                cost=1e6, day1_invested=.9, day3_invested=.95, day5_invested=.95,
                start=start or f'2019-{(episode % 12) + 1:02d}-01', assumption_odd_lot_days=0)


class GridDeclaration(unittest.TestCase):
    def test_grid_counts_ids_and_axes(self):
        grids = json.loads((REPO / 'config/v5_grids.json').read_text())
        ids = [c['id'] for c in grids['candidates']]
        self.assertEqual(len(ids), len(set(ids)))
        for family in 'ABCE':
            cands = [c for c in grids['candidates'] if c['family'] == family]
            self.assertLessEqual(len(cands), 12)
            keys = [json.dumps([c['signal_config'], c['construction']], sort_keys=True) for c in cands]
            self.assertEqual(len(keys), len(set(keys)), 'duplicate declared configuration')
            for c in cands:
                self.assertEqual(set(c['axes']), set(grids['axes'][family]))
                self.assertEqual(grids['signals'][c['signal']]['config'], c['signal_config'])
                self.assertEqual(c['construction']['method'], 'topn_equal')
                self.assertIn(c['construction']['n'], (22, 26))
        self.assertEqual(len([c for c in grids['candidates'] if c['family'] == 'A']), 12)
        self.assertEqual(len(grids['s2_treatments']), 5)
        self.assertEqual(grids['baseline']['id'], 'A0_V3')
        for family in 'BCE':
            self.assertEqual(sum(c['declared_default'] for c in grids['candidates'] if c['family'] == family), 1)


class Statistics(unittest.TestCase):
    def test_pairs_only_both_complete_episodes(self):
        records = [rec('X', e, .02) for e in range(10)]
        records += [rec('V3', e, .01, family='V3', complete=e >= 3) for e in range(10)]
        p = run.paired(records, 'X', 'V3')
        self.assertEqual(p['n'], 7)
        self.assertAlmostEqual(p['median_delta'], .01)
        records[0]['complete'] = False
        self.assertEqual(run.paired(records, 'X', 'V3')['n'], 7)

    def test_rank_order_and_ties(self):
        records = [rec('B01', e, .01 * (e % 5), family='B') for e in range(20)]
        records += [rec('A01', e, .01 * (e % 5), family='A') for e in range(20)]
        records += [rec('C01', e, .05, family='C', complete=e > 5) for e in range(20)]
        ranked = run.rank(run.summarize(records))
        self.assertEqual(list(ranked.candidate), ['A01', 'B01', 'C01'])  # C infeasible despite best median

    def test_behaviour_dedupe_and_stability(self):
        cands = [dict(id=f'A0{i}', family='A', axes=dict(x=x, y=y)) for i, (x, y) in
                 enumerate([(0, 0), (1, 0), (0, 1), (1, 1)], 1)]
        records = []
        for e in range(20):
            records += [rec('A01', e, .02), rec('A02', e, .02), rec('A03', e, .015), rec('A04', e, .0)]
        rep = run.dedupe_behaviour(records, cands)
        self.assertEqual(rep['A02'], 'A01')
        summary = run.summarize(records)
        s = run.stability('A01', cands, ['x', 'y'], summary, rep)
        self.assertFalse(s['passed'])                         # A02 is a duplicate: only 1 distinct neighbour
        self.assertEqual(s['distinct'], 1)
        records += [rec('A05', e, .012) for e in range(20)]
        cands.append(dict(id='A05', family='A', axes=dict(x=2, y=0)))
        rep = run.dedupe_behaviour(records, cands)
        s = run.stability('A01', cands, ['x', 'y'], run.summarize(records), rep)
        self.assertTrue(s['passed'])                          # A03 (-0.5pp) and A05 (-0.8pp)
        records = [r for r in records if r['candidate'] != 'A05'] + [rec('A05', e, .005) for e in range(20)]
        s = run.stability('A01', cands, ['x', 'y'], run.summarize(records), run.dedupe_behaviour(records, cands))
        self.assertFalse(s['passed'])                         # A05 1.5pp below the winner

    def test_s2_keep_boundaries(self):
        self.assertTrue(run.s2_keep(dict(n=10, median_delta=0., p25_delta=-.005)))
        self.assertFalse(run.s2_keep(dict(n=10, median_delta=-1e-6, p25_delta=0.)))
        self.assertFalse(run.s2_keep(dict(n=10, median_delta=.01, p25_delta=-.0051)))
        self.assertFalse(run.s2_keep(dict(n=0, median_delta=float('nan'), p25_delta=float('nan'))))

    def gates(self, x_shift, v3_complete=lambda e: True, a_shift=0.):
        base = np.linspace(-.05, .05, 40)
        records = [rec('A0_V3', e, base[e], family='V3', complete=v3_complete(e)) for e in range(40)]
        records += [rec('A01', e, base[e] + a_shift) for e in range(40)]
        records += [rec('B01', e, base[e] + x_shift, family='B') for e in range(40)]
        stab = {'A01': dict(passed=True), 'B01': dict(passed=True)}
        return run.validation_gates(records, ['A01', 'B01'], 'A0_V3', stab, 'A01')

    def test_tail_tolerance_and_complexity(self):
        g, summary = self.gates(0.)
        self.assertTrue(g['B01']['passes_pre_holdout'])
        g, _ = self.gates(-.001)
        self.assertFalse(g['B01']['v3_median'])
        g, _ = self.gates(.002, a_shift=.003)
        self.assertTrue(g['B01']['v3_median'] and g['B01']['v3_p25'])
        self.assertFalse(g['B01']['complexity_earned'])
        self.assertEqual(run.choose_preferred(*self.gates(.002, a_shift=.003)), 'A01')
        g, _ = self.gates(.001, v3_complete=lambda e: e % 4 != 0)   # incomplete V3 episodes are unpaired, not fatal
        self.assertEqual(g['B01']['v3']['n'], 30)
        self.assertTrue(g['B01']['v3_median'])

    def test_preferred_ties_break_on_ranking(self):
        g, summary = self.gates(0.)
        self.assertEqual(run.choose_preferred(g, summary), 'A01')   # equal medians -> simplicity A < B

    def test_final_gates_holdout_can_only_reject(self):
        g, _ = self.gates(.002)
        freeze = dict(baseline_id='A0_V3', preferred='B01', gates=g)
        records = [rec('A0_V3', e, .01, split='validation', family='V3') for e in range(40)]
        records += [rec('B01', e, .012, split='validation', family='B') for e in range(40)]
        records += [rec('A01', e, .012, split='validation') for e in range(40)]
        for e in range(40, 64):
            records += [rec('A0_V3', e, .02, split='holdout_retrospective', family='V3'),
                        rec('B01', e, .01, split='holdout_retrospective', family='B'),
                        rec('A01', e, .03, split='holdout_retrospective')]
        outcome, final = run.final_gates(records, freeze)
        self.assertEqual(outcome, 'NO_V5_WINNER')
        self.assertFalse(final['B01']['holdout_not_reversed'])
        self.assertTrue(final['A01']['stable_candidate'])          # not preferred: no swap after holdout

    def test_block_bootstrap_is_deterministic(self):
        d = np.arange(12) / 100
        q = [f'2019Q{i % 4 + 1}' for i in range(12)]
        self.assertEqual(report.block_bootstrap(d, q), report.block_bootstrap(d, q))


class EventChain(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_tamper_detection(self):
        for name in ('a', 'b', 'c'):
            run.event(self.tmp, name, dict(value=name))
        run.verify_chain(run.read_events(self.tmp))
        path = self.tmp / 'events.jsonl'
        lines = path.read_text().splitlines()
        row = json.loads(lines[1])
        row['payload']['value'] = 'forged'
        path.write_text('\n'.join([lines[0], json.dumps(row), lines[2]]) + '\n')
        with self.assertRaises(ValueError):
            run.verify_chain(run.read_events(self.tmp))
        path.write_text('\n'.join([lines[0], lines[2]]) + '\n')
        with self.assertRaises(ValueError):
            run.verify_chain(run.read_events(self.tmp))
        with self.assertRaises(ValueError):
            run.event(self.tmp, 'd', {})

    def test_stage_order_and_holdout_before_freeze(self):
        run.event(self.tmp, 'study_start', {})
        with self.assertRaises(RuntimeError):
            run.require_stage(self.tmp, 's1')
        study = run.Study.__new__(run.Study)
        study.output = self.tmp
        with self.assertRaises(RuntimeError):
            study.holdout()
        (self.tmp / 'freeze.json').write_text('{}')
        with self.assertRaises(RuntimeError):     # a freeze file without its chained event
            study.holdout()


# ----------------------------------------------------------- end to end
class _Pred:
    def __init__(self, frame):
        self.frame = frame

    def predictions(self):
        return self.frame


def stub_episode(candidate, episode):
    """Deterministic ledger-shaped result, consistent with the verifier's reconstruction."""
    seed = int(run.canonical([candidate.get('signal_config'), candidate.get('construction'), episode['episode_id']])[:8], 16)
    rng = np.random.default_rng(seed)
    drift = rng.normal(.01, .02)
    initial, commission, lot = 1e9, .001425, 1000
    sessions = episode['sessions']
    names = [f'{1100 + i}.TW' for i in range(22)]
    incomplete = candidate['family'] == 'V3' and episode['episode_id'].endswith(('_03', '_07'))
    days = sessions[:10] if incomplete else sessions
    shares = 40 * lot
    price = 100.
    notional = shares * price
    trades = pd.DataFrame([dict(date=days[0], signal_date=episode['prior_session_date'], symbol=s, shares=shares,
                                price=price, notional=notional, fee=notional * commission, tax=0.) for s in names])
    cash = initial - (notional + notional * commission) * len(names)
    equity, holdings = [], []
    for k, d in enumerate(days, 1):
        close = price * (1 + drift * k / 24)
        nav = cash + len(names) * shares * close
        equity.append(dict(date=d, nav=nav, economic_nav=nav, cash=cash, holdings=len(names), odd_residual_names=0,
                           fees=trades.fee.sum() if k == 1 else 0., taxes=0., violations='',
                           active_cap_breaches='', passive_cap_breaches=''))
        holdings += [dict(date=d, symbol=s, shares=shares, close=close) for s in names]
    equity = pd.DataFrame(equity)
    complete = not incomplete
    final = float(equity.economic_nav.iloc[-1] / initial - 1)
    metrics = dict(complete_episode=complete, episode_return=final if complete else None,
                   forensic_partial_return=None if complete else final, measured_pass=complete,
                   canonical_status='AVAILABLE', episode_max_drawdown=.03, episode_turnover=.9,
                   transaction_cost=float(trades.fee.sum()), day_1_invested_ratio=.9, day_3_invested_ratio=.9,
                   day_5_invested_ratio=.9, assumption_odd_lot_days=0)
    config = dict(initial_cash=initial, commission=commission, sell_tax=.003, lot_size=lot, max_weight=.1,
                  tsmc_max_weight=.25, start=sessions[0], end=sessions[-1])
    result = dict(metrics=metrics, config=config, equity=equity, trades=trades, holdings=pd.DataFrame(holdings))
    if candidate['family'] == 'V3':
        return result, None
    all_days = [episode['prior_session_date'], *sessions]
    pred = pd.DataFrame(dict(decision_date=sessions, observed_date=all_days[:-1]))
    return result, _Pred(pred)


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.patches = [mock.patch.object(run, 'ROOT', self.root), mock.patch.object(verify, 'ROOT', self.root),
                        mock.patch.object(report, 'ROOT', self.root)]
        for p in self.patches:
            p.start()
        (self.root / 'src').mkdir()
        (self.root / 'scripts').mkdir()
        for name in ('src/v5_momentum.py', 'src/v5_ensemble.py', 'src/v5_rank.py', 'src/v5_direct.py',
                     'scripts/v5_run.py'):
            (self.root / name).write_text('# stub\n')
        data = self.root / 'data'
        data.mkdir()
        for name in ('daily.parquet', 'universe.csv', 'execution.csv', 'base.json'):
            (data / name).write_text('stub ' + name)
        dates = pd.bdate_range('2010-01-01', periods=24 * 40)
        registry = []
        splits = ['development'] * 16 + ['validation'] * 8 + ['holdout_retrospective'] * 4 + ['diagnostic_seasonal'] * 2
        for i, split in enumerate(splits):
            block = dates[i * 30 + 1:i * 30 + 25]
            registry.append(dict(episode_id=f'{split}_{i:02d}', split=split, start=str(block[0].date()),
                                 prior_session_date=str(dates[i * 30].date()),
                                 sessions=[str(d.date()) for d in block]))
        (data / 'episodes.json').write_text(json.dumps(registry))
        (self.root / 'config').mkdir()
        (self.root / 'config/study.json').write_text(json.dumps(dict(
            inputs=dict(daily='data/daily.parquet', universe='data/universe.csv', base_config='data/base.json'),
            outputs=dict(episode_registry='data/episodes.json', execution_data='data/execution.csv'))))
        grids = json.loads((REPO / 'config/v5_grids.json').read_text())
        keep = {'A01', 'A02', 'A05', 'A06', 'A09', 'B05', 'B06', 'B07', 'B09', 'C05', 'C07', 'C08', 'E04', 'E08', 'E06'}
        grids['candidates'] = [c for c in grids['candidates'] if c['id'] in keep]
        grids['signals'] = {k: v for k, v in grids['signals'].items() if k in {c['signal'] for c in grids['candidates']}}
        (self.root / 'config/grids.json').write_text(json.dumps(grids))
        self.grids = grids

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.root)

    def fake_features(self, paths, cache=run.CACHE):
        cache = run.repo(cache)
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / 'features.pkl'
        pd.DataFrame(dict(date=[pd.Timestamp('2010-01-01')], symbol=['1100.TW'])).to_pickle(path)
        run.write_json(cache / 'features.manifest.json', dict(inputs={'data/daily.parquet': 'x'}, features_sha256=run.sha256(path)))
        return path

    def fake_scores(self, family, panel, dates, config):
        return pd.DataFrame(dict(decision_date=dates[:2], symbol='1100.TW', score=[int(run.canonical(config)[:6], 16) % 97, 1.]))

    def test_full_protocol_verify_and_report(self):
        with mock.patch.object(run, 'ensure_features', self.fake_features), \
                mock.patch('src.v5_strategy.build_score_table', self.fake_scores), \
                mock.patch.object(run, 'run_candidate_episode', stub_episode), \
                mock.patch.object(run, '_init_worker', lambda *a: None):
            study = run.Study('outputs/v5/study', 'config/study.json', 'config/grids.json', workers=1)
            with self.assertRaises(RuntimeError):
                study.s2()
            with self.assertRaises(RuntimeError):
                study.holdout()
            study.s1()
            study.s1()                                  # idempotent resume
            study.s2()
            study.validation()
            freeze = study.freeze()
            self.assertEqual(set(freeze['candidates']), set('ABCE'))
            study.holdout()
            out = study.output
            dev = pd.read_csv(out / 'development.csv')
            self.assertEqual(set(dev.episode), {e for e in dev.episode if e.startswith('development')})
            self.assertEqual(dev.candidate.nunique(), 16)   # 15 declared (no score duplicates) + A0_V3
            manifest = json.loads((out / 'manifest.json').read_text())
            self.assertFalse(any(Path(k).is_absolute() for k in manifest['outputs']))
            # Resuming re-verifies sealed evidence; corrupt evidence fails closed.
            victim = run.repo(dev.path.iloc[0]) / 'equity.csv'
            original = victim.read_text()
            result = verify.verify('outputs/v5/study', sample=40)
            self.assertEqual(result['status'], 'PASS', result['failures'])
            decision = report.build('outputs/v5/study')
            self.assertIn(decision['outcome'], ('STABLE_CANDIDATE', 'NO_V5_WINNER'))
            text = (self.root / 'reports/v5_final.md').read_text()
            for number in range(1, 8):
                self.assertIn(f'### {number}.', text)
            self.assertEqual((self.root / 'configs/v5_final.json').exists(), decision['outcome'] == 'STABLE_CANDIDATE')
            victim.write_text(original.replace('1', '2', 1))
            self.assertEqual(verify.verify('outputs/v5/study', sample=40)['status'], 'FAIL')
            with self.assertRaises(SystemExit):
                report.build('outputs/v5/study')
            victim.write_text(original)
            with self.assertRaises(ValueError):
                run.evaluate((copy.deepcopy(dict(freeze['candidates']['A'], construction=dict(n=26))),
                              [e for e in study.registry if e['episode_id'] == 'validation_16'], str(out)))

    def test_verifier_flags_holdout_before_freeze_and_order(self):
        out = self.root / 'study'
        out.mkdir()
        run.event(out, 'study_start', {})
        attempt = dict(candidate='A01', episode='h1', split='holdout_retrospective', run_started_at=run.now())
        (out / 'freeze.json').write_text(json.dumps(dict(frozen_at=run.now())))
        run.event(out, 'freeze', dict(freeze_sha256=run.sha256(out / 'freeze.json')))
        (out / 'manifest.json').write_text('{}')
        run.event(out, 'manifest', dict(manifest_sha256=run.sha256(out / 'manifest.json')))
        audit = verify.Audit()
        verify.check_events(audit, out, json.loads((out / 'freeze.json').read_text()), [attempt])
        self.assertTrue(any('before the freeze' in f for f in audit.failures))
        self.assertTrue(any('Missing stage events' in f for f in audit.failures))

if __name__ == '__main__':
    unittest.main()
