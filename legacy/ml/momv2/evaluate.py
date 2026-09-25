"""Momentum-v2 evaluation (docs/momentum_v2_spec.md sections 7-10), one stage at a time.

    .venv/bin/python -m momv2.evaluate coverage                       # H2 keep/drop, before any return
    PYTHONHASHSEED=0 .venv/bin/python -m momv2.evaluate dev --workers 8
    #   -> commit research/results/momentum_v2/freeze.json
    PYTHONHASHSEED=0 .venv/bin/python -m momv2.evaluate validation --workers 8
    PYTHONHASHSEED=0 .venv/bin/python -m momv2.evaluate test --workers 8   # only after a PASS

Every run uses market data from 2014-01-01, the production portfolio layer
and month-start plus mid-month windows, paired per window with Mom20. The DEV
pick rule and both gates are fixed here and in the spec; each stage writes its
result once and refuses a rerun that would change it. Results:
research/results/momentum_v2/.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from competition.rules import ROOT
from research import run_experiment
from research.lgbm_alpha import write_csv
from research.lgbm_jpx2 import episode_rows, metrics, pct

RUNS = ROOT / 'research/runs/momentum_v2'
RESULTS = ROOT / 'research/results/momentum_v2'
COVERAGE, FREEZE = RESULTS / 'h2_coverage.json', RESULTS / 'freeze.json'
VERDICT, TEST_VERDICT = RESULTS / 'validation_verdict.json', RESULTS / 'test_verdict.json'
DATA_START = '2014-01-01'
EPISODES = {'start': '2015-01-01', 'offsets': ['month_start', 'mid_month']}
REFERENCE = 'mom20'
DEFAULTS = dict(h1=dict(resid_window=20, beta_window=60), h2=dict(turnover_window=20), h3=dict(revenue_score='yoy_acc'))
GRID = dict(   # grid order = tie-break order (shorter windows, simpler scores first)
    h1=[dict(resid_window=n, beta_window=b) for n in (20, 25, 40, 60) for b in (60, 120)],
    h2=[dict(turnover_window=n) for n in (20, 25, 60)],
    h3=[dict(revenue_score=s) for s in ('yoy', 'yoy_acc', 'yoy_acc_record')])
PICK = dict(min_median_delta=0., max_turnover_ratio=1.5, min_gain_over_default=.002)
GATES = dict(validation=dict(min_mean_delta=.005, min_median_delta=0.),
             test=dict(min_mean_delta=0., min_median_delta=0.))
COVERAGE_MIN = .95
BLOCKS = {'2015–2018': ('2015-01-01', '2018-12-31'), '2019–2021': ('2019-01-01', '2021-12-31')}


def label(component: str, params: dict) -> str:
    return component + ''.join(f'_{k.split("_")[0]}{v}' for k, v in params.items())


def config(name: str, kind: str | None = None, params: dict | None = None) -> dict:
    base = json.loads((ROOT / 'production/strategy.json').read_text())
    portfolio = base['params']['portfolio']
    common = dict(execution=base['execution'], planner=base['planner'], episodes=EPISODES, data={'start': DATA_START})
    if name == REFERENCE:
        return dict(common, name=f'momv2_{name}', strategy='momentum', params=dict(window=20, portfolio=portfolio))
    return dict(common, name=f'momv2_{name}', strategy='momv2', params=dict(kind=kind, **(params or {}),
                                                                            portfolio=portfolio))


def run_dir(name: str, split: str) -> Path:
    return RUNS / f'{name}__{split}'


def run(name: str, cfg: dict, split: str, workers: int) -> dict:
    path = RUNS / 'configs' / f'{name}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=1))
    argv = ['--config', str(path), '--split', split, '--episodes', 'all', '--workers', str(workers),
            '--out', str(run_dir(name, split))] + (['--i-understand-holdout'] if split == 'holdout' else [])
    with contextlib.redirect_stdout(io.StringIO()):
        manifest = run_experiment.main(argv)
    print(f'{split} {name}: {manifest["status"]} {manifest["n_complete"]}/{len(manifest["episode_ids"])}', flush=True)
    if manifest['status'] != 'COMPLETE':
        raise SystemExit(f'{name} on {split} is {manifest["status"]}: failed {manifest["failed"][:5]}')
    return manifest


def complete(name: str, split: str, start=None, end=None) -> dict:
    rows = {k: s for k, s in episode_rows(run_dir(name, split)).items() if s.get('status') == 'COMPLETE'}
    if start:
        rows = {k: s for k, s in rows.items() if pd.Timestamp(start) <= pd.Timestamp(s['start']) <= pd.Timestamp(end)}
    return rows


def paired_ci(a: dict, b: dict, n_boot: int = 10000) -> list:
    common = sorted(set(a) & set(b))
    d = np.array([a[k]['terminal_return'] - b[k]['terminal_return'] for k in common])
    boot = d[np.random.default_rng(0).integers(0, len(d), size=(n_boot, len(d)))].mean(axis=1)
    return [float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))]


def table(names, split: str, start=None, end=None) -> dict:
    reference = complete(REFERENCE, split, start, end)
    out = {}
    for n in names:
        mine = complete(n, split, start, end)
        if set(mine) != set(reference):
            raise SystemExit(f'{n} and {REFERENCE} cover different windows on {split}')
        m = metrics(mine, reference)
        m['ci95'] = None if n == REFERENCE else paired_ci(mine, reference)
        out[n] = m
    return out


def git_state() -> dict:
    run_git = lambda *a: subprocess.run(['git', *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return dict(commit=run_git('rev-parse', 'HEAD'), dirty=bool(run_git('status', '--porcelain')))


def committed(path: Path) -> bool:
    run_git = lambda *a: subprocess.run(['git', *a], cwd=ROOT, capture_output=True, text=True)
    return run_git('ls-files', '--error-unmatch', str(path)).returncode == 0 and \
        not run_git('status', '--porcelain', str(path)).stdout.strip()


def write_once(path: Path, payload: dict, key: str):
    if path.exists() and json.loads(path.read_text())[key] != payload[key]:
        raise SystemExit(f'{path} already holds a different {key}; this stage runs once')
    path.write_text(json.dumps(payload, indent=1, default=float))


# ---------------------------------------------------------------- stage 0: H2 coverage

def coverage() -> dict:
    """Share of (stock, month) with a price in 2015-01..2026-09 that has shares issued on its month's rows."""
    from competition.data import load_market
    from momv2.signals import panels
    market = load_market().since('2015-01-01')
    shares = panels()['shares'].reindex(index=market.calendar, columns=market.symbols)
    priced = market.valid.groupby(market.calendar.to_period('M')).any()
    known = shares.notna().groupby(market.calendar.to_period('M')).any()
    share = float((known & priced).to_numpy().sum() / priced.to_numpy().sum())
    missing = sorted(priced.columns[(priced & ~known).sum() > 0])
    result = dict(covered_share=share, threshold=COVERAGE_MIN, keep_h2=share >= COVERAGE_MIN,
                  symbols_with_gaps=missing, gap_months={s: int((priced[s] & ~known[s]).sum()) for s in missing})
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_once(COVERAGE, result, 'keep_h2')
    print(json.dumps({k: v for k, v in result.items() if k != 'gap_months'}), flush=True)
    return result


