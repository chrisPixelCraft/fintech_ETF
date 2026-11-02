"""Mechanical execution of the frozen K21Exit / K2Stop preregistration."""
from pathlib import Path
import sys
sys.dont_write_bytecode = True
import hashlib
import json
import os
import multiprocessing as mp
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
LOG = ROOT / 'research/holdout_access_log.jsonl'
NAMES = ['K2b', 'K2.1', 'K2.S']
WORKERS = 4
COMMAND = ('PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. '
           '/Users/chrishsieh/Documents/dev/fintech_ETF/.venv/bin/python research/results/k21_exit/run.py')
ROWS, PATHS, EXITS, PROBLEMS, CONSISTENCY = [], [], [], {}, []
STARTED = time.perf_counter()


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def check_frozen():
    hashes = {}
    for line in (DEST / 'FROZEN.sha256').read_text().splitlines():
        expected, name = line.split()
        assert name in ('strategies.py', 'PREREGISTRATION.md')
        assert digest(DEST / name) == expected, f'Frozen hash mismatch: {name}'
        hashes[name] = expected
    assert len(hashes) == 2
    return hashes


def dump(name, payload):
    (DEST / name).write_text(json.dumps(payload, indent=2, allow_nan=False, default=str) + '\n')


def protected_manifest():
    # Includes every byte of both existing snapshot links, and all files outside
    # the two authorized output locations. No Git commands or metadata writes.
    manifest = {}
    for folder, dirs, files in os.walk(ROOT, followlinks=True):
        here = Path(folder)
        dirs[:] = sorted(d for d in dirs if d not in ('.git', '.venv', '__pycache__')
                         and here / d != DEST)
        for name in sorted(files):
            path = here / name
            if path == LOG:
                continue
            key = str(path.relative_to(ROOT))
            manifest[key] = dict(sha256=digest(path), symlink=os.readlink(path) if path.is_symlink() else '')
        for name in dirs:
            path = here / name
            if path.is_symlink():
                manifest[str(path.relative_to(ROOT)) + '/@link'] = os.readlink(path)
    for name in ('strategies.py', 'PREREGISTRATION.md', 'TASK_BRIEF.md', 'FROZEN.sha256', 'CODEX_PROMPT.txt'):
        manifest[str((DEST / name).relative_to(ROOT))] = digest(DEST / name)
    return manifest


def head_commit():
    gitfile = ROOT / '.git'
    gitdir = Path(gitfile.read_text().strip().split(': ', 1)[1]) if gitfile.is_file() else gitfile
    value = (gitdir / 'HEAD').read_text().strip()
    if not value.startswith('ref: '):
        return value
    ref = value[5:]
    common = (gitdir / (gitdir / 'commondir').read_text().strip()).resolve() if (gitdir / 'commondir').exists() else gitdir
    if (common / ref).exists():
        return (common / ref).read_text().strip()
    for line in (common / 'packed-refs').read_text().splitlines():
        if line.endswith(' ' + ref):
            return line.split()[0]
    raise RuntimeError('Cannot resolve HEAD read-only')


def initialize():
    global MARKET, RULES, BASE, STRATEGIES, REFERENCE
    import strategies as STRATEGIES
    BASE = json.loads((ROOT / 'production/strategy_k2_20.json').read_text())
    RULES = load_rules()
    source = ROOT / 'production_runs/preflight_20261026'
    MARKET = load_market(yahoo_path=source / 'strategy_retest/corrected_nominal_daily.parquet',
                         official_path=source / 'official_combined.csv', calendar_path=source / 'calendar_v2.json')
    REFERENCE = pd.read_csv(DEST.parent / 'k3_weights/results.csv', float_precision='round_trip')
    REFERENCE = REFERENCE[REFERENCE.strategy == 'K2b'].set_index('window_id')
    assert len(REFERENCE) == 281 and REFERENCE.index.is_unique


