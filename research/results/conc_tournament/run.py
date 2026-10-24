"""Mechanical runner for the frozen concentrated tournament preregistration."""
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
from research.run_experiment import build_strategy

DEST = Path(__file__).resolve().parent
sys.path.insert(0, str(DEST))
FROZEN = {
    DEST / 'PREREGISTRATION.md': 'f7d825b4fe47fcfc4dee6a5015eceb1938d24664ec23b81f689fc2bade51c1e9',
    DEST / 'strategies.py': 'c05cb770ff1eaec3ffec8f61d10cfc8e18dc693b7aecb5373402e0c6f6c31121',
    DEST.parent / 'recent5_v3/strategies.py': 'becd34b594fc15d07d3de1660d427bee7d997db4bf17ab83eab90705972815fe',
    DEST.parent / 'recent5_v3/results.csv': '37c35ed79f6f5a308335dfdfa6fdc01485f64fd57b4e06f9444374df549c57dd',
    DEST.parent / 'recent5_new/results.csv': '43f565f833fdc5745993ccdfbe50cb36ac91cbf07b9c88933fcd68db58c789b8',
}
NAMES = ['K1', 'K2', 'T2', 'Mom20']
COMMAND = 'PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/conc_tournament/run.py'
WORKERS = 4
ROWS, PROBLEMS, FILTERS = [], {}, []
STARTED = time.perf_counter()
LOG = ROOT / 'research/holdout_access_log.jsonl'
LOG_BEFORE = None


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_frozen():
    for path, expected in FROZEN.items():
        assert digest(path) == expected, f'Frozen hash mismatch: {path}'


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + '\n')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True)


def execute(job):
    name, ep, dataset = job
    strategy = (build_strategy(BASE, RULES) if name == 'Mom20' else
                STRATEGIES.MomConc(RULES) if name == 'K1' else
                STRATEGIES.TrustRevConc(MARKET.calendar, RULES, MARKET.close.columns) if name == 'K2' else
                STRATEGIES.LargeTrustCached(MARKET.calendar, RULES, MARKET.close.columns))
    result = backtest.run_episode(MARKET, ep, strategy, RULES, engine.policy_of(BASE),
                                  backtest.ExecutionConfig(**BASE['execution']))
    problems = backtest.verify_episode(MARKET, ep, result, RULES)
    s = result['summary']
    row = dict(strategy=name, window_id=ep.episode_id, set=dataset, split=ep.split,
               start=str(ep.start.date()), end=str(ep.end.date()),
               **{k: s[k] for k in ['terminal_return', 'max_drawdown', 'costs', 'turnover',
                                    'warning_days', 'disqualified', 'benchmark_0050_return', 'complete']},
               delta_vs_0050=s['terminal_return'] - s['benchmark_0050_return'],
               verify_problem_count=len(problems))
    assert s['complete'], (name, ep.episode_id, 'incomplete')
    assert all(np.isfinite(v) for v in row.values() if isinstance(v, (int, float))), row
    if dataset == 'recent':
        folder = DEST / 'episodes' / f'{name}_{ep.episode_id}'
        folder.mkdir(parents=True, exist_ok=True)
        for key in ['ledger', 'trades', 'holdings', 'issues']:
            result[key].to_csv(folder / f'{key}.csv', index=False)
        dump(folder / 'strategy_log.json', getattr(strategy, 'log', []))
    filters = [dict(strategy=name, window_id=ep.episode_id, set=dataset, **entry)
               for entry in getattr(strategy, 'log', []) if name == 'K2' and entry.get('action') == 'build']
    return row, problems, filters


def collect(value, pilot=False):
    row, problems, filters = value
    ROWS.append(row)
    PROBLEMS[f"{row['strategy']}_{row['window_id']}"] = problems
    FILTERS.extend(filters)
    if pilot:
        assert not problems, (row, problems)
    if pilot or len(ROWS) % 20 == 0 or row['set'] == 'recent':
        print('PILOT' if pilot else 'PROGRESS', len(ROWS), row['strategy'], row['window_id'],
              row['terminal_return'], 'verify', len(problems), flush=True)


