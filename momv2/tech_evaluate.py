"""Technical Momentum evaluation (docs/technical_momentum_spec.md sections 7-11): one run, one big table.

    PYTHONHASHSEED=0 .venv/bin/python -m momv2.tech_evaluate --workers 8

Runs Mom20 and the 24 variants once over every window of 2015-01 .. 2026-09
(dev from 2015, validation, holdout), paired per window. The Composite gate
is fixed here and in the spec; the verdict is written once. Single indicators
and families are diagnostics only. Output: research/results/technical_momentum/.
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from competition.data import load_market
from competition.rules import ROOT
from momv2.evaluate import REFERENCE, committed, complete, config, git_state, run, write_once
from momv2 import technical as T
from research.baselines import MomentumConfig, momentum_score
from research.lgbm_jpx2 import pct

RESULTS = ROOT / 'research/results/technical_momentum'
VERDICT = RESULTS / 'verdict.json'
SPLITS = ('dev', 'validation', 'holdout')
BLOCKS = {'2015–2018': ('2015-01-01', '2018-12-31'), '2019–2021': ('2019-01-01', '2021-12-31'),
          '2022–2024': ('2022-01-01', '2024-12-31'), '2025–2026': ('2025-01-01', '2026-12-31')}
PRICE_SOURCE = {'2015–2023（HLC3 為主）': ('2015-01-01', '2023-12-31'),
                '2024–2026（官方均價為主）': ('2024-01-01', '2026-12-31')}
GATE = dict(min_mean=.005, min_median=0., min_blocks=3, drop_top=3, min_mean_after_drop=.0025,
            max_turnover_ratio=1.5)


def name_of(variant: str) -> str:
    return 'tech_' + variant.replace(':', '_')


def tech_config(variant: str) -> dict:
    base = config(REFERENCE)
    return dict(base, name=f'momv2_{name_of(variant)}', strategy='technical',
                params=dict(variant=variant, portfolio=base['params']['portfolio']))


def windows(names, ref: str = REFERENCE) -> pd.DataFrame:
    rows = []
    for split in SPLITS:
        runs = {n: complete(n, split) for n in names}
        common = set.intersection(*(set(r) for r in runs.values()))
        if len(common) != len(runs[ref]):
            raise SystemExit(f'{split}: runs cover different windows')
        for eid in sorted(common):
            s = runs[ref][eid]
            row = dict(episode=eid, start=pd.Timestamp(s['start']), end=pd.Timestamp(s['end']))
            for n, r in runs.items():
                x = r[eid]
                row.update({n: x['terminal_return'], f'{n}|turnover': x['turnover'], f'{n}|cost': x['costs'] / 1e9,
                            f'{n}|dq': int(x['disqualified']), f'{n}|warn': x['warning_days']})
            rows.append(row)
    return pd.DataFrame(rows)


def stats(f: pd.DataFrame, name: str, ref: str = REFERENCE) -> dict:
    d = (f[name] - f[ref]).to_numpy()
    boot = d[np.random.default_rng(0).integers(0, len(d), size=(10000, len(d)))].mean(axis=1)
    out = dict(n=len(d), mean=float(f[name].mean()), mean_delta=float(d.mean()), median_delta=float(np.median(d)),
               win=float((d > 0).mean()), ci95=[float(np.percentile(boot, 2.5)), float(np.percentile(boot, 97.5))],
               p10=float(np.percentile(f[name], 10)), worst=float(f[name].min()),
               turnover=float(f[f'{name}|turnover'].mean()), cost=float(f[f'{name}|cost'].mean()),
               dq=int(f[f'{name}|dq'].sum()), warnings=int(f[f'{name}|warn'].sum()),
               mean_after_drop_top=float(np.sort(d)[:-GATE['drop_top']].mean()))
    for label, (lo, hi) in {**BLOCKS, **PRICE_SOURCE}.items():
        g = f[(f.start >= lo) & (f.start <= hi)]
        out[label] = float((g[name] - g[ref]).mean())
    return out


def gate(m: dict, ref: dict) -> dict:
    checks = {
        'mean Δ > +0.5%': m['mean_delta'] > GATE['min_mean'],
        'CI 下界 > 0': m['ci95'][0] > 0,
        'median Δ ≥ 0': m['median_delta'] >= GATE['min_median'],
        '≥ 3 段 Δ > 0': sum(m[b] > 0 for b in BLOCKS) >= GATE['min_blocks'],
        '去掉前 3 大後 > +0.25%': m['mean_after_drop_top'] > GATE['min_mean_after_drop'],
        '周轉 ≤ 1.5 倍': m['turnover'] <= GATE['max_turnover_ratio'] * ref['turnover'],
        '多出成本 < mean Δ': m['cost'] - ref['cost'] < m['mean_delta'],
        '失格、警告不增加': m['dq'] <= ref['dq'] and m['warnings'] <= ref['warnings'],
    }
    return dict(checks=checks, passed=all(checks.values()))


def correlations(starts) -> dict:
    """Mean Spearman correlation with Mom20 inside the top 40 at each window's D-1 (interpretation only)."""
    market = load_market().since(T.DATA_START)
    frames = T.panels()
    acc = {i: [] for i in T.INDICATORS}
    for start in starts:
        d = market.calendar[market.calendar.get_loc(start) - 1]
        view = market.asof(d)
        mom = momentum_score(view, MomentumConfig())
        _, top = T.rerank(view, 'composite', frames)
        for i in T.INDICATORS:
            x = frames[i].loc[d].reindex(top)
            ok = x.notna()
            if ok.sum() >= 10:
                acc[i].append(x[ok].rank().corr(mom[top][ok].rank()))
    return {i: float(np.mean(v)) for i, v in acc.items()}


