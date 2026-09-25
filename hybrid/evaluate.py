"""The one Hybrid evaluation (docs/hybrid_spec.md section 7): Mom20 vs Hybrid v2A / v2B on 2015-2024.

    PYTHONHASHSEED=0 .venv/bin/python -m hybrid.evaluate --workers 10
    PYTHONHASHSEED=0 .venv/bin/python -m hybrid.evaluate --pure-test --workers 10   # only after PASS

Every run uses market data from 2014-01-01 and the production portfolio
layer on the same windows (dev from 2015 plus validation, month start and
mid-month), paired per window with Mom20. The acceptance rule and the version
choice are fixed in the spec; the verdict is written once and a second
evaluation with a different verdict is refused. Panic-window diagnostics are
reported, never used. Results: research/results/hybrid/.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd

from competition.rules import ROOT
from hybrid.walkforward import DATA_START
from research import compare, run_experiment
from research.lgbm_alpha import write_csv
from research.lgbm_jpx2 import episode_rows, metrics, pct

RUNS = ROOT / 'research/runs/hybrid'
RESULTS = ROOT / 'research/results/hybrid'
VERDICT = RESULTS / 'verdict.json'
PORTFOLIO = json.loads((ROOT / 'production/strategy.json').read_text())['params']['portfolio']
EPISODES = {'start': '2015-01-01', 'offsets': ['month_start', 'mid_month']}
SPLITS = {'eval': ('dev', 'validation'), 'test': ('holdout',)}
REFERENCE, VERSIONS = 'mom20', ('hybrid_v2A', 'hybrid_v2B')
GATE = dict(min_mean_delta=.005, min_median_delta=0.)


def config(name: str, period: str) -> dict:
    base = json.loads((ROOT / 'production/strategy.json').read_text())
    common = dict(execution=base['execution'], planner=base['planner'], episodes=EPISODES, data={'start': DATA_START})
    if name == REFERENCE:
        return dict(common, name=f'hybrid_eval_{name}', strategy='momentum', params=dict(window=20, portfolio=PORTFOLIO))
    variant = name.split('_')[1]
    return dict(common, name=f'hybrid_eval_{name}', strategy='hybrid',
                params=dict(window=20, portfolio=PORTFOLIO,
                            predictions=f'data/hybrid/predictions_{variant}_{period}.parquet'))


def run_dir(name: str, split: str) -> Path:
    return RUNS / f'{name}__{split}'


def run(name: str, split: str, period: str, workers: int) -> dict:
    path = RUNS / 'configs' / f'{name}_{period}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config(name, period), indent=1))
    argv = ['--config', str(path), '--split', split, '--episodes', 'all', '--workers', str(workers),
            '--out', str(run_dir(name, split))] + (['--i-understand-holdout'] if split == 'holdout' else [])
    with contextlib.redirect_stdout(io.StringIO()):
        manifest = run_experiment.main(argv)
    print(f'{split} {name}: {manifest["status"]} {manifest["n_complete"]}/{len(manifest["episode_ids"])}', flush=True)
    return manifest


def rows(name: str, splits) -> dict:
    out = {}
    for split in splits:
        out.update(episode_rows(run_dir(name, split)))
    return out


def paired_ci(a: dict, b: dict, n_boot: int = 10000) -> list:
    common = sorted(set(a) & set(b))
    d = np.array([a[k]['terminal_return'] - b[k]['terminal_return'] for k in common])
    boot = d[np.random.default_rng(0).integers(0, len(d), size=(n_boot, len(d)))].mean(axis=1)
    return [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))]


def panic_diagnostics(name: str, splits, reference: dict) -> dict:
    """Windows with at least one panic day: Hybrid minus Mom20 there (report only)."""
    panic_windows, days = set(), 0
    for split in splits:
        for log in run_dir(name, split).glob('episodes/*/strategy_log.json'):
            entries = json.loads(log.read_text())
            hits = sum(e['panic'] for e in entries)
            days += hits
            if hits:
                panic_windows.add(log.parent.name)
    mine = rows(name, splits)
    d = [mine[k]['terminal_return'] - reference[k]['terminal_return'] for k in panic_windows if k in mine and k in reference]
    return dict(panic_days=days, panic_windows=len(panic_windows),
                mean_delta_in_panic_windows=float(np.mean(d)) if d else None,
                median_delta_in_panic_windows=float(np.median(d)) if d else None)


def accept(m: dict, ref: dict) -> dict:
    checks = {
        'mean_delta > +0.5%': m['paired_mean_delta'] > GATE['min_mean_delta'],
        'median_delta >= 0': m['paired_median_delta'] >= GATE['min_median_delta'],
        'no extra disqualification': m['disqualified'] <= ref['disqualified'],
        'extra cost < mean_delta': m['cost_pct'] - ref['cost_pct'] < m['paired_mean_delta'],
    }
    return dict(checks=checks, passed=all(checks.values()))


def choose(results: dict) -> str | None:
    passed = [v for v in VERSIONS if results[v]['acceptance']['passed']]
    return max(passed, key=lambda v: results[v]['metrics']['paired_mean_delta']) if passed else None


def summary_md(verdict: dict) -> str:
    lines = ['# Hybrid 評估：2015–2024（只跑一次）', '',
             f'- 窗口：{verdict["windows"]} 個 24 日窗口（月初＋月中），資料自 {DATA_START} 起',
             '- 規則寫死於 [docs/hybrid_spec.md](../../../docs/hybrid_spec.md)', '',
             f'**{verdict["decision"]}**', '',
             '| 策略 | 平均 | 中位數 | 配對平均 Δ | 配對中位數 Δ | 95% CI | 勝率 | P10 | 最差 | 周轉 | 成本 | 失格 |',
             '|---|' + '---:|' * 11]
    ref = verdict['reference']
    lines.append(f'| Mom20 | {pct(ref["mean"])} | {pct(ref["median"])} | — | — | — | — | {pct(ref["p10"])} | '
                 f'{pct(ref["worst"])} | {ref["turnover"]:.2f} | {ref["cost_pct"]:.2%} | {ref["disqualified"]} |')
    for v in VERSIONS:
        m, ci = verdict['versions'][v]['metrics'], verdict['versions'][v]['ci95']
        lines.append(f'| {v} | {pct(m["mean"])} | {pct(m["median"])} | {pct(m["paired_mean_delta"])} | '
                     f'{pct(m["paired_median_delta"])} | {pct(ci[0])} ~ {pct(ci[1])} | {m["paired_win_rate"]:.0%} | '
                     f'{pct(m["p10"])} | {pct(m["worst"])} | {m["turnover"]:.2f} | {m["cost_pct"]:.2%} | '
                     f'{m["disqualified"]} |')
    lines += ['', '## 通過條件', '']
    for v in VERSIONS:
        checks = verdict['versions'][v]['acceptance']['checks']
        lines.append(f'- {v}：' + '，'.join(f'{"✓" if ok else "✗"} {k}' for k, ok in checks.items()))
    lines += ['', '## Panic 診斷（只報告，不用於任何選擇）', '']
    for v in VERSIONS:
        p = verdict['versions'][v]['panic']
        lines.append(f'- {v}：panic 日 {p["panic_days"]} 天，含 panic 日的窗口 {p["panic_windows"]} 個；這些窗口的 '
                     f'Δ 平均 {pct(p["mean_delta_in_panic_windows"])}、中位數 {pct(p["median_delta_in_panic_windows"])}')
    lines += ['', '- 平常日 Hybrid 就是 Mom20，差異全部來自 panic 日', '']
    return '\n'.join(lines)


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--pure-test', action='store_true', help='2025-2026 test of the chosen version, after PASS')
    args = parser.parse_args(argv)
    RESULTS.mkdir(parents=True, exist_ok=True)
    if args.pure_test:
        stored = json.loads(VERDICT.read_text()) if VERDICT.exists() else {}
        chosen = stored.get('chosen')
        if not chosen:
            raise SystemExit('The pure test runs only after a PASS verdict')
        names, splits, period = (REFERENCE, chosen), SPLITS['test'], 'test'
    else:
        names, splits, period = (REFERENCE, *VERSIONS), SPLITS['eval'], 'eval'
    for name in names:
        for split in splits:
            run(name, split, period, args.workers)
    reference = {k: s for k, s in rows(REFERENCE, splits).items() if s.get('status') == 'COMPLETE'}
    ref_metrics = metrics(rows(REFERENCE, splits), reference)
    versions = {}
    for name in names[1:]:
        mine = rows(name, splits)
        m = metrics(mine, reference)
        versions[name] = dict(metrics=m, ci95=paired_ci({k: s for k, s in mine.items() if s.get('status') == 'COMPLETE'},
                                                       reference),
                              acceptance=accept(m, ref_metrics), panic=panic_diagnostics(name, splits, reference))
    if args.pure_test:
        report = dict(period='2025-2026/09 pure test', chosen=names[1], reference=ref_metrics, versions=versions)
        (RESULTS / 'pure_test.json').write_text(json.dumps(report, indent=1, default=float))
        print(json.dumps({k: v['metrics']['paired_mean_delta'] for k, v in versions.items()}), flush=True)
        return report
    chosen = choose(versions)
    verdict = dict(decision='PASS' if chosen else 'STOP', chosen=chosen, windows=len(reference), reference=ref_metrics,
                   versions=versions)
    if VERDICT.exists() and json.loads(VERDICT.read_text())['decision'] != verdict['decision']:
        raise SystemExit(f'{VERDICT} already holds a different verdict; the evaluation runs once')
    VERDICT.write_text(json.dumps(verdict, indent=1, default=float))
    write_csv(RESULTS / 'comparison.csv', [dict(strategy=REFERENCE, **ref_metrics)] +
              [dict(strategy=v, **x['metrics']) for v, x in versions.items()])
    (RESULTS / 'summary.md').write_text(summary_md(verdict))
    print(verdict['decision'], chosen, flush=True)
    return verdict


if __name__ == '__main__':
    main()
