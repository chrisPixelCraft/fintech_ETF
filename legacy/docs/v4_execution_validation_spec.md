# V4 Execution, Tuning & Validation Spec

## 1. Goal

建立防 hindsight 的 V4 evaluation harness：**Correct simulator first. Strategy tuning second.**

## 2. Phase 0 — Rebuild Execution

先實作 src/v4_execution.py、src/v4_ledger.py，再調 V4。Canonical historical execution：

\[
AveragePrice_t = TradingValue_t / TradingVolume_t
\]

## 3. Historical Data

Signal 可沿用 Yahoo daily history；canonical execution 優先使用 TWSE / TPEx official daily trading value + volume，按上市／上櫃分流。

Cached normalized table 欄位：

```text
date, symbol, open, high, low, close, volume, trading_value
average_execution_price, source, quality_flags
```

官方資料不足須標 official_execution_available = false，不得 silent fallback。可另供 proxy_execution，但報告須分開。

## 4. Execution Comparison

Phase 0 固定 V3 signal 與全部 strategy parameter，只比較 V3 + Open proxy 與 V3 + official average execution，量出舊結論受 Open 假設的影響，再進 Phase 1。

輸出 return difference、MDD difference、turnover、cash violations、weight violations、ranking changes、per-trade execution-price difference。

## 5. Historical Episode Design

每窗 initial cash NT$1B、initial holdings 0、length 24 trading sessions。

| 切分 | 期間 | 用途 |
|---|---|---|
| Development | 2010–2018 | 開發 |
| Validation | 2019–2022 | 驗證 |
| Historical holdout | 2023–2024 | 凍結後評估 |
| Recent stress | 2025–latest complete pre-competition history | 僅 diagnostic |
| Seasonal diagnostic | historical late-Oct → late-Nov 24-session analogues | 僅 diagnostic |

Seasonal / recent 不得反覆選 parameter。

## 6. Rolling Episodes

增加 rolling-start stress test，但 overlapping windows != independent samples。報告須註明，不能當樣本量暴增。

## 7. Search Protocol

Staged search，不直接全域 brute-force：

- Stage A — Execution
	- 固定 V3 strategy。
- Stage B — Architecture
	- 比較 Momentum、Adaptive、Direct。
- Stage C — Horizon
	- 搜尋 3/5/10/20/30/60D。
- Stage D — Rotation / Portfolio
	- holding count、replacement budget、replacement margin、cash target、risk penalty。
- Stage E — Model / Ensemble
	- expert weighting、confidence weighting、forecast models。
- Stage F — Local Refinement
	- validation 前選少數 architecture neighborhoods，僅一次 bounded local search，隨後 freeze。

## 8. No Daily Retuning in Live Competition

正式流程：update data → run frozen architecture → 按預定歷史規則 adaptive model selection → generate target → generate D-Plan → submit。

Frozen code 已定義的 model state、rolling statistics、adaptive ensemble weight update 可更新。禁止人工看昨日競賽結果後改參重跑；除非建立符合 competition policy 的新 version。

## 9. Primary Metrics

每個完整 episode 計 terminal 24D return 的 median、mean、P25、P10、worst、positive-return rate，另計 maximum drawdown、turnover、transaction cost、Day-1 / Day-3 / Day-5 invested ratio。

## 10. Feasibility Metrics

獨立列 complete_episode_rate、measured_compliance_pass_rate、cash violation days、weight-cap violation days、holding-count violation days、odd-lot issues、missing execution-price days、unfilled days、no-valid-plan days。

報告須列 PASS / attempted，不得只算 PASS episodes 報酬而隱藏 failures。

## 11. Selection Order

依序排序，避免單一極端 bull-market episode 決定 winner：

1. canonical data/execution availability
2. measured competition feasibility
3. median 24D return
4. P25
5. P10
6. worst / MDD
7. mean
8. turnover / cost
9. architecture simplicity

