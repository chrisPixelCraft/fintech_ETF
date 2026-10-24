"""Frozen recent-three-window comparison; writes only beside this script."""
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json
import math
import subprocess
import sys
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
EXPECTED_HASH = 'c51f0e6a8295a97598b374899e04bba9fb7654ef5a130bc4703a61254e4348b8'
COMMAND = 'PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/recent3_core_satellite/run.py'
INTERPRETATIONS = [
    'C1/C2 exclude 2330 before taking 24/9 large-cap names. C3 takes the overall top50 first, excludes the separately allocated 2330, then selects 24 by sample std of 60 action-neutral daily simple returns; ties break by symbol.',
    'C1/C3 cap-minus-0.5pp is a D-1 repair trigger, not a lower target cap. Last-three-session freeze takes precedence over repair.',
    'C2 keeps actually held non-core names ranked <=21, fills to 15 by score, and uses portfolio one-way turnover (0.5 times sum absolute stock-weight changes).',
    'G0 warning comparison is required separately in every window. Tie-break considers passing candidates within strictly 0.5pp of the lowest mean drawdown, then highest mean delta.',
    'check(actual trade_date) excludes ETFs dated in the future, leaving by_etf empty before 2026-09-24. For those rows only, compute every ETF via the same production.active_share.active_share formula using the mandated fixed snapshot. Preserve actual-date API status and invalid-data list; all AS remains heuristic/UNVERIFIED. No threshold or snapshot is changed.',
    'nav_tradable is canonical ledger.nav (cash plus holdings, excluding receivables); crash daily returns include initial-capital predecessor and terminal dividend credit, consistent with episode terminal return.'
]


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False, default=str) + '\n')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def status():
    return subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)


class ResidualStrategy:
    def __init__(self, base, rules):
        self.c = SignalConfig(kind='h1', resid_window=120, beta_window=250, benchmark='0050')
        self.p = portfolio.PortfolioConfig.from_dict(base['params']['portfolio'])
        self.rules = rules

    def decide(self, view, state):
        scores, _ = score(view, self.c)
        return portfolio.target_weights(scores, state.weights, state.sessions_remaining, self.rules, self.p)


class CoreStrategy:
    def __init__(self, kind, rules):
        self.kind, self.rules = kind, rules
        self.original = None
        self.core = None
        self.log = []

    def large(self, view):
        valid = view.valid.iloc[-60:]
        assert len(valid) == 60
        names = valid.columns[valid.all()]
        values = (view.close[names] * view.volume[names]).iloc[-60:]
        names = values.columns[np.isfinite(values).all() & values.gt(0).all()]
        return portfolio.ranked(values[names].mean())

    def targets(self, desired):
        assert self.rules.min_positions <= len(desired) <= self.rules.max_positions
        caps = pd.Series({s: self.rules.cap(s) for s in desired})
        weights = portfolio.cap_fill(pd.Series(desired), .95, caps)
        assert abs(weights.sum() - .95) < 1e-12
        assert all(weights[s] <= self.rules.cap(s) + 1e-12 for s in weights.index)
        return weights.to_dict()

    def decide(self, view, state):
        assert view.date == state.asof < state.date
        if self.original is None:
            all_large = self.large(view)
            large = [s for s in all_large if s != '2330.TW']
            assert '2330.TW' in view.symbols
            if self.kind == 'C2':
                self.core = {'2330.TW': .235, **{s: .035 for s in large[:9]}}
            else:
                chosen = large[:24]
                if self.kind == 'C3':
                    pool = [s for s in all_large[:50] if s != '2330.TW']
                    vol = view.ret[pool].iloc[-60:].std(ddof=1)
                    vol = vol.where(view.ret[pool].iloc[-60:].notna().all())
                    chosen = portfolio.ranked(-vol)[:24]
                assert len(chosen) == 24
                self.original = self.targets({'2330.TW': .235, **{s: .715 / 24 for s in chosen}})
        if self.kind != 'C2':
            if state.day_index == 0:
                return self.original.copy()
            if state.sessions_remaining <= 3:
                return dict(state.weights)
            breach = (any(w > self.rules.cap(s) - .005 for s, w in state.weights.items())
                      or state.cash < self.rules.cash_min
                      or state.cash / state.nav >= self.rules.cash_max_exclusive)
            if breach:
                self.log.append({'date': str(state.date.date()), 'action': 'repair_to_original'})
            return self.original.copy() if breach else dict(state.weights)
        if state.sessions_remaining <= 3 and state.weights:
            return dict(state.weights)
        scores = momentum_score(view, MomentumConfig(window=20)).drop(labels=list(self.core), errors='ignore')
        held = [s for s, w in state.weights.items() if w > 0 and s not in self.core]
        chosen = portfolio.select(scores, held, SimpleNamespace(n_holdings=15, keep_rank=21))
        assert len(chosen) == 15
        target = self.targets({**self.core, **{s: .40 / 15 for s in chosen}})
        if self.original is None:
            self.original = target.copy()
        turnover = .5 * sum(abs(target.get(s, 0.) - state.weights.get(s, 0.))
                            for s in set(target) | set(state.weights))
        return dict(state.weights) if state.weights and turnover < .10 else target


