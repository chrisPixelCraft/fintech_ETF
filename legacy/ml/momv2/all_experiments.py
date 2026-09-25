"""Every experiment in one table (report only): research/results/all_experiments.md.

    .venv/bin/python -m momv2.all_experiments

Reads each study's committed result files; each row is paired with Mom20 on
that study's own windows (the period column says which). Nothing here runs a
backtest or changes a decision.
"""
from __future__ import annotations

import json

import pandas as pd

from competition.rules import ROOT

R = ROOT / 'research/results'


def pct(x):
    return '—' if x is None or pd.isna(x) else f'{x:+.2%}'


def row(study, setting, period, mean, delta, win, turnover, role):
    return dict(study=study, setting=setting, period=period, mean=mean, delta=delta, win=win, turnover=turnover,
                role=role)


def from_csv(path, study, period, key, names, role, stage=None):
    f = pd.read_csv(path)
    if stage:
        f = f[f.stage == stage]
    f = f.set_index(key)
    return [row(study, label, period, f.loc[n, 'mean'], f.loc[n, 'paired_mean_delta'], f.loc[n, 'paired_win_rate'],
                f.loc[n, 'turnover'], role(n) if callable(role) else role) for n, label in names.items()]


def load(name):
    return json.loads((R / name).read_text())


