"""Why a TSMC core barely helps (report only): writes research/results/tsmc_core/analysis.md.

    .venv/bin/python -m momv2.tsmc_analysis

Reads the finished TSMC Core runs (research/results/tsmc_core/windows.csv and
the run folders); nothing here selects or changes a strategy.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from competition.data import load_market
from competition.rules import ROOT
from momv2.evaluate import run_dir
from research.lgbm_jpx2 import pct

RESULTS = ROOT / 'research/results/tsmc_core'
BLOCKS = {'2015–2018': ('2015', '2018'), '2019–2021': ('2019', '2021'), '2022–2024': ('2022', '2024'),
          '2025–2026': ('2025', '2026')}
PAIRS = [('tsmc_formal', 'tsmc_core_equal', '0.85／0.15 權重（台積電核心不變）'),
         ('tsmc_core_equal', 'mom20', '台積電 22.5% 核心（等權不變）'),
         ('tsmc_blend_only', 'mom20', '0.85／0.15 權重（無核心）')]


def block(f, start, end):
    return f[(f.start >= start) & (f.start <= f'{end}-12-31')]


def main():
    f = pd.read_csv(RESULTS / 'windows.csv', parse_dates=['start', 'end'])
    lm = np.log1p(load_market().ret['2330.TW'])
    f['tsmc'] = f.apply(lambda r: float(np.expm1(lm.loc[r.start:r.end].sum())), axis=1)
    held = total = 0
    for split in ('dev', 'validation', 'holdout'):
        for path in run_dir('mom20', split).glob('episodes/*/holdings.csv'):
            h = pd.read_csv(path)
            total += h.date.nunique()
            held += h[h.symbol == '2330.TW'].date.nunique()
    price = np.exp(lm.cumsum())

    lines = ['# 為什麼台積電核心幫助很小', '',
             '- 只解釋 [TSMC Core](summary.md) 的結果，不改任何判定',
             '- 由 `python -m momv2.tsmc_analysis` 產生', '',
             '## 結論', '',
             '加碼台積電的比較對象不是 0，而是 Mom20 選出的其他股票。台積電要漲得比它們多，加碼才有用。',
             '2015–2024 台積電小贏，加碼約 +0.1~0.2%；2025–2026 Mom20 的持股漲得比台積電多，加碼約 −1%。', '',
             '## 台積電 vs Mom20 組合（每個 24 日窗口）', '',
             '| 期間 | 台積電累積漲幅 | 台積電平均 | Mom20 組合平均 | 台積電贏的比例 |', '|---|---:|---:|---:|---:|']
    for label, (a, b) in BLOCKS.items():
        g = block(f, a, b)
        s = price.loc[f'{a}-01-01':f'{b}-12-31']
        lines.append(f'| {label} | {s.iloc[-1] / s.iloc[0] - 1:+.0%} | {pct(g.tsmc.mean())} | {pct(g.mom20.mean())} | '
                     f'{(g.tsmc > g.mom20).mean():.0%} |')
    lines += ['', f'- Mom20 本來就有 {held / total:.0%} 的日子持有台積電（排進前 25 名時）',
              '- 加碼部位約 19%（3.8% → 22.5%），所以效果 ≈ 0.19 × 兩者差距', '',
              '## 拆解：好壞來自哪裡', '',
              '| 比較 | 平均 Δ | 95% CI | ' + ' | '.join(BLOCKS) + ' | 周轉 | 警告 |',
              '|---|---:|---:|' + '---:|' * len(BLOCKS) + '---:|---:|']
    for a, b, label in PAIRS:
        d = (f[a] - f[b]).to_numpy()
        boot = d[np.random.default_rng(0).integers(0, len(d), (10000, len(d)))].mean(1)
        seg = ' | '.join(pct((block(f, x, y)[a] - block(f, x, y)[b]).mean()) for x, y in BLOCKS.values())
        lines.append(f'| {label} | {pct(d.mean())} | {pct(np.percentile(boot, 2.5))} ~ {pct(np.percentile(boot, 97.5))} | '
                     f'{seg} | {f[a + "|turnover"].mean():.2f} vs {f[b + "|turnover"].mean():.2f} | '
                     f'{f[a + "|warn"].sum()} vs {f[b + "|warn"].sum()} |')
    lines += ['', '- 台積電核心本身接近中性，換手還比 Mom20 低',
              '- 0.85／0.15 權重在兩種情況都拖累',
              '  - 換手與成本上升',
              '  - 高分股接近 9% 上限，成交後超過 10%，警告增加', '']
    (RESULTS / 'analysis.md').write_text('\n'.join(lines))
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