def components() -> list[str]:
    if not COVERAGE.exists():
        raise SystemExit('Run the coverage stage first (H2 keep/drop is decided before any return)')
    return ['h1', 'h2', 'h3'] if json.loads(COVERAGE.read_text())['keep_h2'] else ['h1', 'h3']


# ---------------------------------------------------------------- stage 1: DEV

def pick(component: str, dev: dict) -> dict:
    """Section 8: the default unless an eligible setting beats it by more than 0.2% mean delta."""
    ref = dev[REFERENCE]
    default = dev[label(component, DEFAULTS[component])]
    best, best_mean = DEFAULTS[component], default['paired_mean_delta'] + PICK['min_gain_over_default']
    for params in GRID[component]:
        m = dev[label(component, params)]
        eligible = (m['paired_median_delta'] >= PICK['min_median_delta']
                    and m['turnover'] <= PICK['max_turnover_ratio'] * ref['turnover']
                    and m['disqualification_rate'] <= ref['disqualification_rate'])
        if params != DEFAULTS[component] and eligible and m['paired_mean_delta'] > best_mean:
            best, best_mean = params, m['paired_mean_delta']
    return best


def dev_stage(workers: int) -> dict:
    comps = components()
    run(REFERENCE, config(REFERENCE), 'dev', workers)
    names = []
    for c in comps:
        for params in GRID[c]:
            name = label(c, params)
            run(name, config(name, c, params), 'dev', workers)
            names.append(name)
    dev = table([REFERENCE, *names], 'dev')
    chosen = {c: pick(c, dev) for c in comps}
    kind = 'composite' if 'h2' in comps else 'composite3'
    composite = {k: v for c in comps for k, v in chosen[c].items()}
    freeze = dict(chosen=chosen, composite=dict(kind=kind, **composite), pick_rule=PICK, gates=GATES,
                  keep_h2='h2' in comps, git=git_state())
    write_once(FREEZE, freeze, 'chosen')
    write_csv(RESULTS / 'dev.csv', [dict(candidate=n, **{k: v for k, v in m.items() if k != 'ci95'},
                                         ci95_low=(m['ci95'] or [None])[0], ci95_high=(m['ci95'] or [None, None])[1])
                                    for n, m in dev.items()])
    (RESULTS / 'dev.json').write_text(json.dumps(dict(table=dev, chosen=chosen), indent=1, default=float))
    (RESULTS / 'summary.md').write_text(summary_md())
    print(json.dumps(chosen), flush=True)
    return freeze


