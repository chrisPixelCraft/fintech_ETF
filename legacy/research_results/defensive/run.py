"""Mechanical execution of the frozen Defensive D preregistration."""
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
NAMES = ['K2b', 'D']
WORKERS = 4
COMMAND = ('PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. '
           '/Users/chrishsieh/Documents/dev/fintech_ETF/.venv/bin/python research/results/defensive/run.py')
ROWS, PATHS, PROBLEMS, CONSISTENCY = [], [], {}, []
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
    import importlib.util
    spec = importlib.util.spec_from_file_location("k21_base", DEST.parent / "k21_exit/strategies.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    STRATEGIES.base = module.base
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
    cls = {'K2b': STRATEGIES.base.K2Buffer, 'D': STRATEGIES.LowVol}[name]
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
               plan_status=json.dumps(s['plan_status'], sort_keys=True))
    for status in ('OK', 'REPAIRED', 'INFEASIBLE', 'HOLD_FALLBACK'):
        row['plan_status_' + status] = s['plan_status'].get(status, 0)
    assert s['complete'] or s['disqualified'], (name, ep.episode_id, 'unexplained incomplete')
    row['sessions'] = s['sessions']
    row['warning_reasons'] = json.dumps([dict(date=str(r.date), reason=str(r.warning_reasons))
                                         for r in result['ledger'].itertuples() if r.warning])
    assert sum(s['plan_status'].values()) == s['sessions']
    ledger = result['ledger']
    row['cash_ratio_min'] = float(ledger.cash_ratio.min())
    row['cash_ratio_max'] = float(ledger.cash_ratio.max())
    nav = ledger.nav.to_numpy(float) + ledger.receivable.to_numpy(float)
    bench = (1 + MARKET.benchmark_ret.loc[list(ep.sessions)].fillna(0.)).cumprod() - 1
    paths = [dict(strategy=name, window=ep.episode_id, day=i + 1, date=str(day.date()), nav=float(nav[i]),
                  bench_cumulative=float(bench.iloc[i])) for i, day in enumerate(ep.sessions[:len(ledger)])]
    return row, problems, paths


def collect(value, pilot=False):
    row, problems, paths = value
    ROWS.append(row)
    PROBLEMS[f"{row['strategy']}:{row['window_id']}"] = problems
    PATHS.extend(paths)
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


def cvar(values):
    values = np.asarray(values, dtype=float)
    n = int(np.ceil(len(values) * .10))
    return float(np.sort(values)[:n].mean()) if n else None


def cvar_bootstrap(baseline, candidate):
    ordered = baseline.sort_values('window_id')
    other = candidate.set_index('window_id').loc[ordered.window_id]
    quarters = pd.PeriodIndex(pd.to_datetime(ordered.start), freq='Q')
    blocks = [np.flatnonzero(quarters == q) for q in quarters.unique()]
    b, d = ordered.terminal_return.to_numpy(), other.terminal_return.to_numpy()
    rng = np.random.default_rng(0)
    draws = []
    for _ in range(10000):
        idx = np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))])
        draws.append(cvar(d[idx]) - cvar(b[idx]))
    lo, hi = np.quantile(draws, [.025, .975])
    return dict(lower=float(lo), upper=float(hi), draws=10000, seed=0, blocks=len(blocks),
                method='paired calendar-quarter blocks of start; replacement; pooled windows; own worst ceil(10% of resampled count); percentile 95%')


def metric(part):
    n = len(part)
    if not n:
        return dict(count=0)
    delta = part.delta_vs_0050
    return dict(count=n, mean_terminal_return=float(part.terminal_return.mean()),
                cvar10=cvar(part.terminal_return), cvar10_count=int(np.ceil(n * .10)),
                mean_max_drawdown=float(part.max_drawdown.mean()),
                mean_0050_return=float(part.benchmark_0050_return.mean()),
                mean_delta_vs_0050=float(delta.mean()), win_rate_vs_0050=float((delta > 0).mean()),
                p_gain_5pp_vs_0050=float((delta >= .05).mean()),
                p_loss_5pp_vs_0050=float((delta <= -.05).mean()),
                cash_ratio_min=float(part.cash_ratio_min.min()), cash_ratio_max=float(part.cash_ratio_max.max()),
                mean_turnover=float(part.turnover.mean()), total_turnover=float(part.turnover.sum()),
                mean_costs=float(part.costs.mean()), total_costs=float(part.costs.sum()),
                disqualifications=int(part.disqualified.sum()), warning_days=int(part.warning_days.sum()),
                verify_problems=int(part.verify_problem_count.sum()), complete=int(part.complete.sum()),
                incomplete=int((~part.complete).sum()))