def main():
    started = time.perf_counter()
    assert digest(PRE) == EXPECTED_HASH
    assert Path(competition.__file__).resolve().parent == ROOT / 'competition'
    assert Path(research.__file__).resolve().parent == ROOT / 'research'
    before_status = status()
    assert len(before_status.splitlines()) == 33, 'Unexpected starting working-tree status'
    inputs = [PRE, DEST / 'TASK_BRIEF.md', ROOT / 'production/strategy.json',
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
    for i, name in enumerate(['W1', 'W2', 'W3']):
        days = tuple(market.calendar[end - 72 + i * 24:end - 48 + i * 24])
        assert len(days) == 24
        windows[name] = Episode(name, 'recent_diagnostic', market.calendar[market.position(days[0]) - 1], days)
    assert windows['W3'].sessions[0] == pd.Timestamp('2026-09-16')
    comparison = json.loads((ROOT / 'research/results/recent24_strategies/comparison.json').read_text())
    residual_reference = next(r['net_return'] for r in comparison['results'] if r['method'] == 'residual120_h1')
    etfs = active_share.load_etf_holdings(ROOT / 'data/momv2/etf_top10_20260924.csv')
    required = sorted(etfs)
    assert required
    rows, as_rows, replay = [], [], {}
    consistency = {}
    # Execute both consistency episodes first; stop immediately on a mismatch.
    jobs = [('Mom20', 'W3'), ('C4', 'W3')]
    jobs += [(s, w) for w in windows for s in ['Mom20', 'C1', 'C2', 'C3', 'C4'] if (s, w) not in jobs]
    for name, window in jobs:
        ep = windows[window]
        strategy = (build_strategy(base, rules) if name == 'Mom20' else
                    ResidualStrategy(base, rules) if name == 'C4' else CoreStrategy(name, rules))
        result = backtest.run_episode(market, ep, strategy, rules, engine.policy_of(base),
                                      backtest.ExecutionConfig(**base['execution']))
        problems = backtest.verify_episode(market, ep, result, rules)
        summary = result['summary']
        if window == 'W3' and name in ['Mom20', 'C4']:
            expected, tolerance = (-.10124596758199833, 1e-12) if name == 'Mom20' else (residual_reference, 1e-9)
            actual = summary['terminal_return']
            consistency[name] = dict(actual=actual, expected=expected, tolerance=tolerance, passed=abs(actual - expected) <= tolerance)
            assert consistency[name]['passed'], (name, consistency[name])
        dest = DEST / 'episodes' / f'{name}_{window}'
        dest.mkdir(parents=True, exist_ok=True)
        for key in ['ledger', 'trades', 'holdings', 'issues']:
            result[key].to_csv(dest / f'{key}.csv', index=False)
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
                   as_use='gate' if window == 'W3' else 'descriptive')
        rows.append(row)
        print(name, window, summary['terminal_return'], 'verify', len(problems), flush=True)
    by_key = {(r['strategy'], r['window']): r for r in rows}
    for row in rows:
        row['delta_vs_mom20'] = row['terminal_return'] - by_key['Mom20', row['window']]['terminal_return']
        row['delta_vs_0050'] = row['terminal_return'] - row['benchmark_0050_return']
    daily = {}
    for name in ['Mom20', 'C1', 'C2', 'C3', 'C4']:
        res = replay[name, 'W3']
        nav = res['ledger'].nav.to_numpy(float).copy()
        nav[-1] += res['summary']['dividend_credit']
        daily[name] = pd.Series(nav / np.r_[rules.initial_capital, nav[:-1]] - 1,
                                index=res['ledger'].date)
    crash_dates = list(daily['Mom20'].sort_values(kind='stable').index[:5])
    crash = [dict(strategy=s, dates=crash_dates, compounded_return=float((1 + series.loc[crash_dates]).prod() - 1),
                  daily_returns={d: float(series[d]) for d in crash_dates}) for s, series in daily.items()]
    for row in rows:
        exposure = next(r for r in crash if r['strategy'] == row['strategy'])
        row['crash_w3_compounded_return'] = exposure['compounded_return']
        row['crash_w3_dates'] = exposure['dates']
        row['crash_w3_daily_returns'] = exposure['daily_returns']
    baseline_mean_dd = float(np.mean([by_key['Mom20', w]['max_drawdown'] for w in windows]))
    gates = {}
    for name in ['C1', 'C2', 'C3', 'C4']:
        candidate = [by_key[name, w] for w in windows]
        mean_delta = float(np.mean([r['delta_vs_mom20'] for r in candidate]))
        wins = sum(r['delta_vs_mom20'] > 0 for r in candidate)
        mean_dd = float(np.mean([r['max_drawdown'] for r in candidate]))
        w3 = by_key[name, 'W3']
        g0_checks = {w: dict(complete=by_key[name, w]['complete'], verify_problems=by_key[name, w]['verification_problem_count'],
                             disqualified=by_key[name, w]['disqualified'], warning_days=by_key[name, w]['warning_days'],
                             mom20_warning_days=by_key['Mom20', w]['warning_days']) for w in windows}
        g0 = all(x['complete'] and x['verify_problems'] == 0 and not x['disqualified']
                 and x['warning_days'] <= x['mom20_warning_days'] for x in g0_checks.values())
        dd_limit = by_key['Mom20', 'W3']['max_drawdown'] - .02
        gates[name] = dict(G0=dict(passed=g0, windows=g0_checks),
                           G1=dict(passed=mean_delta >= .01 and wins >= 2, mean_delta=mean_delta, minimum_mean_delta=.01, wins=wins, required_wins=2),
                           G2=dict(passed=w3['max_drawdown'] <= dd_limit and mean_dd <= baseline_mean_dd,
                                   w3_max_drawdown=w3['max_drawdown'], w3_limit=dd_limit,
                                   mean_max_drawdown=mean_dd, mom20_mean_max_drawdown=baseline_mean_dd),
                           G3=dict(passed=w3['as_rows'] == 24 and w3['min_as'] >= .15, min_as=w3['min_as'], threshold=.15,
                                   days_below_15pct=w3['as_days_below_15pct'], rows=w3['as_rows'], status='heuristic/UNVERIFIED'))
        gates[name]['all_passed'] = all(gates[name][g]['passed'] for g in ['G0', 'G1', 'G2', 'G3'])
    passed = [s for s in gates if gates[s]['all_passed']]
    selected = None
    if passed:
        best_dd = min(gates[s]['G2']['mean_max_drawdown'] for s in passed)
        tied = [s for s in passed if gates[s]['G2']['mean_max_drawdown'] - best_dd < .005]
        selected = max(tied, key=lambda s: gates[s]['G1']['mean_delta'])
    decision = dict(candidates=gates, passing_candidates=passed, selected_candidate=selected,
                    final_verdict='可替代候選（由使用者決定是否換）' if passed else '維持 Mom20',
                    automatic_strategy_change=False)
    assert len(rows) == 15 and sum(r['complete'] for r in rows) == 15
    assert len(as_rows) == 5 * 72 and all(r['as_rows'] == 24 for r in rows)
    assert all(np.isfinite(v) for r in rows for v in r.values() if isinstance(v, (int, float)))
    assert all(np.isfinite(r['min_as']) for r in as_rows)
    assert all(digest(ROOT / p) == h for p, h in frozen.items())
    assert all(digest(ROOT / p) == h for p, h in market.provenance.items())
    assert status() == before_status, 'Working-tree status changed outside existing untracked result directory'
    rows.sort(key=lambda r: (r['window'], r['strategy']))
    verification = {f'{r["strategy"]}_{r["window"]}': r['verification_problems'] for r in rows}
    checks = dict(episodes_expected=15, episodes_processed=len(rows), episodes_complete=sum(r['complete'] for r in rows),
                  verification_calls=len(verification), verification_problems=sum(map(len, verification.values())),
                  as_expected=360, as_processed=len(as_rows), as_formula_fallback_rows=sum(r['snapshot_formula_fallback'] for r in as_rows),
                  consistency=consistency, finite_results=True, protected_input_hashes_unchanged=True,
                  git_status_unchanged=True, baseline_git_status_lines=len(before_status.splitlines()))
    payload = dict(status='COMPLETE', results=rows, crash_exposure=crash, checks=checks,
                   windows={w: dict(start=str(e.start.date()), end=str(e.end.date()), prior_session=str(e.prior_session.date())) for w, e in windows.items()},
                   interpretations=INTERPRETATIONS, input_sha256={**frozen, **market.provenance}, command=COMMAND,
                   elapsed_seconds=time.perf_counter() - started,
                   limitations=['Three recent windows only; W3 observed before design; survivorship-biased universe.',
                                'Active Share uses incomplete fixed top10 snapshot; historical dates mismatched; no official certification.'])
    dump(DEST / 'results.json', payload)
    flat = [{k: json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v for k, v in r.items()} for r in rows]
    pd.DataFrame(flat).to_csv(DEST / 'results.csv', index=False)
    pd.DataFrame(as_rows).to_csv(DEST / 'active_share.csv', index=False)
    dump(DEST / 'gates.json', decision)
    report = ['# CODEX_REPORT', '', '- Status: COMPLETE; ' + decision['final_verdict'] + (f'; selected {selected}.' if selected else '.'),
              '- Changed files: run.py, results.json, results.csv, gates.json, active_share.csv, CODEX_REPORT.md; 15 episodes × {ledger,trades,holdings,issues}.csv. All under research/results/recent3_core_satellite/.',
              '- Tests run and results: exact command below; both W3 consistency asserts PASS; 15/15 verify_episode calls, ' + str(checks['verification_problems']) + ' problems; 360/360 AS rows; no NaN/Inf in results; protected input hashes and 33-line git status unchanged.',
              '- Items expected/processed/failed: episodes 15/15/0; verification calls 15/15/' + str(sum(bool(v) for v in verification.values())) + '; AS rows 360/360/0.',
              '- Acceptance criteria: all execution/output checks PASS. Candidate gate failures are experimental results, not execution failures.',
              '- Execution: `' + COMMAND + '`; elapsed ' + f'{payload["elapsed_seconds"]:.2f}s; main directory; no git writes.',
              '- Quota: not available; no sweeps or parameter search.', '- Blockers: none.', '', '## Frozen decision', '',
              '| Candidate | G0 | G1 | G2 | G3 heuristic | Mean Δ | Mean MDD |', '|---|---|---|---|---|---|---|']
    for s, g in gates.items():
        report.append('| ' + s + ' | ' + ' | '.join('PASS' if g[x]['passed'] else 'FAIL' for x in ['G0', 'G1', 'G2', 'G3']) + f' | {g["G1"]["mean_delta"]:.4%} | {g["G2"]["mean_max_drawdown"]:.4%} |')
    report += ['', '## Plan deviations / interpretations', ''] + ['- ' + x for x in INTERPRETATIONS]
    report += ['', f'- AS actual-date formula fallback: {checks["as_formula_fallback_rows"]}/360 rows; all 360 statuses UNVERIFIED.',
               '- No scope, architecture, candidates, parameters, or gates changed. Results are descriptive; no production strategy changed.',
               '', '## Verification problems', '', json.dumps(verification, sort_keys=True), '']
    (DEST / 'CODEX_REPORT.md').write_text('\n'.join(report))
    print(json.dumps(checks, indent=2))
    print(decision['final_verdict'], selected)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        (DEST / 'CODEX_REPORT.md').write_text('# CODEX_REPORT\n\n- Status: STOPPED\n- Changed files: run.py and any partial outputs under allowed directory.\n- Tests run and results: execution stopped at ' + repr(exc) + '\n- Items expected/processed/failed: 15 episodes expected; incomplete.\n- Acceptance criteria: not met.\n- Plan deviations: no gates changed.\n- Execution: `' + COMMAND + '`\n- Quota: unavailable.\n- Blockers: ' + repr(exc) + '\n')
        raise