# ---------------------------------------------------------------- stages 2-3: validation and test

def frozen() -> dict:
    if not FREEZE.exists() or not committed(FREEZE):
        raise SystemExit(f'{FREEZE} must exist and be committed before validation')
    return json.loads(FREEZE.read_text())


def finalists(freeze: dict) -> dict:
    """name -> config for the Composite and the three frozen diagnostics."""
    out = {'composite': config('composite', freeze['composite']['kind'],
                               {k: v for k, v in freeze['composite'].items() if k != 'kind'})}
    for c, params in freeze['chosen'].items():
        out[c] = config(c, c, params)
    return out


def gate(m: dict, ref: dict, stage: str) -> dict:
    g = GATES[stage]
    checks = {
        f'mean_delta > {g["min_mean_delta"]:+.1%}': m['paired_mean_delta'] > g['min_mean_delta'],
        'median_delta >= 0': m['paired_median_delta'] >= g['min_median_delta'],
        'no extra disqualification': m['disqualified'] <= ref['disqualified'],
        'extra cost < mean_delta': m['cost_pct'] - ref['cost_pct'] < m['paired_mean_delta'],
    }
    return dict(checks=checks, passed=all(checks.values()))


def validation_stage(workers: int) -> dict:
    freeze, cfgs = frozen(), None
    cfgs = finalists(freeze)
    for split in ('validation', 'dev'):          # DEV Composite is report-only
        run(REFERENCE, config(REFERENCE), split, workers)
        for name, cfg in cfgs.items():
            run(name, cfg, split, workers)
    body = table([REFERENCE, *cfgs], 'validation')
    report = {'dev': table([REFERENCE, *cfgs], 'dev'),
              **{b: table([REFERENCE, *cfgs], 'dev', lo, hi) for b, (lo, hi) in BLOCKS.items()}}
    acceptance = gate(body['composite'], body[REFERENCE], 'validation')
    verdict = dict(decision='PASS' if acceptance['passed'] else 'STOP', acceptance=acceptance, table=body,
                   report_only=report, freeze_commit=freeze['git']['commit'], git=git_state())
    write_once(VERDICT, verdict, 'decision')
    (RESULTS / 'summary.md').write_text(summary_md())
    print(verdict['decision'], json.dumps(acceptance['checks']), flush=True)
    return verdict


def test_stage(workers: int) -> dict:
    if not VERDICT.exists() or json.loads(VERDICT.read_text())['decision'] != 'PASS':
        raise SystemExit('The 2025-2026 test runs only after a validation PASS')
    cfgs = finalists(frozen())
    run(REFERENCE, config(REFERENCE), 'holdout', workers)
    for name, cfg in cfgs.items():
        run(name, cfg, 'holdout', workers)
    body = table([REFERENCE, *cfgs], 'holdout')
    acceptance = gate(body['composite'], body[REFERENCE], 'test')
    verdict = dict(decision='PASS' if acceptance['passed'] else 'STOP', acceptance=acceptance, table=body,
                   git=git_state())
    write_once(TEST_VERDICT, verdict, 'decision')
    (RESULTS / 'summary.md').write_text(summary_md())
    print(verdict['decision'], json.dumps(acceptance['checks']), flush=True)
    return verdict


