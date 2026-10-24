"""Mechanical execution of the frozen K3 preregistration; no strategy changes."""
from pathlib import Path
import sys
sys.dont_write_bytecode = True
import hashlib
import json
import multiprocessing as mp
import subprocess
import time
import traceback
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from competition import backtest
from competition.data import load_market
from competition.episodes import Episode, build_episodes
from competition.rules import ROOT, load_rules
from production import engine

DEST = Path(__file__).resolve().parent
sys.path.insert(0, str(DEST))
FROZEN = {
    DEST / 'PREREGISTRATION.md': 'f22f894156ff740754bffbcf712b08dd5aabf8d6cfdad6bfb1429b3b600a06ab',
    DEST / 'strategies.py': 'ff357195946691a22691dd604e2086d2d228c9d60faf2e30d5182182a0a0d85d',
    DEST.parent / 'conc_tournament/strategies.py': 'c05cb770ff1eaec3ffec8f61d10cfc8e18dc693b7aecb5373402e0c6f6c31121',
    DEST.parent / 'conc_tournament/results.csv': 'de312da76203b2b6e82bcc590bb593814d701a66af1a393caa11cea26444303a',
}
NAMES = ['K3.0', 'K3.1']
CONTROLS = ['K2', 'K1', 'T2', 'Mom20']
WORKERS = 4
COMMAND = 'PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/k3_composite/run.py'
LOG = ROOT / 'research/holdout_access_log.jsonl'
ROWS, PROBLEMS, BUILDS, DIAGNOSTICS = [], {}, [], []
STARTED = time.perf_counter()


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_frozen():
    for path, expected in FROZEN.items():
        assert digest(path) == expected, f'Frozen hash mismatch: {path}'


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + '\n')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True)


def warnings(result):
    return [dict(date=str(r.date), warning_reasons=str(r.warning_reasons))
            for r in result['ledger'].itertuples() if r.warning]


def simulate(ep, strategy):
    result = backtest.run_episode(MARKET, ep, strategy, RULES, engine.policy_of(BASE),
                                  backtest.ExecutionConfig(**BASE['execution']))
    problems = backtest.verify_episode(MARKET, ep, result, RULES)
    assert result['summary']['complete'], (ep.episode_id, 'incomplete')
    return result, problems


def execute(job):
    name, ep, dataset = job
    cls = STRATEGIES.K30Average if name == 'K3.0' else STRATEGIES.K31ConfirmedMomentum
    strategy = cls(MARKET.calendar, RULES, MARKET.close.columns)
    result, problems = simulate(ep, strategy)
    s = result['summary']
    reasons = warnings(result)
    assert len(reasons) == s['warning_days']
    row = dict(strategy=name, window_id=ep.episode_id, set=dataset, split=ep.split,
               start=str(ep.start.date()), end=str(ep.end.date()),
               **{k: s[k] for k in ['terminal_return', 'max_drawdown', 'costs', 'turnover',
                                    'warning_days', 'disqualified', 'benchmark_0050_return', 'complete']},
               delta_vs_0050=s['terminal_return'] - s['benchmark_0050_return'],
               verify_problem_count=len(problems), warning_reasons=json.dumps(reasons, allow_nan=False))
    assert all(np.isfinite(v) for v in row.values() if isinstance(v, (int, float)))
    if dataset == 'recent':
        folder = DEST / 'episodes' / f'{name}_{ep.episode_id}'
        folder.mkdir(parents=True, exist_ok=True)
        for key in ['ledger', 'trades', 'holdings', 'issues']:
            result[key].to_csv(folder / f'{key}.csv', index=False)
        dump(folder / 'strategy_log.json', strategy.log)
    builds = [dict(strategy=name, window_id=ep.episode_id, set=dataset, **entry)
              for entry in strategy.log if name == 'K3.1' and entry['action'] == 'build']
    return row, problems, builds


