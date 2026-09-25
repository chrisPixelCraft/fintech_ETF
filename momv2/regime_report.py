"""Report-only: when does market-residual momentum beat plain momentum? (mom20, mom25, resid20, resid25)

    .venv/bin/python -m momv2.regime_report

Uses finished runs in research/runs/momentum_v2 (resid = h1 with beta 60).
Every window is described by its state at D-1 (the session before its first
day), plus the 0050 return during the window (after the fact, descriptive
only). Windows are pooled over 2015-2026 and split into terciles per state
variable. Exploratory: many cuts on the same windows, nothing here selects a
strategy. Output: research/results/residual_regimes/.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from competition.data import load_market
from competition.rules import ROOT
from momv2.evaluate import complete
from research.lgbm_jpx2 import pct

RESULTS = ROOT / 'research/results/residual_regimes'
RUNS = dict(mom20='mom20', mom25='mom25', resid20='h1_resid20_beta60', resid25='h1_resid25_beta60')
PAIRS = {'resid20 − mom20': ('resid20', 'mom20'), 'resid25 − mom25': ('resid25', 'mom25'),
         'mom25 − mom20': ('mom25', 'mom20'), 'resid25 − mom20': ('resid25', 'mom20')}
PERIODS = {'2015–2021': 'dev', '2022–2024': 'validation', '2025–2026/09': 'holdout'}
STATES = {
    'mkt_r20': '0050 近 20 日報酬',
    'mkt_r60': '0050 近 60 日報酬',
    'mkt_vol20': '0050 近 20 日波動（年化）',
    'dispersion': '個股 20 日報酬的橫斷面標準差',
    'market_r2': '個股對 0050 的平均 R²（60 日）',
    'beta_tilt': 'Mom20 前 25 名平均 β − 全體平均 β',
    'mkt_during': '窗口期間 0050 報酬（事後，只描述）',
}


def windows() -> pd.DataFrame:
    rows = []
    for period, split in PERIODS.items():
        runs = {k: complete(v, split) for k, v in RUNS.items()}
        common = set.intersection(*(set(r) for r in runs.values()))
        for eid in sorted(common):
            s = runs['mom20'][eid]
            rows.append(dict(episode=eid, period=period, start=pd.Timestamp(s['start']), end=pd.Timestamp(s['end']),
                             **{k: runs[k][eid]['terminal_return'] for k in RUNS},
                             **{f'turnover_{k}': runs[k][eid]['turnover'] for k in RUNS}))
    return pd.DataFrame(rows)


def states(frame: pd.DataFrame) -> pd.DataFrame:
    m = load_market()
    lr, lm = np.log1p(m.ret), np.log1p(m.benchmark_ret)
    roll = lambda x, w: x.rolling(w, min_periods=int(.8 * w))
    mean_x, mean_m = roll(lr, 60).mean(), roll(lm, 60).mean()
    cov = roll(lr.mul(lm, axis=0), 60).mean() - mean_x.mul(mean_m, axis=0)
    var_m = roll(lm, 60).var(ddof=0)
    var_x = roll(lr, 60).var(ddof=0)
    beta = cov.div(var_m, axis=0)
    r2 = (cov ** 2).div(var_x.mul(var_m, axis=0))
    r20 = roll(lr, 20).sum()
    out = []
    for _, w in frame.iterrows():
        i = m.calendar.get_loc(w.start) - 1                   # D-1
        d = m.calendar[i]
        ok = m.valid.iloc[max(0, i - 19):i + 1].all()
        top = r20.loc[d][ok].nlargest(25).index
        out.append(dict(
            mkt_r20=float(np.expm1(lm.iloc[i - 19:i + 1].sum())),
            mkt_r60=float(np.expm1(lm.iloc[i - 59:i + 1].sum())),
            mkt_vol20=float(lm.iloc[i - 19:i + 1].std() * np.sqrt(250)),
            dispersion=float(r20.loc[d][ok].std()),
            market_r2=float(r2.loc[d][ok].mean()),
            beta_tilt=float(beta.loc[d][top].mean() - beta.loc[d][ok].mean()),
            mkt_during=float(np.expm1(lm.loc[w.start:w.end].sum()))))
    return pd.concat([frame.reset_index(drop=True), pd.DataFrame(out)], axis=1)


def analyse(frame: pd.DataFrame) -> dict:
    for name, (a, b) in PAIRS.items():
        frame[name] = frame[a] - frame[b]
    periods = {}
    for period, g in [*frame.groupby('period', sort=False), ('全部', frame)]:
        periods[period] = dict(
            n=len(g), mean={k: float(g[k].mean()) for k in RUNS}, median={k: float(g[k].median()) for k in RUNS},
            turnover={k: float(g[f'turnover_{k}'].mean()) for k in RUNS},
            delta={p: dict(mean=float(g[p].mean()), median=float(g[p].median()), win=float((g[p] > 0).mean()))
                   for p in PAIRS})
    regimes = {}
    for state in STATES:
        cuts = frame[state].quantile([1 / 3, 2 / 3]).to_numpy()
        bucket = np.digitize(frame[state], cuts)
        regimes[state] = dict(
            cuts=[float(c) for c in cuts],
            spearman={p: float(frame[state].corr(frame[p], method='spearman')) for p in PAIRS},
            terciles=[dict(n=int((bucket == k).sum()), state_mean=float(frame[state][bucket == k].mean()),
                           **{p: float(frame[p][bucket == k].mean()) for p in PAIRS},
                           **{f'win {p}': float((frame[p][bucket == k] > 0).mean()) for p in PAIRS},
                           periods={per: int(((bucket == k) & (frame.period == per)).sum()) for per in PERIODS})
                      for k in range(3)])
    return dict(periods=periods, regimes=regimes)


def summary_md(result: dict) -> str:
    lines = ['# Residual 什麼時候贏？mom20、mom25、resid20、resid25', '',
             '- **只看不選**：所有期間都已看過，不用來換 production',
             '- resid = 近 N 日 Σ(lr − β × lm)，β 為 60 日；組合層與 production 相同；逐窗口配對', '',
             '## 1. 各期間', '',
             '| 期間 | 窗口 | mom20 | mom25 | resid20 | resid25 | resid20 − mom20 | resid25 − mom25 | 周轉 m20／m25／r20／r25 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---|']
    for period, p in result['periods'].items():
        d = p['delta']
        lines.append(f'| {period} | {p["n"]} | ' + ' | '.join(pct(p['mean'][k]) for k in RUNS) + ' | ' +
                     f'{pct(d["resid20 − mom20"]["mean"])}（勝 {d["resid20 − mom20"]["win"]:.0%}） | '
                     f'{pct(d["resid25 − mom25"]["mean"])}（勝 {d["resid25 − mom25"]["win"]:.0%}） | ' +
                     '／'.join(f'{p["turnover"][k]:.2f}' for k in RUNS) + ' |')
    lines += ['', '數字為各策略窗口平均報酬；Δ 為配對平均（括號為勝率）。', '',
              '## 2. 依窗口開始前的市場狀態分三組（2015–2026 全部窗口）', '',
              '探索性分析：同一批窗口切很多種，單一格子的差距可能是雜訊；看的是多個變數是否指向同一個機制。', '']
    for state, r in result['regimes'].items():
        lines += [f'### {STATES[state]}（`{state}`）', '',
                  '| 組 | 窗口 | 狀態平均 | resid20 − mom20 | resid25 − mom25 | mom25 − mom20 | 各期間窗口數 |',
                  '|---|---:|---:|---:|---:|---:|---|']
        for k, t in enumerate(r['terciles']):
            fmt = (lambda v: f'{v:+.2f}') if state in ('beta_tilt', 'market_r2') else pct
            lines.append(f'| {("低", "中", "高")[k]} | {t["n"]} | {fmt(t["state_mean"])} | '
                         f'{pct(t["resid20 − mom20"])}（勝 {t["win resid20 − mom20"]:.0%}） | '
                         f'{pct(t["resid25 − mom25"])}（勝 {t["win resid25 − mom25"]:.0%}） | {pct(t["mom25 − mom20"])} | '
                         + '／'.join(str(v) for v in t['periods'].values()) + ' |')
        s = r['spearman']
        lines += ['', f'Spearman 相關：resid20 − mom20 {s["resid20 − mom20"]:+.2f}，resid25 − mom25 {s["resid25 − mom25"]:+.2f}', '']
    return '\n'.join(lines)


def main():
    frame = states(windows())
    result = analyse(frame)
    RESULTS.mkdir(parents=True, exist_ok=True)
    frame.to_csv(RESULTS / 'windows.csv', index=False)
    (RESULTS / 'report.json').write_text(json.dumps(result, indent=1, default=float))
    (RESULTS / 'summary.md').write_text(summary_md(result))
    print(summary_md(result))


if __name__ == '__main__':
    main()
