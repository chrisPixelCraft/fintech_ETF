# 24-Day Competition Strategy Tuning Protocol

以 frozen `x0352_daily_baseline` 為起點，選出適用 AI CUP 約 24 交易日的固定參數：NT$1B、零持股起步，最大化 robust terminal NAV。主目標不是 2010–2026 cumulative return、CAGR 或單一最好月份。

## 1. Tuning skill 與邊界

優先用已安裝的 repository / agent tuning skill，處理 parameter / grid / brute-force search、pruning、local refinement、experiment tracking、parallel execution、caching、resume、result comparison。

本 spec 固定 objective、competition constraints、train/validation/holdout split、look-ahead rule、execution assumptions；skill 不得更改。

## 2. Baseline：先重現再調參

```text
x0352_daily_baseline
target_count = 20
return_short = 10
return_long = 30
ema_fast = 10
ema_slow = 30
macd = 8 / 21 / 5
momentum_weight = 0.55
long_return_fraction = 0.20
max_replacements_per_day = 0
replacement_margin = 0.20
volatility_spike_ratio = 2.0
one_day_chase_return = 0.04
volume_low = 0.8
volume_high = 2.5
cash_guard_ratio = 0.12
```

## 3. Data Split

Yahoo daily：2009-01-01 → latest complete trading day in 2026-09。

| 切分 | 期間 | 允許用途 |
|---|---|---|
| Development | 2010–2018 | 大量調參 |
| Validation | 2019–2022 | 選 candidate、有限 local refinement |
| Holdout（FREEZE 後） | 2023–2024 | 禁調參 |
| Recent stress test | 2025–2026/09 | 禁調參；只診斷 regime 崩潰 |

## 4. Evaluation Unit

每個 episode 從 NT$1,000,000,000、zero holdings 開始，跑 24 trading sessions，記 terminal NAV；不以 16 年連續帳本為主目標。

Primary episodes 從每月第一交易日起跑；另做 rolling 24D windows robustness stress test。重疊窗口不算獨立 statistical samples。

## 5. Primary Metrics

```text
median_24d_return, mean_24d_return
p25_24d_return, p10_24d_return, worst_24d_return
positive_episode_rate
median_mdd, worst_mdd
valid_episode_rate, compliance_pass_rate
median_turnover, median_days_to_20_holdings
```

## 6. Candidate Selection Policy

採 lexicographic selection，不用 argmax(mean return)：

1. 淘汰 compliance / feasibility 過差者。
2. median_24d_return 最大。
3. p25_return 最大。
4. p10_return 較佳。
5. median MDD 較低。
6. mean return 較高。
7. turnover 較低。

接近時選 simpler、more stable、closer to x0352。

## 7. Tuning Strategy

Baseline → Phase A structural → B signal horizon → C turnover / replacement → D score weighting → E risk / entry filters → F joint brute-force refinement → Validation → Freeze → Holdout。

## 8. Phase A — Structural Search

只動結構，先比較 20 檔集中與 25–30 檔分散、不換股與短期換 winner；保留 top 20–30% 進下一階段。

```text
target_count: 20, 22, 25, 28, 30
max_replacements_per_day: 0, 1, 2, 4
```

## 9. Phase B — Signal Horizon Search

最重要的一輪：用 coarse grid + pruning 找 24-day horizon 的 signal speed。

```text
return_short: 3, 5, 7, 10, 15, 20
return_long: 15, 20, 30, 40, 50, 60
只保留 short < long
EMA: 5 / 15; 5 / 20; 8 / 21; 10 / 30; 15 / 40; 20 / 50
MACD: 5 / 13 / 4; 6 / 13 / 4; 8 / 21 / 5; 12 / 26 / 9; 16 / 35 / 9
```

## 10. Phase C — Replacement / Turnover

```text
max_replacements_per_day: 0, 1, 2, 4
replacement_margin: 0, 0.025, 0.05, 0.10, 0.20, 0.30
```

`replacement_margin` 只在 max_replacements_per_day > 0 有效。重點比較 0 replacement 與 1 replacement/day：短賽期可能需要 rotate winners，但額外換股若未提升 median return 或 P25，就不採用。

## 11. Phase D — Ranking Weights

只調 mixture，不改 score family。

```text
momentum_weight: 0.40, 0.50, 0.55, 0.65, 0.75, 0.85, 0.95
long_return_fraction: 0, 0.10, 0.20, 0.35, 0.50, 0.70
short momentum = m × (1-q)
long momentum = m × q
volume = (1-m) × 0.4
MACD = (1-m) × 0.2
trend = (1-m) × 0.2
long trend = (1-m) × 0.2
```

## 12. Phase E — Risk / Entry Filters

