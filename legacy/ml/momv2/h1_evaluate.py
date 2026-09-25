"""H1-only study (docs/h1_residual_spec.md): DEV grid -> freeze -> validation sanity -> 2025-2026 test.

    PYTHONHASHSEED=0 .venv/bin/python -m momv2.h1_evaluate dev --workers 8
    #   -> commit research/results/h1_residual/freeze.json
    PYTHONHASHSEED=0 .venv/bin/python -m momv2.h1_evaluate validation --workers 8
    PYTHONHASHSEED=0 .venv/bin/python -m momv2.h1_evaluate test --workers 8     # only after the sanity check

DEV runs share research/runs/momentum_v2 with the Momentum-v2 study, so the
eight identical H1 settings are reused, not rerun. Each stage writes its
result once and refuses a rerun that would change it. Results:
research/results/h1_residual/.
"""
from __future__ import annotations

import argparse
import json

import numpy as np

from competition.rules import ROOT
from momv2.evaluate import (PICK, REFERENCE, committed, complete, config, git_state, label, run, table,
                            write_once)
from research.lgbm_alpha import write_csv
from research.lgbm_jpx2 import pct

RESULTS = ROOT / 'research/results/h1_residual'
FREEZE, SANITY, TEST = RESULTS / 'freeze.json', RESULTS / 'validation_sanity.json', RESULTS / 'test_verdict.json'
DEFAULT = dict(resid_window=60, beta_window=120)
GRID = [dict(resid_window=n, beta_window=b) for n in (20, 25, 40, 60, 90, 120) for b in (60, 120, 250) if n <= b]
FINAL = 'h1study'
TEST_GATE = dict(min_mean_delta=0., min_median_delta=0.)


def pick(dev: dict) -> dict:
    ref, default = dev[REFERENCE], dev[label('h1', DEFAULT)]
    best, best_mean = DEFAULT, default['paired_mean_delta'] + PICK['min_gain_over_default']
    for params in GRID:
        m = dev[label('h1', params)]
        eligible = (m['paired_median_delta'] >= PICK['min_median_delta']
                    and m['turnover'] <= PICK['max_turnover_ratio'] * ref['turnover']
                    and m['disqualification_rate'] <= ref['disqualification_rate'])
        if params != DEFAULT and eligible and m['paired_mean_delta'] > best_mean:
            best, best_mean = params, m['paired_mean_delta']
    return best


def tails(name: str, split: str) -> dict:
    mine, ref = complete(name, split), complete(REFERENCE, split)
    d = {k: mine[k]['terminal_return'] - ref[k]['terminal_return'] for k in ref}
    v = np.array(list(d.values()))
    worst = sorted(d, key=d.get)[:5]
    return dict(avg_win=float(v[v > 0].mean()) if (v > 0).any() else None,
                avg_loss=float(v[v < 0].mean()) if (v < 0).any() else None,
                worst5=[dict(start=ref[k]['start'], delta=d[k]) for k in worst])


def dev_stage(workers: int) -> dict:
    run(REFERENCE, config(REFERENCE), 'dev', workers)
    names = []
    for params in GRID:
        name = label('h1', params)
        run(name, config(name, 'h1', params), 'dev', workers)
        names.append(name)
    dev = table([REFERENCE, *names], 'dev')
    chosen = pick(dev)
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_once(FREEZE, dict(chosen=chosen, default=DEFAULT, grid=GRID, pick_rule=PICK, git=git_state()), 'chosen')
    write_csv(RESULTS / 'dev.csv', [dict(candidate=n, **{k: v for k, v in m.items() if k != 'ci95'},
                                         ci95_low=(m['ci95'] or [None])[0], ci95_high=(m['ci95'] or [None, None])[1])
                                    for n, m in dev.items()])
    (RESULTS / 'dev.json').write_text(json.dumps(dict(table=dev, chosen=chosen), indent=1, default=float))
    (RESULTS / 'summary.md').write_text(summary_md())
    print(json.dumps(chosen), flush=True)
    return chosen


def final_config() -> dict:
    if not FREEZE.exists() or not committed(FREEZE):
        raise SystemExit(f'{FREEZE} must exist and be committed first')
    return config(FINAL, 'h1', json.loads(FREEZE.read_text())['chosen'])


def validation_stage(workers: int) -> dict:
    cfg = final_config()
    run(REFERENCE, config(REFERENCE), 'validation', workers)
    run(FINAL, cfg, 'validation', workers)
    body = table([REFERENCE, FINAL], 'validation')
    passed = body[FINAL]['paired_mean_delta'] > 0
    verdict = dict(decision='CONTINUE' if passed else 'STOP', check={'mean_delta > 0': passed}, table=body,
                   tails=tails(FINAL, 'validation'), git=git_state())
    write_once(SANITY, verdict, 'decision')
    (RESULTS / 'summary.md').write_text(summary_md())
    print(verdict['decision'], flush=True)
    return verdict


