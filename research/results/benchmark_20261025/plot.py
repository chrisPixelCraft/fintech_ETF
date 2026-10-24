"""Three report figures from the benchmark data (no recomputation).

    .venv/bin/python research/results/benchmark_20261025/plot.py

1. latest24.png  - net return of every strategy on 2026-09-16..10-22, 0050 as a reference line.
2. recent5.png   - return minus 0050 per window W1..W5 (diverging heatmap, values printed).
3. holdout40.png - README figure redrawn: 2025-2026 mean return over 40 windows, K2 added.
"""
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import MaxNLocator

HERE = Path(__file__).resolve().parent
OUT = HERE / 'figures'
INK, MUTED, GRID, SURFACE = '#0b0b0b', '#52514e', '#e4e3de', '#ffffff'
OURS, NEW, OLD = '#2a78d6', '#eb6834', '#c9c8c2'
OURS_ID = 'K2b'
plt.rcParams.update({'font.family': ['PingFang TC', 'Heiti TC', 'Arial Unicode MS'], 'font.size': 11})
# Production K2 is now the 22-name K2b; it and the K3w candidate come from the k3_weights study
# (same windows, data and settlement). The 20-name K2 rows are replaced, not shown twice.
EXTRA = HERE.parent / 'k3_weights/results.csv'
EXTRA_IDS = {'K2b': 'new', 'K3w_05': 'new'}


def with_extra(frame: pd.DataFrame, recent: bool) -> pd.DataFrame:
    """Benchmark rows with K2 swapped for K2b and the K3w candidate added (window ids are shared)."""
    extra = pd.read_csv(EXTRA)
    extra = extra[extra.strategy.isin(EXTRA_IDS) & (extra.set == ('recent' if recent else 'historical'))]
    if not recent:
        extra = extra[extra.split == 'holdout']
    extra = extra.rename(columns={'window_id': 'window'}).assign(group=lambda x: x.strategy.map(EXTRA_IDS))
    extra = extra[extra.window.isin(frame.window.unique())]
    return pd.concat([frame[frame.strategy != 'K2'], extra[frame.columns.intersection(extra.columns)]],
                     ignore_index=True)

LEGACY_LABEL = {
    'mom20': 'Mom20（舊正式）', 'mom25': 'Mom25', 'mom10': 'Mom10', 'mom60': 'Mom60',
    'residual20_0050': 'Residual 20 日（扣 0050）', 'residual20_ew': 'Residual 20 日（扣等權）',
    'residual120_h1': 'Residual 120 日（H1）', 'tech_composite': '技術指標 Composite',
    'macd_combined': '多尺度 MACD', 'tsmc_core_equal': '台積電 22.5%＋等權',
    'tsmc_formal': '台積電 22.5%＋混合', 'autots_h5': 'AutoTS', 'lgbm_D_lr0.01': 'LightGBM',
}
NEW_LABEL = {'K2b': 'K2 投信＋營收 22 檔（Ours）', 'K3w_05': 'K3w 動能25%＋營收75%', 'T2': 'T2 大型股＋投信', 'C1': 'C1 台積電＋大型股',
             'C3': 'C3 低波動大型股', 'S1': 'S1 營收動能', 'S2': 'S2 崩跌防護',
             'T1': 'T1 投信＋營收', 'T3': 'T3 大型股＋營收'}


def label(s):
    return NEW_LABEL.get(s) or LEGACY_LABEL.get(s, s)


def color(s, group):
    return OURS if s == OURS_ID else NEW if group == 'new' else OLD


def style_axis(ax):
    ax.set_facecolor(SURFACE)
    for side in ('top', 'right', 'left'):
        ax.spines[side].set_visible(False)
    ax.spines['bottom'].set_color(GRID)
    ax.tick_params(axis='y', length=0, colors=INK)
    ax.tick_params(axis='x', colors=MUTED)
    ax.grid(axis='x', color=GRID, linewidth=.8)
    ax.set_axisbelow(True)


