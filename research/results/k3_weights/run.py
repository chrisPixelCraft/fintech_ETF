"""Execute the frozen K3 weight protocol, with phase barriers and no tuning."""
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
from fractions import Fraction

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
    DEST / 'PREREGISTRATION.md': 'b22cde4c3008ff1f88825a1ffff9ee5d0ade2d9ce51759078ca20af1ddeeb286',
    DEST / 'strategies.py': '2f3b2adcdc615a7d0d109c64427f79a36294e6bdb8debe10e1d7c147fb9ae7d7',
    DEST.parent / 'k3_composite/strategies.py': 'ff357195946691a22691dd604e2086d2d228c9d60faf2e30d5182182a0a0d85d',
    DEST.parent / 'conc_tournament/strategies.py': 'c05cb770ff1eaec3ffec8f61d10cfc8e18dc693b7aecb5373402e0c6f6c31121',
    DEST.parent / 'conc_tournament/results.csv': 'de312da76203b2b6e82bcc590bb593814d701a66af1a393caa11cea26444303a',
}
COMMAND = 'PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/k3_weights/run.py'
LOG = ROOT / 'research/holdout_access_log.jsonl'
WORKERS = 8
STARTED = time.perf_counter()
ROWS, PROBLEMS, PHASES = [], {}, {}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_frozen():
    for path, expected in FROZEN.items():
        assert digest(path) == expected, f'Frozen hash mismatch: {path}'


def dump(name, value):
    (DEST / name).write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + '\n')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True)


def execute(job):
    name, ep, phase = job
    weight = WEIGHTS[name]
    strategy = (STRATEGIES.K2Buffer(MARKET.calendar, RULES, MARKET.close.columns) if weight is None
                else STRATEGIES.K3Weighted(MARKET.calendar, RULES, MARKET.close.columns, weights=weight))
    result = backtest.run_episode(MARKET, ep, strategy, RULES, engine.policy_of(BASE),
                                  backtest.ExecutionConfig(**BASE['execution']))
    problems = backtest.verify_episode(MARKET, ep, result, RULES)
    s = result['summary']
    assert s['complete'], (name, ep.episode_id, 'incomplete')
    reasons = [dict(date=str(r.date), warning_reasons=str(r.warning_reasons))
               for r in result['ledger'].itertuples() if r.warning]
    assert len(reasons) == s['warning_days']
    row = dict(strategy=name, weights=json.dumps(weight if weight is not None else []),
               window_id=ep.episode_id, phase=phase, split=ep.split,
               set='recent' if ep.split == 'recent_diagnostic' else 'historical',
               start=str(ep.start.date()), end=str(ep.end.date()),
               **{k: s[k] for k in ['terminal_return', 'max_drawdown', 'costs', 'turnover',
                                    'warning_days', 'disqualified', 'benchmark_0050_return', 'complete']},
               delta_vs_0050=s['terminal_return']-s['benchmark_0050_return'],
               warning_reasons=json.dumps(reasons, allow_nan=False), verify_problem_count=len(problems))
    assert all(np.isfinite(v) for v in row.values() if isinstance(v, (int, float)))
    return row, problems


def collect(value, pilot=False):
    row, problems = value
    ROWS.append(row)
    PROBLEMS[f"{row['strategy']}:{row['window_id']}"] = problems
    if pilot:
        assert not problems, problems
    if pilot or len(ROWS) % 100 == 0:
        print('PILOT' if pilot else 'PROGRESS', len(ROWS), row['phase'], row['strategy'], flush=True)


