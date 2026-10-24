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
    PRE: '1659fba5e3b2234ca37c5f59a037eedadd7e414f4db76331843d559b3b9f1ff6',
    DEST / 'strategies.py': '386900cbe56e6af8ee14ed35480805dbd81114c0ad9112e17233abfe8a631793',
    DEST / 'data/revenue_202609.csv': '06c6ca14e1159dbe8fc3d56a8dfd31b24247a178ac6526a3359f962069ab6c35',
    DEST.parent / 'recent3_core_satellite/PREREGISTRATION.md': 'c51f0e6a8295a97598b374899e04bba9fb7654ef5a130bc4703a61254e4348b8',
}
NAMES = ['Mom20', 'C1', 'C2', 'C3', 'C4', 'S1', 'S2']
CANDIDATES = ['S1', 'S2', 'C1', 'C2', 'C3', 'C4']
PROGRESS = []


def check_frozen():
    for path, expected in FROZEN.items():
        assert digest(path) == expected, f'Frozen hash mismatch: {path}'


def load_strategies():
    spec = importlib.util.spec_from_file_location('recent3_frozen_runner', DEST.parent / 'recent3_core_satellite/run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.path.insert(0, str(DEST))
    from strategies import RevenueMomentum, CrashGuard
    return module.CoreStrategy, module.ResidualStrategy, RevenueMomentum, CrashGuard
COMMAND = 'PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/recent5_new/run.py'
INTERPRETATIONS = [
    'Closest non-passers means fewest failed gates; list all ties. This is descriptive and introduces no selection gate.',
    'S1 logs signal eligibility and release age on each decision; S2 logs daily state and slot count; other strategy logs are empty unless exposed by the frozen strategy.',
    'C1/C2 exclude 2330 before taking 24/9 large-cap names. C3 takes the overall top50 first, excludes the separately allocated 2330, then selects 24 by sample std of 60 action-neutral daily simple returns; ties break by symbol.',
    'C1/C3 cap-minus-0.5pp is a D-1 repair trigger, not a lower target cap. Last-three-session freeze takes precedence over repair.',
    'C2 keeps actually held non-core names ranked <=21, fills to 15 by score, and uses portfolio one-way turnover (0.5 times sum absolute stock-weight changes).',
    'G0 compares warnings separately in all five windows. Tie-break: passing candidates within strictly 0.5pp of lowest mean drawdown, then highest mean delta vs 0050.',
    'check(actual trade_date) excludes ETFs dated in the future, leaving by_etf empty before 2026-09-24. For those rows only, compute every ETF via the same production.active_share.active_share formula using the mandated fixed snapshot. Preserve actual-date API status and invalid-data list; all AS remains heuristic/UNVERIFIED. No threshold or snapshot is changed.',
    'nav_tradable is canonical ledger.nav (cash plus holdings, excluding receivables); crash daily returns include initial-capital predecessor and terminal dividend credit, consistent with episode terminal return.'
]


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + '\n')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def status():
    return subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)