def summary_md(v: dict) -> str:
    t, ref = v['table'], v['table'][REFERENCE]
    lines = ['# Technical Momentum：Mom20 前 40 名內用技術指標重排', '',
             '- 規則寫死於 [docs/technical_momentum_spec.md](../../../docs/technical_momentum_spec.md)',
             '- Retrospective causal stress test：2015-01 到 2026-09，所有期間都已看過',
             f'- {ref["n"]} 個 24 日窗口，逐窗口和 Mom20 配對；組合層與 production 相同', '',
             f'## Composite：**{v["decision"]}**', '',
             '- ' + '，'.join(f'{"✓" if ok else "✗"} {k}' for k, ok in v['acceptance']['checks'].items()), '',
             '## 大表', '',
             'Δ = 對 Mom20 的配對平均差；相關 = 前 40 名內和 Mom20 的平均 Spearman（只解釋）。', '',
             '| 策略 | 平均 | Δ | 中位數 Δ | 95% CI | 勝率 | P10 | 周轉 | 成本 | ' +
             ' | '.join(BLOCKS) + ' | ' + ' | '.join(k.split('（')[0] for k in PRICE_SOURCE) + ' | 相關 |',
             '|---|' + '---:|' * (9 + len(BLOCKS) + len(PRICE_SOURCE) + 1)]
    order = [REFERENCE, name_of('composite'), *(name_of(f'family:{f}') for f in T.FAMILIES),
             *(name_of(f'ind:{i}') for i in T.INDICATORS)]
    for n in order:
        m = t[n]
        is_ref = n == REFERENCE
        code = n.split('_', 2)[-1] if n.startswith('tech_ind') else None
        label = n.replace('tech_', '').replace('ind_', '').replace('family_', '族群：')
        label = f'**{label}**' if n == name_of('composite') else label
        cells = [pct(m['mean']), '—' if is_ref else pct(m['mean_delta']), '—' if is_ref else pct(m['median_delta']),
                 '—' if is_ref else f'{pct(m["ci95"][0])} ~ {pct(m["ci95"][1])}',
                 '—' if is_ref else f'{m["win"]:.0%}', pct(m['p10']), f'{m["turnover"]:.2f}', f'{m["cost"]:.2%}',
                 *('—' if is_ref else pct(m[b]) for b in BLOCKS), *('—' if is_ref else pct(m[k]) for k in PRICE_SOURCE),
                 f'{v["correlations"][code]:+.2f}' if code else '—']
        lines.append(f'| {label} | ' + ' | '.join(cells) + ' |')
    lines += ['', '- 單一指標與族群只是診斷，不觸發任何決策',
              f'- Composite 去掉 Δ 最大的 3 個窗口後：{pct(t[name_of("composite")]["mean_after_drop_top"])}', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    spec = ROOT / 'docs/technical_momentum_spec.md'
    if not committed(spec):
        raise SystemExit('Commit the frozen spec first')
    for split in SPLITS:
        run(REFERENCE, config(REFERENCE), split, args.workers)
        for variant in T.VARIANTS:
            run(name_of(variant), tech_config(variant), split, args.workers)
    names = [REFERENCE, *(name_of(v) for v in T.VARIANTS)]
    f = windows(names)
    table = {n: stats(f, n) for n in names}
    acceptance = gate(table[name_of('composite')], table[REFERENCE])
    verdict = dict(decision='PASS' if acceptance['passed'] else 'STOP', acceptance=acceptance, table=table,
                   correlations=correlations(f.start), gate=GATE, git=git_state())
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_once(VERDICT, verdict, 'decision')
    f.to_csv(RESULTS / 'windows.csv', index=False)
    (RESULTS / 'summary.md').write_text(summary_md(verdict))
    print(summary_md(verdict))


if __name__ == '__main__':
    main()