def collect(value, pilot=False):
    row, problems, builds = value
    ROWS.append(row)
    PROBLEMS[f"{row['strategy']}_{row['window_id']}"] = problems
    BUILDS.extend(builds)
    if pilot:
        assert not problems, (row, problems)
    if pilot or len(ROWS) % 40 == 0:
        print('PILOT' if pilot else 'PROGRESS', len(ROWS), row['strategy'], row['window_id'],
              'verify', len(problems), flush=True)


def metric(frame):
    delta = frame.delta_vs_0050
    up, down = float((delta >= .05).mean()), float((delta <= -.05).mean())
    return dict(count=len(frame), mean_delta_vs_0050=float(delta.mean()),
                median_delta_vs_0050=float(delta.median()), win_rate=float((delta > 0).mean()),
                p_gain_5pp=up, p_gain_10pp=float((delta >= .10).mean()),
                p_gain_15pp=float((delta >= .15).mean()), p_loss_5pp=down, tail_edge=up-down,
                p90_max_drawdown=float(frame.max_drawdown.quantile(.9)),
                total_warning_days=int(frame.warning_days.sum()), max_warning_days=int(frame.warning_days.max()),
                disqualifications=int(frame.disqualified.sum()), mean_turnover=float(frame.turnover.mean()),
                mean_costs=float(frame.costs.mean()))


def evaluate(stats, blocks):
    control = stats['historical']['K2']
    gates = {}
    for name in NAMES:
        h, r = stats['historical'][name], stats['recent'][name]
        positive = sum(b['tail_edge'] > 0 for b in blocks if b['strategy'] == name)
        g = dict(
            G0=dict(passed=h['disqualifications'] == 0 and h['total_warning_days'] == 0,
                    disqualifications=h['disqualifications'], maximum_disqualifications=0,
                    total_warning_days=h['total_warning_days'], maximum_total_warning_days=0),
            G1=dict(passed=h['p_gain_5pp'] >= control['p_gain_5pp'] + .03 and
                           h['p_gain_10pp'] >= control['p_gain_10pp'] + .02,
                    p_gain_5pp=h['p_gain_5pp'], minimum_gain_5pp=control['p_gain_5pp'] + .03,
                    p_gain_10pp=h['p_gain_10pp'], minimum_gain_10pp=control['p_gain_10pp'] + .02),
            G2=dict(passed=h['p_loss_5pp'] <= control['p_loss_5pp'] + .03,
                    p_loss_5pp=h['p_loss_5pp'], maximum_loss_5pp=control['p_loss_5pp'] + .03),
            G3=dict(passed=positive >= 3, positive_blocks=positive, required_positive_blocks=3,
                    blocks=[b for b in blocks if b['strategy'] == name]),
            G4=dict(passed=r['mean_delta_vs_0050'] >= -.02,
                    recent_mean_delta_vs_0050=r['mean_delta_vs_0050'], minimum=-.02))
        g['failing_gates'] = [k for k in ['G0', 'G1', 'G2', 'G3', 'G4'] if not g[k]['passed']]
        g['all_passed'] = not g['failing_gates']
        gates[name] = g
    passing = [n for n in NAMES if gates[n]['all_passed']]
    selected = [n for n in passing if stats['historical'][n]['tail_edge'] ==
                max(stats['historical'][s]['tail_edge'] for s in passing)]
    closest = [n for n in NAMES if len(gates[n]['failing_gates']) ==
               min(len(gates[s]['failing_gates']) for s in NAMES)] if not passing else []
    return dict(candidates=gates, k2_control=control, passing_candidates=passing,
                selected_candidates=selected, closest_candidates_if_none_pass=closest,
                closest_definition='Fewest failing gates; ties retained, descriptive only (not preregistered).',
                verdict='Propose ' + ', '.join(selected) + ' for user decision' if passing else 'Maintain K2',
                automatic_strategy_change=False)


