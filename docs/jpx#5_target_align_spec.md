# LightGBM Target Alignment Study

## 0. Goal

只研究一件事：

> **對 150 檔 → Top25 的 task，LightGBM 到底應該預測什麼？**

比較三種 target：

```text
A. Raw Return
B. Relative Alpha
C. Execution-Aligned Relative Alpha
```

全部共用：

```text
Same Features
Same LightGBM
Same Training
Same Top25
Same Portfolio
Same Planner
Same Ledger
Same Episodes
```

因此結果可以直接 attribution 到 **target formulation**。

---

# 1. Research Questions

依序回答：

1. **Relative Alpha 是否優於 Raw Return？**
2. **把 label 對齊真正成交時間後，是否再改善？**
3. 改善來自：
   - signal quality
   - rank stability
   - turnover
   - 還是沒有實質改善？

---

# 2. Hard Scope

## 2.1 本輪只允許改

```text
target definition
target audit
target tests
diagnostics/reporting
```

## 2.2 完全 Freeze

```text
LightGBM parameters
features
lookback
refit cadence
portfolio
turnover gate
planner
execution
cost model
episode set
```

## 2.3 禁止

```text
新 features
JPX5 features
LightGBMRanker
XGBoost
Optuna
parameter tuning

SOX / ADR / 夜盤
法人
sector features
regime

TopK-drop
score smoothing
portfolio optimizer
ensemble
```

不要看到結果後臨時增加 scope。

---

# 3. Existing Baseline

Branch：

```text
claude/recent-window
```

已完成：

```text
JPX2 LightGBM Raw Return
70 dev episodes
2019–2021
month_start + mid_month
```

結果：

```text
Momentum     +3.41%
Raw LGBM     +3.15%
AutoTS       +2.99%
```

這些結果作 baseline，不要重新設計 benchmark。

---

# 4. Architecture

不要建立第二套 LightGBM。

沿用：

```text
lgbm_strategy/
├── features.py
├── dataset.py
├── model.py
└── strategy.py
```

新增：

```text
lgbm_strategy/
└── targets.py
```

目標：

```text
features.py
→ 只算 features

targets.py
→ 只算 labels

dataset.py
→ features + matured labels

model.py
→ 完全不懂 target semantics

strategy.py
→ orchestration only
```

---

# 5. Target Modes

Config 增加：

```text
target_mode
```

只允許：

```text
raw_return
relative_alpha
execution_alpha
```

不要用散落的 `if alpha_v2...`。

---

# 6. Target A — Raw Return

必須完全重現現有 JPX2 baseline。

對 feature origin `t`：

\[
r*{i,t}^{(h)}
=
\frac{P*{i,t+h}}{P\_{i,t}}-1
\]

設定：

```text
horizon = 10
```

用途：

```text
CONTROL
```

---

# 7. Target B — Relative Alpha

先計算：

\[
r\_{i,t}^{10}
\]

再算該日期股票池平均：

\[
r*{m,t}^{10}
=
\frac{1}{N_t}
\sum_i r*{i,t}^{10}
\]

最後：

\[
\alpha*{i,t}
=
r*{i,t}^{10}

- r\_{m,t}^{10}
  \]

  ***

## 7.1 Market Universe

market mean 只使用：

```text
competition 150 stocks
+
finite matured forward labels
```

不要使用：

```text
0050
Top25
feature_ready subset
future holdings
```

---

## 7.2 Coverage

若：

```text
finite labels < 20
```

則：

```text
整個 origin date target invalid
```

不要用極小 cross-section 算 alpha。

---

## 7.3 Invariant

每個有效 origin：

```text
mean(relative_alpha) ≈ 0
```

必須 test。

---

# 8. Target C — Execution-Aligned Relative Alpha

這是本 study 最重要的新實驗。

目前：

```text
feature at D-1
↓
target 從 D-1 close 開始
```

但真實比賽：

```text
D-1 information
↓
08:55 前決策
↓
D 日成交
↓
PnL 才真正開始
```

因此 target 應改成：

```text
Feature origin = D-1
Entry          = D execution price
Exit           = D+10 execution price
```

公式：

\[
r^{exec}_{i,t}
=
\frac{X_{i,t+1+h}}
{X\_{i,t+1}}
-1
\]

其中：

