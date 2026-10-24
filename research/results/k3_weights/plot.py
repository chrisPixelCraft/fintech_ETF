"""K3 weight-search figure (no recomputation).

    .venv/bin/python research/results/k3_weights/plot.py

Left: DEV tail edge for each of the 15 simplex weights (x = momentum weight, y = revenue weight,
trust = 1 - x - y), selected weight outlined. Right: tail edge by period for the selected weight, K2 and
equal-weight K3 (validation is the only untouched test; later periods were seen before).
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

HERE = Path(__file__).resolve().parent
INK, MUTED, GRID, SURFACE = '#0b0b0b', '#52514e', '#e4e3de', '#ffffff'
SEL, K2C, EQ = '#2a78d6', '#eb6834', '#c9c8c2'
plt.rcParams.update({'font.family': ['PingFang TC', 'Heiti TC', 'Arial Unicode MS'], 'font.size': 11})


def tail(frame):
    d = frame.delta_vs_0050
    return (d >= .05).mean() - (d <= -.05).mean()


def period_table():
    sel = json.loads((HERE / 'decision.json').read_text())['selected_strategy']
    grid = pd.read_csv(HERE / 'dev_grid.csv').set_index('strategy')
    val = pd.read_csv(HERE / 'validation.csv').set_index('strategy') if 'strategy' in pd.read_csv(HERE / 'validation.csv') else None
    rep = pd.read_csv(HERE / 'report.csv')
    conc = pd.read_csv(HERE.parent / 'conc_tournament/results.csv')
    k2 = conc[conc.strategy == 'K2']
    rows = {}
    rows[sel] = [grid.loc[sel, 'tail_edge']]
    rows['K3w_15'] = [grid.loc['K3w_15', 'tail_edge']]
    rows['K2'] = [tail(k2[k2.window_id.str.startswith('dev_')])]
    for p in ('validation', 'holdout', 'recent'):
        for s in rows:
            hit = rep[(rep.period == p) & (rep.strategy == s)]
            if hit.empty and val is not None and p == 'validation':
                hit = val.loc[[s]] if s in val.index else hit
            if hit.empty and s == 'K2':
                part = k2[k2.window_id.str.startswith('validation_')] if p == 'validation' else \
                       k2[k2.window_id.str.startswith('holdout_')] if p == 'holdout' else k2[k2.window_id.str.startswith('W')]
                rows[s].append(tail(part))
            else:
                rows[s].append(float(hit.tail_edge.iloc[0]))
    return sel, grid, rows


def main():
    sel, grid, rows = period_table()
    fig, (left, right) = plt.subplots(1, 2, figsize=(13, 5.6), facecolor=SURFACE,
                                      gridspec_kw=dict(width_ratios=[1, 1.25]))
    cmap = LinearSegmentedColormap.from_list('seq', ['#eaf2fc', '#2a78d6', '#0d3a73'])
    pts = grid[grid.index != 'K3w_15']
    lo, hi = pts.tail_edge.min(), pts.tail_edge.max()
    for s, r in pts.iterrows():
        m, t, v = json.loads(r.weights)
        c = cmap((r.tail_edge - lo) / (hi - lo))
        left.scatter(m, v, s=1500, marker='s', color=c, edgecolor=SEL if s == sel else SURFACE,
                     linewidth=3 if s == sel else 2, zorder=2)
        left.text(m, v, f'{100 * r.tail_edge:+.0f}', ha='center', va='center', fontsize=10, zorder=3,
                  color='#ffffff' if (r.tail_edge - lo) / (hi - lo) > .55 else INK,
                  fontweight='bold' if s == sel else 'normal')
    left.set_xlim(-.15, 1.15)
    left.set_ylim(-.15, 1.15)
    left.set_xticks([0, .25, .5, .75, 1], ['0', '25%', '50%', '75%', '100%'])
    left.set_yticks([0, .25, .5, .75, 1], ['0', '25%', '50%', '75%', '100%'])
    left.set_xlabel('動能權重', color=MUTED)
    left.set_ylabel('營收權重', color=MUTED)
    left.text(.98, .98, '投信權重 = 1 − 動能 − 營收', transform=left.transAxes, ha='right', va='top', fontsize=9.5, color=MUTED)
    left.set_title('DEV 2015–2021：16 組權重的尾端差（pp）', loc='left', fontsize=12.5, fontweight='bold', color=INK)
    for side in ('top', 'right'):
        left.spines[side].set_visible(False)
    for side in ('left', 'bottom'):
        left.spines[side].set_color(GRID)
    left.tick_params(colors=MUTED, length=0)

    periods = ['DEV\n2015–2021', 'Validation\n2022–2024\n（唯一未看過）', '2025–2026\n（已看過）', '近 5 期\n（已看過）']
    names = [(sel, '選出：動能 25%＋營收 75%', SEL), ('K2', 'K2（目前正式）', K2C), ('K3w_15', 'K3 三等分', EQ)]
    x = np.arange(len(periods))
    width = .26
    for k, (s, lab, col) in enumerate(names):
        vals = 100 * np.array(rows[s])
        bars = right.bar(x + (k - 1) * width, vals, width=width - .03, color=col, label=lab, edgecolor=SURFACE)
        for b, v in zip(bars, vals):
            right.text(b.get_x() + b.get_width() / 2, v + (1 if v >= 0 else -1), f'{v:+.0f}', ha='center',
                       va='bottom' if v >= 0 else 'top', fontsize=9, color=INK)
    right.axhline(0, color=MUTED, linewidth=.8)
    right.axvspan(.5, 1.5, color='#f3f7fd', zorder=0)
    right.set_xticks(x, periods, fontsize=9.5)
    right.set_ylabel('尾端差（pp）＝大贏率 − 大輸率', color=MUTED)
    right.legend(frameon=False, fontsize=9.5, loc='upper left')
    right.set_title('各期間尾端差：選出權重 vs K2', loc='left', fontsize=12.5, fontweight='bold', color=INK)
    for side in ('top', 'right', 'left'):
        right.spines[side].set_visible(False)
    right.spines['bottom'].set_color(GRID)
    right.tick_params(axis='y', colors=MUTED, length=0)
    right.tick_params(axis='x', length=0, colors=INK)
    right.grid(axis='y', color=GRID, linewidth=.8)
    right.set_axisbelow(True)
    fig.text(.01, .012, '大贏＝贏 0050 ≥5pp、大輸＝輸 ≥5pp；含費稅；22 檔組合層；股票池為 2026 名單；近 5 期只有 5 個窗口',
             color=MUTED, fontsize=9)
    fig.tight_layout(rect=(0, .03, 1, 1))
    (HERE / 'figures').mkdir(exist_ok=True)
    fig.savefig(HERE / 'figures/weights.png', dpi=180, facecolor=SURFACE)
    plt.close(fig)
    print({s: [round(100 * v, 1) for v in r] for s, r in rows.items()})


if __name__ == '__main__':
    main()