def main():
    global MARKET, RULES, BASE, STRATEGIES
    check_frozen()
    before_status = git('status', '--porcelain')
    assert len(before_status.splitlines()) == 46, 'Unexpected starting Git status'
    log_before = LOG.read_bytes()
    assert len(log_before.splitlines()) == 94 and log_before.endswith(b'\n')
    protected = {p: digest(p) for p in DEST.iterdir() if p.is_file()}
    import strategies as STRATEGIES
    BASE = json.loads((ROOT / 'production/strategy.json').read_text())
    RULES = load_rules()
    source = ROOT / 'production_runs/preflight_20261026'
    MARKET = load_market(yahoo_path=source / 'strategy_retest/corrected_nominal_daily.parquet',
                         official_path=source / 'official_combined.csv', calendar_path=source / 'calendar_v2.json')
    historical = [e for s in ('dev', 'validation', 'holdout') for e in build_episodes(
        MARKET.calendar, s, offsets=('month_start', 'mid_month'), start='2015-01-01', data_end='2026-10-22')]
    assert len(historical) == 276
    reference = pd.read_csv(DEST.parent / 'conc_tournament/results.csv', float_precision='round_trip')
    assert set(reference.strategy) == set(CONTROLS)
    assert len(reference) == 1124 and not reference.duplicated(['strategy', 'window_id']).any()
    end = MARKET.position('2026-10-22') + 1
    recent = []
    for i in range(5):
        days = tuple(MARKET.calendar[end - 120 + i*24:end - 96 + i*24])
        assert len(days) == 24
        recent.append(Episode(f'W{i+1}', 'recent_diagnostic', MARKET.calendar[MARKET.position(days[0])-1], days))
    assert recent[0].start == pd.Timestamp('2026-04-30')
    assert recent[-1].start == pd.Timestamp('2026-09-16') and recent[-1].end == pd.Timestamp('2026-10-22')
    windows = [(e, 'historical') for e in historical] + [(e, 'recent') for e in recent]
    for ep, dataset in windows:
        refs = reference[reference.window_id == ep.episode_id]
        assert len(refs) == 4
        assert all(r.set == dataset and r.split == ep.split and pd.Timestamp(r.start) == ep.start and
                   pd.Timestamp(r.end) == ep.end for r in refs.itertuples())
    for ep in historical[:3]:
        for name in NAMES:
            collect(execute((name, ep, 'historical')), pilot=True)
    assert LOG.read_bytes() == log_before
    with LOG.open('ab') as out:
        for name in NAMES:
            record = dict(utc=datetime.now(timezone.utc).isoformat(), run_id=f'{name}__holdout_k3_composite',
                          config=str(DEST / 'strategies.py'), config_hash=digest(DEST / 'strategies.py'),
                          git_commit=git('rev-parse', 'HEAD').strip(), git_dirty=bool(before_status), argv=sys.argv)
            out.write((json.dumps(record) + '\n').encode())
    jobs = [(name, ep, dataset) for ep, dataset in windows if ep not in historical[:3] for name in NAMES]
    with mp.get_context('fork').Pool(WORKERS) as pool:
        for value in pool.imap(execute, jobs, chunksize=1):
            collect(value)
    by_id = {ep.episode_id: ep for ep, _ in windows}
    diagnostic_refs = reference[(reference.strategy == 'K1') & (reference.warning_days > 0)]
    assert len(diagnostic_refs) == 7
    for ref in diagnostic_refs.itertuples():
        result, problems = simulate(by_id[ref.window_id], STRATEGIES.v4.MomConc(RULES))
        actual = result['summary']['terminal_return']
        reasons = warnings(result)
        assert abs(actual - ref.terminal_return) <= 1e-12
        assert len(reasons) == ref.warning_days and not problems
        DIAGNOSTICS.append(dict(window_id=ref.window_id, set=ref.set, stored_return=ref.terminal_return,
                                rerun_return=actual, abs_diff=abs(actual-ref.terminal_return), tolerance=1e-12,
                                warning_days=len(reasons), verify_problem_count=len(problems), matched=True,
                                warning_reasons=json.dumps(reasons, allow_nan=False)))
    frame = pd.DataFrame(ROWS).sort_values(['set', 'start', 'window_id', 'strategy'])
    assert len(frame) == 562 and frame.complete.all()
    assert not frame.duplicated(['strategy', 'window_id']).any()
    assert not frame.isna().any().any() and np.isfinite(frame.select_dtypes(include='number').to_numpy()).all()
    assert frame.groupby(['strategy', 'set']).size().to_dict() == {
        (name, dataset): count for name in NAMES for dataset, count in [('historical', 276), ('recent', 5)]}
    assert sum(map(len, PROBLEMS.values())) == 0
    joined = frame.merge(reference[reference.strategy == 'K2'][['window_id', 'benchmark_0050_return']],
                         on='window_id', suffixes=('', '_control'), validate='many_to_one')
    assert len(joined) == 562 and np.allclose(joined.benchmark_0050_return,
        joined.benchmark_0050_return_control, rtol=0, atol=1e-12)
    # 0050 is the exact action-neutral benchmark used by run_episode, not a new strategy simulation.
    benchmark_rows = []
    for ep, dataset in windows:
        path = np.r_[1., (1 + MARKET.benchmark_ret.loc[list(ep.sessions)].fillna(0.)).cumprod().to_numpy()]
        value = float(path[-1]-1)
        ref = reference[(reference.strategy == 'K2') & (reference.window_id == ep.episode_id)].iloc[0]
        assert abs(value-ref.benchmark_0050_return) <= 1e-12
        benchmark_rows.append(dict(strategy='0050', window_id=ep.episode_id, set=dataset, start=str(ep.start.date()),
                                   delta_vs_0050=0., max_drawdown=float((1-path/np.maximum.accumulate(path)).max()),
                                   warning_days=0, disqualified=False, turnover=0., costs=0.))
    combined = pd.concat([frame, reference, pd.DataFrame(benchmark_rows)], ignore_index=True)
    all_names = NAMES + CONTROLS + ['0050']
    stats = {dataset: {name: metric(combined[(combined.set == dataset) & (combined.strategy == name)])
                       for name in all_names} for dataset in ['historical', 'recent']}
    blocks = []
    for name in all_names:
        for lo, hi in [(2015, 2018), (2019, 2021), (2022, 2024), (2025, 2026)]:
            part = combined[(combined.set == 'historical') & (combined.strategy == name) &
                            combined.start.str[:4].astype(int).between(lo, hi)]
            m = metric(part)
            blocks.append(dict(strategy=name, block=f'{lo}–{hi}', count=len(part), tail_edge=m['tail_edge'],
                               mean_delta_vs_0050=m['mean_delta_vs_0050']))
    decision = evaluate(stats, blocks)
    distributions = {}
    for dataset, count in [('historical', 276), ('recent', 5)]:
        values = pd.Series([b['confirmed'] for b in BUILDS if b['set'] == dataset], dtype=int)
        assert len(values) == count
        distributions[dataset] = dict(count=len(values), min=int(values.min()), median=float(values.median()),
            mean=float(values.mean()), max=int(values.max()),
            distribution={str(k): int(v) for k, v in values.value_counts().sort_index().items()})
    check_frozen()
    assert all(digest(p) == h for p, h in protected.items())
    assert all(digest(ROOT / p) == h for p, h in MARKET.provenance.items())
    log_after = LOG.read_bytes()
    assert log_after.startswith(log_before) and len(log_after.splitlines()) == 96
    added = [json.loads(line) for line in log_after[len(log_before):].splitlines()]
    assert [r['run_id'] for r in added] == [f'{n}__holdout_k3_composite' for n in NAMES]
    assert git('status', '--porcelain') == before_status
    folders = sorted(p for p in (DEST / 'episodes').iterdir() if p.is_dir())
    assert len(folders) == 10
    for folder in folders:
        ledger = pd.read_csv(folder / 'ledger.csv', keep_default_na=False)
        assert len(ledger) == 24
        for artifact in folder.iterdir():
            if artifact.suffix == '.csv':
                data = pd.read_csv(artifact, keep_default_na=False)
                assert np.isfinite(data.select_dtypes(include='number').to_numpy()).all()
    checks = dict(episodes_expected=562, episodes_processed=len(frame), episodes_failed=0,
        episodes_complete=int(frame.complete.sum()), pilot_episodes=6, verification_calls=len(PROBLEMS),
        verification_problem_count=sum(map(len, PROBLEMS.values())),
        verification_problem_counts={k: len(v) for k, v in PROBLEMS.items()},
        k1_diagnostic_expected=7, k1_diagnostic_matched=len(DIAGNOSTICS), k1_diagnostic_verification_problems=0,
        frozen_hashes_unchanged=True, protected_inputs_unchanged=True, input_provenance_unchanged=True,
        baseline_git_status_lines=46, git_status_unchanged=True, holdout_prior_lines=94, holdout_added_lines=2,
        holdout_prior_bytes_sha256=hashlib.sha256(log_before).hexdigest(), holdout_prior_bytes_unchanged=True,
        finite_nonmissing_results=True, benchmark_matches_controls=True, recent_ledger_directories=10)
    elapsed = time.perf_counter() - STARTED
    payload = dict(status='COMPLETE', metrics=stats, checks=checks, command=COMMAND, elapsed_seconds=elapsed,
        workers=WORKERS, parallelism='fork ordered imap; independent strategies; pilot and K1 diagnostic serial',
        frozen_sha256={str(p.relative_to(ROOT)): h for p, h in FROZEN.items()},
        k31_build_confirmed_distribution=distributions, k31_build_records=BUILDS,
        benchmark_definition='0050 action-neutral daily compounded returns; zero trading costs/turnover and rule warnings; drawdown includes initial NAV.',
        windows=[dict(window_id=e.episode_id, set=d, split=e.split, start=str(e.start.date()), end=str(e.end.date()))
                 for e, d in windows])
    frame.to_csv(DEST / 'results.csv', index=False)
    pd.DataFrame(blocks).to_csv(DEST / 'blocks.csv', index=False)
    pd.DataFrame(DIAGNOSTICS).to_csv(DEST / 'k1_warning_diagnostic.csv', index=False)
    dump(DEST / 'metrics.json', payload)
    dump(DEST / 'gates.json', decision)
    # Re-read deliverables to check serialization, schema, and absence of non-finite values.
    for filename in ['results.csv', 'blocks.csv', 'k1_warning_diagnostic.csv']:
        data = pd.read_csv(DEST / filename, keep_default_na=False, float_precision='round_trip')
        assert np.isfinite(data.select_dtypes(include='number').to_numpy()).all()
        assert not data.isna().any().any()
    for filename in ['metrics.json', 'gates.json']:
        json.loads((DEST / filename).read_text(), parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
    report = ['# CODEX_REPORT', '', '- Status: COMPLETE — ' + decision['verdict'] + '.',
        '- Changed files: run.py, results.csv, metrics.json, blocks.csv, gates.json, k1_warning_diagnostic.csv, '
        'CODEX_REPORT.md; episodes/{K3.0,K3.1}_{W1..W5}/{ledger,trades,holdings,issues}.csv and strategy_log.json; '
        'research/holdout_access_log.jsonl (+2 lines). Allowed paths only.',
        '- Tests/asserts: specified command; 6 pilot episodes; 562 verify_episode calls / 0 problems; '
        '7 K1 diagnostic reruns match stored returns within 1e-12 and verify cleanly; '
        '276 historical + 5 recent windows match all controls; finite/nonmissing serialized outputs; '
        '10 recent ledger directories × 24 days; four frozen hashes, protected inputs, market provenance and '
        '46-line Git status unchanged; original 94 log lines byte-identical, +2 lines.',
        '- Items expected/processed/failed: new episodes 562/562/0; K1 diagnostic windows 7/7/0.',
        '- Plan deviations: none in scope, architecture, strategies, parameters or gates. '
        'Closest means fewest failed gates (ties retained), descriptive only; preregistration gives no distance. '
        'Exact passing tail-edge ties remain tied. P90 uses pandas linear quantile; win means Δ50 > 0. '
        '0050 uses the simulator benchmark path with zero trading costs/turnover and rule warnings.',
        f'- Execution: `{COMMAND}`; main directory; {elapsed:.2f}s; {WORKERS} workers; no Git writes.',
        '- Quota: unavailable; one frozen batch, no search.', '- Blockers: none.', '',
        '| Set | Strategy | Mean Δ50 | Median Δ50 | Win | ≥+5pp | ≥+10pp | ≥+15pp | ≤−5pp | Tail edge | P90 DD | Warn total/max | DQ | Turnover | Costs TWD |',
        '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for dataset in ['historical', 'recent']:
        for name in all_names:
            m = stats[dataset][name]
            vals = [m[k] for k in ['mean_delta_vs_0050', 'median_delta_vs_0050', 'win_rate', 'p_gain_5pp',
                'p_gain_10pp', 'p_gain_15pp', 'p_loss_5pp', 'tail_edge', 'p90_max_drawdown']]
            report.append(f'| {dataset} | {name} | ' + ' | '.join(f'{v:.3%}' for v in vals) +
                f' | {m["total_warning_days"]}/{m["max_warning_days"]} | {m["disqualifications"]} | '
                f'{m["mean_turnover"]:.4f} | {m["mean_costs"]:.2f} |')
    report += ['', '| Candidate | G0 | G1 | G2 | G3 | G4 |', '|---|---|---|---|---|---|']
    for name, g in decision['candidates'].items():
        report.append('| ' + name + ' | ' + ' | '.join('PASS' if g[k]['passed'] else 'FAIL'
            for k in ['G0', 'G1', 'G2', 'G3', 'G4']) + ' |')
    report += ['', 'Failing gates (probabilities/returns in decimal units):']
    for name, g in decision['candidates'].items():
        report.append('- ' + name + ': ' + ('; '.join(k + ' ' + json.dumps(g[k], ensure_ascii=False)
            for k in g['failing_gates']) or 'none'))
    report += ['- Selected: ' + (', '.join(decision['selected_candidates']) or 'none') +
        '; closest if none pass: ' + (', '.join(decision['closest_candidates_if_none_pass']) or 'N/A') + '.',
        '- K3.1 build confirmed counts: ' + json.dumps(distributions),
        '- Details: per-episode verification counts and build records in metrics.json; block metrics in blocks.csv; '
        'K1 warning dates/reasons in k1_warning_diagnostic.csv; K3 warning dates/reasons in results.csv.',
        '- Limits: 2026 survivor universe, overlapping windows, previously accessed holdout, '
        'post-K1/K2 design, assumed revenue availability on day 11.']
    (DEST / 'CODEX_REPORT.md').write_text('\n'.join(report) + '\n')
    check_frozen()
    assert git('status', '--porcelain') == before_status
    assert LOG.read_bytes() == log_after
    print('COMPLETE', len(frame), f'{elapsed:.2f}s', decision['verdict'], flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        hashes = {str(p.relative_to(ROOT)): p.exists() and digest(p) == h for p, h in FROZEN.items()}
        report = ['# CODEX_REPORT', '', '- Status: STOPPED',
            '- Changed files: run.py, CODEX_REPORT.md and any partial allowed outputs.',
            '- Tests/asserts: stopped; frozen checks: ' + json.dumps(hashes),
            f'- Items expected/processed/failed: new episodes 562/{len(ROWS)}/incomplete; K1 diagnostics 7/{len(DIAGNOSTICS)}/incomplete.',
            '- Metrics / gate table: pending; incomplete execution.', '- Plan deviations: none; no strategy changes.',
            f'- Execution: `{COMMAND}`; {WORKERS} workers; {time.perf_counter()-STARTED:.2f}s.',
            '- Quota: unavailable.', '- Blockers: ' + repr(exc), '', '```', traceback.format_exc(), '```']
        (DEST / 'CODEX_REPORT.md').write_text('\n'.join(report) + '\n')
        raise
