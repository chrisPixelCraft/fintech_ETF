"""Residual fixes (docs/residual_fixes_spec.md): run six fixed residuals once per period, one big table.

    PYTHONHASHSEED=0 .venv/bin/python -m momv2.fixes_report --workers 8

Exploratory: every period was seen before. The shadow rule (spec section 6)
is applied mechanically; production stays Mom20 whatever it says.
Output: research/results/residual_fixes/.
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from competition.rules import ROOT
from momv2.evaluate import complete, config, label, run
from momv2.regime_report import PERIODS, states
from research.lgbm_jpx2 import pct

RESULTS = ROOT / 'research/results/residual_fixes'
BASE = {'mom20': 'mom20', 'mom25': 'mom25', 'resid20': 'h1_resid20_beta60', 'resid25': 'h1_resid25_beta60'}
FIXES = {f'{fix}{n}': (n, params) for n in (20, 25)
         for fix, params in (('ew', dict(benchmark='ew')), ('ind', dict(benchmark='industry')),
                             ('shrink', dict(beta_shrink=.5)))}
SPLITS = {v: k for k, v in PERIODS.items()}          # split -> period label


def run_dir_name(name: str) -> str:
    return BASE.get(name, f'fix_{name}')


def run_fixes(workers: int):
    for name, (n, params) in FIXES.items():
        p = dict(resid_window=n, beta_window=60, **params)
        cfg = dict(config(run_dir_name(name), 'h1', p), name=f'momv2_fix_{name}')
        for split in PERIODS.values():
            run(run_dir_name(name), cfg, split, workers)


def frame() -> pd.DataFrame:
    rows = []
    names = [*BASE, *FIXES]
    for split in PERIODS.values():
        runs = {n: complete(run_dir_name(n), split) for n in names}
        common = set.intersection(*(set(r) for r in runs.values()))
        if len(common) != len(runs['mom20']):
            raise SystemExit(f'{split}: strategies cover different windows')
        for eid in sorted(common):
            s = runs['mom20'][eid]
            rows.append(dict(episode=eid, period=SPLITS[split], start=pd.Timestamp(s['start']),
                             end=pd.Timestamp(s['end']),
                             **{n: runs[n][eid]['terminal_return'] for n in names},
                             **{f'turnover_{n}': runs[n][eid]['turnover'] for n in names},
                             **{f'cost_{n}': runs[n][eid]['costs'] / 1e9 for n in names}))
    return pd.DataFrame(rows)


def length(name: str) -> int:
    return int(''.join(c for c in name if c.isdigit()))


def table(f: pd.DataFrame) -> dict:
    q = f.beta_tilt.quantile(2 / 3)
    high = f.beta_tilt > q
    out = {}
    for name in [*BASE, *FIXES]:
        n = length(name)
        mom, resid = f'mom{n}', f'resid{n}'
        row = dict(length=n)
        for period, g in [*f.groupby('period', sort=False), ('全期', f)]:
            d_mom = g[name] - g[mom]
            d_resid = g[name] - g[resid]
            row[period] = dict(mean=float(g[name].mean()), delta_mom=float(d_mom.mean()),
                               win_mom=float((d_mom > 0).mean()), delta_resid=float(d_resid.mean()),
                               worst=float(g[name].min()), turnover=float(g[f'turnover_{name}'].mean()),
                               cost=float(g[f'cost_{name}'].mean()))
        row['high_beta_tilt'] = float((f[name] - f[mom])[high].mean())
        out[name] = row
    return out


def decide(t: dict) -> dict:
    """Spec section 6, fixes only."""
    checks = {}
    for name in FIXES:
        r, base = t[name], t[f'resid{length(name)}']
        checks[name] = {
            '全期 Δ(對 mom) > 0': r['全期']['delta_mom'] > 0,
            '每段 Δ(對 mom) ≥ −0.5%': all(r[p]['delta_mom'] >= -.005 for p in PERIODS),
            '2025–2026 Δ 優於原 residual': r['2025–2026/09']['delta_mom'] > base['2025–2026/09']['delta_mom'],
        }
    passed = [n for n, c in checks.items() if all(c.values())]
    chosen = max(passed, key=lambda n: t[n]['全期']['delta_mom']) if passed else None
    return dict(checks=checks, passed=passed, shadow=chosen)


def summary_md(t: dict, verdict: dict) -> str:
    per = [*PERIODS, '全期']
    lines = ['# Residual 修正：一張大表', '',
             '- 規則寫死於 [docs/residual_fixes_spec.md](../../../docs/residual_fixes_spec.md)',
             '- **探索性**：所有期間都已看過；production 維持 Mom20', '',
             '## 1. 報酬與配對差距', '',
             'Δmom = 對同長度 mom 的配對平均 Δ（括號為勝率）；Δresid = 對同長度原 residual（0050、β 60）。', '',
             '| 策略 | ' + ' | '.join(f'{p} 平均' for p in per) + ' | ' +
             ' | '.join(f'{p} Δmom' for p in per) + ' | 全期 Δresid | 高 β 偏向窗口 Δmom |',
             '|---|' + '---:|' * (2 * len(per) + 2)]
    for name, r in t.items():
        is_mom = name.startswith('mom')
        cells = [pct(r[p]['mean']) for p in per]
        cells += ['—' if is_mom else f'{pct(r[p]["delta_mom"])}（{r[p]["win_mom"]:.0%}）' for p in per]
        cells += ['—' if is_mom or name.startswith('resid') else pct(r['全期']['delta_resid']),
                  '—' if is_mom else pct(r['high_beta_tilt'])]
        lines.append(f'| {name} | ' + ' | '.join(cells) + ' |')
    lines += ['', '## 2. 周轉、成本、最差窗口（全期）', '',
              '| 策略 | 周轉 | 成本／窗口 | 最差窗口 | 2025–2026 最差 |', '|---|---:|---:|---:|---:|']
    for name, r in t.items():
        lines.append(f'| {name} | {r["全期"]["turnover"]:.2f} | {r["全期"]["cost"]:.2%} | {pct(r["全期"]["worst"])} | '
                     f'{pct(r["2025–2026/09"]["worst"])} |')
    lines += ['', '## 3. 判定（spec 第 6 節）', '']
    for name, c in verdict['checks'].items():
        lines.append(f'- {name}：' + '，'.join(f'{"✓" if ok else "✗"} {k}' for k, ok in c.items()))
    lines += ['', f'**進 shadow：{verdict["shadow"] or "無，residual 研究結案"}**', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    run_fixes(args.workers)
    f = states(frame())
    t = table(f)
    verdict = decide(t)
    RESULTS.mkdir(parents=True, exist_ok=True)
    f.to_csv(RESULTS / 'windows.csv', index=False)
    (RESULTS / 'report.json').write_text(json.dumps(dict(table=t, verdict=verdict), indent=1, default=float))
    (RESULTS / 'summary.md').write_text(summary_md(t, verdict))
    print(summary_md(t, verdict))


if __name__ == '__main__':
    main()