# ---------------------------------------------------------------- report

def rows_md(t: dict, order) -> list[str]:
    lines = ['| 策略 | 窗口 | 平均 | 中位數 | 配對平均 Δ | 配對中位數 Δ | 95% CI | 勝率 | P10 | 最差 | 周轉 | 成本 | 失格 |',
             '|---|' + '---:|' * 12]
    for n in order:
        m, ref = t[n], n == REFERENCE
        ci = '—' if ref else f'{pct(m["ci95"][0])} ~ {pct(m["ci95"][1])}'
        lines.append(f'| {n} | {m["n"]} | {pct(m["mean"])} | {pct(m["median"])} | '
                     f'{"—" if ref else pct(m["paired_mean_delta"])} | {"—" if ref else pct(m["paired_median_delta"])} | '
                     f'{ci} | {"—" if ref else format(m["paired_win_rate"], ".0%")} | {pct(m["p10"])} | '
                     f'{pct(m["worst"])} | {m["turnover"]:.2f} | {m["cost_pct"]:.2%} | {m["disqualified"]} |')
    return lines


def summary_md() -> str:
    lines = ['# Momentum-v2 評估', '', '- 規則寫死於 [docs/momentum_v2_spec.md](../../../docs/momentum_v2_spec.md)',
             f'- 市場資料自 {DATA_START} 起；24 日窗口（月初＋月中），逐窗口和 Mom20 配對', '']
    if COVERAGE.exists():
        c = json.loads(COVERAGE.read_text())
        lines += [f'- H2 發行股數覆蓋率 {c["covered_share"]:.1%}（門檻 {c["threshold"]:.0%}）→ '
                  f'{"保留 H2" if c["keep_h2"] else "取消 H2"}', '']
    if (RESULTS / 'dev.json').exists():
        dev = json.loads((RESULTS / 'dev.json').read_text())
        order = [REFERENCE] + sorted((n for n in dev['table'] if n != REFERENCE), key=lambda n: (n[:2], n))
        lines += ['## 1. DEV 2015–2021：調參（只用來選參數）', ''] + rows_md(dev['table'], order)
        lines += ['', '選參規則：中位數 Δ ≥ 0、周轉 ≤ 1.5 倍、失格率不增加，且平均 Δ 比預設值高 0.2% 以上才換掉預設值。', '',
                  '選出：' + '；'.join(f'{c} → {json.dumps(p)}' for c, p in dev['chosen'].items()), '']
    if VERDICT.exists():
        v = json.loads(VERDICT.read_text())
        order = [REFERENCE, 'composite', *[n for n in ('h1', 'h2', 'h3') if n in v['table']]]
        lines += ['## 2. Validation 2022–2024（第一道 gate，只跑一次）', '', f'**{v["decision"]}**', '']
        lines += rows_md(v['table'], order)
        lines += ['', '- Composite：' + '，'.join(f'{"✓" if ok else "✗"} {k}' for k, ok in v['acceptance']['checks'].items()),
                  '- H1、H2、H3 只是診斷，不觸發任何決策', '',
                  '### 只報告：DEV 與分段（Composite 參數已凍結後才跑，不當 gate）', '']
        for block, t in v['report_only'].items():
            lines += [f'**{block}**', ''] + rows_md(t, order) + ['']
    if TEST_VERDICT.exists():
        v = json.loads(TEST_VERDICT.read_text())
        order = [REFERENCE, 'composite', *[n for n in ('h1', 'h2', 'h3') if n in v['table']]]
        lines += ['## 3. Test 2025-01 到 2026-09（第二道 gate，只跑一次）', '', f'**{v["decision"]}**', '']
        lines += rows_md(v['table'], order)
        lines += ['', '- Composite：' + '，'.join(f'{"✓" if ok else "✗"} {k}' for k, ok in v['acceptance']['checks'].items()),
                  '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('stage', choices=['coverage', 'dev', 'validation', 'test'])
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    RESULTS.mkdir(parents=True, exist_ok=True)
    if args.stage == 'coverage':
        return coverage()
    return dict(dev=dev_stage, validation=validation_stage, test=test_stage)[args.stage](args.workers)


if __name__ == '__main__':
    main()