## 12. Statistical Robustness

測 parameter neighborhood stability、different start dates、bull / bear / sideways regimes、high / low volatility、sector leadership changes。若只有 exactly 5D、margin 0.075、lambda 0.31 好而附近全失效，標 FRAGILE，不採用。

## 13. Alpha vs Compliance Reporting

每候選分列 alpha_status、compliance_status，不能混同。例如：

```text
alpha_status = PROMISING
compliance_status = BLOCK_MISSING_OFFICIAL_EXECUTION

alpha_status = REJECTED
compliance_status = PASS_MEASURED
```

## 14. Required Tests

至少新增：

```text
tests/test_v4_causality.py
tests/test_v4_execution.py
tests/test_v4_features.py
tests/test_v4_optimizer.py
tests/test_v4_ledger.py
tests/test_v4_episode.py
tests/test_v4_reproducibility.py
```

驗證未來資料不改過去 decision、turnover / volume 正確、orders 的 shares % 1000 == 0、現金不負、持股 20–30、weight cap 及 buy commission / sell commission / sell tax 正確。相同 config + data hash 須 same output hash。

## 15. Required Scripts

```text
scripts/v4_build_execution_data.py
scripts/v4_run_baseline.py
scripts/v4_tune_momentum.py
scripts/v4_tune_adaptive.py
scripts/v4_tune_direct.py
scripts/v4_evaluate.py
scripts/v4_verify.py
scripts/v4_report.py
```

## 16. Required Configs

```text
config/v4_study.json
config/v4_momentum_search.json
config/v4_adaptive_search.json
config/v4_direct_search.json
```

Frozen candidate 存 configs/v4_final.json；無合格者輸出 NO_V4_WINNER，不硬選。

## 17. Required Reports

```text
reports/v4_execution_comparison.md
reports/v4_momentum.md
reports/v4_adaptive.md
reports/v4_direct.md
reports/v4_ablation.md
reports/v4_validation.md
reports/v4_final.md
```

`v4_final.md` 必答：

1. Open → official-average execution 改變多少？
2. Momentum baseline 表現？
3. Adaptive 是否提升？
4. Direct optimizer 是否提升？
5. Confidence 有無價值？
6. Regime 有無價值？
7. Daily rotation 的 optimal region？
8. Portfolio optimizer 是否勝 equal weight？
9. 哪些 component 無效？
10. 哪些只在 recent regime 有效？
11. Lower tail 如何？
12. Compliance failure 最大來源？
13. 有無 stable V4 candidate？
14. 能否正式產出 D-Plan？

## 18. Execution Order for Codex

嚴格依序：

1. Inspect entire current repo.
2. Read official competition documents.
3. Preserve all V2/V3 frozen evidence.
4. Implement V4 official-average execution data layer.
5. Reproduce V3 under Open and new execution.
6. Verify accounting and causal correctness.
7. Implement shared V4 features.
8. Implement V4-Momentum.
9. Run bounded Momentum study.
10. Implement V4-Adaptive.
11. Run bounded Adaptive study.
12. Implement V4-Direct.
13. Run bounded Direct study.
14. Run common validation comparison.
15. Freeze architecture before holdout.
16. Run historical holdout.
17. Run recent / seasonal stress tests.
18. Run ablations.
19. Independently verify artifacts.
20. Produce final report.

## 19. Stop Conditions

Execution data unreliable、causal test fails、ledger mismatch、audit fails、validation improvement disappears 或 parameter neighborhood fragile 時，停搜並保留 evidence；不得以更多 brute-force 掩蓋工程問題。

## 20. Final Definition of Done

完成須具備 correct execution、causal strategy、bounded research、clear ablation、robust 24D evidence、competition-compatible portfolio、reproducible artifacts，而非只找到最高歷史報酬。

Evidence 支持才建立 configs/v4_final.json，否則 NO_V4_WINNER。