def windows():
    historical = [e for s in ('dev', 'validation', 'holdout') for e in build_episodes(
        MARKET.calendar, s, offsets=('month_start', 'mid_month'), start='2015-01-01', data_end='2026-10-22')]
    assert len(historical) == 276
    assert {s: sum(e.split == s for e in historical) for s in ('dev', 'validation', 'holdout')} == dict(dev=166, validation=70, holdout=40)
    end = MARKET.position('2026-10-22') + 1
    recent = []
    for i in range(5):
        days = tuple(MARKET.calendar[end - 120 + i * 24:end - 96 + i * 24])
        assert len(days) == 24
        recent.append(Episode(f'W{i+1}', 'recent_diagnostic', MARKET.calendar[MARKET.position(days[0]) - 1], days))
    assert recent[0].start == pd.Timestamp('2026-04-30')
    assert recent[-1].start == pd.Timestamp('2026-09-16') and recent[-1].end == pd.Timestamp('2026-10-22')
    for ep in historical + recent:
        ref = REFERENCE.loc[ep.episode_id]
        assert pd.Timestamp(ref.start) == ep.start and pd.Timestamp(ref.end) == ep.end
    return historical, recent


def execute(job):
    name, ep = job
    cls = {'K2b': STRATEGIES.base.K2Buffer, 'K2.1': STRATEGIES.K21Exit, 'K2.S': STRATEGIES.K2Stop}[name]
    strategy = cls(MARKET.calendar, RULES, MARKET.close.columns)
    result = backtest.run_episode(MARKET, ep, strategy, RULES, engine.policy_of(BASE),
                                  backtest.ExecutionConfig(**BASE['execution']))
    problems = backtest.verify_episode(MARKET, ep, result, RULES)
    s = result['summary']
    logs = getattr(strategy, 'log', [])
    row = dict(strategy=name, window_id=ep.episode_id,
               set='recent' if ep.split == 'recent_diagnostic' else 'historical', split=ep.split,
               start=str(ep.start.date()), end=str(ep.end.date()),
               **{k: s[k] for k in ['terminal_return', 'max_drawdown', 'costs', 'turnover',
                                    'warning_days', 'disqualified', 'benchmark_0050_return', 'complete']},
               delta_vs_0050=s['terminal_return'] - s['benchmark_0050_return'], verify_problem_count=len(problems),
               exits=sum(e.get('action') == 'exit' for e in logs),
               exit_retries=sum(e.get('action') == 'exit_retry' for e in logs),
               exit_names=sum(len(e.get('sold', [])) for e in logs if e.get('action') == 'exit'),
               plan_status=json.dumps(s['plan_status'], sort_keys=True))
    for status in ('OK', 'REPAIRED', 'INFEASIBLE', 'HOLD_FALLBACK'):
        row['plan_status_' + status] = s['plan_status'].get(status, 0)
    assert s['complete'] or s['disqualified'], (name, ep.episode_id, 'unexplained incomplete')
    row['sessions'] = s['sessions']
    row['warning_reasons'] = json.dumps([dict(date=str(r.date), reason=str(r.warning_reasons))
                                         for r in result['ledger'].itertuples() if r.warning])
    assert sum(s['plan_status'].values()) == s['sessions']
    ledger = result['ledger']
    nav = ledger.nav.to_numpy(float) + ledger.receivable.to_numpy(float)
    bench = (1 + MARKET.benchmark_ret.loc[list(ep.sessions)].fillna(0.)).cumprod() - 1
    paths = [dict(strategy=name, window=ep.episode_id, day=i + 1, date=str(day.date()), nav=float(nav[i]),
                  bench_cumulative=float(bench.iloc[i])) for i, day in enumerate(ep.sessions[:len(ledger)])]
    exits = []
    for entry in logs:
        if entry.get('action') != 'exit':
            continue
        day = pd.Timestamp(entry['date'])
        next_days = [d for d in ep.sessions if day < d <= pd.Timestamp(ledger.date.iloc[-1])][:5]
        # Log pairs describe the frozen strategy's exit intent, including planner
        # HOLD attempts; retries are counted separately, never invented fills.
        for sold, bought in zip(entry['sold'], entry['bought']):
            returns = (1 + MARKET.ret.loc[next_days, [sold, bought]].fillna(0.)).prod() - 1
            exits.append(dict(strategy=name, window=ep.episode_id, date=entry['date'], sold=sold, bought=bought,
                              whipsaw_5d=bool(returns[sold] > returns[bought]), sessions_used=len(next_days),
                              truncated=len(next_days) < 5, sold_return=float(returns[sold]),
                              bought_return=float(returns[bought]),
                              plan_status=str(ledger.loc[ledger.date.astype(str) == entry['date'], 'plan_status'].iloc[0])))
    return row, problems, paths, exits


