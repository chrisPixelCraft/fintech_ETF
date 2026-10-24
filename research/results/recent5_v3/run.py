"""Frozen recent-five-window comparison; writes only beside this script."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import math
import subprocess
import sys
sys.dont_write_bytecode = True
import importlib.util
import traceback
import time

import numpy as np
import pandas as pd
import competition
import research
from competition import backtest, portfolio
from competition.data import load_market
from competition.episodes import Episode
from competition.rules import ROOT, load_rules
from production import active_share, engine
from research.baselines import MomentumConfig, momentum_score
from research.run_experiment import build_strategy

sys.path.append(str(ROOT / 'legacy/ml'))
from momv2.signals import SignalConfig, score

DEST = Path(__file__).resolve().parent
PRE = DEST / 'PREREGISTRATION.md'
FROZEN = {
    PRE: 'd4da1785d3873d8824dd26757298d5164943d1efbe7a7f7acd7219a7d1bb5065',
    DEST / 'strategies.py': 'becd34b594fc15d07d3de1660d427bee7d997db4bf17ab83eab90705972815fe',
    DEST / 'data/flows_20260924_20261022.csv': '14cd7ae3609489794d8f00ec7ee29bed35e084d81c7bea20b0f5e00694bb741f',
    DEST.parent / 'recent5_new/strategies.py': '386900cbe56e6af8ee14ed35480805dbd81114c0ad9112e17233abfe8a631793',
    DEST.parent / 'recent5_new/results.csv': '43f565f833fdc5745993ccdfbe50cb36ac91cbf07b9c88933fcd68db58c789b8',
}
NAMES = ['Mom20', 'C1', 'T1', 'T2', 'T3']
CANDIDATES = ['T1', 'T2', 'T3']
PROGRESS = []
COMMAND = 'PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/recent5_v3/run.py'
INTERPRETATIONS = [
    'C1 is the reference for G0 warnings, G2 mean delta and G3 drawdown/crash; only T1/T2/T3 enter selection.',
    'G4 requires 24 W5 daily rows with minimum across all required ETFs >= 15%; AS is heuristic/UNVERIFIED.',
    'Same template AS fixed-snapshot fallback: when actual-date check has no by_etf, use production.active_share.active_share against the mandated 2026-09-24 top10 snapshot; retain actual-date status and invalid-data fields.',
    'Same template crash definition: Mom20 W5 five worst sessions, stable ordering; compound each strategy on those dates with initial-capital predecessor and final dividend credit.',
    'Tie-break is highest win count, then highest mean delta vs 0050; exact ties are listed without an added criterion.',
    'Strategy logs are the unchanged strategies own exposed log; T1 filter counts include build and all revenue rescreens.',
]


def check_frozen():
    for path, expected in FROZEN.items():
        assert digest(path) == expected, f'Frozen hash mismatch: {path}'


def load_strategies():
    spec = importlib.util.spec_from_file_location('recent3_frozen_runner', DEST.parent / 'recent3_core_satellite/run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.path.insert(0, str(DEST))
    from strategies import TrustRev, LargeTrust, LargeRev
    return module.CoreStrategy, {'T1': TrustRev, 'T2': LargeTrust, 'T3': LargeRev}


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + '\n')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def status():
    return subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)


def main():
    started = time.perf_counter()
    check_frozen()
    CoreStrategy, candidate_classes = load_strategies()
    assert Path(competition.__file__).resolve().parent == ROOT / 'competition'
    assert Path(research.__file__).resolve().parent == ROOT / 'research'
    before_status = status()
    assert len(before_status.splitlines()) == 35, 'Unexpected starting working-tree status'
    inputs = [*FROZEN, DEST / 'TASK_BRIEF.md', DEST.parent / 'recent3_core_satellite/run.py',
              DEST.parent / 'recent3_core_satellite/results.csv', ROOT / 'production/strategy.json',
              ROOT / 'data/reference/competition_rules.json', ROOT / 'data/momv2/etf_top10_20260924.csv']
    inputs += [p for folder in ['production', 'competition', 'research'] for p in (ROOT / folder).glob('*.py')]
    inputs += [ROOT / 'legacy/ml/momv2/signals.py', ROOT / 'research/results/recent24_strategies/comparison.json',
               ROOT / 'data/hybrid/flows.csv', ROOT / 'data/hybrid/revenue.csv',
               ROOT / 'data/reference/universe_competition_20260731.csv',
               DEST.parent / 'recent5_new/data/revenue_202609.csv', DEST.parent / 'recent5_new/run.py']
    inputs += [p for p in DEST.rglob('*') if p.is_file() and p.name not in
               {'run.py', 'results.csv', 'results.json', 'gates.json', 'active_share.csv', 'CODEX_REPORT.md'}
               and 'episodes' not in p.relative_to(DEST).parts]
    frozen = {str(p.relative_to(ROOT)): digest(p) for p in inputs}
    base = json.loads((ROOT / 'production/strategy.json').read_text())
    rules = load_rules()
    source = ROOT / 'production_runs/preflight_20261026'
    market = load_market(yahoo_path=source / 'strategy_retest/corrected_nominal_daily.parquet',
                         official_path=source / 'official_combined.csv', calendar_path=source / 'calendar_v2.json')
    end = market.position('2026-10-22') + 1
    windows = {}
    for i, name in enumerate(['W1', 'W2', 'W3', 'W4', 'W5']):
        days = tuple(market.calendar[end - 120 + i * 24:end - 96 + i * 24])
        assert len(days) == 24
        windows[name] = Episode(name, 'recent_diagnostic', market.calendar[market.position(days[0]) - 1], days)
    assert windows['W5'].sessions[0] == pd.Timestamp('2026-09-16')
    reference = pd.read_csv(DEST.parent / 'recent3_core_satellite/results.csv', float_precision='round_trip')
    for old, new in [('W1', 'W3'), ('W2', 'W4'), ('W3', 'W5')]:
        old_rows = reference[reference.window == old]
        assert len(old_rows) == 5
        for r in old_rows.itertuples():
            assert (pd.Timestamp(r.start), pd.Timestamp(r.end)) == (windows[new].start, windows[new].end)
    reference = pd.read_csv(DEST.parent / 'recent5_new/results.csv', float_precision='round_trip')
    etfs = active_share.load_etf_holdings(ROOT / 'data/momv2/etf_top10_20260924.csv')
    required = sorted(etfs)
    assert required
    rows, as_rows, replay = [], [], {}
    consistency = {}
    # Replay all ten frozen references before candidates; each episode gets a fresh instance.
    jobs = [(s, w) for w in windows for s in ['Mom20', 'C1']]
    jobs += [(s, w) for w in windows for s in CANDIDATES]
    for name, window in jobs:
        ep = windows[window]
        strategy = (build_strategy(base, rules) if name == 'Mom20' else
                    CoreStrategy('C1', rules) if name == 'C1' else
                    candidate_classes[name](market.calendar, rules, market.close.columns))
        result = backtest.run_episode(market, ep, strategy, rules, engine.policy_of(base),
                                      backtest.ExecutionConfig(**base['execution']))
        problems = backtest.verify_episode(market, ep, result, rules)
        summary = result['summary']
        PROGRESS.append(dict(strategy=name, window=window, complete=summary['complete'],
                             verification_problem_count=len(problems), verification_problems=problems))
        if name in ['Mom20', 'C1']:
            ref = reference[(reference.strategy == name) & (reference.window == window)]
            assert len(ref) == 1
            expected, actual = float(ref.iloc[0].terminal_return), summary['terminal_return']
            consistency[f'{name}_{window}'] = dict(actual=actual, expected=expected, tolerance=1e-12,
                                                  passed=abs(actual - expected) <= 1e-12)
            assert consistency[f'{name}_{window}']['passed'], (name, window, consistency[f'{name}_{window}'])
        if name == 'Mom20' and window == 'W5':
            assert abs(summary['terminal_return'] - (-.10124596758199833)) <= 1e-12
        dest = DEST / 'episodes' / f'{name}_{window}'
        dest.mkdir(parents=True, exist_ok=True)
        for key in ['ledger', 'trades', 'holdings', 'issues']:
            result[key].to_csv(dest / f'{key}.csv', index=False)
        dump(dest / 'strategy_log.json', getattr(strategy, 'log', []))
        replay[name, window] = result
        local_as = []
        for day in result['ledger'].itertuples():
            holdings = result['holdings'][result['holdings'].date == day.date]
            weights = {r.symbol.split('.')[0]: float(r.shares * r.close / day.nav) for r in holdings.itertuples()}
            report = active_share.check(weights, etfs, required=required, trade_date=str(day.date))
            fallback = not bool(report.by_etf)
            by_etf = report.by_etf or {e: active_share.active_share(weights, etfs[e]) for e in required}
            closest = min(by_etf, key=by_etf.get)
            record = dict(strategy=name, window=window, date=str(day.date), min_as=float(by_etf[closest]),
                          closest_etf=closest, below_15pct=bool(by_etf[closest] < .15), status=report.status,
                          snapshot_formula_fallback=fallback, invalid_data=';'.join(report.invalid_data),
                          stale=';'.join(report.stale), incomplete=';'.join(report.incomplete),
                          by_etf=json.dumps(by_etf, sort_keys=True), nav_tradable=float(day.nav))
            as_rows.append(record)
            local_as.append(record)
        worst_as = min(local_as, key=lambda x: x['min_as'])
        row = dict(strategy=name, window=window, **summary, verification_problem_count=len(problems),
                   verification_problems=problems, min_as=worst_as['min_as'], closest_etf=worst_as['closest_etf'],
                   min_as_date=worst_as['date'], as_days_below_15pct=sum(r['below_15pct'] for r in local_as),
                   as_rows=len(local_as), active_share_status=active_share.UNVERIFIED,
                   as_use='gate' if window == 'W5' else 'descriptive')
        rows.append(row)
        print(name, window, summary['terminal_return'], 'verify', len(problems), flush=True)
    by_key = {(r['strategy'], r['window']): r for r in rows}
    for row in rows:
        row['delta_vs_c1'] = row['terminal_return'] - by_key['C1', row['window']]['terminal_return']
        row['delta_vs_mom20'] = row['terminal_return'] - by_key['Mom20', row['window']]['terminal_return']
        row['delta_vs_0050'] = row['terminal_return'] - row['benchmark_0050_return']
    daily = {}
    for name in NAMES:
        res = replay[name, 'W5']
        nav = res['ledger'].nav.to_numpy(float).copy()
        nav[-1] += res['summary']['dividend_credit']
        daily[name] = pd.Series(nav / np.r_[rules.initial_capital, nav[:-1]] - 1,
                                index=res['ledger'].date)
    crash_dates = list(daily['Mom20'].sort_values(kind='stable').index[:5])
    crash = [dict(strategy=s, dates=crash_dates, compounded_return=float((1 + series.loc[crash_dates]).prod() - 1),
                  daily_returns={d: float(series[d]) for d in crash_dates}) for s, series in daily.items()]
    for row in rows:
        exposure = next(r for r in crash if r['strategy'] == row['strategy'])
        row['crash_w5_compounded_return'] = exposure['compounded_return']
        row['crash_w5_dates'] = exposure['dates']
        row['crash_w5_daily_returns'] = exposure['daily_returns']
    baseline_mean_dd = float(np.mean([by_key['C1', w]['max_drawdown'] for w in windows]))
    baseline_delta = float(np.mean([by_key['C1', w]['delta_vs_0050'] for w in windows]))
    baseline_crash = by_key['C1', 'W5']['crash_w5_compounded_return']
    gates = {}
    for name in CANDIDATES:
        candidate = [by_key[name, w] for w in windows]
        mean_delta50 = float(np.mean([r['delta_vs_0050'] for r in candidate]))
        wins50 = sum(r['delta_vs_0050'] > 0 for r in candidate)
        mean_dd = float(np.mean([r['max_drawdown'] for r in candidate]))
        w5 = by_key[name, 'W5']
        g0_checks = {w: dict(complete=by_key[name, w]['complete'],
                             verify_problems=by_key[name, w]['verification_problem_count'],
                             disqualified=by_key[name, w]['disqualified'],
                             warning_days=by_key[name, w]['warning_days'],
                             c1_warning_days=by_key['C1', w]['warning_days']) for w in windows}
        g0 = len(candidate) == 5 and all(x['complete'] and x['verify_problems'] == 0 and not x['disqualified']
                 and x['warning_days'] <= x['c1_warning_days'] for x in g0_checks.values())
        gates[name] = dict(
            G0=dict(passed=g0, completed=sum(r['complete'] for r in candidate), required_complete=5,
                    verification_problems=sum(r['verification_problem_count'] for r in candidate),
                    disqualifications=sum(r['disqualified'] for r in candidate), windows=g0_checks),
            G1=dict(passed=wins50 >= 4 and mean_delta50 > 0, wins=wins50, required_wins=4,
                    mean_delta_vs_0050=mean_delta50, minimum_mean_delta_exclusive=0),
            G2=dict(passed=mean_delta50 >= baseline_delta - .005, mean_delta_vs_0050=mean_delta50,
                    c1_mean_delta_vs_0050=baseline_delta, tolerance=.005, threshold=baseline_delta - .005),
            G3=dict(passed=mean_dd <= baseline_mean_dd + .01 and
                           w5['crash_w5_compounded_return'] >= baseline_crash - .01,
                    mean_max_drawdown=mean_dd, c1_mean_max_drawdown=baseline_mean_dd,
                    max_mean_drawdown=baseline_mean_dd + .01,
                    crash_w5_compounded_return=w5['crash_w5_compounded_return'],
                    c1_crash_w5_compounded_return=baseline_crash, min_crash_return=baseline_crash - .01),
            G4=dict(passed=w5['as_rows'] == 24 and w5['min_as'] >= .15, min_as=w5['min_as'],
                    threshold=.15, days_below_15pct=w5['as_days_below_15pct'], rows=w5['as_rows'],
                    status='heuristic/UNVERIFIED'))
        gates[name]['failing_gates'] = [g for g in ['G0', 'G1', 'G2', 'G3', 'G4'] if not gates[name][g]['passed']]
        gates[name]['all_passed'] = not gates[name]['failing_gates']
    descriptive = {}
    for name in NAMES:
        candidate = [by_key[name, w] for w in windows]
        descriptive[name] = dict(
            mean_delta_vs_0050_excluding_w1=float(np.mean([by_key[name, w]['delta_vs_0050'] for w in windows if w != 'W1'])),
            w1_w2_delta_vs_0050={w: by_key[name, w]['delta_vs_0050'] for w in ['W1', 'W2']},
            w1_w2_mean_delta_vs_0050=float(np.mean([by_key[name, w]['delta_vs_0050'] for w in ['W1', 'W2']])),
            turnover_by_window={r['window']: r['turnover'] for r in candidate},
            costs_by_window={r['window']: {k: r[k] for k in ['costs', 'fees', 'taxes']} for r in candidate},
            mean_turnover=float(np.mean([r['turnover'] for r in candidate])),
            total_costs=sum(r['costs'] for r in candidate))
    filters = {}
    for w in windows:
        log = json.loads((DEST / 'episodes' / f'T1_{w}' / 'strategy_log.json').read_text())
        filters[w] = [entry for entry in log if 'passed_filter' in entry]
    passed = [s for s in CANDIDATES if gates[s]['all_passed']]
    ranked = sorted(passed, key=lambda s: (gates[s]['G1']['wins'], gates[s]['G1']['mean_delta_vs_0050']), reverse=True)
    selected = [s for s in ranked if (gates[s]['G1']['wins'], gates[s]['G1']['mean_delta_vs_0050']) ==
                (gates[ranked[0]]['G1']['wins'], gates[ranked[0]]['G1']['mean_delta_vs_0050'])] if ranked else []
    decision = dict(candidates=gates, passing_candidates=passed, ranked_passing_candidates=ranked,
                    selected_candidates=selected, selected_candidate=selected[0] if len(selected) == 1 else None,
                    final_verdict='候選通過，由使用者決定是否取代 C1' if passed else 'C1 仍是首選',
                    automatic_strategy_change=False, descriptive=descriptive, t1_filter_counts=filters)
    assert len(rows) == 25 and sum(r['complete'] for r in rows) == 25
    assert len(as_rows) == 600 and all(r['as_rows'] == 24 for r in rows)
    assert len(consistency) == 10 and all(v['passed'] for v in consistency.values())
    assert all(np.isfinite(v) for r in rows for v in r.values() if isinstance(v, (int, float)))
    assert all(np.isfinite(r['min_as']) for r in as_rows)
    # Serialization checks nested numeric fields as well.
    json.dumps(rows, allow_nan=False)
    check_frozen()
    assert all(digest(ROOT / p) == h for p, h in frozen.items())
    assert all(digest(ROOT / p) == h for p, h in market.provenance.items())
    assert status() == before_status, 'Working-tree status changed'
    rows.sort(key=lambda r: (r['window'], r['strategy']))
    verification = {f'{r["strategy"]}_{r["window"]}': r['verification_problems'] for r in rows}
    checks = dict(episodes_expected=25, episodes_processed=len(rows), episodes_complete=sum(r['complete'] for r in rows),
                  verification_calls=len(verification), verification_problems=sum(map(len, verification.values())),
                  as_expected=600, as_processed=len(as_rows), as_formula_fallback_rows=sum(r['snapshot_formula_fallback'] for r in as_rows),
                  consistency=consistency, finite_results=True, protected_input_hashes_unchanged=True,
                  git_status_unchanged=True, baseline_git_status_lines=len(before_status.splitlines()),
                  verification_problem_counts={k: len(v) for k, v in verification.items()})
    payload = dict(status='COMPLETE', results=rows, crash_exposure=crash, descriptive=descriptive,
                   t1_filter_counts=filters, checks=checks,
                   windows={w: dict(start=str(e.start.date()), end=str(e.end.date()), prior_session=str(e.prior_session.date())) for w, e in windows.items()},
                   interpretations=INTERPRETATIONS, input_sha256={**frozen, **market.provenance}, command=COMMAND,
                   elapsed_seconds=time.perf_counter() - started,
                   limitations=['Five windows; W3-W5 already observed; 2026 universe; revenue assumed available on day 11.',
                                'AS uses incomplete fixed-date top10 holdings, heuristic/UNVERIFIED.'])
    dump(DEST / 'results.json', payload)
    flat = [{k: json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v for k, v in r.items()} for r in rows]
    pd.DataFrame(flat).to_csv(DEST / 'results.csv', index=False)
    pd.DataFrame(as_rows).to_csv(DEST / 'active_share.csv', index=False)
    dump(DEST / 'gates.json', decision)
    report = ['# CODEX_REPORT', '',
        '- Status: COMPLETE; ' + decision['final_verdict'] + '.',
        '- Changed files: run.py, results.csv, results.json, gates.json, active_share.csv, CODEX_REPORT.md; episodes/{strategy}_{window}/{ledger,trades,holdings,issues}.csv and strategy_log.json (25 directories). Allowed paths only.',
        '- Tests run and results: 25/25 verify_episode calls; ' + str(checks['verification_problems']) + ' problems; 10/10 C1/Mom20 consistency checks (1e-12), Mom20 W5 fixed return, template date assertions, 600 AS rows, finite results, start/end frozen hashes, input/provenance hashes and unchanged 35-line Git status PASS.',
        '- Items expected/processed/failed: episodes 25/25/0; verification 25/25/' + str(sum(bool(v) for v in verification.values())) + '; AS 600/600/0.',
        '- Acceptance criteria: all execution/output checks PASS; candidate gate failures are outcomes.',
        '- Execution: `' + COMMAND + '`; main directory; elapsed ' + f'{payload["elapsed_seconds"]:.2f}s; no Git writes.',
        '- Quota: unavailable; one fixed batch, no parameter search.', '- Blockers: none.', '',
        '| Candidate | G0 | G1 | G2 | G3 | G4 heuristic | Wins | Mean Δ50 |',
        '|---|---|---|---|---|---|---|---|']
    for name, g in gates.items():
        report.append('| ' + name + ' | ' + ' | '.join('PASS' if g[x]['passed'] else 'FAIL' for x in ['G0','G1','G2','G3','G4']) +
                      f' | {g["G1"]["wins"]}/5 | {g["G1"]["mean_delta_vs_0050"]:.4%} |')
    report += ['', 'Failing gates (returns/drawdowns in decimal units):', '']
    for name, g in gates.items():
        report.append('- ' + name + ': ' + ('; '.join(k + ' ' + json.dumps(g[k], sort_keys=True) for k in g['failing_gates']) or 'none'))
    report += ['', '| Strategy | Mean Δ50 ex W1 | W1 Δ50 | W2 Δ50 | Mean turnover | Total costs (TWD) |',
               '|---|---|---|---|---|---|']
    for name, d in descriptive.items():
        report.append(f'| {name} | {d["mean_delta_vs_0050_excluding_w1"]:.4%} | {d["w1_w2_delta_vs_0050"]["W1"]:.4%} | {d["w1_w2_delta_vs_0050"]["W2"]:.4%} | {d["mean_turnover"]:.4f} | {d["total_costs"]:.2f} |')
    report += ['', '- T1 filter counts (build/rescreen chronological): ' + '; '.join(w + ': ' + ', '.join(str(x['passed_filter']) for x in log) for w, log in filters.items()) + '.',
               '- Per-window turnover, fees and taxes: results.csv and gates.json descriptive fields.',
               '', '| Strategy | W5 crash compounded return |', '|---|---|']
    report += [f'| {r["strategy"]} | {r["compounded_return"]:.4%} |' for r in crash]
    report += ['', '- Crash dates (Mom20 W5 worst five): ' + ', '.join(crash_dates) + '.',
               '', '- Verification problem counts (W1/W2/W3/W4/W5):']
    report += ['  - ' + name + ': ' + '/'.join(str(by_key[name, w]['verification_problem_count']) for w in windows) for name in NAMES]
    report += ['', 'Plan deviations / interpretations:', ''] + ['- ' + x for x in INTERPRETATIONS]
    report += ['- No deviations; strategy logic, parameters, gates and architecture unchanged.',
               f'- AS fallback: {checks["as_formula_fallback_rows"]}/600 rows. AS remains heuristic/UNVERIFIED.',
               '- Selection order: ' + (', '.join(ranked) if ranked else 'none; retain C1') + '.']
    (DEST / 'CODEX_REPORT.md').write_text('\n'.join(report) + '\n')
    print(json.dumps(checks, indent=2))
    print(decision['final_verdict'], selected)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        frozen_ok = {str(p.relative_to(ROOT)): p.exists() and digest(p) == h for p, h in FROZEN.items()}
        report = ['# CODEX_REPORT', '', '- Status: STOPPED',
                  '- Changed files: run.py and partial outputs within allowed directory only.',
                  '- Tests run and results: stopped; see traceback.',
                  f'- Items expected/processed/failed: 25 expected; {len(PROGRESS)} processed; remaining incomplete.',
                  '- Acceptance criteria: not met.', '- Plan deviations / interpretations: no strategy or gate changes.',
                  '- Execution: `' + COMMAND + '`', '- Quota: unavailable.', '- Blockers: ' + repr(exc),
                  '- Frozen hash checks at stop: ' + json.dumps(frozen_ok), '',
                  '| Gate | Result |', '|---|---|', '| G0–G4 | Not evaluated: stopped |', '',
                  'Processed episodes: ' + json.dumps(PROGRESS), '', 'Traceback:', '```', traceback.format_exc(), '```', '']
        (DEST / 'CODEX_REPORT.md').write_text('\n'.join(report))
        raise