```text
t = feature date
t+1 = actual next trading session / execution date
h = 10
X = historical execution price
```

---

# 9. Execution Price

**必須和目前 backtest execution semantics 使用同一套 price source。**

不要重新發明 label price。

使用：

```text
execution.mode = auto
proxy = hlc3
```

歷史 execution price：

```text
if official_vwap exists:
    X = official_vwap
else:
    X = HLC3 proxy
```

其中：

\[
HLC3 = (High+Low+Close)/3
\]

---

# 10. Critical Rule — Single Source of Truth

不要在 `targets.py` 自己重新實作：

```text
official VWAP fallback logic
HLC3 calculation rules
```

應抽取 / reuse competition execution 已有 helper。

目標：

```text
backtest execution price
==
training execution-label price
```

同一 semantics。

---

# 11. Historical Coverage Limitation

必須在報告明確區分：

## 2019–2021 DEV

幾乎沒有 official VWAP：

```text
execution_alpha
≈ HLC3-proxy-aligned target
```

## 2025–2026

official VWAP 大部分存在：

```text
execution_alpha
≈ actual competition execution-aligned target
```

因此禁止聲稱：

> 2019–2021 已驗證官方 VWAP target。

正確說法：

> 驗證的是「與 simulator execution semantics 對齊」。

---

# 12. Label Timeline

### Raw / Relative Alpha

```text
feature t
│
├──────────────→ t+10
│                 label end
```

### Execution Alpha

```text
feature t
│
└─ trade t+1
      │
      └──────────→ t+1+10
                    label end
```

所以 execution target 的 maturity 比原 target 晚 **1 session**。

---

# 13. Label Maturity

Decision day `D`：

所有 training label 必須：

```text
label_end <= D-1
```

Execution target：

```text
origin + 1 + horizon <= D-1
```

任何 violation：

```text
FAIL HARD
```

禁止 silent drop/fallback 隱藏 leakage。

---

# 14. Features

**完全不改。**

沿用 JPX2：

```text
Open
High
Low
Close
Volume

return_20
return_40
return_60

volatility_20
volatility_40
volatility_60

MA_gap_20
MA_gap_40
MA_gap_60
```

禁止新增：

```text
R2
R5
ADV rank
stock ID
sector
```

那些是下一階段。

---

# 15. Model

**完全不改 `model.py` semantics。**

```text
LightGBMRegressor

objective = regression
boosting = gbdt

learning_rate = 0.005
n_estimators = 3000

early_stopping = 300
validation metric = Pearson

random_state = 2026
n_jobs = 1
```

不要 tuning。

---

# 16. Training

保持：

```text
lookback = 750 sessions
refit_every = 5
```

每次 refit：

```text
D-1 cutoff
↓
find matured labels
↓
latest ≤750 origins
↓
chronological train/validation
↓
purge horizon overlap
↓
fit
```

---

# 17. Validation Split

保持現在 implementation：

```text
Earlier ~80%
→ train

purge
→ horizon gap

Latest ~20%
→ validation
```

禁止 random split。

---

# 18. Prediction

不論 target mode：

```text
D-1 features
↓
LightGBM score
↓
descending cross-sectional rank
↓
Top25
```

不需要：

```text
score normalization
rank transform
calibration
```

本輪只比較 target。

---

# 19. Portfolio

完全 freeze：

```text
n_holdings = 25
keep_rank = 35

weighting = equal
invested = 0.88
cap_scale = 0.9

rebalance_threshold = 0.10
freeze_last_days = 3
```

禁止因 LGBM turnover 高而改。

---

# 20. Configs

建立：

```text
research/configs/
├── baseline_lgbm_raw.json
├── baseline_lgbm_alpha.json
└── baseline_lgbm_execution_alpha.json
```

除了：

```text
target_mode
```

其他 LightGBM / portfolio config 必須 identical。

---

# 21. Parity Gate

在跑任何新 experiment 前：

```text
target_mode = raw_return
```

重跑 3–6 個既有 episode。

必須與舊 JPX2：

```text
training rows
predictions
target weights
episode return
```

一致。

如果不一致：

```text
STOP
```

先修 parity。

---

# 22. Unit Tests

至少：

```text
tests/test_lgbm_targets.py
```

更新：

```text
test_lgbm_dataset.py
test_lgbm_causality.py
test_lgbm_strategy.py
```

---