def collect() -> list[dict]:
    rows = []
    rows += from_csv(R / 'lgbm_jpx2/comparison.csv', 'LightGBM：JPX #2 基準', '2019–2021（70）', 'strategy',
                     {'lgbm_jpx2': 'LightGBM（JPX #2 特徵）', 'autots': 'AutoTS 基準'}, '停止', stage='full')
    rows += from_csv(R / 'lgbm_alpha/comparison.csv', 'LightGBM：相對強弱標籤', '2019–2021（70）', 'strategy',
                     {'lgbm_alpha': '標籤減市場平均'}, '停止', stage='full')
    rows += from_csv(R / 'target_alignment/comparison.csv', 'LightGBM：成交對齊標籤', '2019–2021（70）', 'strategy',
                     {'lgbm_execution_alpha': '隔天成交價進場的標籤'}, '停止', stage='full')
    rows += from_csv(R / 'data_usage/comparison.csv', 'LightGBM：資料使用', '2020–2021（46）', 'strategy',
                     {'lgbm_control': '對照組', 'lgbm_relaxed_volatility': '放寬波動限制',
                      'lgbm_refit_on_all': 'C：用 100% 資料重訓', 'lgbm_both': '兩者都做'},
                     lambda n: '採用 C' if n == 'lgbm_refit_on_all' else '診斷', stage='full')
    best = load('tune_quick/summary.json')['best']
    for key, period in (('select', '2010–2024（8）'), ('test', '2025–2026（4）')):
        m = best[key]
        rows.append(row('AutoTS 調參（quick）', f'最佳設定 {best["config"]}', period, m['mean_return'], m['mean_excess'],
                        m['win_rate'], m['mean_turnover'], '樣本太小，未採用'))
    ft = pd.read_csv(R / 'final_test/comparison.csv').set_index('setting')
    for s in ft.index:
        if s == 'momentum_20':
            continue
        rows.append(row('最終測試（21 組）', s, '2025–2026（40）', ft.loc[s, 'mean'], ft.loc[s, 'paired_mean_delta'],
                        ft.loc[s, 'paired_win_rate'], ft.loc[s, 'turnover'], '輸'))
    sweep = pd.read_csv(R / 'momentum_sweep/dev.csv').set_index('candidate')
    for c in sweep.index.drop('mom20'):
        rows.append(row('P1 動能參數（DEV）', c, '2019–2021（70）', sweep.loc[c, 'mean'], sweep.loc[c, 'paired_mean_delta'],
                        sweep.loc[c, 'paired_win_rate'], sweep.loc[c, 'turnover'], '選出 mom30' if c == 'mom30' else '網格'))
    val = load('momentum_sweep/validation.json')['table']['mom30']
    rows.append(row('P1 動能參數（validation）', 'mom30', '2022–2024（70）', val['mean'], val['paired_mean_delta'],
                    val['paired_win_rate'], val['turnover'], '未過 +0.5%'))
    hy = pd.read_csv(R / 'hybrid/comparison.csv').set_index('strategy')
    for s in ('hybrid_v2A', 'hybrid_v2B'):
        rows.append(row('Hybrid', s, '2015–2024（236）', hy.loc[s, 'mean'], hy.loc[s, 'paired_mean_delta'],
                        hy.loc[s, 'paired_win_rate'], hy.loc[s, 'turnover'], '未過'))
    mv = pd.read_csv(R / 'momentum_v2/dev.csv').set_index('candidate')
    chosen = {'h1_resid60_beta120', 'h2_turnover20', 'h3_revenueyoy'}
    for c in mv.index.drop('mom20'):
        rows.append(row('Momentum-v2（DEV 調參）', c, '2015–2021（166）', mv.loc[c, 'mean'], mv.loc[c, 'paired_mean_delta'],
                        mv.loc[c, 'paired_win_rate'], mv.loc[c, 'turnover'], '選出' if c in chosen else '網格'))
    vv = load('momentum_v2/validation_verdict.json')['table']
    for c, role in (('composite', '決策對象：未過'), ('h1', '診斷'), ('h2', '診斷'), ('h3', '診斷')):
        m = vv[c]
        rows.append(row('Momentum-v2（validation）', c, '2022–2024（70）', m['mean'], m['paired_mean_delta'],
                        m['paired_win_rate'], m['turnover'], role))
    h1 = pd.read_csv(R / 'h1_residual/dev.csv').set_index('candidate')
    for c in h1.index.drop('mom20'):
        rows.append(row('H1 residual（DEV 調參）', c, '2015–2021（166）', h1.loc[c, 'mean'], h1.loc[c, 'paired_mean_delta'],
                        h1.loc[c, 'paired_win_rate'], h1.loc[c, 'turnover'],
                        '選出' if c == 'h1_resid120_beta250' else '網格'))
    for f, period, role in (('validation_sanity.json', '2022–2024（70）', 'sanity 通過'),
                            ('test_verdict.json', '2025–2026（40）', '決策：STOP')):
        m = load(f'h1_residual/{f}')['table']['h1study']
        rows.append(row('H1 residual', 'resid 120／β 250', period, m['mean'], m['paired_mean_delta'],
                        m['paired_win_rate'], m['turnover'], role))
    m25 = load('mom25_report/report.json')
    for split, period in (('dev', '2015–2021（166）'), ('validation', '2022–2024（70）'), ('holdout', '2025–2026（40）')):
        m = m25[split]['mom25']
        rows.append(row('Mom25（只看不選）', 'mom25', period, m['mean'], m['paired_mean_delta'], m['paired_win_rate'],
                        m['turnover'], '打平'))
    rf = load('residual_fixes/report.json')['table']
    for c in ('resid20', 'resid25', 'ew20', 'ew25', 'ind20', 'ind25', 'shrink20', 'shrink25'):
        m = rf[c]['全期']
        rows.append(row('Residual 與修正', c, '2015–2026（276）', m['mean'], m['delta_mom'], m['win_mom'], m['turnover'],
                        '規則選出 ew20（打平）' if c == 'ew20' else '對同長度 mom'))
    for study, path, formal in (('技術指標重排', 'technical_momentum/verdict.json', 'tech_composite'),
                                ('多尺度 MACD', 'macd_momentum/verdict.json', 'macd_combined'),
                                ('台積電核心', 'tsmc_core/verdict.json', 'tsmc_formal')):
        t = load(path)['table']
        for n, m in t.items():
            if n.startswith('mom20'):
                continue
            rows.append(row(study, n.replace('tech_', ''), '2015–2026（276）', m['mean'], m['mean_delta'], m['win'],
                            m['turnover'], '決策對象：STOP' if n == formal else '診斷'))
    return rows


def full_table(rows) -> str:
    lines = ['| 研究 | 設定 | 評估期間（窗口） | 平均 | 對 Mom20 Δ | 勝率 | 周轉 | 角色／結論 |',
             '|---|---|---|---:|---:|---:|---:|---|']
    for r in rows:
        lines.append(f'| {r["study"]} | `{r["setting"]}` | {r["period"]} | {pct(r["mean"])} | {pct(r["delta"])} | '
                     f'{"—" if r["win"] is None or pd.isna(r["win"]) else format(r["win"], ".0%")} | '
                     f'{"—" if r["turnover"] is None or pd.isna(r["turnover"]) else format(r["turnover"], ".2f")} | {r["role"]} |')
    return '\n'.join(lines)


def main():
    rows = collect()
    body = ['# 全部實驗', '',
            '- 由 `python -m momv2.all_experiments` 從各研究的結果檔產生',
            '- 每列都和同期的 Mom20 逐窗口配對；Δ = 平均報酬差',
            '- 期間不同的列不能直接互比', '', full_table(rows), '']
    (R / 'all_experiments.md').write_text('\n'.join(body))
    print(len(rows), 'rows')


if __name__ == '__main__':
    main()