def weak_diagnostic(frame, paths, ids):
    day4 = paths[paths.day == 4].set_index(['strategy', 'window'])
    indexed = frame.set_index(['strategy', 'window_id'])
    records = []
    for window in ids:
        rest = {}
        for name in NAMES:
            row = indexed.loc[(name, window)]
            p4 = day4.loc[(name, window)]
            last = paths[(paths.strategy == name) & (paths.window == window)].iloc[-1]
            rest[name] = float(last.nav / p4.nav - 1)
        delta = rest['D'] - rest['K2b']
        records.append(dict(window=window, K2b_return=rest['K2b'], D_return=rest['D'],
                            raw_delta=delta, after_0_6pp_cost=delta - .006,
                            complete=bool(indexed.loc[('D', window), 'complete'] and
                                          indexed.loc[('K2b', window), 'complete'])))
    return dict(count=len(records), records=records,
                mean_raw_delta=float(np.mean([r['raw_delta'] for r in records])) if records else None,
                mean_after_0_6pp_cost=float(np.mean([r['after_0_6pp_cost'] for r in records])) if records else None,
                definition='Day-4 close through final actually observed ledger close (day 24 if complete), NAV includes receivable; switch cost subtracts .006 from paired return')


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
    for ep in historical[:3]:
        for name in NAMES:
            collect(execute((name, ep)), pilot=True)
    print('PILOT_PASS 6/6; baseline 3/3 <= 1e-9', flush=True)
    batch([(name, ep) for ep in historical[3:] if ep.split != 'holdout' for name in NAMES])
    assert LOG.read_bytes() == log_before
    run_ids = [f'{name}__holdout_defensive' for name in NAMES]
    existing = [json.loads(line) for line in log_before.splitlines()
                if json.loads(line)['run_id'] in run_ids]
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
    log_after = LOG.read_bytes()
    batch([(name, ep) for ep in historical if ep.split == 'holdout' for name in NAMES])
    batch([(name, ep) for ep in recent for name in NAMES])
    frame = pd.DataFrame(ROWS).sort_values(['set', 'start', 'window_id', 'strategy'])
    paths = pd.DataFrame(PATHS).sort_values(['strategy', 'window', 'day'])
    assert len(frame) == 562 and not frame.duplicated(['strategy', 'window_id']).any()
    assert len(CONSISTENCY) == 281 and max(r['abs_diff'] for r in CONSISTENCY) <= 1e-9
    assert len(paths) == int(frame.sessions.sum())
    sizes = paths.groupby(['strategy', 'window']).size()
    for row in frame.itertuples():
        assert sizes.loc[(row.strategy, row.window_id)] == row.sessions
    for output in (frame, paths):
        assert not output.isna().any().any()
        assert np.isfinite(output.select_dtypes(include='number').to_numpy()).all()
    sets = {'all-236': frame[frame.split.isin(['dev', 'validation'])],
            'all-281': frame, 'dev': frame[frame.split == 'dev'],
            'validation': frame[frame.split == 'validation'], 'holdout': frame[frame.split == 'holdout'],
            'recent': frame[frame.set == 'recent']}
    stats = {label: {n: metric(part[part.strategy == n]) for n in NAMES} for label, part in sets.items()}
    for label, count in [('all-236',236),('all-281',281),('dev',166),('validation',70),('holdout',40),('recent',5)]:
        assert all(stats[label][n]['count'] == count for n in NAMES)
    # 0050 is diagnostic only; its own worst-decile returns use the same windows.
    benchmarks = {label: dict(count=len(part[part.strategy == 'K2b']),
                             mean_return=float(part[part.strategy == 'K2b'].benchmark_0050_return.mean()),
                             cvar10=cvar(part[part.strategy == 'K2b'].benchmark_0050_return))
                  for label, part in sets.items()}
    b, d = stats['all-236']['K2b'], stats['all-236']['D']
    diff = d['cvar10'] - b['cvar10']
    ci = cvar_bootstrap(sets['all-236'][sets['all-236'].strategy == 'K2b'],
                        sets['all-236'][sets['all-236'].strategy == 'D'])
    g3 = b['mean_max_drawdown'] - d['mean_max_drawdown']
    g4 = d['mean_terminal_return'] - b['mean_terminal_return']
    g5 = stats['validation']['D']['cvar10'] - stats['validation']['K2b']['cvar10']
    g7 = stats['holdout']['D']['cvar10'] - stats['holdout']['K2b']['cvar10']
    gates = dict(G1=dict(passed=diff >= .02, value=diff, minimum=.02),
                 G2=dict(passed=ci['lower'] > 0, ci95=ci, lower_exclusive=0),
                 G3=dict(passed=g3 >= .01, value=g3, minimum=.01),
                 G4=dict(passed=g4 >= -.01, value=g4, minimum=-.01),
                 G5=dict(passed=g5 > 0, value=g5, minimum_exclusive=0),
                 G6=dict(passed=d['disqualifications'] == 0 and d['warning_days'] == 0 and d['verify_problems'] == 0,
                         disqualifications=d['disqualifications'], warning_days=d['warning_days'],
                         verify_problems=d['verify_problems'], maximum_each=0),
                 G7=dict(passed=g7 >= 0, value=g7, minimum=0))
    failed = [key for key, value in gates.items() if not value['passed']]
    decision = dict(candidate='D', baseline='K2b', decision='STOP' if failed else 'PASS',
                    gates=gates, failed_gates=failed, automatic_production_change=False,
                    evidence_sets='G1-G4/G6: 236 dev+validation; G5: 70 validation; G7: 40 holdout',
                    rule='All seven pass => PASS, otherwise STOP; no parameter search')
    b4 = paths[(paths.strategy == 'K2b') & (paths.day == 4)]
    gaps = b4.nav / RULES.initial_capital - 1 - b4.bench_cumulative
    weak_ids = list(b4.loc[gaps.between(-.035, -.015), 'window'])
    primary_ids = set(sets['all-236'].window_id)
    weak = {'all-281': weak_diagnostic(frame, paths, weak_ids),
            'all-236': weak_diagnostic(frame, paths, [w for w in weak_ids if w in primary_ids])}
    frame.to_csv(DEST / 'results.csv', index=False)
    paths.to_csv(DEST / 'paths.csv', index=False)
    dump('decision.json', decision)
    from check_causality import check
    causality = check(MARKET, RULES, BASE, STRATEGIES, historical, recent)
    print('CAUSALITY_PASS', causality['decisions_checked'], flush=True)
    check_frozen()
    assert before == protected_manifest(), 'Protected file/snapshot bytes or symlinks changed'
    assert LOG.read_bytes() == log_after and log_after.startswith(log_before)
    added = log_after[len(log_before):].splitlines()
    assert len(added) == (0 if existing else 2)
    assert all(digest(ROOT / path) == value for path, value in MARKET.provenance.items())
    serialized = {}
    for name in ('results.csv', 'paths.csv'):
        output = pd.read_csv(DEST / name, float_precision='round_trip')
        assert not output.isna().any().any()
        assert np.isfinite(output.select_dtypes(include='number').to_numpy()).all()
        serialized[name] = dict(rows=len(output), sha256=digest(DEST / name))
    checks = dict(expected=562, processed=len(frame), complete=int(frame.complete.sum()),
                  incomplete=int((~frame.complete).sum()),
                  incomplete_records=frame.loc[~frame.complete].to_dict('records'),
                  pilot_runs=6, pilot_baseline_matched=3, baseline_matched=281, baseline_tolerance=1e-9,
                  baseline_max_abs_diff=max(r['abs_diff'] for r in CONSISTENCY), baseline_consistency=CONSISTENCY,
                  verification_calls=len(PROBLEMS), verify_problem_count=sum(map(len, PROBLEMS.values())),
                  verification_by_strategy={n: int(frame.loc[frame.strategy == n, 'verify_problem_count'].sum()) for n in NAMES},
                  verification_counts={key: len(v) for key,v in PROBLEMS.items()},
                  nonzero_verification_problems={key: v for key,v in PROBLEMS.items() if v},
                  finite_nonmissing=True, protected_files_unchanged=True, snapshot_unchanged=True,
                  protected_manifest_entries=len(before), frozen_unchanged=True,
                  holdout_prior_bytes_unchanged=True, holdout_prior_sha256=hashlib.sha256(log_before).hexdigest(),
                  holdout_added_lines=len(added), holdout_strategy_entries=2, holdout_log_reused=bool(existing), causality=causality,
                  serialized_artifacts=serialized)
    elapsed = time.perf_counter() - STARTED
    dump('metrics.json', dict(status='COMPLETE', acceptance_passed=True, prior_attempt_report=prior_attempt, metrics=stats,
         benchmark_0050=benchmarks, recent_windows=frame[frame.set == 'recent'].to_dict('records'),
         diagnostics=dict(weak_start=weak, weak_start_membership='K2b day-4 NAV including receivable / initial capital - 1 minus cumulative 0050, inclusive [-.035,-.015], all 281 windows'),
         checks=checks, frozen_sha256=frozen, protected_manifest=before, command=COMMAND, workers=WORKERS,
         elapsed_seconds=elapsed, units='decimal returns; .01 = 1pp; costs TWD',
         plan_status_counts={n: frame[frame.strategy == n][[c for c in frame if c.startswith('plan_status_')]].sum().to_dict() for n in NAMES}))
    for name in ('metrics.json', 'decision.json'):
        json.loads((DEST / name).read_text(), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    report = ['# CODEX_REPORT', '', '- Status: COMPLETE; frozen decision ' + decision['decision'] + '.',
       '- Changed files: research/results/defensive/{run.py,check_causality.py,results.csv,paths.csv,metrics.json,decision.json,CODEX_REPORT.md}; research/holdout_access_log.jsonl append only (' + str(len(added)) + ' lines in final attempt; two study entries total, first attempt +2, recovery +0).',
       '- Tests: 6/6 pilot; 281/281 K2b reference matches <=1e-9 (max ' + str(checks['baseline_max_abs_diff']) + '); 562 verification calls; problems by strategy ' + json.dumps(checks['verification_by_strategy']) + '; no CSV NaN/Inf; strict JSON; frozen/protected/snapshot hashes and prior holdout log bytes unchanged.',
       f'- Items: expected/executed=562/{len(frame)}; complete={checks["complete"]}; incomplete={checks["incomplete"]}; paths={len(paths)}; causality decisions checked={causality["decisions_checked"]}, cases=3, extra backtests=6.',
       '- Acceptance: PASS; 562/562 executed, all 281 K2b match, finite outputs, verify counts reported, causality passed, G1-G7 present, snapshot untouched. Gate failures are study outcomes.',
       '- Deviations: none to scope, strategy, parameters or gates. G2 uses paired quarter resampling, pooled windows, separately recomputed own worst ceil(10%) tails per draw, percentile 95% CI, 10000 draws, default_rng(0). G6 uses the preregistered primary 236; all-window diagnostics also reported. Weak-start membership uses all 281 K2b day-4 paths, with a 236-only subset; returns use NAV including receivable from day-4 close to actual last observed close, no padding; assumed switch cost subtracts .006. No external research or extra variants.',
       f'- Execution: `{COMMAND}`; worktree root; 4 fork workers, ordered imap, serial pilot retained; elapsed {elapsed:.2f}s. No Git commands, commit, push, branch or merge; HEAD metadata read directly for the access log.',
       '- Quota: unavailable; one frozen study; recovery approach 1: causality probes now halt before the first future decision, after comparing seven decisions and portfolio states with the original sessions_remaining. The initial 562 study runs completed before poisoned future validity triggered the frozen 22-name assertion outside the comparison horizon. Reran the identical 562-run study; 1124 study executions total, 562 unique final rows; prior two holdout log entries validated and reused. No strategy/gate changes; no further recovery used.', '- Blockers: none.', '',
       '| Gate | Result | Value / details |', '|---|---|---|']
    for key, value in gates.items():
        report.append('| ' + key + ' | ' + ('PASS' if value['passed'] else 'FAIL') + ' | ' + json.dumps(value, sort_keys=True) + ' |')
    report += ['', '- Causality items, per-window verification counts, weak-start records and all diagnostics: metrics.json. Decision application: ' + decision['decision'] + '; no automatic production change.']
    (DEST / 'CODEX_REPORT.md').write_text('\n'.join(report) + '\n')
    print('COMPLETE', len(frame), decision['decision'], f'{elapsed:.2f}s', flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        (DEST / 'CODEX_REPORT.md').write_text('# CODEX_REPORT\n\n- Status: STOPPED\n'
            '- Changed files: runner/check_causality and any allowed output artifacts; holdout log append only if reached.\n'
            '- Tests: stopped at failed check; traceback below.\n'
            f'- Items: expected/executed=562/{len(ROWS)}.\n- Acceptance: incomplete.\n'
            '- Deviations: no scope, strategy or gate changes.\n'
            f'- Execution: `{COMMAND}`; 4 fork workers; elapsed {time.perf_counter()-STARTED:.2f}s.\n'
            '- Quota: unavailable.\n- Blockers: ' + repr(exc) + '\n\n```\n' + traceback.format_exc() + '```\n')
        raise