def bars(ax, frame, value, ref, ref_text, digits=2):
    """Horizontal bars sorted high to low; ours bold; 0050 dashed reference."""
    frame = frame.sort_values(value)
    y = np.arange(len(frame))
    ax.barh(y, 100 * frame[value], height=.66, color=[color(s, g) for s, g in zip(frame.strategy, frame.group)],
            edgecolor=SURFACE, linewidth=2)
    span = 100 * (frame[value].max() - min(frame[value].min(), 0))
    for i, (s, v) in enumerate(zip(frame.strategy, frame[value])):
        pad = .012 * span
        ax.text(100 * v + (pad if v >= 0 else -pad), i, f'{100 * v:+.{digits}f}%', va='center',
                ha='left' if v >= 0 else 'right', fontsize=10, color=INK,
                fontweight='bold' if s == OURS_ID else 'normal')
    ax.set_yticks(y, [label(s) for s in frame.strategy])
    for tick, s in zip(ax.get_yticklabels(), frame.strategy):
        tick.set_fontweight('bold' if s == OURS_ID else 'normal')
    ax.axvline(0, color=MUTED, linewidth=.8)
    ax.axvline(100 * ref, color=MUTED, linestyle=(0, (4, 3)), linewidth=1.2)
    ax.text(100 * ref, len(frame) - .2, ref_text, color=MUTED, fontsize=9.5, ha='left', va='bottom')
    ax.xaxis.set_major_locator(MaxNLocator(integer=True, steps=[1, 2, 5, 10]))
    ax.xaxis.set_major_formatter(lambda v, _: f'{v:+.0f}%')
    style_axis(ax)
    lo, hi = ax.get_xlim()
    ax.set_xlim(lo - .06 * (hi - lo), hi + .08 * (hi - lo))


def legend(fig, has_new=True):
    handles = [Patch(color=OURS, label='正式策略 K2'), Patch(color=OLD, label='舊策略（README 13 種）'),
               Line2D([], [], color=MUTED, linestyle=(0, (4, 3)), label='0050 參考')]
    if has_new:
        handles.insert(1, Patch(color=NEW, label='本輪新候選'))
    fig.legend(handles=handles, loc='upper right', ncol=len(handles), frameon=False, fontsize=9.5,
               bbox_to_anchor=(.99, .955))


def latest24():
    d = with_extra(pd.read_csv(HERE / 'latest24.csv'), recent=True)
    assert set(EXTRA_IDS) <= set(d.strategy)
    ref = float(d.loc[d.strategy == '0050', 'terminal_return'].iloc[0])
    d = d[d.strategy != '0050']
    fig, ax = plt.subplots(figsize=(9.5, 8.2), facecolor=SURFACE)
    bars(ax, d, 'terminal_return', ref, f'0050 {100 * ref:+.2f}%')
    ax.set_title('最近 24 個交易日淨報酬（2026-09-16 → 10-22）', loc='left', color=INK, fontsize=14,
                 fontweight='bold', pad=30)
    legend(fig)
    fig.text(.01, .012, '起始全現金 10 億元；含手續費與證交稅；同一份修復資料與結算；Active Share 未驗證',
             color=MUTED, fontsize=9)
    fig.tight_layout(rect=(0, .03, 1, 1))
    fig.savefig(OUT / 'latest24.png', dpi=180, facecolor=SURFACE)
    plt.close(fig)