def batch(jobs):
    # Fork inherits read-only market/panel snapshots. Each job builds a fresh strategy.
    with mp.get_context('fork').Pool(WORKERS) as pool:
        for value in pool.imap(execute, jobs, chunksize=1):
            collect(value)


def metric(frame):
    delta = frame.delta_vs_0050
    up, down = float((delta >= .05).mean()), float((delta <= -.05).mean())
    return dict(count=len(frame), mean_delta_vs_0050=float(delta.mean()),
                median_delta_vs_0050=float(delta.median()), win_rate=float((delta > 0).mean()),
                p_gain_5pp=up, p_loss_5pp=down, tail_edge=up - down,
                p_gain_10pp=float((delta >= .10).mean()),
                p90_max_drawdown=float(frame.max_drawdown.quantile(.9)),
                total_warning_days=int(frame.warning_days.sum()),
                max_warning_days=int(frame.warning_days.max()),
                disqualifications=int(frame.disqualified.sum()),
                mean_turnover=float(frame.turnover.mean()), mean_costs=float(frame.costs.mean()))


def main():
    global MARKET, RULES, BASE, STRATEGIES, LOG_BEFORE
    check_frozen()
    before_status = git('status', '--porcelain')
    def unrelated_status(value):
        return '\n'.join(line for line in value.splitlines() if line[3:] != 'research/holdout_access_log.jsonl')
    assert len(unrelated_status(before_status).splitlines()) == 36, 'Unexpected starting Git status'
    log_initial = LOG.read_bytes()
    resumed = len(log_initial.splitlines()) == 81
    LOG_BEFORE = b''.join(log_initial.splitlines(keepends=True)[:77])
    assert len(LOG_BEFORE.splitlines()) == 77 and LOG_BEFORE.endswith(b'\n')
    assert LOG_BEFORE == subprocess.check_output(['git', 'show', 'HEAD:research/holdout_access_log.jsonl'], cwd=ROOT)
    assert len(log_initial.splitlines()) in (77, 81)
    # Preserve original inputs; generated outputs may be replaced by a deterministic recovery run.
    protected = {p: digest(p) for p in DEST.iterdir() if p.is_file() and p.name in
                 ['PREREGISTRATION.md', 'TASK_BRIEF.md', 'strategies.py', 'CODEX_PROMPT.txt', 'run.py']}
    if resumed:
        prior_entries = [json.loads(line) for line in log_initial[len(LOG_BEFORE):].splitlines()]
        assert [r['run_id'] for r in prior_entries] == [f'{n}__holdout_conc_tournament' for n in NAMES]
        assert all(r['config'] == str(DEST / 'strategies.py') and
                   r['config_hash'] == digest(DEST / 'strategies.py') and
                   r['git_commit'] == git('rev-parse', 'HEAD').strip() and r['argv'] == sys.argv
                   for r in prior_entries)
    import strategies as STRATEGIES
    BASE = json.loads((ROOT / 'production/strategy.json').read_text())
    RULES = load_rules()
    source = ROOT / 'production_runs/preflight_20261026'
    MARKET = load_market(yahoo_path=source / 'strategy_retest/corrected_nominal_daily.parquet',
                         official_path=source / 'official_combined.csv', calendar_path=source / 'calendar_v2.json')
    historical = [e for s in ('dev', 'validation', 'holdout') for e in build_episodes(
        MARKET.calendar, s, offsets=('month_start', 'mid_month'), start='2015-01-01', data_end='2026-10-22')]
    assert len(historical) == 276
    end = MARKET.position('2026-10-22') + 1
    recent = []
    reference = pd.read_csv(DEST.parent / 'recent5_v3/results.csv', float_precision='round_trip')
    for i in range(5):
        days = tuple(MARKET.calendar[end - 120 + i * 24:end - 96 + i * 24])
        assert len(days) == 24
        ep = Episode(f'W{i+1}', 'recent_diagnostic', MARKET.calendar[MARKET.position(days[0]) - 1], days)
        refs = reference[reference.window == ep.episode_id]
        assert len(refs) == 5
        assert all(pd.Timestamp(r.start) == ep.start and pd.Timestamp(r.end) == ep.end for r in refs.itertuples())
        recent.append(ep)
    assert recent[0].start == pd.Timestamp('2026-04-30')
    assert recent[-1].start == pd.Timestamp('2026-09-16') and recent[-1].end == pd.Timestamp('2026-10-22')
    # Pilot is retained in the 1,124 results; no duplicate episodes.
    for ep in historical[:3]:
        for name in NAMES:
            collect(execute((name, ep, 'historical')), pilot=True)
    consistency = {}
    for ep in recent:
        for name in NAMES:
            value = execute((name, ep, 'recent'))
            collect(value)
            if name in ('T2', 'Mom20'):
                ref = reference[(reference.strategy == name) & (reference.window == ep.episode_id)]
                assert len(ref) == 1
                actual, expected = value[0]['terminal_return'], float(ref.iloc[0].terminal_return)
                record = dict(actual=actual, expected=expected, abs_diff=abs(actual - expected), tolerance=1e-12)
                consistency[f'{name}_{ep.episode_id}'] = record
                assert record['abs_diff'] <= 1e-12, record
    batch([(name, ep, 'historical') for ep in historical[3:] if ep.split != 'holdout' for name in NAMES])
    # Log once immediately before any holdout execution; never rewrite old bytes.
    assert LOG.read_bytes() == log_initial
    commit, dirty = git('rev-parse', 'HEAD').strip(), bool(before_status)
    if not resumed:
        with LOG.open('ab') as out:
            for name in NAMES:
                record = dict(utc=datetime.now(timezone.utc).isoformat(), run_id=f'{name}__holdout_conc_tournament',
                              config=str(DEST / 'strategies.py'), config_hash=digest(DEST / 'strategies.py'),
                              git_commit=commit, git_dirty=dirty, argv=sys.argv)
                out.write((json.dumps(record) + '\n').encode())
    batch([(name, ep, 'historical') for ep in historical if ep.split == 'holdout' for name in NAMES])
    frame = pd.DataFrame(ROWS).sort_values(['set', 'start', 'window_id', 'strategy'])
    assert len(frame) == 1124 and frame.complete.all()
    assert not frame.duplicated(['strategy', 'window_id']).any()
    assert np.isfinite(frame.select_dtypes(include='number').to_numpy()).all()
    assert not frame.isna().any().any()
    assert frame.groupby(['strategy', 'set']).size().to_dict() == {
        (name, dataset): count for name in NAMES for dataset, count in [('historical', 276), ('recent', 5)]}
    stats = {dataset: {name: metric(frame[(frame.set == dataset) & (frame.strategy == name)]) for name in NAMES}
             for dataset in ['historical', 'recent']}
    blocks = []
    for name in NAMES:
        for lo, hi in [(2015, 2018), (2019, 2021), (2022, 2024), (2025, 2026)]:
            part = frame[(frame.set == 'historical') & (frame.strategy == name) &
                         frame.start.str[:4].astype(int).between(lo, hi)]
            m = metric(part)
            blocks.append(dict(strategy=name, block=f'{lo}–{hi}', count=len(part),
                               tail_edge=m['tail_edge'], mean_delta_vs_0050=m['mean_delta_vs_0050']))
    gates = {}
    threshold = max(stats['historical'][n]['p_gain_5pp'] for n in ['T2', 'Mom20']) + .05
    for name in ['K1', 'K2']:
        h, r = stats['historical'][name], stats['recent'][name]
        positive_blocks = sum(b['tail_edge'] > 0 for b in blocks if b['strategy'] == name)
        g = dict(
            G0=dict(passed=h['disqualifications'] == 0 and h['max_warning_days'] <= 1 and
                           h['total_warning_days'] <= stats['historical']['Mom20']['total_warning_days'],
                    disqualifications=h['disqualifications'], maximum_disqualifications=0,
                    max_warning_days=h['max_warning_days'], maximum_warning_days=1,
                    total_warning_days=h['total_warning_days'],
                    maximum_total_warning_days=stats['historical']['Mom20']['total_warning_days']),
            G1=dict(passed=h['tail_edge'] > 0 and h['mean_delta_vs_0050'] > 0,
                    tail_edge=h['tail_edge'], mean_delta_vs_0050=h['mean_delta_vs_0050'], threshold_exclusive=0),
            G2=dict(passed=h['p_gain_5pp'] >= threshold, p_gain_5pp=h['p_gain_5pp'], threshold=threshold,
                    controls={n: stats['historical'][n]['p_gain_5pp'] for n in ['T2', 'Mom20']}, margin=.05),
            G3=dict(passed=positive_blocks >= 3, positive_blocks=positive_blocks, required_positive_blocks=3,
                    blocks=[b for b in blocks if b['strategy'] == name]),
            G4=dict(passed=r['mean_delta_vs_0050'] >= -.02,
                    recent_mean_delta_vs_0050=r['mean_delta_vs_0050'], minimum=-.02))
        g['failing_gates'] = [k for k in ['G0', 'G1', 'G2', 'G3', 'G4'] if not g[k]['passed']]
        g['all_passed'] = not g['failing_gates']
        gates[name] = g
    passing = [n for n in gates if gates[n]['all_passed']]
    selected = [n for n in passing if stats['historical'][n]['tail_edge'] ==
                max(stats['historical'][s]['tail_edge'] for s in passing)]
    # Preregistration does not define 'closest' for failed candidates: describe by fewest failed gates,
    # retaining ties, without changing eligibility or adding a selection gate.
    closest = [n for n in gates if len(gates[n]['failing_gates']) == min(len(g['failing_gates']) for g in gates.values())]
    decision = dict(candidates=gates, passing_candidates=passing, selected_candidates=selected,
                    closest_candidates_if_none_pass=closest if not passing else [],
                    closest_definition='Fewest failed gates, retaining ties; descriptive only.',
                    verdict='錦標賽候選，由使用者決定是否採用' if passing else '不建議集中版',
                    automatic_strategy_change=False)
    old = pd.read_csv(DEST.parent / 'final_test/episode_returns.csv', float_precision='round_trip')
    comparison = frame[(frame.strategy == 'Mom20') & (frame.set == 'historical')].merge(
        old[['episode', 'momentum_20']], left_on='window_id', right_on='episode', how='inner')
    descriptive = dict(matched_count=len(comparison),
                       max_abs_diff=float((comparison.terminal_return - comparison.momentum_20).abs().max())
                       if len(comparison) else None,
                       note='Descriptive only; repaired data snapshot differences expected; legacy CSV rounded.')
    filter_stats = {}
    for dataset in ['historical', 'recent']:
        values = pd.Series([r['passed_filter'] for r in FILTERS if r['set'] == dataset], dtype=int)
        assert len(values) == (276 if dataset == 'historical' else 5)
        filter_stats[dataset] = dict(count=len(values), min=int(values.min()), median=float(values.median()),
                                    mean=float(values.mean()), max=int(values.max()),
                                    distribution={str(k): int(v) for k, v in values.value_counts().sort_index().items()})
    check_frozen()
    assert all(digest(p) == h for p, h in protected.items())
    assert all(digest(ROOT / p) == h for p, h in MARKET.provenance.items())
    log_after = LOG.read_bytes()
    assert log_after.startswith(LOG_BEFORE) and len(log_after.splitlines()) == 81
    added = [json.loads(line) for line in log_after[len(LOG_BEFORE):].splitlines()]
    assert [r['run_id'] for r in added] == [f'{n}__holdout_conc_tournament' for n in NAMES]
    assert unrelated_status(git('status', '--porcelain')) == unrelated_status(before_status)
    folders = sorted(p for p in (DEST / 'episodes').iterdir() if p.is_dir())
    assert len(folders) == 20
    for folder in folders:
        assert len(pd.read_csv(folder / 'ledger.csv')) == 24
    checks = dict(episodes_expected=1124, episodes_processed=len(frame), episodes_complete=int(frame.complete.sum()),
                  episodes_failed=0, verification_calls=len(PROBLEMS),
                  verification_problem_count=sum(map(len, PROBLEMS.values())),
                  verification_problem_counts={k: len(v) for k, v in PROBLEMS.items()},
                  nonzero_verification_problems={k: v for k, v in PROBLEMS.items() if v},
                  recent_consistency=consistency, frozen_hashes_unchanged=True, input_provenance_unchanged=True,
                  protected_input_files_unchanged=True, unrelated_git_status_unchanged=True, baseline_git_status_lines=36,
                  holdout_prior_lines=77, holdout_added_lines=4,
                  holdout_prior_bytes_sha256=hashlib.sha256(LOG_BEFORE).hexdigest(),
                  holdout_prior_bytes_unchanged=True, finite_results=True, recent_ledger_directories=20)
    elapsed = time.perf_counter() - STARTED
    payload = dict(status='COMPLETE', metrics=stats, checks=checks, k2_build_filter_distribution=filter_stats,
                   k2_build_filter_records=FILTERS, mom20_historical_comparison=descriptive,
                   frozen_sha256={str(p.relative_to(ROOT)): h for p, h in FROZEN.items()},
                   command=COMMAND, elapsed_seconds=elapsed, workers=WORKERS,
                   recovery_replay=resumed,
                   parallelism='fork, ordered imap; independent fresh strategies; pilot and recent serial',
                   windows=[dict(window_id=e.episode_id, split=e.split, start=str(e.start.date()), end=str(e.end.date()))
                            for e in historical + recent])
    frame.to_csv(DEST / 'results.csv', index=False)
    pd.DataFrame(blocks).to_csv(DEST / 'blocks.csv', index=False)
    dump(DEST / 'metrics.json', payload)
    dump(DEST / 'gates.json', decision)
    report = ['# CODEX_REPORT', '', '- Status: COMPLETE — ' + decision['verdict'],
              '- Changed files: run.py, results.csv, metrics.json, blocks.csv, gates.json, CODEX_REPORT.md; '
              'episodes/{K1,K2,T2,Mom20}_{W1..W5}/{ledger,trades,holdings,issues}.csv and strategy_log.json; '
              'research/holdout_access_log.jsonl (+4 lines). Allowed paths only.',
              '- Tests run and results: specified command; 12 pilot episodes passed; '
              f'1,124 verification calls / {checks["verification_problem_count"]} problems; '
              '10 recent consistency checks within 1e-12; 276 historical + 5 recent date/count checks; '
              'finite/nonmissing results; 20 recent ledgers × 24 rows; frozen/provenance hashes, '
              'protected inputs and 36-line unrelated Git status unchanged; prior 77 log lines unchanged, +4 PASS.',
              '- Items expected/processed/failed: episodes 1124/1124/0.',
              '- Acceptance criteria: all execution/output checks PASS; gate failures are outcomes.',
              '- Plan deviations / interpretations: no scope, architecture, strategy, parameter or gate changes. '
              'Closest failed candidate means fewest failed gates (ties retained), descriptive only; '
              'the preregistration supplies no distance metric. Exact passing tail-edge ties remain tied. '
              'P90 uses pandas linear quantile; win means Δ50 > 0; K2 distribution uses initial build only.',
              f'- Execution: `{COMMAND}`; main directory; {elapsed:.2f}s; {WORKERS} fork workers '
              '(pilot/recent serial); no Git writes.',
              '- Quota: unavailable; one frozen batch, no parameter search.', '- Blockers: none.', '',
              '| Set | Strategy | Mean Δ50 | Median Δ50 | Win | ≥+5pp | ≤−5pp | Tail edge | ≥+10pp | P90 DD | Warn total/max | DQ | Mean turnover | Mean costs TWD |',
              '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for dataset in ['historical', 'recent']:
        for name in NAMES:
            m = stats[dataset][name]
            vals = [m[k] for k in ['mean_delta_vs_0050', 'median_delta_vs_0050', 'win_rate', 'p_gain_5pp',
                                    'p_loss_5pp', 'tail_edge', 'p_gain_10pp', 'p90_max_drawdown']]
            report.append(f'| {dataset} | {name} | ' + ' | '.join(f'{v:.3%}' for v in vals) +
                          f' | {m["total_warning_days"]}/{m["max_warning_days"]} | {m["disqualifications"]} | '
                          f'{m["mean_turnover"]:.4f} | {m["mean_costs"]:.2f} |')
    report += ['', '| Candidate | G0 | G1 | G2 | G3 | G4 |', '|---|---|---|---|---|---|']
    for name, g in gates.items():
        report.append('| ' + name + ' | ' + ' | '.join('PASS' if g[k]['passed'] else 'FAIL'
                                                        for k in ['G0', 'G1', 'G2', 'G3', 'G4']) + ' |')
    report += ['', 'Failing gates (decimal return/probability units):']
    for name, g in gates.items():
        report.append('- ' + name + ': ' + ('; '.join(k + ' ' + json.dumps(g[k], ensure_ascii=False)
                                                       for k in g['failing_gates']) or 'none'))
    report += ['- Selected: ' + (', '.join(selected) or 'none') + '; closest if none pass: ' +
               (', '.join(closest) if not passing else 'N/A') + '.',
               '- Mom20 historical reference: ' + json.dumps(descriptive),
               '- K2 initial-build filter distribution: metrics.json (counts/histograms by set).',
               '- Verification counts for all 1,124 IDs, block metrics and provenance: metrics.json / blocks.csv.',
               '- Limitations: 2026 survivor universe, overlapping windows, revenue available on day 11, '
               'holdout already accessed previously; large-win probability is only a top-six proxy.']
    if resumed:
        report += ['- Execution recovery: first command completed 1,124 episodes (38.12s, zero verification problems), '
                   'then failed an overly strict Git-status assertion because it included the authorized log append. '
                   'Corrected runner-only assertion to exclude that one authorized path and reran the same command '
                   'with unchanged strategies/gates. Reused and validated the four existing run-log entries; '
                   'no extra log lines. Unique deliverable episodes: 1,124; total executed across two attempts: 2,248.']
    (DEST / 'CODEX_REPORT.md').write_text('\n'.join(report) + '\n')
    print('COMPLETE', len(frame), f'{elapsed:.2f}s', decision['verdict'], flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        check = {str(p.relative_to(ROOT)): p.exists() and digest(p) == h for p, h in FROZEN.items()}
        report = ['# CODEX_REPORT', '', '- Status: STOPPED',
                  '- Changed files: run.py, CODEX_REPORT.md and any partial allowed outputs.',
                  '- Tests run and results: execution stopped; frozen checks: ' + json.dumps(check),
                  f'- Items expected/processed/failed: 1124/{len(ROWS)}/incomplete.',
                  '- Acceptance criteria: not met.', '- Plan deviations / interpretations: no strategy or gate changes.',
                  f'- Execution: `{COMMAND}`; {WORKERS} workers; {time.perf_counter()-STARTED:.2f}s.',
                  '- Quota: unavailable.', '- Blockers: ' + repr(exc), '',
                  '| Metrics / gates | Status |', '|---|---|', '| Pending | Stopped |', '',
                  '```', traceback.format_exc(), '```']
        (DEST / 'CODEX_REPORT.md').write_text('\n'.join(report) + '\n')
        raise
