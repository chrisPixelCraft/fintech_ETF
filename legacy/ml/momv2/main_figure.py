"""Main figure and main table (report only): every alternative vs Mom20 (ours), paired per window.

    .venv/bin/python -m momv2.main_figure

Full period: 276 windows, 2015-01 .. 2026-09 (research/runs/momentum_v2).
Test: the 40 windows of 2025-01 .. 2026-09; model baselines come from
research/results/final_test/episode_returns.csv (same window ids, same Mom20).
Delta = strategy return - Mom20 return in the same 24-day window; 95% CI is
a paired bootstrap (10,000 draws, seed 0).
Outputs: docs/figures/main_figure.png, research/results/main_table.md.
"""
from __future__ import annotations

import json

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

from competition.rules import ROOT
from momv2.evaluate import complete

FIGURES = ROOT / 'docs/figures'
TABLE = ROOT / 'research/results/main_table.md'
SPLITS = ('dev', 'validation', 'holdout')
TYPES = {'價格訊號': ('Price signal', 'o', 0), '組合層': ('Portfolio', 's', 1), '模型': ('Model', 'D', 2)}
THEME = {
    'light': dict(surface='#fcfcfb', text='#0b0b0b', muted='#52514e', grid='#e4e3de',
                  series=('#2a78d6', '#eb6834', '#1baf7a')),
    'dark': dict(surface='#1a1a19', text='#ffffff', muted='#c3c2b7', grid='#3a3a37',
                 series=('#3987e5', '#d95926', '#199e70')),
}
# label, English label, type, run name per split (or a final_test column), verdict
METHODS = [
    ('Mom25', 'Mom25', '價格訊號', 'mom25', '打平'),
    ('Mom10', 'Mom10', '價格訊號', 'ft:momentum_10', '輸'),
    ('Mom60', 'Mom60', '價格訊號', 'ft:momentum_60', '輸'),
    ('Residual 20 日（扣 0050）', 'Residual 20d (0050)', '價格訊號', 'h1_resid20_beta60', '打平'),
    ('Residual 20 日（扣等權市場）', 'Residual 20d (equal-weight mkt)', '價格訊號', 'fix_ew20', '打平'),
    ('Residual 120 日（H1）', 'Residual 120d (H1) †', '價格訊號',
     dict(dev='h1_resid120_beta250', validation='h1study', holdout='h1study'), 'test 反轉'),
    ('技術指標 Composite（19 個）', 'Technical composite (19)', '價格訊號', 'tech_composite', '輸'),
    ('多尺度 MACD', 'Multi-scale MACD', '價格訊號', 'macd_combined', '輸'),
    ('台積電 22.5% ＋ 等權', 'TSMC 22.5% + equal', '組合層', 'tsmc_core_equal', '打平（診斷）'),
    ('台積電 22.5% ＋ 0.85／0.15', 'TSMC 22.5% + 0.85/0.15', '組合層', 'tsmc_formal', '未過，警告 11'),
    ('AutoTS', 'AutoTS', '模型', 'ft:autots_h5', '輸'),
    ('LightGBM（最佳）', 'LightGBM (best)', '模型', 'ft:lgbm_D_lr0.01', '輸'),
]


def returns(spec, splits) -> dict:
    """episode -> terminal return (and turnover when available)."""
    if isinstance(spec, str) and spec.startswith('ft:'):
        frame = pd.read_csv(ROOT / 'research/results/final_test/episode_returns.csv').set_index('episode')
        if splits != ('holdout',):
            return {}
        return {k: dict(r=float(v), turnover=None) for k, v in frame[spec[3:]].items()}
    out = {}
    for split in splits:
        name = spec[split] if isinstance(spec, dict) else spec
        out.update({k: dict(r=s['terminal_return'], turnover=s['turnover']) for k, s in complete(name, split).items()})
    return out


def paired(mine: dict, ref: dict) -> dict | None:
    common = sorted(set(mine) & set(ref))
    if not common:
        return None
    if len(common) != len(ref):
        raise SystemExit('windows do not match Mom20')
    d = np.array([mine[k]['r'] - ref[k]['r'] for k in common])
    boot = d[np.random.default_rng(0).integers(0, len(d), (10000, len(d)))].mean(axis=1)
    turn = [mine[k]['turnover'] for k in common if mine[k]['turnover'] is not None]
    return dict(n=len(d), mean=float(np.mean([mine[k]['r'] for k in common])), delta=float(d.mean()),
                lo=float(np.percentile(boot, 2.5)), hi=float(np.percentile(boot, 97.5)), win=float((d > 0).mean()),
                turnover=float(np.mean(turn)) if turn else None)


def collect() -> dict:
    ref_full, ref_test = returns('mom20', SPLITS), returns('mom20', ('holdout',))
    rows = {'Mom20（Ours）': dict(type='ours', full=dict(n=len(ref_full), mean=np.mean([x['r'] for x in ref_full.values()]),
                                                          turnover=np.mean([x['turnover'] for x in ref_full.values()])),
                                 test=dict(n=len(ref_test), mean=np.mean([x['r'] for x in ref_test.values()]),
                                           win=None), verdict='正式策略', en='Mom20 (ours)')}
    for label, en, kind, spec, verdict in METHODS:
        rows[label] = dict(type=kind, en=en, verdict=verdict, full=paired(returns(spec, SPLITS), ref_full),
                           test=paired(returns(spec, ('holdout',)), ref_test))
    return rows


