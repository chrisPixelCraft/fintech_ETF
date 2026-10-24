"""Tail comparison figure for the K3 study (no recomputation).

    .venv/bin/python research/results/k3_composite/plot.py

Butterfly chart over the 276 historical windows: P(return - 0050 >= +5pp) to the right,
P(<= -5pp) to the left; right-hand columns show warnings and the recent-5 mean.
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
INK, MUTED, GRID, SURFACE, WIN, LOSS = '#0b0b0b', '#52514e', '#e4e3de', '#ffffff', '#2a78d6', '#e34948'
CURRENT = 'K2'
LABEL = {'K3.0': 'K3.0 三分數平均', 'K3.1': 'K3.1 確認動能', 'K2': 'K2 投信＋營收（正式）',
         'K1': 'K1 動能集中', 'Mom20': 'Mom20', 'T2': 'T2 大型股＋投信'}
plt.rcParams.update({'font.family': ['PingFang TC', 'Heiti TC', 'Arial Unicode MS'], 'font.size': 11})


def rows():
    m = json.loads((HERE / 'metrics.json').read_text())['metrics']
    out = []
    for s in LABEL:
        h, r = m['historical'][s], m['recent'][s]
        out.append(dict(s=s, win=h['p_gain_5pp'], loss=h['p_loss_5pp'], big=h['p_gain_10pp'],
                        warn=h['total_warning_days'], recent=r['mean_delta_vs_0050']))
    return sorted(out, key=lambda x: x['win'] - x['loss'])


def main():
    data = rows()
    fig, ax = plt.subplots(figsize=(10, 5.6), facecolor=SURFACE)
    for i, d in enumerate(data):
        ax.barh(i, 100 * d['win'], height=.6, color=WIN, edgecolor=SURFACE, linewidth=2)
        ax.barh(i, -100 * d['loss'], height=.6, color=LOSS, edgecolor=SURFACE, linewidth=2)
        bold = 'bold' if d['s'] == CURRENT else 'normal'
        ax.text(100 * d['win'] + .6, i, f'{100 * d["win"]:.1f}%', va='center', fontsize=10, color=INK, fontweight=bold)
        ax.text(-100 * d['loss'] - .6, i, f'{100 * d["loss"]:.1f}%', va='center', ha='right', fontsize=10,
                color=INK, fontweight=bold)
        ax.text(36, i, f'{100 * (d["win"] - d["loss"]):+.1f}', va='center', ha='center', fontsize=10, color=INK, fontweight=bold)
        ax.text(43, i, str(d['warn']), va='center', ha='center', fontsize=10,
                color=LOSS if d['warn'] else INK, fontweight=bold)
        ax.text(51, i, f'{100 * d["recent"]:+.2f}', va='center', ha='center', fontsize=10, color=INK, fontweight=bold)
    top = len(data) - .35
    for x, t in ((36, '尾端差'), (43, '警告日'), (51, '近5期\n平均 Δ50')):
        ax.text(x, top, t, ha='center', va='bottom', fontsize=9.5, color=MUTED)
    ax.set_yticks(range(len(data)), [LABEL[d['s']] for d in data])
    for tick, d in zip(ax.get_yticklabels(), data):
        tick.set_fontweight('bold' if d['s'] == CURRENT else 'normal')
    ax.axvline(0, color=MUTED, linewidth=.8)
    ax.set_xlim(-31, 55)
    ax.set_xticks([-20, -10, 0, 10, 20, 30], ['20%', '10%', '0', '10%', '20%', '30%'])
    for side in ('top', 'right', 'left'):
        ax.spines[side].set_visible(False)
    ax.spines['bottom'].set_color(GRID)
    ax.tick_params(axis='y', length=0, colors=INK)
    ax.tick_params(axis='x', colors=MUTED)
    ax.grid(axis='x', color=GRID, linewidth=.8)
    ax.set_axisbelow(True)
    ax.text(-13, -.85, '← 大輸：輸 0050 ≥5pp', ha='center', fontsize=9.5, color=LOSS)
    ax.text(15, -.85, '大贏：贏 0050 ≥5pp →', ha='center', fontsize=9.5, color=WIN)
    ax.set_ylim(-1.2, len(data) - .1)
    ax.set_title('綜合版 K3：大贏與大輸的機率（2015–2026，276 個窗口）', loc='left', color=INK,
                 fontsize=14, fontweight='bold', pad=26)
    fig.text(.01, .012, '依尾端差排序；含費稅；股票池為 2026 名單（早年偏樂觀）；近5期 W3–W5 已看過', color=MUTED, fontsize=9)
    fig.tight_layout(rect=(0, .03, 1, 1))
    (HERE / 'figures').mkdir(exist_ok=True)
    fig.savefig(HERE / 'figures/tails.png', dpi=180, facecolor=SURFACE)
    plt.close(fig)


if __name__ == '__main__':
    main()
