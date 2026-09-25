# V4 Execution, Tuning & Validation Spec

## 1. Goal

建立一套不能靠 hindsight 自欺的 V4 evaluation harness。

核心原則：

> Correct simulator first. Strategy tuning second.

---

# 2. Phase 0 — Rebuild Execution

第一個工作不是 tune V4。

先實作：

```text
src/v4_execution.py
src/v4_ledger.py
```

canonical historical execution：

\[
AveragePrice_t
=
TradingValue_t / TradingVolume_t
\]

---

# 3. Historical Data

Signal data 可以繼續使用 Yahoo daily history。

但 canonical execution price 優先使用：

```text
TWSE official daily trading value + volume
TPEx official daily trading value + volume
```

依上市／上櫃正確分流。

建立 cached normalized table：

```text
date
symbol
open
high
low
close
volume
trading_value
average_execution_price
source
quality_flags
```

如果官方 data 不完整：

```text
official_execution_available = false
```

不得 silent fallback。

可以另外提供：

```text
proxy_execution
```

但 report 必須明確分開。

---

# 4. Execution Comparison

Phase 0 固定 V3 signal，不改任何 strategy parameter。

只比較：

```text
V3 + Open proxy
vs
V3 + official average execution
```

輸出：

```text
return difference
MDD difference
turnover
cash violations
weight violations
ranking changes
per-trade execution-price difference
```

回答：

> V3 過去的結論有多少來自 Open execution assumption？

完成後才進 Phase 1。

---

# 5. Historical Episode Design

每個 episode：

```text
initial cash:
NT$1B

initial holdings:
0

length:
24 trading sessions
```

至少保留：

### Development

```text
2010–2018
```

### Validation

```text
2019–2022
```

### Historical holdout

```text
2023–2024
```

### Recent stress

```text
2025–latest complete pre-competition history
```

### Seasonal diagnostic

```text
historical late-Oct → late-Nov 24-session analogues
```

Seasonal / recent 只能 diagnostic。

不得用來反覆選 parameter。

---

# 6. Rolling Episodes

增加 rolling-start stress test。

但：

```text
overlapping windows != independent samples
```

report 中必須明確標示。

不可把大量 overlapping windows 當成樣本量暴增。

---

# 7. Search Protocol

採 staged search。

不要直接 brute-force 所有東西。

## Stage A — Execution

固定 V3 strategy。

## Stage B — Architecture

比較：

```text
Momentum
Adaptive
Direct
```

## Stage C — Horizon

搜尋：

```text
3/5/10/20/30/60D
```

## Stage D — Rotation / Portfolio

搜尋：

```text
holding count
replacement budget
replacement margin
cash target
risk penalty
```

## Stage E — Model / Ensemble

才調：

```text
expert weighting
confidence weighting
forecast models
```

## Stage F — Local Refinement

只對 validation 前已選定的少數 architecture neighborhoods 做一次 bounded local search。

之後 freeze。

---

# 8. No Daily Retuning in Live Competition

正式 competition：

```text
update data
↓
run frozen architecture
↓
adaptive model selection using its predefined historical rule
↓
generate target
↓
generate D-Plan
↓
submit
```

允許：

```text
model state update
rolling statistics update
adaptive ensemble weight update
```

前提是這些機制已在 frozen code 中定義。

禁止：

```text
human sees yesterday's competition result
→ changes parameter
→ reruns
```

除非正式建立新的 version，並符合 competition policy。

---

# 9. Primary Metrics

每個完整 episode 計算：

```text
terminal 24D return
median
mean
P25
P10
worst
positive-return rate

maximum drawdown

turnover

transaction cost

Day-1 invested ratio
Day-3 invested ratio
Day-5 invested ratio
```

---

# 10. Feasibility Metrics

獨立計算：

```text
complete_episode_rate

measured_compliance_pass_rate

cash violation days
weight-cap violation days
holding-count violation days
odd-lot issues
missing execution-price days
unfilled days
no-valid-plan days
```

不要只對 PASS episodes 算報酬然後隱藏 failures。

報告同時列：