def run_phase(phase, windows, names, pilot=False):
    start = time.perf_counter()
    jobs = [(name, ep, phase) for ep in windows for name in names]
    done = set()
    if pilot:
        for ep in windows[:3]:
            for name in ['K3w_00', 'K3w_15']:
                collect(execute((name, ep, phase)), pilot=True)
                done.add((name, ep.episode_id))
    pending = [j for j in jobs if (j[0], j[1].episode_id) not in done]
    with mp.get_context('fork').Pool(WORKERS) as pool:
        for value in pool.imap(execute, pending, chunksize=1):
            collect(value)
    frame = pd.DataFrame([r for r in ROWS if r['phase'] == phase])
    assert len(frame) == len(jobs) and frame.complete.all()
    assert not frame.duplicated(['strategy', 'window_id']).any()
    assert not frame.isna().any().any()
    assert np.isfinite(frame.select_dtypes(include='number').to_numpy()).all()
    PHASES[phase] = dict(expected=len(jobs), processed=len(frame), failed=0,
                         seconds=time.perf_counter()-start, strategies=names)
    pd.DataFrame(ROWS).to_csv(DEST / 'results.csv', index=False)
    dump('metrics.json', dict(status='IN_PROGRESS', phases=PHASES, verification_problems=PROBLEMS))
    return frame


def metric(frame):
    assert len(frame)
    d = frame.delta_vs_0050
    up, down = int((d >= .05).sum()), int((d <= -.05).sum())
    return dict(count=len(frame), p_gain_5pp=up/len(frame), p_gain_10pp=float((d >= .10).mean()),
                p_loss_5pp=down/len(frame), tail_edge=(up-down)/len(frame),
                tail_count=up-down, gain_10pp_count=int((d >= .10).sum()),
                mean_delta_vs_0050=float(d.mean()), median_delta_vs_0050=float(d.median()),
                win_rate=float((d > 0).mean()), total_warning_days=int(frame.warning_days.sum()),
                max_warning_days=int(frame.warning_days.max()), disqualifications=int(frame.disqualified.sum()),
                mean_turnover=float(frame.turnover.mean()), mean_costs=float(frame.costs.mean()),
                verify_problem_count=int(frame.verify_problem_count.sum()))


def summary(frame, names, period):
    return [dict(period=period, strategy=n, weights=json.dumps(WEIGHTS.get(n) or []),
                 **metric(frame[frame.strategy == n])) for n in names]


def reference_for(reference, windows, names):
    # Numeric control outcomes are accessed only for the current phase.
    ids = [e.episode_id for e in windows]
    part = reference[reference.window_id.isin(ids) & reference.strategy.isin(names)].copy()
    assert len(part) == len(ids)*len(names)
    for ep in windows:
        refs = part[part.window_id == ep.episode_id]
        assert all(r.split == ep.split and pd.Timestamp(r.start) == ep.start and
                   pd.Timestamp(r.end) == ep.end for r in refs.itertuples())
    return part


def select_weight(dev_stats):
    by_name = {r['strategy']: r for r in dev_stats}
    smooth, traces = {}, []
    for i, weight in enumerate(STRATEGIES.GRID):
        name = f'K3w_{i:02d}'
        # The added equal-weight point is isolated, as explicitly required in TASK_BRIEF.
        neighbours = [] if i == 15 else [f'K3w_{j:02d}' for j, other in enumerate(STRATEGIES.GRID[:15])
            if j != i and all(abs(a-b) <= .25 for a, b in zip(weight, other))]
        members = [name] + neighbours
        value = sum((Fraction(by_name[n]['tail_count'], by_name[n]['count']) for n in members), Fraction())/len(members)
        smooth[name] = value
        r = by_name[name]
        r.update(eligible=r['disqualifications'] == 0 and r['max_warning_days'] <= 1 and r['total_warning_days'] <= 2,
                 smoothed_tail_edge=float(value), smoothed_tail_edge_exact=str(value),
                 neighbours=json.dumps(neighbours), boundary=any(w in (0, 1) for w in weight))
    candidates = [f'K3w_{i:02d}' for i in range(16) if by_name[f'K3w_{i:02d}']['eligible']]
    assert candidates, 'No eligible DEV weight; no fallback is preregistered'
    traces.append(dict(step='eligibility', remaining=candidates[:]))
    best = max(smooth[n] for n in candidates)
    candidates = [n for n in candidates if smooth[n] == best]
    traces.append(dict(step='maximum_smoothed_tail_edge', value=float(best), exact=str(best), remaining=candidates[:]))
    gain = max(by_name[n]['gain_10pp_count'] for n in candidates)
    candidates = [n for n in candidates if by_name[n]['gain_10pp_count'] == gain]
    traces.append(dict(step='maximum_p_gain_10pp', value=gain/166, remaining=candidates[:]))
    selected = candidates[0]
    traces.append(dict(step='earliest_grid_order', selected=selected))
    return dict(selected_strategy=selected, selected_weight=WEIGHTS[selected], boundary=by_name[selected]['boundary'],
                smoothed_values={n: dict(value=float(v), exact=str(v)) for n,v in smooth.items()}, tie_break_trace=traces)