BARS = {   # label on the chart -> row in rows (2025-2026 test mean return)
    'Mom20（Ours）': 'Mom20（Ours）', 'Mom25': 'Mom25', 'Residual 20 日': 'Residual 20 日（扣等權市場）',
    '台積電 22.5% 核心': '台積電 22.5% ＋ 等權', '多尺度 MACD': '多尺度 MACD', 'LightGBM': 'LightGBM（最佳）',
    'AutoTS': 'AutoTS', 'H1 residual 120 日': 'Residual 120 日（H1）', '技術指標重排': '技術指標 Composite（19 個）',
}
OURS, OTHER, INK, MUTED = '#2a78d6', '#c9c8c2', '#0b0b0b', '#52514e'


def figure(rows: dict):
    """One white bar chart: mean return per 24-day window on the 2025-2026 test."""
    data = sorted(((label, rows[key]['test']['mean']) for label, key in BARS.items()), key=lambda x: x[1])
    plt.rcParams.update({'font.family': ['PingFang TC', 'Heiti TC', 'Arial Unicode MS'], 'font.size': 12})
    fig, ax = plt.subplots(figsize=(8, 5), facecolor='white')
    ax.set_facecolor('white')
    labels, values = zip(*data)
    colors = [OURS if l.endswith('（Ours）') else OTHER for l in labels]
    ax.barh(labels, [100 * v for v in values], color=colors, height=.62, edgecolor='white', linewidth=2)
    for i, (l, v) in enumerate(data):
        ours = l.endswith('（Ours）')
        ax.text(100 * v + .12, i, f'{v:+.2%}', va='center', ha='left', fontsize=12,
                color=INK, fontweight='bold' if ours else 'normal')
    for tick in ax.get_yticklabels():
        tick.set_color(INK)
        tick.set_fontweight('bold' if tick.get_text().endswith('（Ours）') else 'normal')
    ax.set_xlim(0, 100 * max(values) * 1.18)
    ax.xaxis.set_visible(False)
    for side in ('top', 'right', 'bottom'):
        ax.spines[side].set_visible(False)
    ax.spines['left'].set_color('#d7d6d1')
    ax.tick_params(axis='y', length=0)
    ax.set_title('2025–2026 每 24 個交易日的平均報酬', loc='left', color=INK, fontsize=15, fontweight='bold', pad=14)
    fig.text(.01, .01, '40 個窗口，每月月初、月中起跑；同一套組合與成本', color=MUTED, fontsize=10)
    fig.tight_layout(rect=(0, .04, 1, 1))
    FIGURES.mkdir(parents=True, exist_ok=True)
    path = FIGURES / 'main_figure.png'
    fig.savefig(path, dpi=200, facecolor='white')
    plt.close(fig)
    return path


def pct(x, sign=True):
    return '—' if x is None else (f'{x:+.2%}' if sign else f'{x:.2%}')


def table_md(rows: dict) -> str:
    lines = ['| 方法 | 類型 | 2015–2026 Δ（95% CI） | 2025–2026 平均 | 2025–2026 Δ（95% CI） | 2025–2026 勝率 | 周轉 | 結論 |',
             '|---|---|---:|---:|---:|---:|---:|---|']
    for label, v in rows.items():
        if v['type'] == 'ours':
            lines.append(f'| **{label}** | 動能 | **基準**（平均 {pct(v["full"]["mean"])}） | **{pct(v["test"]["mean"])}** | '
                         f'**基準** | — | {v["full"]["turnover"]:.2f} | **{v["verdict"]}** |')
            continue
        f, t = v['full'], v['test']
        ci = lambda s: '—' if s is None else f'{pct(s["delta"])}（{pct(s["lo"])} ~ {pct(s["hi"])}）'
        turn = (f or t or {}).get('turnover')
        lines.append(f'| {label} | {v["type"]} | {ci(f)} | {pct(t["mean"]) if t else "—"} | {ci(t)} | '
                     f'{t["win"]:.0%} | {"—" if turn is None else f"{turn:.2f}"} | {v["verdict"]} |'
                     if t else f'| {label} | {v["type"]} | {ci(f)} | — | — | — | — | {v["verdict"]} |')
    return '\n'.join(lines)


def main():
    rows = collect()
    print(figure(rows))
    md = ['# Main table', '', '由 `python -m momv2.main_figure` 產生；Δ = 和 Mom20 在同一個 24 日窗口的報酬差，'
          '95% CI 為配對 bootstrap。', '', table_md(rows), '']
    TABLE.write_text('\n'.join(md))
    (ROOT / 'research/results/main_table.json').write_text(json.dumps(rows, indent=1, default=float))
    print('\n'.join(md))


if __name__ == '__main__':
    main()