def collect(value, pilot=False):
    row, problems, paths, exits = value
    ROWS.append(row)
    PROBLEMS[f"{row['strategy']}:{row['window_id']}"] = problems
    PATHS.extend(paths)
    EXITS.extend(exits)
    if row['strategy'] == 'K2b':
        difference = abs(row['terminal_return'] - float(REFERENCE.loc[row['window_id'], 'terminal_return']))
        CONSISTENCY.append(dict(window=row['window_id'], abs_diff=difference))
        assert difference <= 1e-9, (row, difference)
        assert not problems and row['complete'], (row, problems)
    if pilot:
        assert not problems, (row, problems)
    if pilot or len(ROWS) % 60 == 0:
        print('PILOT' if pilot else 'PROGRESS', len(ROWS), row['strategy'], row['window_id'], 'verify', len(problems), flush=True)


def batch(jobs):
    with mp.get_context('fork').Pool(WORKERS) as pool:
        for value in pool.imap(execute, jobs, chunksize=1):
            collect(value)


def bootstrap(frame, delta):
    # Resample calendar-quarter clusters, retaining all windows in each selected
    # cluster; use the pooled window mean (variable cluster sizes retained).
    quarters = pd.PeriodIndex(pd.to_datetime(frame.start), freq='Q')
    groups = [delta.to_numpy()[quarters == q] for q in quarters.unique()]
    if not groups:
        return dict(lower=None, upper=None, draws=10000, seed=0, blocks=0)
    sums = np.array([g.sum() for g in groups])
    counts = np.array([len(g) for g in groups])
    picks = np.random.default_rng(0).integers(0, len(groups), size=(10000, len(groups)))
    means = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    lo, hi = np.quantile(means, [.025, .975])
    return dict(lower=float(lo), upper=float(hi), draws=10000, seed=0, blocks=len(groups),
                method='calendar-quarter cluster bootstrap; pooled window mean; percentile 95%')


def metric(part, baseline, path_frame, weak=False):
    part = part.sort_values('window_id').reset_index(drop=True)
    control = baseline.set_index('window_id').loc[part.window_id].reset_index()
    delta = part.delta_vs_0050.copy()
    paired = part.terminal_return - control.terminal_return
    if weak:
        day4 = path_frame[path_frame.day == 4].set_index(['strategy', 'window'])
        values, paired_values = [], []
        for row in part.itertuples():
            d4 = day4.loc[(row.strategy, row.window_id)]
            b4 = day4.loc[('K2b', row.window_id)]
            bterminal = float(control.loc[control.window_id == row.window_id, 'terminal_return'].iloc[0])
            rest = (1 + row.terminal_return) * RULES.initial_capital / d4.nav - 1
            bench_rest = (1 + row.benchmark_0050_return) / (1 + d4.bench_cumulative) - 1
            values.append(rest - bench_rest)
            paired_values.append(rest - ((1 + bterminal) * RULES.initial_capital / b4.nav - 1))
        delta, paired = pd.Series(values, dtype=float), pd.Series(paired_values, dtype=float)
    n = len(part)
    up, down = (float((delta >= .05).mean()), float((delta <= -.05).mean())) if n else (None, None)
    return dict(count=n, mean_delta_vs_0050=float(delta.mean()) if n else None,
                median_delta_vs_0050=float(delta.median()) if n else None,
                win_rate=float((delta > 0).mean()) if n else None,
                p_gain_5pp=up, p_loss_5pp=down, tail_edge=up - down if n else None,
                mean_paired_delta_vs_K2b=float(paired.mean()) if n else None,
                paired_ci95=bootstrap(part, paired),
                mean_turnover=float(part.turnover.mean()) if n else None,
                mean_costs=float(part.costs.mean()) if n else None,
                total_warning_days=int(part.warning_days.sum()), disqualifications=int(part.disqualified.sum()),
                complete_count=int(part.complete.sum()), incomplete_count=int((~part.complete).sum()),
                verify_problem_count=int(part.verify_problem_count.sum()),
                mean_exits=float(part.exits.mean()) if n else None, exit_pairs=int(part.exit_names.sum()),
                exit_retries=int(part.exit_retries.sum()),
                return_horizon='after day 4 close to window end' if weak else 'whole window',
                activity_horizon='whole window')