def recent5():
    d = with_extra(pd.read_csv(HERE / 'recent5.csv'), recent=True)
    assert all((d.strategy == s).sum() == 5 for s in EXTRA_IDS)
    d = d[d.strategy != '0050']
    windows = d.groupby('window')[['start', 'end']].first().sort_index()
    grid = d.pivot(index='strategy', columns='window', values='delta_vs_0050').reindex(columns=windows.index) * 100
    group = d.groupby('strategy').group.first()
    order = grid.mean(axis=1).sort_values(ascending=False).index
    grid = grid.loc[order]
    wins = (grid > 0).sum(axis=1)
    cmap = LinearSegmentedColormap.from_list('div', ['#e34948', '#f0efec', '#2a78d6'])
    bound = float(np.nanmax(np.abs(grid.values)))
    fig, ax = plt.subplots(figsize=(9.5, 9), facecolor=SURFACE)
    ax.imshow(grid.values, cmap=cmap, norm=TwoSlopeNorm(0, -bound, bound), aspect='auto')
    for i in range(grid.shape[0]):
        for j in range(grid.shape[1]):
            v = grid.values[i, j]
            ax.text(j, i, f'{v:+.1f}', ha='center', va='center', fontsize=9.5,
                    color='#ffffff' if abs(v) > .55 * bound else INK)
        ax.text(grid.shape[1] - .35, i, f'{grid.values[i].mean():+.2f}　{wins.iloc[i]}/5', ha='left',
                va='center', fontsize=9.5, color=INK, fontweight='bold' if order[i] == OURS_ID else 'normal')
    ax.text(grid.shape[1] - .35, -.85, '平均　贏期', ha='left', va='center', fontsize=9.5, color=MUTED)
    ax.set_xticks(range(grid.shape[1]), [f'{w}\n{s[5:]}→{e[5:]}' for w, (s, e) in windows.iterrows()], fontsize=9.5)
    ax.xaxis.tick_top()
    ax.set_yticks(range(grid.shape[0]), [label(s) for s in order])
    for tick, s in zip(ax.get_yticklabels(), order):
        tick.set_fontweight('bold' if s == OURS_ID else 'normal')
        tick.set_color(OURS if s == OURS_ID else NEW if group[s] == 'new' else INK)
    ax.tick_params(length=0)
    for side in ax.spines.values():
        side.set_visible(False)
    ax.set_xticks(np.arange(-.5, grid.shape[1]), minor=True)
    ax.set_yticks(np.arange(-.5, grid.shape[0]), minor=True)
    ax.grid(which='minor', color=SURFACE, linewidth=2)
    ax.tick_params(which='minor', length=0)
    ax.set_title('近 5 期：每期報酬減 0050（百分點）', loc='left', color=INK, fontsize=14, fontweight='bold',
                 pad=48)
    fig.text(.01, .012, '藍＝贏 0050、紅＝輸；依 5 期平均排序；名稱藍色＝正式 K2（22 檔）、橘色＝本輪新候選；W3–W5 為已看過期間',
             color=MUTED, fontsize=9)
    fig.tight_layout(rect=(0, .03, .93, 1))
    fig.savefig(OUT / 'recent5_all.png', dpi=180, facecolor=SURFACE)
    plt.close(fig)


FOCUS = ['K2b', 'T2', 'C1', 'K3w_05', 'mom20']
FOCUS_LABEL = {'K2b': 'K2 22 檔（正式）', 'T2': 'T2 大型股＋投信', 'C1': 'C1 台積電＋大型股',
               'K3w_05': 'K3w 動能＋營收', 'mom20': 'Mom20（舊正式）', 'legacy': '舊策略 13 種平均'}