# 23. Required Target Tests

## Raw parity

舊 target bit-identical / numerical-identical。

## Alpha zero mean

```text
mean(alpha_t) ≈ 0
```

## Execution price parity

對抽樣日期：

```text
label execution price
==
backtest execution price helper
```

## Timeline

確認：

```text
raw label_end = t+h
execution label_end = t+1+h
```

## Missing VWAP

確認 fallback：

```text
official_vwap NaN
→ exactly same HLC3 logic
```

---

# 24. Causality

建立 dataset A。

建立 dataset B，只改：

```text
D 之後資料
```

例如：

```text
multiply
shuffle
delete
replace
```

必須：

```text
features identical
matured labels identical
training rows identical
predictions identical
weights identical
```

---

# 25. Future Execution Corruption Test

Execution target 特別增加：

修改：

```text
execution price after D-1
```

不得影響 D decision。

因為那些 labels 尚未 mature。

---

# 26. Determinism

同一：

```text
data
config
seed
```

必須：

```text
targets identical
best_iteration identical
predictions identical
weights identical
```

---

# 27. Diagnostics — Target

每個 refit 記：

```text
target_mode

target_mean
target_std

cross_section_count
label_start
label_end

official_vwap_share
proxy_hlc3_share
```

---

# 28. Diagnostics — Model

沿用：

```text
best_iteration
train_pearson
validation_pearson
n_train_rows
n_validation_rows
```

不要用它作最終 selection。

---

# 29. Diagnostics — Rank Stability

每個 decision day 記：

```text
Top25 overlap previous day
Top35 overlap
entries
exits
one-way turnover
```

核心：

\[
Overlap*t
=
\frac{|Top25_t\cap Top25*{t-1}|}{25}
\]

只做 diagnosis。

---

# 30. Experiment Stage 0

```text
ALL unit tests
ALL causality tests
ALL parity tests
```

全部 PASS 才進 backtest。

---

# 31. Stage 1 — Smoke

相同 6 dev episodes：

```text
Momentum
Raw LGBM
Alpha LGBM
Execution Alpha LGBM
AutoTS
```

Smoke 只檢查：

```text
correctness
runtime
no warnings
no disqualification
```

禁止從 6 episodes tuning。

---

# 32. Stage 2 — Full DEV

使用完全相同：

```text
2019–2021
70 episodes
month_start + mid_month
```

不得新增 / 移除窗口。

---

# 33. Primary Comparison

優先順序：

### Q1

```text
Relative Alpha
vs
Raw Return
```

回答：

> 去 market component 是否有效？

### Q2

```text
Execution Alpha
vs
Relative Alpha
```

回答：

> 對齊真正成交區間是否有效？

### Q3

```text
Execution Alpha
vs
Momentum
```

回答：

> 最後能不能真正勝過 baseline？

---

# 34. Metrics

每個 strategy：

```text
Mean 24D return
Median 24D return

P25
P10
Worst

Mean MDD

Turnover
Transaction cost

Warnings
Disqualification
```

---

# 35. Paired Metrics

每組 comparison 必須：

```text
paired mean Δ
paired median Δ
win rate
bootstrap 95% CI
```

不要只比較 aggregate mean。

---

# 36. Signal / Cost 分開

報告分兩層：

## Signal

```text
paired gross/net result
rank stability
validation Pearson
Top25 overlap
```

## Cost

```text
turnover
cost
entries/exits
```

禁止只看到：

```text
cost difference > net gap
```

就宣稱：

> 沒成本一定會贏。

除非真的跑 counterfactual。

---

# 37. Success Logic

## Relative Alpha Wins Raw

若：

```text
mean Δ > 0
AND
median Δ > 0
```

輸出：

```text
RELATIVE_ALPHA_SUPPORTED
```

---

## Execution Alignment Wins Alpha

若：

```text
Execution Alpha
vs Alpha

mean Δ > 0
AND
median Δ > 0
```

輸出：

```text
EXECUTION_ALIGNMENT_SUPPORTED
```

---

# 38. Final DEV Gate

只有最佳 target：

```text
paired mean Δ vs Momentum > 0
AND
paired median Δ vs Momentum > 0
AND
no worse disqualification
```

才輸出：

```text
PASS_TARGET_STUDY
```

並進 Validation。

---

# 39. Interesting Failure Case