```text
one_day_chase_return: 0.03, 0.04, 0.055, 0.07, 0.085, 0.10
volatility_spike_ratio: 1.5, 2.0, 2.5, 3.0, 4.0
volume_low: 0.2, 0.5, 0.8, 1.0
volume_high: 2, 2.5, 3, 5, 8
cash_guard_ratio: 0.08, 0.10, 0.12, 0.15, 0.18
```

同看 return、candidate availability、days to 20 holdings、compliance；避免過嚴 filter 造成 FAIL_TOO_FEW_ELIGIBLE，而非只求降低風險。

## 13. Phase F — Brute-Force Refinement

前五階段後取 Top 5–10 parameter regions，只在縮小範圍做 Cartesian grid brute-force。例如：

```text
最佳附近：return = 5 / 20; EMA = 8 / 21; MACD = 8 / 21 / 5
replacement = 1; momentum_weight = 0.65
局部搜尋：
return_short: 3 / 5 / 7
return_long: 15 / 20 / 25
EMA: 5/20, 8/21, 10/25
replacement: 0 / 1 / 2
momentum_weight: 0.55 / 0.65 / 0.75
```

## 14. Optional Global Search

資源足夠可另跑 random search、Sobol、Bayesian optimization 或 TPE，由 skill 選方法；沿用 spec bounds，禁止無限擴張。用途是找 staged search 遺漏的跨參數 interaction，不取代 staged search。

## 15. Comparison of Search Methods

相同 validation episodes 比較 x0352 baseline、staged tuning winner、local brute-force winner、global tuning-skill winner；不可直接比較看過不同資料的 optimizer。

## 16. Validation Selection

Development 後取 Top ~10–20 candidates，重跑 2019–2022 validation；不能直接採開發第一名。選兩期皆好且 parameter behavior 穩定者，validation 明顯崩潰則 reject。

## 17. Parameter Stability

最終候選做 neighborhood test。例如 momentum_weight = 0.65，另測 0.60、0.70；若只有 0.65 好、兩側崩潰，標 PARAMETER_FRAGILE。優先 performance plateau。

## 18. Freeze

Validation 後只選 ONE strategy：competition_24d_candidate，保存 configs/competition_24d_final.json，記錄 parameter values、selection date、training period、validation period、number of candidates tried、objective、code commit、SHA256，隨即 FREEZE。

## 19. Holdout

Freeze 後才跑 2023–2024，輸出 median、mean、P25、P10、worst、positive rate、MDD、compliance。結果差可否決策略，不能重調後仍稱 holdout；若重新設計，須明記 2023–2024 改作 development data。

## 20. Recent Stress Test

最後跑 2025-01 → 2026-09 latest complete trading day，比較 x0352 與 competition_24d_final；檢查 median 24D、P25、worst、Oct-Nov analogue、recent 6 months、days to deploy capital。此為最接近賽前 regime，不得自動 tuning。

## 21. October–November Analogue

2010–2025 各年 first trading day >= Oct 26 → next 24 sessions；僅 secondary metric，不單獨 optimize。

## 22. Early Pruning

Development 的 compliance rate 太低、median 明顯低於 baseline 或 P25 明顯惡化，可 early stop。例如 valid_episode_rate < 90% → prune；median return < baseline median - tolerance → prune。Tolerance 由 skill 依 sample size 設定。

## 23. Stop Condition

任一成立即停：連續兩輪 refinement 的 median 提升 < 0.2 percentage point 且 P25 無明顯改善；附近形成穩定 plateau；或 validation 不再改善。

## 24. What NOT to Optimize

禁止以 highest 16-year cumulative return、highest single-month / single 24D return、2025–2026 alone、October–November alone、Development mean return alone 選 winner。

## 25. Final Decision

平衡 24D median、lower tail、compliance、drawdown、parameter stability、validation generalization。選擇在未知 2026/10/26–11/27 regime 最有機會穩定產生高 terminal NAV 的固定策略，而非最大可能收益。

## 26. Required Final Table

| Strategy | Median 24D | P25 | P10 | Worst | Positive % | Median MDD | Valid % |
|---|---:|---:|---:|---:|---:|---:|---:|
| x0352 | | | | | | | |
| Staged winner | | | | | | | |
| Brute-force winner | | | | | | | |
| Global skill winner | | | | | | | |

分列 Development、Validation、Holdout、2025–2026/09 stress、Oct-Nov analogues。

## 27. Final Agent Workflow

reproduce x0352 → build 24D evaluation harness → Phase A structural → B signal horizons → C replacements → D score weights → E filters → local brute force → optional global tuning-skill search → Development shortlist → Validation → parameter stability test → FREEZE → 2023–2024 Holdout → 2025–2026/09 stress test → Oct-Nov analogues → final report。

積極用 skill 提升執行與搜尋效率；不得越過上述實驗邊界。
