"""MACD Momentum and TSMC Core evaluations (docs/macd_momentum_spec.md, docs/tsmc_core_spec.md).

    PYTHONHASHSEED=0 .venv/bin/python -m momv2.macd_tsmc_evaluate --workers 8

Each strategy runs once over every window of 2015-01 .. 2026-09, paired with
Mom20. Gates are fixed in the specs; verdicts are written once. TSMC Core also
gets a daily Active Share check on 2025-2026 against the 2026-09-24 ETF top-10
snapshot. Output: research/results/{macd_momentum,tsmc_core}/.
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from competition.data import load_market
from competition.rules import ROOT
from momv2 import macd as M
from momv2 import tsmc as TS
from momv2.evaluate import REFERENCE, committed, config, git_state, run, run_dir, write_once
from momv2.tech_evaluate import BLOCKS, PRICE_SOURCE, SPLITS, gate, stats, windows
from production import active_share as AS
from research.baselines import MomentumConfig, momentum_score
from research.lgbm_jpx2 import pct

MACD_RESULTS = ROOT / 'research/results/macd_momentum'
TSMC_RESULTS = ROOT / 'research/results/tsmc_core'
MACD_REF = 'mom20_d2013'
ETF_SNAPSHOT = ROOT / 'data/momv2/etf_top10_20260924.csv'


def macd_configs() -> dict:
    base = config(REFERENCE)
    data = {'start': M.DATA_START}
    out = {MACD_REF: dict(base, name=f'momv2_{MACD_REF}', data=data)}
    for v in M.VARIANTS:
        name = 'macd_' + v.replace('scale:', '')
        out[name] = dict(base, name=f'momv2_{name}', strategy='macd', data=data,
                         params=dict(variant=v, portfolio=base['params']['portfolio']))
    return out


def tsmc_configs() -> dict:
    base = config(REFERENCE)
    return {f'tsmc_{v}': dict(base, name=f'momv2_tsmc_{v}', strategy='tsmc',
                              params=dict(variant=v, portfolio=base['params']['portfolio'])) for v in TS.VARIANTS}


# ---------------------------------------------------------------- extra diagnostics

def worst_reference_windows(f: pd.DataFrame, name: str, ref: str, k: int = 10) -> float:
    worst = f.nsmallest(k, ref)
    return float((worst[name] - worst[ref]).mean())


def macd_correlation(starts) -> float:
    market = load_market().since(M.DATA_START)
    frames = M.panels()
    vals = []
    for start in starts:
        d = market.calendar[market.calendar.get_loc(start) - 1]
        view = market.asof(d)
        _, top = M.rerank(view, 'combined', frames)
        mom = momentum_score(view, MomentumConfig())[top]
        vals.append(M.macd_score(d, top, 'combined', frames).rank().corr(mom.rank()))
    return float(np.mean(vals))


def daily_weights(name: str, split: str):
    """(date, {ticker: weight}) at every close of every window of a run."""
    for folder in sorted(run_dir(name, split).glob('episodes/*')):
        ledger = pd.read_csv(folder / 'ledger.csv', usecols=['date', 'nav']).set_index('date').nav
        holdings = pd.read_csv(folder / 'holdings.csv')
        for date, rows in holdings.groupby('date'):
            value = rows.shares * rows.close
            yield folder.name, date, {s.split('.')[0]: float(v / ledger[date]) for s, v in zip(rows.symbol, value)}


def active_share_check(name: str) -> dict:
    etfs = AS.load_etf_holdings(ETF_SNAPSHOT)
    days, low, worst, worst_at = 0, 0, 1., None
    for eid, date, w in daily_weights(name, 'holdout'):
        m = min(AS.active_share(w, e) for e in etfs.values())
        days += 1
        low += m < AS.OFFICIAL_MIN
        if m < worst:
            worst, worst_at = m, f'{eid} {date}'
    return dict(days=days, days_below_20=int(low), minimum=float(worst), minimum_at=worst_at, etfs=len(etfs))


def tsmc_profile(name: str, f: pd.DataFrame) -> dict:
    weights, trims = [], 0
    for split in SPLITS:
        for _, _, w in daily_weights(name, split):
            weights.append(w.get('2330', 0.))
        for log in run_dir(name, split).glob('episodes/*/strategy_log.json'):
            trims += sum(e.get('action') == 'trim' for e in json.loads(log.read_text()))
    weights = np.array(weights)
    market = load_market()
    lm = np.log1p(market.ret['2330.TW'])
    tsmc_ret = f.apply(lambda r: float(np.expm1(lm.loc[r.start:r.end].sum())), axis=1)
    d = f[name] - f[REFERENCE]
    y2022 = (f.start >= '2022-01-01') & (f.start <= '2022-12-31')
    return dict(tsmc_weight_mean=float(weights.mean()), tsmc_weight_max=float(weights.max()),
                days_over_25=int((weights > .25 + 1e-10).sum()), trims=int(trims),
                corr_delta_tsmc_return=float(d.corr(tsmc_ret)),
                delta_when_tsmc_down=float(d[tsmc_ret < 0].mean()), windows_tsmc_down=int((tsmc_ret < 0).sum()),
                delta_when_tsmc_up=float(d[tsmc_ret >= 0].mean()),
                delta_2022=float(d[y2022].mean()), worst_delta=float(d.min()))


# ---------------------------------------------------------------- report

def table_md(t: dict, ref: str, order) -> list[str]:
    lines = ['| 策略 | 平均 | Δ | 中位數 Δ | 95% CI | 勝率 | P10 | 最差 | 周轉 | 成本 | ' + ' | '.join(BLOCKS) +
             ' | ' + ' | '.join(k.split('（')[0] for k in PRICE_SOURCE) + ' |',
             '|---|' + '---:|' * (10 + len(BLOCKS) + len(PRICE_SOURCE))]
    for n in order:
        m, is_ref = t[n], n == ref
        cells = [pct(m['mean']), *(['—'] * 4 if is_ref else
                                   [pct(m['mean_delta']), pct(m['median_delta']),
                                    f'{pct(m["ci95"][0])} ~ {pct(m["ci95"][1])}', f'{m["win"]:.0%}']),
                 pct(m['p10']), pct(m['worst']), f'{m["turnover"]:.2f}', f'{m["cost"]:.2%}',
                 *('—' if is_ref else pct(m[b]) for b in BLOCKS), *('—' if is_ref else pct(m[k]) for k in PRICE_SOURCE)]
        lines.append(f'| {n} | ' + ' | '.join(cells) + ' |')
    return lines


def macd_md(v: dict) -> str:
    t = v['table']
    lines = ['# MACD Momentum：Mom20 前 40 名內用多尺度標準化 MACD 重排', '',
             '- 規則寫死於 [docs/macd_momentum_spec.md](../../../docs/macd_momentum_spec.md)',
             f'- {t[MACD_REF]["n"]} 個 24 日窗口，逐窗口和 Mom20（資料自 2013）配對', '',
             f'## 三尺度 MACD 重排：**{v["decision"]}**', '',
             '- ' + '，'.join(f'{"✓" if ok else "✗"} {k}' for k, ok in v['acceptance']['checks'].items()), '',
             '## 大表', ''] + table_md(t, MACD_REF, [MACD_REF, 'macd_combined', 'macd_fast', 'macd_medium', 'macd_slow'])
    d = v['diagnostics']
    lines += ['', '## 診斷（只解釋）', '',
              f'- 前 40 名內 MACD 分數與 Mom20 的平均 Spearman 相關：{d["corr_with_mom20"]:+.2f}',
              f'- Mom20 最差 10 個窗口的平均 Δ：{pct(d["worst10_delta"])}',
              f'- 去掉 Δ 最大 3 個窗口後的平均 Δ：{pct(t["macd_combined"]["mean_after_drop_top"])}', '']
    return '\n'.join(lines)


def tsmc_md(v: dict) -> str:
    t, p, a = v['table'], v['profile'], v['active_share']
    lines = ['# TSMC Core：台積電 22.5% ＋ 0.85 分數／0.15 反波動加權', '',
             '- 規則寫死於 [docs/tsmc_core_spec.md](../../../docs/tsmc_core_spec.md)',
             '- **後見之明偏誤**：已知台積電 2015–2026 大漲才選它加碼；回測只當風險描述',
             f'- {t[REFERENCE]["n"]} 個 24 日窗口，逐窗口和 Mom20 配對', '',
             f'## 正式方案：**{v["decision"]}**', '',
             '- ' + '，'.join(f'{"✓" if ok else "✗"} {k}' for k, ok in v['acceptance']['checks'].items()), '',
             '## 大表', ''] + table_md(t, REFERENCE, [REFERENCE, 'tsmc_formal', 'tsmc_core_equal', 'tsmc_blend_only'])
    lines += ['', '- tsmc_core_equal：台積電 22.5% ＋ 其餘等權（診斷 A）',
              '- tsmc_blend_only：無台積電核心，25 檔 0.85／0.15 加權（診斷 B）', '',
              '## Active Share（2025–2026 每日，對 2026-09-24 的 30 檔主動 ETF 前 10 大）', '',
              '| 策略 | 天數 | < 20% 的天數 | 最低 | 最低發生在 |', '|---|---:|---:|---:|---|']
    for n, x in a.items():
        lines.append(f'| {n} | {x["days"]} | {x["days_below_20"]} | {x["minimum"]:.1%} | {x["minimum_at"]} |')
    lines += ['', '## 風險描述（正式方案，只報告）', '',
              f'- 台積電權重：平均 {p["tsmc_weight_mean"]:.1%}，最高 {p["tsmc_weight_max"]:.1%}，'
              f'收盤超過 25% 的天數 {p["days_over_25"]}，強制減碼 {p["trims"]} 次',
              f'- 台積電窗口報酬 < 0 的 {p["windows_tsmc_down"]} 個窗口：平均 Δ {pct(p["delta_when_tsmc_down"])}；'
              f'其餘窗口 {pct(p["delta_when_tsmc_up"])}',
              f'- Δ 與台積電窗口報酬的相關：{p["corr_delta_tsmc_return"]:+.2f}',
              f'- 2022 年窗口平均 Δ：{pct(p["delta_2022"])}；最差單一窗口 Δ：{pct(p["worst_delta"])}', '']
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    for spec in ('docs/macd_momentum_spec.md', 'docs/tsmc_core_spec.md'):
        if not committed(ROOT / spec):
            raise SystemExit(f'Commit {spec} first')
    mc, tc = macd_configs(), tsmc_configs()
    for split in SPLITS:
        for name, cfg in mc.items():
            run(name, cfg, split, args.workers)
        run(REFERENCE, config(REFERENCE), split, args.workers)
        for name, cfg in tc.items():
            run(name, cfg, split, args.workers)

    f = windows(list(mc), MACD_REF)
    t = {n: stats(f, n, MACD_REF) for n in mc}
    acc = gate(t['macd_combined'], t[MACD_REF])
    macd_v = dict(decision='PASS' if acc['passed'] else 'STOP', acceptance=acc, table=t,
                  diagnostics=dict(corr_with_mom20=macd_correlation(f.start),
                                   worst10_delta=worst_reference_windows(f, 'macd_combined', MACD_REF)),
                  git=git_state())
    MACD_RESULTS.mkdir(parents=True, exist_ok=True)
    write_once(MACD_RESULTS / 'verdict.json', macd_v, 'decision')
    f.to_csv(MACD_RESULTS / 'windows.csv', index=False)
    (MACD_RESULTS / 'summary.md').write_text(macd_md(macd_v))

    g = windows([REFERENCE, *tc])
    t2 = {n: stats(g, n) for n in [REFERENCE, *tc]}
    acc2 = gate(t2['tsmc_formal'], t2[REFERENCE])
    shares = {n: active_share_check(n) for n in [REFERENCE, *tc]}
    acc2['checks']['Active Share 每天 ≥ 20%'] = shares['tsmc_formal']['days_below_20'] == 0
    acc2['passed'] = all(acc2['checks'].values())
    tsmc_v = dict(decision='PASS' if acc2['passed'] else 'STOP', acceptance=acc2, table=t2, active_share=shares,
                  profile=tsmc_profile('tsmc_formal', g), git=git_state())
    TSMC_RESULTS.mkdir(parents=True, exist_ok=True)
    write_once(TSMC_RESULTS / 'verdict.json', tsmc_v, 'decision')
    g.to_csv(TSMC_RESULTS / 'windows.csv', index=False)
    (TSMC_RESULTS / 'summary.md').write_text(tsmc_md(tsmc_v))
    print(macd_md(macd_v))
    print(tsmc_md(tsmc_v))


if __name__ == '__main__':
    main()