```text
PASS / attempted
```

---

# 11. Selection Order

候選 ranking：

```text
1. canonical data/execution availability
2. measured competition feasibility
3. median 24D return
4. P25
5. P10
6. worst / MDD
7. mean
8. turnover / cost
9. architecture simplicity
```

不要因一個極端 bull-market episode 選 winner。

---

# 12. Statistical Robustness

至少測：

```text
parameter neighborhood stability
different start dates
bull / bear / sideways regimes
high / low volatility
sector leadership changes
```

如果最好的參數只有：

```text
exactly 5D
exactly margin 0.075
exactly lambda 0.31
```

而附近全部失效：

```text
mark as FRAGILE
```

不要採用。

---

# 13. Alpha vs Compliance Reporting

每個候選輸出：

```text
alpha_status
compliance_status
```

例如：

```text
alpha_status = PROMISING
compliance_status = BLOCK_MISSING_OFFICIAL_EXECUTION
```

或：

```text
alpha_status = REJECTED
compliance_status = PASS_MEASURED
```

兩者不得混為一談。

---

# 14. Required Tests

新增至少：

```text
tests/test_v4_causality.py
tests/test_v4_execution.py
tests/test_v4_features.py
tests/test_v4_optimizer.py
tests/test_v4_ledger.py
tests/test_v4_episode.py
tests/test_v4_reproducibility.py
```

重點測：

### Causality

修改未來資料不得改變過去 decision。

### Execution

確認：

```text
turnover / volume
```

計算正確。

### Board lot

所有 orders：

```text
shares % 1000 == 0
```

### Cash

不得 negative。

### Holdings

20–30。

### Weight cap

正確。

### Cost

buy commission、sell commission、sell tax 正確。

### Reproducibility

相同 config + data hash：

```text
same output hash
```

---

# 15. Required Scripts

新增：

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

---

# 16. Required Configs

```text
config/v4_study.json
config/v4_momentum_search.json
config/v4_adaptive_search.json
config/v4_direct_search.json
```

最後 frozen candidate：

```text
configs/v4_final.json
```

如果沒有合格候選：

```text
NO_V4_WINNER
```

不要硬產生 winner。

---

# 17. Required Reports

至少：

```text
reports/v4_execution_comparison.md
reports/v4_momentum.md
reports/v4_adaptive.md
reports/v4_direct.md
reports/v4_ablation.md
reports/v4_validation.md
reports/v4_final.md
```

`v4_final.md` 必須回答：

```text
1. Open → official-average execution 改變多少？

2. Momentum baseline 表現？

3. Adaptive 是否真的提升？

4. Direct optimizer 是否提升？

5. Confidence 有沒有價值？

6. Regime 有沒有價值？

7. Daily rotation 的 optimal region 在哪？

8. Portfolio optimizer 是否勝過 equal weight？

9. 哪些 component 沒用？

10. 哪些結果只在 recent regime 有效？

11. Lower tail 如何？

12. Compliance failure 最大來源？

13. 是否存在 stable V4 candidate？

14. 是否可正式產出 D-Plan？
```

---

# 18. Execution Order for Codex

嚴格依序：

```text
1. Inspect entire current repo.

2. Read official competition documents.

3. Preserve all V2/V3 frozen evidence.

4. Implement V4 official-average execution data layer.

5. Reproduce V3 under both Open and new execution.

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
```

---

# 19. Stop Conditions

停止 search 並保留現有 evidence，如果：

```text
execution data is unreliable
causal test fails
ledger mismatch exists
audit fails
validation improvement disappears
parameter neighborhood is fragile
```

不要用更多 brute-force search 掩蓋工程問題。

---

# 20. Final Definition of Done

V4 完成不是：

```text
found highest historical return
```

而是：

```text
correct execution
+
causal strategy
+
bounded research
+
clear ablation
+
robust 24D evidence
+
competition-compatible portfolio
+
reproducible artifacts
```

最終只在 evidence 支持時建立：

```text
configs/v4_final.json
```

否則明確輸出：

```text
NO_V4_WINNER
```