def main():
    assert os.environ.get('PYTHONHASHSEED') == '0'
    assert os.environ.get('PYTHONDONTWRITEBYTECODE') == '1'
    frozen = check_frozen()
    prior_attempt = (DEST / 'CODEX_REPORT.md').read_text() if (DEST / 'CODEX_REPORT.md').exists() else ''
    before = protected_manifest()
    log_before = LOG.read_bytes()
    assert log_before.endswith(b'\n')
    initialize()
    historical, recent = windows()
    # Retain pilot results in the final 843, with no repeated study windows.
    for ep in historical[:3]:
        for name in NAMES:
            collect(execute((name, ep)), pilot=True)
    print('PILOT_PASS 9/9; baseline 3/3 <= 1e-9; verify 0', flush=True)
    batch([(name, ep) for ep in historical[3:] if ep.split != 'holdout' for name in NAMES])
    # Append one entry per strategy immediately before opening holdout windows.
    assert LOG.read_bytes() == log_before
    run_ids = [f'{name}__holdout_k21_exit' for name in NAMES]
    prior = [json.loads(line) for line in log_before.splitlines()]
    existing = [r for r in prior if r['run_id'] in run_ids]
    reused_log = bool(existing)
    if existing:
        assert [r['run_id'] for r in existing] == run_ids
        assert all(r['config_hash'] == digest(DEST / 'strategies.py') and r['argv'] == sys.argv
                   and r['git_commit'] == head_commit() for r in existing)
    else:
        with LOG.open('ab') as stream:
            for name, run_id in zip(NAMES, run_ids):
                record = dict(utc=datetime.now(timezone.utc).isoformat(), run_id=run_id,
                              config=str(DEST / 'strategies.py'), config_hash=digest(DEST / 'strategies.py'),
                              git_commit=head_commit(), git_dirty=True, argv=sys.argv)
                stream.write((json.dumps(record) + '\n').encode())
                stream.flush()
    log_after_append = LOG.read_bytes()
    batch([(name, ep) for ep in historical if ep.split == 'holdout' for name in NAMES])
    batch([(name, ep) for ep in recent for name in NAMES])
    frame = pd.DataFrame(ROWS).sort_values(['set', 'start', 'window_id', 'strategy'])
    path_frame = pd.DataFrame(PATHS).sort_values(['strategy', 'window', 'day'])
    exit_columns = ['strategy', 'window', 'date', 'sold', 'bought', 'whipsaw_5d', 'sessions_used', 'truncated',
                    'sold_return', 'bought_return', 'plan_status']
    exit_frame = pd.DataFrame(EXITS, columns=exit_columns).sort_values(['strategy', 'window', 'date', 'sold'])
    assert len(frame) == 843
    assert (frame.complete | frame.disqualified).all()
    assert not frame.duplicated(['strategy', 'window_id']).any()
    assert len(path_frame) == int(frame.sessions.sum())
    sizes = path_frame.groupby(['strategy', 'window']).size()
    for row in frame.itertuples():
        assert sizes.loc[(row.strategy, row.window_id)] == row.sessions
    assert len(CONSISTENCY) == 281 and max(r['abs_diff'] for r in CONSISTENCY) <= 1e-9
    for output in (frame, path_frame, exit_frame):
        assert not output.isna().any().any()
        assert np.isfinite(output.select_dtypes(include='number').to_numpy()).all()
    baseline = frame[frame.strategy == 'K2b']
    # All-236 means dev + validation; weak-start is descriptive over all 281 windows.
    b4 = path_frame[(path_frame.strategy == 'K2b') & (path_frame.day == 4)]
    gaps = b4.nav / RULES.initial_capital - 1 - b4.bench_cumulative
    weak_ids = list(b4.loc[gaps.between(-.035, -.015), 'window'])
    sets = {
        'all-236': frame[frame.split.isin(['dev', 'validation'])],
        'dev': frame[frame.split == 'dev'], 'validation': frame[frame.split == 'validation'],
        'holdout': frame[frame.split == 'holdout'], 'recent': frame[frame.set == 'recent'],
        'weak-start': frame[frame.window_id.isin(weak_ids)]}
    stats = {dataset: {name: metric(part[part.strategy == name], baseline, path_frame, dataset == 'weak-start')
                       for name in NAMES} for dataset, part in sets.items()}
    decisions = {}
    for name in NAMES[1:]:
        primary, b = stats['all-236'][name], stats['all-236']['K2b']
        g = dict(
            G1=dict(passed=primary['mean_paired_delta_vs_K2b'] >= .005,
                    mean_paired_delta=primary['mean_paired_delta_vs_K2b'], minimum=.005),
            G2=dict(passed=primary['paired_ci95']['lower'] > 0, ci95=primary['paired_ci95'], lower_exclusive=0),
            G3=dict(passed=primary['tail_edge'] >= b['tail_edge'] - .01,
                    tail_edge=primary['tail_edge'], minimum=b['tail_edge'] - .01, baseline_tail_edge=b['tail_edge']),
            G4=dict(passed=stats['validation'][name]['mean_paired_delta_vs_K2b'] > 0,
                    validation_mean_paired_delta=stats['validation'][name]['mean_paired_delta_vs_K2b'], minimum_exclusive=0),
            G5=dict(passed=primary['disqualifications'] == 0 and primary['total_warning_days'] <= b['total_warning_days']
                    and primary['verify_problem_count'] == 0,
                    disqualifications=primary['disqualifications'], warning_days=primary['total_warning_days'],
                    maximum_warning_days=b['total_warning_days'], verify_problem_count=primary['verify_problem_count']),
            G6=dict(passed=stats['holdout'][name]['mean_paired_delta_vs_K2b'] >= -.005,
                    holdout_mean_paired_delta=stats['holdout'][name]['mean_paired_delta_vs_K2b'], minimum=-.005))
        failed = [key for key, value in g.items() if not value['passed']]
        decisions[name] = dict(gates=g, failed_gates=failed, decision='STOP' if failed else 'PASS',
                               role='primary' if name == 'K2.1' else 'secondary')
    decision = dict(candidates=decisions, primary_decision=decisions['K2.1']['decision'],
                    secondary_eligible_for_separate_user_decision=decisions['K2.1']['decision'] == 'STOP'
                    and decisions['K2.S']['decision'] == 'PASS',
                    secondary_requires_multiplicity_disclosure=True, automatic_production_change=False,
                    evidence_set='all-236 for G1-G3/G5; validation for G4; holdout veto for G6')
    # Persist all study data before the independent causality acceptance check.
    frame.to_csv(DEST / 'results.csv', index=False)
    path_frame.to_csv(DEST / 'paths.csv', index=False)
    exit_frame.to_csv(DEST / 'exits.csv', index=False)
    dump('decision.json', decision)
    from check_causality import check
    causality = check(MARKET, RULES, BASE, STRATEGIES, historical, recent)
    print('CAUSALITY_PASS', causality['decisions_checked'], 'decisions', flush=True)
    check_frozen()
    assert before == protected_manifest(), 'Protected file/snapshot bytes or symlinks changed'
    assert LOG.read_bytes() == log_after_append and log_after_append.startswith(log_before)
    added = log_after_append[len(log_before):].splitlines()
    assert len(added) == (0 if reused_log else 3)
    if added:
        assert [json.loads(line)['run_id'] for line in added] == run_ids
    assert all(digest(ROOT / path) == value for path, value in MARKET.provenance.items())
    for name, count in [('all-236', 236), ('dev', 166), ('validation', 70), ('holdout', 40), ('recent', 5)]:
        assert all(stats[name][s]['count'] == count for s in NAMES)
    serialized = {}
    for name in ('results.csv', 'paths.csv', 'exits.csv'):
        output = pd.read_csv(DEST / name, float_precision='round_trip')
        assert not output.isna().any().any()
        assert np.isfinite(output.select_dtypes(include='number').to_numpy()).all()
        serialized[name] = dict(rows=len(output), sha256=digest(DEST / name))
    diagnostics = dict(weak_start_windows=weak_ids,
                       weak_start_definition='K2b day-4 NAV including receivable / initial capital - 1 minus cumulative 0050, inclusive [-.035,-.015], all 281 windows',
                       whipsaw={name: dict(pairs=len(exit_frame[exit_frame.strategy == name]),
                           rate=float(exit_frame.loc[exit_frame.strategy == name, 'whipsaw_5d'].mean())
                           if len(exit_frame[exit_frame.strategy == name]) else None,
                           truncated_pairs=int(exit_frame.loc[exit_frame.strategy == name, 'truncated'].sum()))
                           for name in NAMES},
                       exits_definition='Strategy exit log events; one row per zipped sold/bought pair, unpaired skipped; planner status retained; retries separate',
                       whipsaw_horizon='Next five sessions strictly after exit decision/execution date, capped at window end; action-neutral ret; missing daily ret filled with zero as in backtest benchmark',
                       weak_start_activity='Return metrics use rest of window; costs/turnover/warnings/exits remain whole-window diagnostics')
    checks = dict(expected=843, processed=len(frame), complete=int(frame.complete.sum()), failed=int((~frame.complete).sum()),
                  incomplete_records=frame.loc[~frame.complete, ['strategy', 'window_id', 'sessions', 'disqualified', 'warning_reasons']].to_dict('records'),
                  pilot_runs=9, baseline_matched=281, baseline_tolerance=1e-9,
                  baseline_max_abs_diff=max(r['abs_diff'] for r in CONSISTENCY), baseline_consistency=CONSISTENCY,
                  verification_calls=len(PROBLEMS), verify_problem_count=sum(map(len, PROBLEMS.values())),
                  verification_by_strategy={n: int(frame.loc[frame.strategy == n, 'verify_problem_count'].sum()) for n in NAMES},
                  nonzero_verification_problems={key: value for key, value in PROBLEMS.items() if value},
                  finite_nonmissing=True, protected_files_unchanged=True, protected_manifest_entries=len(before),
                  snapshot_unchanged=True, frozen_unchanged=True, holdout_prior_bytes_unchanged=True,
                  holdout_prior_sha256=hashlib.sha256(log_before).hexdigest(),
                  holdout_added_lines=len(added), holdout_log_reused=reused_log, causality=causality,
                  serialized_artifacts=serialized)
    elapsed = time.perf_counter() - STARTED
    acceptance_passed = bool(frame.complete.all())
    status = 'COMPLETE' if acceptance_passed else 'COMPLETE_WITH_ACCEPTANCE_FAILURE'
    dump('metrics.json', dict(status=status, acceptance_passed=acceptance_passed, prior_attempt_report=prior_attempt,
                             recovery='Approach 1: retain engine-disqualified incomplete windows as measured outcomes; no strategy changes',
                             metrics=stats, diagnostics=diagnostics, checks=checks,
                             frozen_sha256=frozen, protected_manifest=before, command=COMMAND, workers=WORKERS,
                             elapsed_seconds=elapsed, bootstrap_units='decimal returns/probabilities; .005 = 0.50pp',
                             plan_status_counts={n: frame[frame.strategy == n][[c for c in frame if c.startswith('plan_status_')]].sum().to_dict() for n in NAMES}))
    json.loads((DEST / 'metrics.json').read_text(), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    json.loads((DEST / 'decision.json').read_text(), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    report = ['# CODEX_REPORT', '', '- Status: ' + status + '; primary ' + decision['primary_decision'] + '; secondary ' + decisions['K2.S']['decision'] + '.',
              '- Changed files: research/results/k21_exit/{run.py,check_causality.py,results.csv,paths.csv,exits.csv,metrics.json,decision.json,CODEX_REPORT.md}; research/holdout_access_log.jsonl appended by ' + str(len(added)) + ' lines.',
              '- Tests: 9/9 pilot, 0 verify problems; 843/843 study verification calls; all 281 K2b returns match reference within 1e-9; max difference ' + str(checks['baseline_max_abs_diff']) + '; verification counts ' + json.dumps(checks['verification_by_strategy']) + '; finite/nonmissing CSVs and strict JSON; frozen, protected files, full linked snapshot and prior holdout log bytes unchanged.',
              f'- Items: study expected/processed/incomplete=843/843/{checks["failed"]}; complete={checks["complete"]}; paths=' + str(len(path_frame)) + '; exit pairs=' + str(len(exit_frame)) + '; causality cases=6, decisions checked=' + str(causality['decisions_checked']) + ', extra backtests=12.',
              '- Acceptance: ' + ('PASS' if acceptance_passed else 'FAIL: frozen candidates were disqualified before completing all 24 sessions; 843/843 complete cannot be satisfied without changing prohibited strategy/engine rules') + '. Other checks passed; G1-G6 are computed from unmodified engine outcomes.',
              '- Deviations: no scope, strategy, parameter or gate changes. Literal conventions: 95% percentile CI, calendar-quarter blocks resampled with replacement and pooled window means, NumPy default_rng(0), 10000 draws per set/strategy; win means delta > 0. G5 uses the primary 236-window evidence set. Weak-start membership uses all 281 K2b day-4 paths; its return metrics measure day-4 close to window end. Exit rows represent logged intent, with planner HOLD retained; unpaired names skipped. Whipsaw uses sessions strictly after the exit date, caps at window end and flags truncation; missing action-neutral returns filled with zero. For disqualified windows whipsaw is capped at the final observed ledger day; weak-start rest returns use engine terminal returns, whose endpoint can be early. NAV paths include dividend receivable daily.',
              f'- Execution: `{COMMAND}`; worktree root; {WORKERS} fork workers; ordered imap; serial pilot reused; elapsed {elapsed:.2f}s. No Git commands, commits, pushes, branches or merges. Read-only Git HEAD used for required holdout metadata; dirty=true because runner outputs are uncommitted.',
              '- Quota: unavailable; no parameter search.',
              '- Blockers: ' + ('none.' if acceptance_passed else 'Acceptance requires 843 complete episodes, but frozen candidate disqualifications terminate episodes in the unchanged engine.'),
              '- Recovery: approach 1 only. Initial runner stopped after 49 collected results at K2.1 dev_2015_09 (23 sessions, 3 OVERDUE_CAP:3081.TWO warnings, verify 0); one isolated diagnostic confirmed the engine disqualification. Runner now records actual incomplete outcomes and paths, never pads them. The full frozen study was rerun; the 9-run pilot was repeated. Initial in-flight worker executions are not counted as final study rows. Prior failure report retained in metrics.json.', '',
              '| Candidate | G1 | G2 | G3 | G4 | G5 | G6 | Decision |', '|---|---|---|---|---|---|---|---|']
    for name, value in decisions.items():
        report.append('| ' + name + ' | ' + ' | '.join('PASS' if value['gates'][f'G{i}']['passed'] else 'FAIL' for i in range(1, 7)) + ' | ' + value['decision'] + ' |')
    report += ['', '- Gate values and thresholds: decision.json; metrics, paired CIs, verification detail and snapshot hashes: metrics.json.',
               '- Causality: 2019, 2023, W5 × K2.1/K2.S; future prices/volume/action-neutral returns/validity/benchmark, trust flows and revenue panels corrupted after day 6 close; all decisions through day 7 and preceding NAVs unchanged; later ledgers changed. No zero-check pass.',
               '- Decision application: any failed gate means STOP; production stays K2b. K2.S is secondary and requires K2.1 failure plus a separate user decision with multiplicity disclosure; no automatic adoption.']
    (DEST / 'CODEX_REPORT.md').write_text('\n'.join(report) + '\n')
    print('COMPLETE', len(frame), decision['primary_decision'], f'{elapsed:.2f}s', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        (DEST / 'CODEX_REPORT.md').write_text('# CODEX_REPORT\n\n- Status: STOPPED\n'
            '- Changed files: runner/check_causality and any partial outputs in allowed directory; holdout log append only if reached.\n'
            '- Tests: stopped at failed assertion; see traceback.\n'
            f'- Items: expected/processed=843/{len(ROWS)}.\n- Acceptance: incomplete.\n'
            '- Deviations: no strategy, parameters or gates changed.\n'
            f'- Execution: `{COMMAND}`; four fork workers; elapsed {time.perf_counter()-STARTED:.2f}s.\n'
            '- Quota: unavailable.\n- Blockers: ' + repr(exc) + '\n\n```\n' + traceback.format_exc() + '```\n')
        raise