def bootstrap(dev, control, selected):
    paired = dev[dev.strategy == selected].merge(control[['window_id', 'delta_vs_0050']],
        on='window_id', suffixes=('', '_control'), validate='one_to_one').sort_values('window_id')
    assert len(paired) == 166
    def tail(d):
        a = d.to_numpy()
        return (a >= .05).astype(int)-(a <= -.05).astype(int)
    difference = tail(paired.delta_vs_0050)-tail(paired.delta_vs_0050_control)
    rng = np.random.default_rng(0)
    estimates = difference[rng.integers(0, len(difference), size=(10000, len(difference)))].mean(axis=1)
    return dict(draws=10000, seed=0, unit='paired window', difference_selected_minus_k2=float(difference.mean()),
                percentile_95_ci=np.quantile(estimates, [.025, .975]).tolist(), interval='95% percentile; linear quantiles')


def main():
    global MARKET, RULES, BASE, STRATEGIES, WEIGHTS
    check_frozen()
    before_status = git('status', '--porcelain')
    assert len(before_status.splitlines()) == 47
    log_before = LOG.read_bytes()
    assert len(log_before.splitlines()) == 96 and log_before.endswith(b'\n')
    protected = {p: digest(p) for p in DEST.iterdir() if p.is_file()}
    # Snapshot all pre-existing dirty files so the status check also protects their content.
    outside = {}
    raw = subprocess.check_output(['git', 'status', '--porcelain', '-z', '--untracked-files=all'], cwd=ROOT)
    for entry in raw.split(b'\0'):
        if not entry:
            continue
        p = ROOT / entry[3:].decode()
        if p.is_file() and not p.is_relative_to(DEST) and p != LOG:
            outside[p] = digest(p)
    import strategies as STRATEGIES
    WEIGHTS = {f'K3w_{i:02d}': tuple(w) for i,w in enumerate(STRATEGIES.GRID)}
    WEIGHTS['K2b'] = None
    assert len(STRATEGIES.GRID) == 16
    BASE = json.loads((ROOT / 'production/strategy.json').read_text())
    RULES = load_rules()
    source = ROOT / 'production_runs/preflight_20261026'
    MARKET = load_market(yahoo_path=source / 'strategy_retest/corrected_nominal_daily.parquet',
                         official_path=source / 'official_combined.csv', calendar_path=source / 'calendar_v2.json')
    windows = {s: build_episodes(MARKET.calendar, s, offsets=('month_start', 'mid_month'),
                 start='2015-01-01', data_end='2026-10-22') for s in ['dev', 'validation', 'holdout']}
    assert {s: len(w) for s,w in windows.items()} == dict(dev=166, validation=70, holdout=40)
    reference = pd.read_csv(DEST.parent / 'conc_tournament/results.csv', float_precision='round_trip')
    assert len(reference) == 1124 and not reference.duplicated(['strategy', 'window_id']).any()
    end = MARKET.position('2026-10-22')+1
    recent = []
    for i in range(5):
        days = tuple(MARKET.calendar[end-120+i*24:end-96+i*24])
        assert len(days) == 24
        recent.append(Episode(f'W{i+1}', 'recent_diagnostic', MARKET.calendar[MARKET.position(days[0])-1], days))
    assert recent[0].start == pd.Timestamp('2026-04-30')
    assert recent[-1].start == pd.Timestamp('2026-09-16') and recent[-1].end == pd.Timestamp('2026-10-22')
    # Phase 1. Only DEV simulations and DEV control outcomes enter selection.
    dev_names = list(WEIGHTS)
    dev = run_phase('dev', windows['dev'], dev_names, pilot=True)
    dev_control = reference_for(reference, windows['dev'], ['K2'])
    dev_stats = summary(dev, dev_names, 'dev')
    selection = select_weight(dev_stats)
    selected = selection['selected_strategy']
    selection['paired_bootstrap'] = bootstrap(dev, dev_control, selected)
    selection['equal_weight_duplicate'] = selected == 'K3w_15'
    pd.DataFrame([r for r in dev_stats if r['strategy'] != 'K2b']).to_csv(DEST / 'dev_grid.csv', index=False)
    dump('selection.json', selection)
    assert LOG.read_bytes() == log_before
    print('DEV_SELECTED', selected, WEIGHTS[selected], flush=True)
    # Phase 2. Selection is persisted before accessing validation outcomes.
    names = list(dict.fromkeys([selected, 'K3w_15', 'K2b']))
    validation = run_phase('validation', windows['validation'], names)
    validation_control = reference_for(reference, windows['validation'], ['K2'])
    validation_stats = summary(pd.concat([validation, validation_control]), names+['K2'], 'validation')
    stats = {r['strategy']: r for r in validation_stats}
    s, k = stats[selected], stats['K2']
    gates = dict(
        V1=dict(passed=s['tail_count'] >= k['tail_count'], selected=s['tail_edge'], minimum=k['tail_edge']),
        V2=dict(passed=s['p_loss_5pp'] <= k['p_loss_5pp']+.03, selected=s['p_loss_5pp'], maximum=k['p_loss_5pp']+.03),
        V3=dict(passed=s['mean_delta_vs_0050'] > 0, selected=s['mean_delta_vs_0050'], exclusive_minimum=0),
        V4=dict(passed=s['disqualifications'] == 0 and s['total_warning_days'] == 0,
                disqualifications=s['disqualifications'], warning_days=s['total_warning_days'], maximum_each=0))
    decision = dict(decision='ADOPT' if all(g['passed'] for g in gates.values()) else 'REJECT',
                    selected_strategy=selected, selected_weight=WEIGHTS[selected], gates=gates,
                    reference_rows_report_only=['K3w_15', 'K2b'], automatic_strategy_change=False)
    pd.DataFrame(validation_stats).to_csv(DEST / 'validation.csv', index=False)
    dump('decision.json', decision)
    assert LOG.read_bytes() == log_before
    print('VALIDATION', decision['decision'], flush=True)
    # Phase 3. Append access records before any holdout strategy simulation.
    with LOG.open('ab') as out:
        for name in names:
            record = dict(utc=datetime.now(timezone.utc).isoformat(), run_id=f'{name}__holdout_k3_weights',
                          config=str(DEST / 'strategies.py'), config_hash=digest(DEST / 'strategies.py'),
                          git_commit=git('rev-parse', 'HEAD').strip(), git_dirty=bool(before_status), argv=sys.argv)
            out.write((json.dumps(record)+'\n').encode())
    report = run_phase('report', windows['holdout']+recent, names)
    controls = ['K2', 'K1', 'Mom20', 'T2']
    report_control = reference_for(reference, windows['holdout']+recent, controls)
    combined = pd.concat([report, report_control])
    report_stats = []
    for period, split in [('holdout', 'holdout'), ('recent', 'recent_diagnostic')]:
        report_stats += summary(combined[combined.split == split], names+controls, period)
    pd.DataFrame(report_stats).to_csv(DEST / 'report.csv', index=False)
    # Full-run acceptance checks and serialized artifact checks.
    frame = pd.DataFrame(ROWS)
    expected = 2822 + (70+45)*len(names)
    assert len(frame) == expected and len(PROBLEMS) == expected
    assert sum(map(len, PROBLEMS.values())) == 0
    assert PHASES['dev']['processed'] == 2822
    assert PHASES['validation']['processed'] == 70*len(names)
    assert PHASES['report']['processed'] == 45*len(names)
    joined = frame.merge(reference[reference.strategy == 'K2'][['window_id', 'benchmark_0050_return']],
                         on='window_id', suffixes=('', '_control'), validate='many_to_one')
    assert len(joined) == expected and np.allclose(joined.benchmark_0050_return,
           joined.benchmark_0050_return_control, rtol=0, atol=1e-12)
    check_frozen()
    assert all(digest(p) == h for p,h in protected.items())
    assert all(digest(p) == h for p,h in outside.items())
    assert all(digest(ROOT / p) == h for p,h in MARKET.provenance.items())
    log_after = LOG.read_bytes()
    assert log_after.startswith(log_before) and len(log_after.splitlines()) == 96+len(names)
    added = [json.loads(line) for line in log_after[len(log_before):].splitlines()]
    assert [r['run_id'] for r in added] == [f'{n}__holdout_k3_weights' for n in names]
    assert git('status', '--porcelain') == before_status
    for name in ['dev_grid.csv', 'validation.csv', 'report.csv', 'results.csv']:
        data = pd.read_csv(DEST / name, float_precision='round_trip')
        assert not data.isna().any().any()
        assert np.isfinite(data.select_dtypes(include='number').to_numpy()).all()
    for name in ['selection.json', 'decision.json']:
        json.loads((DEST / name).read_text(), parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
    checks = dict(episodes_expected=expected, episodes_processed=len(frame), episodes_failed=0,
                  episodes_complete=int(frame.complete.sum()), pilot_episodes=6, verification_calls=len(PROBLEMS),
                  verification_problem_count=0, frozen_hashes_unchanged=True, protected_inputs_unchanged=True,
                  market_provenance_unchanged=True, dirty_files_unchanged=True, baseline_git_status_lines=47,
                  git_status_unchanged=True, holdout_prior_lines=96, holdout_added_lines=len(names),
                  holdout_prior_bytes_sha256=hashlib.sha256(log_before).hexdigest(),
                  holdout_prior_bytes_unchanged=True, finite_nonmissing_outputs=True, benchmark_matches_controls=True)
    elapsed = time.perf_counter()-STARTED
    dump('metrics.json', dict(status='COMPLETE', command=COMMAND, workers=WORKERS, elapsed_seconds=elapsed,
         phases=PHASES, checks=checks, verification_problems=PROBLEMS, dev=dev_stats,
         dev_k2_control=metric(dev_control), validation=validation_stats, report=report_stats,
         paired_bootstrap=selection['paired_bootstrap'],
         frozen_sha256={str(p.relative_to(ROOT)): h for p,h in FROZEN.items()}))
    json.loads((DEST / 'metrics.json').read_text(), parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
    write_report(selection, decision, dev_stats, validation_stats, report_stats, checks, elapsed)
    check_frozen()
    assert git('status', '--porcelain') == before_status and LOG.read_bytes() == log_after
    print('COMPLETE', expected, decision['decision'], f'{elapsed:.2f}s', flush=True)


def write_report(selection, decision, dev, validation, report, checks, elapsed):
    lines = ['# CODEX_REPORT', '', f"- Status: COMPLETE; validation {decision['decision']} (no strategy change).",
        '- Changed files: run.py, dev_grid.csv, selection.json, validation.csv, decision.json, report.csv, results.csv, metrics.json, CODEX_REPORT.md; holdout_access_log.jsonl appended only.',
        f"- Items expected/processed/failed: {checks['episodes_expected']}/{checks['episodes_processed']}/0; DEV 2822, validation {PHASES['validation']['processed']}, report {PHASES['report']['processed']}.",
        '- Asserts: 6 pilot episodes; every episode complete and verified (0 problems); window counts 166/70/40+5 and IDs/dates match controls; finite/nonmissing outputs; 5 frozen hashes, market provenance, pre-existing dirty files and 47-line Git status unchanged; prior 96 log lines byte-identical.',
        f"- Selection: {selection['selected_strategy']}, weights (momentum, trust, revenue)={selection['selected_weight']}; boundary={selection['boundary']}; equal-weight duplicate={selection['equal_weight_duplicate']}.",
        '- DEV paired bootstrap: '+json.dumps(selection['paired_bootstrap']),
        '- Plan deviations / literal conventions: no scope, architecture or rule changes. Equal-weight point is isolated; smoothing includes all neighbouring grid points regardless of eligibility. Exact rational smoothing/ties; win means Δ50 > 0. Bootstrap uses NumPy default_rng(0), 95% percentile interval (level unspecified). Turnover/costs are episode means. CSV weight IDs map to weights in every output. Control rows are stored values, never rerun; their warning dates were not re-created. No external research.',
        f'- Execution: `{COMMAND}`; main directory; {elapsed:.2f}s; {WORKERS} fork workers, ordered imap; serial 3-window × 2-strategy DEV pilot reused in counts. Phase files persisted before the next phase.',
        '- Quota: unavailable; one grid batch; no adaptive weight search.', '- Blockers: none.', '',
        '| DEV weight | Eligible | ≥+5pp | ≥+10pp | ≤−5pp | Tail | Smoothed | Mean Δ50 | Warn total/max | DQ |',
        '|---|---|---|---|---|---|---|---|---|---|']
    for r in dev:
        if r['strategy'] == 'K2b':
            continue
        lines.append(f"| {r['weights']} | {r['eligible']} | {r['p_gain_5pp']:.2%} | {r['p_gain_10pp']:.2%} | {r['p_loss_5pp']:.2%} | {r['tail_edge']:.2%} | {r['smoothed_tail_edge']:.2%} | {r['mean_delta_vs_0050']:.2%} | {r['total_warning_days']}/{r['max_warning_days']} | {r['disqualifications']} |")
    lines += ['', '| Validation gate | Passed | Numbers (decimal units) |', '|---|---|---|']
    for name,g in decision['gates'].items():
        lines.append(f"| {name} | {g['passed']} | {json.dumps({k:v for k,v in g.items() if k != 'passed'})} |")
    lines += ['', '| Period | Strategy | N | Mean Δ50 | Median Δ50 | Win | ≥+5pp | ≥+10pp | ≤−5pp | Tail | Warn total/max | DQ | Turnover | Costs TWD |', '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for r in [next(r for r in dev if r['strategy'] == 'K2b')]+validation+report:
        values = [r[k] for k in ['mean_delta_vs_0050','median_delta_vs_0050','win_rate','p_gain_5pp','p_gain_10pp','p_loss_5pp','tail_edge']]
        lines.append(f"| {r['period']} | {r['strategy']} | {r['count']} | "+' | '.join(f'{v:.2%}' for v in values)+f" | {r['total_warning_days']}/{r['max_warning_days']} | {r['disqualifications']} | {r['mean_turnover']:.4f} | {r['mean_costs']:.2f} |")
    lines += ['', '- Full DEV metrics and heatmap coordinates: dev_grid.csv. Episode warning dates/reasons: results.csv; verification details: metrics.json. Selection tie trace: selection.json.',
              '- Limits: 2026 survivor universe, overlapping windows, 16-weight selection bias, 70-window validation, assumed revenue availability on day 11; holdout/recent are report-only.']
    (DEST / 'CODEX_REPORT.md').write_text('\n'.join(lines)+'\n')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        (DEST / 'CODEX_REPORT.md').write_text('# CODEX_REPORT\n\n- Status: STOPPED\n'
            '- Changed files: run.py, CODEX_REPORT.md and partial allowed outputs only.\n'
            f'- Items expected/processed/failed: 3167 (or duplicate-adjusted)/{len(ROWS)}/incomplete.\n'
            f'- Asserts/blocker: {exc!r}\n- Plan deviations: none; stopped at failed check.\n'
            f'- Execution: `{COMMAND}`; workers={WORKERS}; {time.perf_counter()-STARTED:.2f}s.\n'
            '- Quota: unavailable.\n\n```\n'+traceback.format_exc()+'```\n')
        raise