如果：

```text
Execution Alpha > Raw LGBM
但
Execution Alpha < Momentum
```

輸出：

```text
TARGET_IMPROVED_MODEL_STILL_WEAK
```

下一階段才研究：

```text
JPX5 features
or
Learning-to-Rank
```

不要立刻 tuning。

---

# 40. If All Fail

若三個 LGBM targets 都沒明顯改善：

```text
STOP_TARGET_FORMULATION
```

結論：

> 問題不只在 target。

下一個合理方向：

```text
JPX5 behavioral features
or
direct ranking objective
```

---

# 41. Validation

只有：

```text
PASS_TARGET_STUDY
```

才跑：

```text
2022–2024
```

設定 frozen。

不得因 validation 改 target。

---

# 42. Holdout

只有 freeze 後：

```text
2025–2026/09
```

正式跑一次。

此階段 official VWAP coverage 高，因此特別報：

```text
official execution-label share
```

---

# 43. Automation

Agent one-shot：

```text
inspect existing target/execution helpers
↓
minimal target abstraction
↓
raw parity
↓
tests
↓
smoke
↓
70 DEV episodes × 3 target modes
↓
paired comparison
↓
decision gate
↓
if PASS → validation
↓
freeze
↓
if allowed → holdout
↓
report
```

---

# 44. Long Jobs

耗時 backtest 必須：

```text
CLI / shell
resumable
parallel where safe
```

Agent 不應長時間：

```text
tail logs
poll every few seconds
load full ledgers into context
```

---

# 45. Token Efficiency

Agent正常只讀：

```text
summary.json
comparison.csv
target_diagnostics.csv
model_diagnostics.csv
failures.md
```

只有 failure 才深入：

```text
ledger
orders
per-day prediction
```

---

# 46. Clean Code

要求：

```text
single responsibility
pure target functions where possible
explicit target_mode enum
type hints
dataclass config
deterministic sorting
explicit NaN handling
fail loudly
```

---

## 禁止

```text
duplicated execution logic
magic offsets
target_v2 / final_target names
large orchestrator functions
hidden future access
silent fallback
unrelated refactor
```

---

# 47. Naming

使用：

```text
RawReturnTarget
RelativeAlphaTarget
ExecutionAlphaTarget

entry_session
exit_session
execution_price
market_forward_return
relative_alpha
```

避免：

```text
v2
new
tmp
special_case
```

---

# 48. Output

```text
research/results/target_alignment/
├── summary.md
├── summary.json
├── comparison.csv
├── target_diagnostics.csv
├── model_diagnostics.csv
└── failures.md
```

---

# 49. summary.md

只需四段：

## Decision

```text
PASS_TARGET_STUDY
TARGET_IMPROVED_MODEL_STILL_WEAK
STOP_TARGET_FORMULATION
```

## Main Table

```text
Momentum
Raw LGBM
Alpha LGBM
Execution Alpha LGBM
AutoTS
```

columns：

```text
Mean
Median
Δ vs Momentum
Win Rate
P10
Worst
MDD
Turnover
Cost
```

## Target Comparison

```text
Raw → Alpha
Alpha → Execution Alpha
```

## Next Action

最多 3 行。

---

# 50. Git Hygiene

不要 commit：

```text
run folders
model binaries
full ledgers
prediction dumps
cache
long logs
```

只 commit：

```text
source
tests
configs
small reports
comparison tables
```

---

# 51. Definition of Done

```text
[ ] target abstraction implemented

[ ] raw parity PASS
[ ] relative alpha mean ≈ 0
[ ] execution-price parity PASS
[ ] maturity timeline PASS
[ ] causality PASS
[ ] determinism PASS

[ ] same features
[ ] same LightGBM
[ ] same portfolio
[ ] same 70 DEV episodes

[ ] Raw complete
[ ] Alpha complete
[ ] Execution Alpha complete

[ ] paired comparisons complete
[ ] turnover diagnostics complete
[ ] target diagnostics complete

[ ] summary generated
[ ] no holdout before freeze
[ ] no unrelated refactor
```

---

# 52. Final Principle

本 study 只回答：

```text
What should the model predict?
```

而不是：

```text
How do we make LightGBM more complicated?
```

順序固定：

```text
Raw Return
→ remove market component
→ align with actual execution
→ only then change features/model/portfolio
```