def recent5_simple():
    """Readable version: the production strategy, three candidates, the old one and a legacy average."""
    d = with_extra(pd.read_csv(HERE / 'recent5.csv'), recent=True)
    d = d[d.strategy != '0050']
    windows = d.groupby('window')[['start', 'end']].first().sort_index()
    grid = d.pivot(index='strategy', columns='window', values='delta_vs_0050').reindex(columns=windows.index) * 100
    legacy = grid.loc[[s for s in grid.index if d.loc[d.strategy == s, 'group'].iloc[0] == 'legacy']].mean()
    grid = pd.concat([grid.loc[FOCUS], legacy.rename('legacy').to_frame().T])
    cmap = LinearSegmentedColormap.from_list('div', ['#e34948', '#f0efec', '#2a78d6'])
    bound = 11.
    fig, ax = plt.subplots(figsize=(10, 5.4), facecolor=SURFACE)
    ax.imshow(grid.values, cmap=cmap, norm=TwoSlopeNorm(0, -bound, bound), aspect='auto')
    n_rows, n_cols = grid.shape
    for i, s in enumerate(grid.index):
        bold = 'bold' if s == OURS_ID else 'normal'
        for j in range(n_cols):
            v = grid.values[i, j]
            ax.text(j, i, f'{v:+.1f}', ha='center', va='center', fontsize=13, fontweight=bold,
                    color='#ffffff' if abs(v) > .6 * bound else INK)
        mean, wins = grid.values[i].mean(), int((grid.values[i] > 0).sum())
        ax.text(n_cols - .2, i, f'{mean:+.1f}', ha='center', va='center', fontsize=13, fontweight=bold, color=INK)
        ax.text(n_cols + .55, i, f'{wins} / 5', ha='center', va='center', fontsize=13, fontweight=bold, color=INK)
    for x, t in ((n_cols - .2, '平均'), (n_cols + .55, '贏幾期')):
        ax.text(x, -.75, t, ha='center', va='center', fontsize=11, color=MUTED)
    ax.set_xticks(range(n_cols), [f'{int(s[5:7])}/{int(s[8:])}–{int(e[5:7])}/{int(e[8:])}'
                                  for s, e in windows.values], fontsize=11)
    ax.xaxis.tick_top()
    ax.set_yticks(range(n_rows), [FOCUS_LABEL[s] for s in grid.index], fontsize=12)
    for tick, s in zip(ax.get_yticklabels(), grid.index):
        tick.set_fontweight('bold' if s == OURS_ID else 'normal')
        tick.set_color(OURS if s == OURS_ID else MUTED if s == 'legacy' else INK)
    ax.set_xlim(-.5, n_cols + 1)
    ax.tick_params(length=0)
    for side in ax.spines.values():
        side.set_visible(False)
    ax.set_xticks(np.arange(-.5, n_cols), minor=True)
    ax.set_yticks(np.arange(-.5, n_rows), minor=True)
    ax.grid(which='minor', color=SURFACE, linewidth=3)
    ax.tick_params(which='minor', length=0)
    ax.set_title('近 5 期：每期比 0050 多賺（藍）或少賺（紅）幾個百分點', loc='left', color=INK,
                 fontsize=14, fontweight='bold', pad=36)
    fig.text(.01, .015, '每期 24 個交易日、起始全現金；含費稅；後 3 期為已看過期間；完整 22 種策略見 recent5_all.png',
             color=MUTED, fontsize=9)
    fig.tight_layout(rect=(0, .04, 1, 1))
    fig.savefig(OUT / 'recent5.png', dpi=180, facecolor=SURFACE)
    plt.close(fig)


def holdout40():
    h = with_extra(pd.read_csv(HERE / 'holdout40.csv'), recent=False)
    assert all((h.strategy == s).sum() == 40 for s in EXTRA_IDS)
    s = h.groupby('strategy').terminal_return.mean().rename('mean_return').reset_index()
    ref = float(s.loc[s.strategy == '0050', 'mean_return'].iloc[0])
    s = s[s.strategy != '0050'].assign(group=lambda x: np.where(x.strategy.isin(['T2', *EXTRA_IDS]), 'new', 'legacy'))
    fig, ax = plt.subplots(figsize=(9, 7), facecolor=SURFACE)
    bars(ax, s, 'mean_return', ref, f'0050 {100 * ref:+.2f}%')
    ax.set_title('2025–2026 每 24 個交易日的平均報酬', loc='left', color=INK, fontsize=14, fontweight='bold', pad=30)
    legend(fig)
    fig.text(.01, .012, '40 個窗口，每月月初、月中起跑；全部用同一份修復資料重算；holdout 已用過、股票池為 2026 名單',
             color=MUTED, fontsize=9)
    fig.tight_layout(rect=(0, .03, 1, 1))
    fig.savefig(OUT / 'holdout40.png', dpi=180, facecolor=SURFACE)
    plt.close(fig)


if __name__ == '__main__':
    OUT.mkdir(exist_ok=True)
    latest24()
    recent5()
    recent5_simple()
    holdout40()
    print(sorted(p.name for p in OUT.glob('*.png')))