def main():
    started = time.perf_counter()
    check_frozen()
    CoreStrategy, ResidualStrategy, RevenueMomentum, CrashGuard = load_strategies()
    assert Path(competition.__file__).resolve().parent == ROOT / 'competition'
    assert Path(research.__file__).resolve().parent == ROOT / 'research'
    before_status = status()
    assert len(before_status.splitlines()) == 34, 'Unexpected starting working-tree status'
    inputs = [*FROZEN, DEST / 'TASK_BRIEF.md', DEST.parent / 'recent3_core_satellite/run.py',
              DEST.parent / 'recent3_core_satellite/results.csv', ROOT / 'production/strategy.json',
              ROOT / 'data/reference/competition_rules.json', ROOT / 'data/momv2/etf_top10_20260924.csv']
    inputs += [p for folder in ['production', 'competition', 'research'] for p in (ROOT / folder).glob('*.py')]
    inputs += [ROOT / 'legacy/ml/momv2/signals.py', ROOT / 'research/results/recent24_strategies/comparison.json']
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
    etfs = active_share.load_etf_holdings(ROOT / 'data/momv2/etf_top10_20260924.csv')
    required = sorted(etfs)
    assert required
    rows, as_rows, replay = [], [], {}
    consistency = {}
    # Replay all fifteen old episodes before any new candidate/window.
    jobs = [(s, w) for w in ['W3', 'W4', 'W5'] for s in NAMES[:5]]
    jobs += [(s, w) for w in windows for s in NAMES if (s, w) not in jobs]
    for name, window in jobs:
        ep = windows[window]
        strategy = (build_strategy(base, rules) if name == 'Mom20' else
                    ResidualStrategy(base, rules) if name == 'C4' else
                    RevenueMomentum(market.calendar, rules, base['params']['portfolio']) if name == 'S1' else
                    CrashGuard(rules) if name == 'S2' else CoreStrategy(name, rules))
        result = backtest.run_episode(market, ep, strategy, rules, engine.policy_of(base),
                                      backtest.ExecutionConfig(**base['execution']))
        problems = backtest.verify_episode(market, ep, result, rules)
        summary = result['summary']
        PROGRESS.append(dict(strategy=name, window=window, complete=summary['complete'],
                             verification_problem_count=len(problems), verification_problems=problems))
        if window in ['W3', 'W4', 'W5'] and name in NAMES[:5]:
            old = {'W3': 'W1', 'W4': 'W2', 'W5': 'W3'}[window]
            ref = reference[(reference.strategy == name) & (reference.window == old)]
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
    baseline_mean_dd = float(np.mean([by_key['Mom20', w]['max_drawdown'] for w in windows]))
    baseline_worst = min(by_key['Mom20', w]['terminal_return'] for w in windows)
    gates, new_only = {}, {}
    for name in CANDIDATES:
        candidate = [by_key[name, w] for w in windows]
        mean_delta50 = float(np.mean([r['delta_vs_0050'] for r in candidate]))
        mean_delta_m = float(np.mean([r['delta_vs_mom20'] for r in candidate]))
        wins50 = sum(r['delta_vs_0050'] > 0 for r in candidate)
        wins_m = sum(r['delta_vs_mom20'] > 0 for r in candidate)
        mean_dd = float(np.mean([r['max_drawdown'] for r in candidate]))
        worst = min(r['terminal_return'] for r in candidate)
        w5 = by_key[name, 'W5']
        g0_checks = {w: dict(complete=by_key[name, w]['complete'], verify_problems=by_key[name, w]['verification_problem_count'],
                             disqualified=by_key[name, w]['disqualified'], warning_days=by_key[name, w]['warning_days'],
                             mom20_warning_days=by_key['Mom20', w]['warning_days']) for w in windows}
        g0 = len(candidate) == 5 and all(x['complete'] and x['verify_problems'] == 0 and not x['disqualified']
                 and x['warning_days'] <= x['mom20_warning_days'] for x in g0_checks.values())
        gates[name] = dict(
            G0=dict(passed=g0, windows=g0_checks),
            G1=dict(passed=mean_delta50 > 0 and wins50 >= 3, mean_delta_vs_0050=mean_delta50,
                    minimum_mean_delta_exclusive=0, wins=wins50, required_wins=3),
            G2=dict(passed=mean_delta_m >= .01 and wins_m >= 3, mean_delta_vs_mom20=mean_delta_m,
                    minimum_mean_delta=.01, wins=wins_m, required_wins=3),
            G3=dict(passed=mean_dd <= baseline_mean_dd and worst >= baseline_worst,
                    mean_max_drawdown=mean_dd, mom20_mean_max_drawdown=baseline_mean_dd,
                    worst_terminal_return=worst, mom20_worst_terminal_return=baseline_worst),
            G4=dict(passed=w5['as_rows'] == 24 and w5['min_as'] >= .15, min_as=w5['min_as'], threshold=.15,
                    days_below_15pct=w5['as_days_below_15pct'], rows=w5['as_rows'], status='heuristic/UNVERIFIED'))
        gates[name]['failing_gates'] = [g for g in ['G0', 'G1', 'G2', 'G3', 'G4'] if not gates[name][g]['passed']]
        gates[name]['all_passed'] = not gates[name]['failing_gates']
    for name in NAMES:
        new_only[name] = dict(
            windows={w: {k: by_key[name, w][k] for k in ['delta_vs_0050', 'delta_vs_mom20']} for w in ['W1', 'W2']},
            mean_delta_vs_0050=float(np.mean([by_key[name, w]['delta_vs_0050'] for w in ['W1', 'W2']])),
            mean_delta_vs_mom20=float(np.mean([by_key[name, w]['delta_vs_mom20'] for w in ['W1', 'W2']])))
    passed = [s for s in gates if gates[s]['all_passed']]
    selected = None
    if passed:
        best_dd = min(gates[s]['G3']['mean_max_drawdown'] for s in passed)
        tied = [s for s in passed if gates[s]['G3']['mean_max_drawdown'] - best_dd < .005]
        selected = max(tied, key=lambda s: gates[s]['G1']['mean_delta_vs_0050'])
    closest = [] if passed else [s for s in gates if len(gates[s]['failing_gates']) == min(len(g['failing_gates']) for g in gates.values())]
    decision = dict(candidates=gates, passing_candidates=passed, selected_candidate=selected,
                    closest_candidates=closest, closest_definition='Fewest failing gates; ties all listed (descriptive only).',
                    new_windows_descriptive=new_only,
                    final_verdict='可替代候選（由使用者決定是否換）' if passed else '維持 Mom20',
                    automatic_strategy_change=False)
    assert len(rows) == 35 and sum(r['complete'] for r in rows) == 35
    assert len(as_rows) == 7 * 120 and all(r['as_rows'] == 24 for r in rows)
    assert all(np.isfinite(v) for r in rows for v in r.values() if isinstance(v, (int, float)))
    assert all(np.isfinite(r['min_as']) for r in as_rows)
    check_frozen()
    assert all(digest(ROOT / p) == h for p, h in frozen.items())
    assert all(digest(ROOT / p) == h for p, h in market.provenance.items())
    assert status() == before_status, 'Working-tree status changed outside existing untracked result directory'
    rows.sort(key=lambda r: (r['window'], r['strategy']))
    verification = {f'{r["strategy"]}_{r["window"]}': r['verification_problems'] for r in rows}
    checks = dict(episodes_expected=35, episodes_processed=len(rows), episodes_complete=sum(r['complete'] for r in rows),
                  verification_calls=len(verification), verification_problems=sum(map(len, verification.values())),
                  as_expected=840, as_processed=len(as_rows), as_formula_fallback_rows=sum(r['snapshot_formula_fallback'] for r in as_rows),
                  consistency=consistency, finite_results=True, protected_input_hashes_unchanged=True,
                  git_status_unchanged=True, baseline_git_status_lines=len(before_status.splitlines()))
    payload = dict(status='COMPLETE', results=rows, crash_exposure=crash, new_windows_descriptive=new_only, checks=checks,
                   windows={w: dict(start=str(e.start.date()), end=str(e.end.date()), prior_session=str(e.prior_session.date())) for w, e in windows.items()},
                   interpretations=INTERPRETATIONS, input_sha256={**frozen, **market.provenance}, command=COMMAND,
                   elapsed_seconds=time.perf_counter() - started,
                   limitations=['Five recent windows only; W3-W5 observed before design; survivorship-biased universe; revenue availability assumed on day 11.',
                                'Active Share uses incomplete fixed top10 snapshot; historical dates mismatched; no official certification.'])
    dump(DEST / 'results.json', payload)
    flat = [{k: json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v for k, v in r.items()} for r in rows]
    pd.DataFrame(flat).to_csv(DEST / 'results.csv', index=False)
    pd.DataFrame(as_rows).to_csv(DEST / 'active_share.csv', index=False)
    dump(DEST / 'gates.json', decision)
    report = ['# CODEX_REPORT', '',
        '- Status: COMPLETE; ' + decision['final_verdict'] + (f'; selected {selected}.' if selected else '.'),
        '- Changed files: run.py, results.csv, results.json, gates.json, active_share.csv, CODEX_REPORT.md; 35 episode directories with ledger/trades/holdings/issues.csv and strategy_log.json. Only allowed paths written.',
        f'- Tests run and results: 35/35 verify_episode calls; {checks["verification_problems"]} problems; 15/15 prior-result consistency checks and Mom20 W5 fixed-value check PASS; dates PASS; 840/840 AS rows; finite results PASS; start/end frozen hashes PASS; input/provenance hashes and 34-line Git status unchanged.',
        f'- Items expected/processed/failed: episodes 35/{len(rows)}/{sum(not r["complete"] for r in rows)}; verification 35/{len(verification)}/{sum(bool(v) for v in verification.values())}; AS 840/{len(as_rows)}/0.',
        '- Acceptance criteria: all execution/output checks PASS. Candidate gate failures are experimental outcomes.',
        '- Execution: `' + COMMAND + '`; main directory; elapsed ' + f'{payload["elapsed_seconds"]:.2f}s; no Git writes.',
        '- Quota: unavailable; one fixed run, no search or sweeps.', '- Blockers: none.', '',
        '| Candidate | G0 | G1 | G2 | G3 | G4 heuristic | Mean Δ50 | Mean ΔM | Mean MDD |',
        '|---|---|---|---|---|---|---|---|---|']
    for name, g in gates.items():
        report.append('| ' + name + ' | ' + ' | '.join('PASS' if g[x]['passed'] else 'FAIL' for x in ['G0','G1','G2','G3','G4']) +
                      f' | {g["G1"]["mean_delta_vs_0050"]:.4%} | {g["G2"]["mean_delta_vs_mom20"]:.4%} | {g["G3"]["mean_max_drawdown"]:.4%} |')
    report += ['', '## Failing gates (numeric evidence)', '']
    for name, g in gates.items():
        report.append('- ' + name + ': ' + ('; '.join(k + ' ' + json.dumps(g[k], sort_keys=True) for k in g['failing_gates']) or 'none'))
    report += ['', '- Closest: ' + (', '.join(closest) + ' (fewest failed gates; descriptive only).' if closest else 'not applicable; a candidate passed all gates.'), '',
               '## New W1-W2 only (descriptive)', '', '| Strategy | Mean Δ50 | Mean ΔM |', '|---|---|---|']
    for name, values in new_only.items():
        report.append(f'| {name} | {values["mean_delta_vs_0050"]:.4%} | {values["mean_delta_vs_mom20"]:.4%} |')
    report += ['', '## Crash: Mom20 W5 worst five sessions', '', '- Dates: ' + ', '.join(crash_dates), '', '| Strategy | Compounded return |', '|---|---|']
    for r in crash:
        report.append(f'| {r["strategy"]} | {r["compounded_return"]:.4%} |')
    report += ['', '## Plan deviations / interpretations', ''] + ['- ' + x for x in INTERPRETATIONS]
    report += ['', f'- AS fixed-snapshot fallback: {checks["as_formula_fallback_rows"]}/840 rows; all AS heuristic/UNVERIFIED.',
               '- No strategy logic, parameters, scope, architecture, or gates changed. No production change.',
               '', '## Verification problem counts (each call)', '']
    report += ['- ' + k + ': ' + str(len(v)) + (' ' + json.dumps(v) if v else '') for k, v in verification.items()]
    (DEST / 'CODEX_REPORT.md').write_text('\n'.join(report) + '\n')
    print(json.dumps(checks, indent=2))
    print(decision['final_verdict'], selected)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        frozen_ok = {str(p.relative_to(ROOT)): digest(p) == h for p, h in FROZEN.items()}
        report = ["# CODEX_REPORT", "", "- Status: STOPPED", "- Changed files: run.py and partial outputs in allowed directory only.",
                  "- Tests run and results: stopped; see traceback.",
                  f"- Items expected/processed/failed: 35 expected; {len(PROGRESS)} processed; remaining incomplete.",
                  "- Acceptance criteria: not met.", "- Plan deviations / interpretations: no strategy patch or gate changes.",
                  "- Execution: `" + COMMAND + "`", "- Quota: unavailable.", "- Blockers: " + repr(exc),
                  "- Frozen hash checks at stop: " + json.dumps(frozen_ok),
                  "", "| Gate | Result |", "|---|---|", "| G0-G4 | Not evaluated: stopped |", "",
                  "## Processed episodes", json.dumps(PROGRESS, indent=2), "", "## Traceback", "```", traceback.format_exc(), "```", ""]
        (DEST / 'CODEX_REPORT.md').write_text('\n'.join(report))
        raise