def test_stage(workers: int) -> dict:
    if not SANITY.exists() or json.loads(SANITY.read_text())['decision'] != 'CONTINUE':
        raise SystemExit('The 2025-2026 test runs only after the validation sanity check passes')
    cfg = final_config()
    run(REFERENCE, config(REFERENCE), 'holdout', workers)
    run(FINAL, cfg, 'holdout', workers)
    body = table([REFERENCE, FINAL], 'holdout')
    m, ref = body[FINAL], body[REFERENCE]
    checks = {'mean_delta > 0': m['paired_mean_delta'] > TEST_GATE['min_mean_delta'],
              'median_delta >= 0': m['paired_median_delta'] >= TEST_GATE['min_median_delta'],
              'no extra disqualification': m['disqualified'] <= ref['disqualified'],
              'extra cost < mean_delta': m['cost_pct'] - ref['cost_pct'] < m['paired_mean_delta']}
    verdict = dict(decision='PASS' if all(checks.values()) else 'STOP', checks=checks, table=body,
                   tails=tails(FINAL, 'holdout'), git=git_state())
    write_once(TEST, verdict, 'decision')
    (RESULTS / 'summary.md').write_text(summary_md())
    print(verdict['decision'], json.dumps(checks), flush=True)
    return verdict


def rows_md(t: dict) -> list[str]:
    lines = ['| 策略 | 窗口 | 平均 | 中位數 | 配對平均 Δ | 配對中位數 Δ | 95% CI | 勝率 | P10 | 最差 | 周轉 | 成本 | 失格 |',
             '|---|' + '---:|' * 12]
    for n, m in t.items():
        ref = n == REFERENCE
        ci = '—' if ref else f'{pct(m["ci95"][0])} ~ {pct(m["ci95"][1])}'
        lines.append(f'| {n} | {m["n"]} | {pct(m["mean"])} | {pct(m["median"])} | '
                     f'{"—" if ref else pct(m["paired_mean_delta"])} | {"—" if ref else pct(m["paired_median_delta"])} | '
                     f'{ci} | {"—" if ref else format(m["paired_win_rate"], ".0%")} | {pct(m["p10"])} | '
                     f'{pct(m["worst"])} | {m["turnover"]:.2f} | {m["cost_pct"]:.2%} | {m["disqualified"]} |')
    return lines


def tails_md(t: dict) -> list[str]:
    return [f'- 贏的窗口平均 {pct(t["avg_win"])}，輸的窗口平均 {pct(t["avg_loss"])}',
            '- 最差 5 個窗口：' + '、'.join(f'{w["start"]} {pct(w["delta"])}' for w in t['worst5'])]


def summary_md() -> str:
    lines = ['# H1：market-residual momentum 單獨評估', '',
             '- 規則寫死於 [docs/h1_residual_spec.md](../../../docs/h1_residual_spec.md)',
             '- 市場資料自 2014-01-01 起；24 日窗口（月初＋月中），逐窗口和 Mom20 配對', '']
    if (RESULTS / 'dev.json').exists():
        dev = json.loads((RESULTS / 'dev.json').read_text())
        lines += ['## 1. DEV 2015–2021：調參', ''] + rows_md(dev['table'])
        lines += ['', f'選出：{json.dumps(dev["chosen"])}（預設 {json.dumps(DEFAULT)}，高 0.2% 以上才換）', '']
    if SANITY.exists():
        v = json.loads(SANITY.read_text())
        lines += ['## 2. Validation 2022–2024：sanity check（不乾淨，H1 是看過這段後挑的）', '',
                  f'**{v["decision"]}**（平均 Δ > 0）', ''] + rows_md(v['table']) + [''] + tails_md(v['tails']) + ['']
    if TEST.exists():
        v = json.loads(TEST.read_text())
        lines += ['## 3. Test 2025-01 到 2026-09：決定性 gate（只跑一次）', '', f'**{v["decision"]}**', '']
        lines += rows_md(v['table']) + ['']
        lines += ['- ' + '，'.join(f'{"✓" if ok else "✗"} {k}' for k, ok in v['checks'].items())] + tails_md(v['tails'])
        lines += ['']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('stage', choices=['dev', 'validation', 'test'])
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    return dict(dev=dev_stage, validation=validation_stage, test=test_stage)[args.stage](args.workers)


if __name__ == '__main__':
    main()
