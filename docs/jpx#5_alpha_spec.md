# LightGBM Relative-Alpha Baseline Spec

## 0. Mission

建立下一個 baseline：

> **固定原 LightGBM、features、portfolio、execution，只把 prediction target 從 raw future return 改成 cross-sectional relative alpha。**

唯一研究問題：

```text
Raw-return LightGBM 失敗
        ↓
問題是否在 target？
        ↓
預測「誰比市場強」
是否比
預測「誰會漲多少」
更適合這個 task？
```

---

# 1. Core Hypothesis

目前 JPX2 baseline：

```text
features
↓
LightGBM
↓
predict 10D raw return
↓
rank 150 stocks
↓
Top 25
```

新 baseline：

```text
same features
↓
same LightGBM
↓
predict 10D relative alpha
↓
rank 150 stocks
↓
same Top 25 portfolio
```

其中：

\[
\alpha*{i,t}^{10D}
=
r*{i,t}^{10D}

- \frac{1}{N*t}\sum_j r*{j,t}^{10D}
  \]

也就是：

> 股票未來 10 天報酬 − 同期股票池平均報酬。

---

# 2. Hard Scope

## 2.1 This Spec Changes

只改：

```text
training target
raw return
→
cross-sectional alpha
```

以及必要的：

```text
tests
audit
reporting
```

---

## 2.2 Must Stay Frozen

完全沿用上一版：

```text
LightGBM model
feature set
lookback
refit cadence
portfolio
planner
execution
transaction costs
episode set
evaluation protocol
```

原因：

> 如果同時改 target、features、model，就無法知道 improvement 來自哪裡。

---

## 2.3 Explicitly Forbidden

本輪不要加入：

```text
JPX5 tail-only training
SecuritiesCode categorical
R2 mean reversion
131D momentum
new LightGBM params
LightGBMRanker
XGBoost
Optuna
AutoTS ensemble

SOX
NASDAQ
TSM ADR
台指夜盤
三大法人
sector features
regime detection

portfolio optimizer
new weighting method
new turnover threshold
```

全部留到後續 spec。

---

# 3. Evaluation Period

沿用目前 `claude/recent-window` 已完成 baseline 的 **完全相同 episode IDs**。

## Development

```text
2019–2021
month_start + mid_month
70 episodes
```

不要重新定義 episode。

新的 Alpha LightGBM、舊 JPX2 LightGBM、Momentum、AutoTS：

> **必須逐 episode 完全配對。**

---

## Validation

Development 通過後才跑：

```text
2022–2024
```

使用 repo 現有 recent-window split。

---

## Holdout

只有 validation freeze 後：

```text
2025–2026/09
```

只允許一次正式 evaluation。

---

# 4. Historical Context

只限制：

```text
evaluation episode >= 2019
```

模型仍可使用 decision date 之前的歷史資料。

例如：

```text
2019 decision
↓
最多往前 750 sessions
↓
約 2016–2018 historical context
```

這不算評估 pre-2019。

---

# 5. Reuse Existing Implementation

不要建立第二套 LightGBM framework。

優先 reuse：

```text
lgbm_strategy/
├── features.py
├── dataset.py
├── model.py
└── strategy.py
```

以及：

```text
autots_strategy/portfolio.py
competition/
research/
```

---

# 6. Clean Architecture

推薦改法：

```text
lgbm_strategy/
├── features.py      ← 不改 feature semantics
├── targets.py       ← 新增 target logic
├── dataset.py       ← 支援 target_mode
├── model.py         ← 不改模型
└── strategy.py      ← config 傳 target_mode
```

不要複製：

```text
lgbm_alpha_strategy/
```

整套第二份 code。

---

# 7. Target API

新增：

```python
build_target(
    returns,
    horizon,
    mode,
)
```

支援：

```text
mode = "raw_return"
mode = "relative_alpha"
```

---

# 8. Raw Return

保留舊 target：

\[
r*{i,t}^{h}
=
\frac{P*{i,t+h}}{P\_{i,t}}-1
\]

用於 parity test。

---

# 9. Relative Alpha

## 9.1 Definition

先計算：

```text
forward return for every stock
```

再逐 origin date：

```text
market_return(t)
=
mean(
    valid matured forward returns
    across competition universe
)
```

最後：

```text
alpha(i,t)
=
stock_forward_return(i,t)
-
market_return(t)
```

---

## 9.2 Universe

計算 market mean 時：

只包含：

```text
competition 150-stock universe
AND
finite forward label
```

不要使用：

```text
0050
feature_ready filtering
predicted Top25
future portfolio members
```

理由：

> market target 應代表當時可比較的股票 universe，而不是被 feature eligibility 再選一次。

---

## 9.3 Minimum Coverage

若某日期可用 forward labels 太少：

```text
valid_labels < 20
```

則：

```text
整個 origin date target = invalid
```

不要用極小樣本市場平均。

---

# 10. Target Invariant

每個有效 training date：

```text
mean(alpha_i) ≈ 0
```

加入 test：

```text
abs(cross_section_mean_alpha) < numerical tolerance
```

---

# 11. Horizon

主 baseline 固定：

```text
horizon = 10
```

不要這一輪測：

```text
5 / 20
```

先回答 target 問題。

如果 10D alpha 成立，再另開 horizon ablation。

---

# 12. Features

**完全不改。**

沿用目前 JPX2：

## Raw

```text
Open
High
Low
Close
Volume
```

## Return

```text
return_20
return_40
return_60
```

## Volatility

```text
volatility_20
volatility_40
volatility_60
```

## MA Gap

```text
ma_gap_20
ma_gap_40
ma_gap_60
```

所有 feature 必須：

```text
per-symbol
chronological
causal
D-1 only
```

---

# 13. Model

**完全沿用現有 `model.py`。**

不要改參數：

```text
LightGBMRegressor

objective = regression
boosting_type = gbdt

learning_rate = 0.005
n_estimators = 3000

early_stopping = 300
eval metric = Pearson

n_jobs = 1
random_state = 2026
```

其他參數保持 library default。

---

# 14. Training Protocol

完全沿用：

```text
lookback = 750 sessions
refit_every = 5 sessions
```

每次 refit：

```text
Decision D
↓
AsOfView through D-1
↓
matured labels only
↓
latest ≤750 dates
↓
chronological train / validation split
↓
LightGBM
```

---

# 15. Label Maturity

Hard rule：

```text
label_end <= D-1
```

保持現有：

```python
latest_label_end <= view.date
```

任何 violation：

```text
FAIL
```

禁止 fallback。

---

# 16. Internal Validation

保持：

```text
chronological split
earlier ~80% → train
latest ~20% → validation
```

中間保留 horizon purge。

禁止：

```text
random split
shuffle split
future normalization
```

---

# 17. Prediction

Decision D：

```text
D-1 data
↓
features
↓
current LightGBM
↓
predicted alpha
↓
cross-sectional sort
↓
score
```

score 就是：

```text
predicted alpha
```

不需要另外 normalization。

---

# 18. Portfolio

完全沿用 Momentum / JPX2 baseline：

```text
n_holdings = 25
keep_rank = 35

weighting = equal
invested = 0.88
cap_scale = 0.9

rebalance_threshold = 0.10
freeze_last_days = 3
```

本輪禁止為了救 turnover 改這些設定。

---

# 19. Why Turnover Is Frozen

上一版：

```text
Momentum turnover = 3.41
JPX2 turnover     = 6.01
```

LightGBM 多付約：

```text
+0.77% transaction cost / episode
```

這很重要。

但這一輪先問：

> **Alpha target 本身能不能讓 ranking 更穩、更有資訊？**

所以先不調 turnover。

否則：

```text
target change
+
turnover change
```

同時發生後無法 attribution。

---

# 20. Cheap Turnover Diagnostics

可以新增 diagnostics，但不得影響策略。

每天記：

```text
Top25 overlap with previous decision
Top35 overlap
number of entries
number of exits
one-way turnover
transaction cost
```

---

## Rank Stability

建議記：

```text
top25_overlap_ratio
=
|Top25_t ∩ Top25_t-1| / 25
```

目的：

> 判斷 alpha target 是否自然產生較穩定 ranking。

不要用它 tuning。

---

# 21. Model Diagnostics

每次 refit 記：

```text
best_iteration
validation_pearson
train_pearson

target_std
prediction_std

n_train_rows
n_validation_rows
n_symbols
latest_label_end
```

---

# 22. Alpha-Specific Diagnostics

每個 training window 額外記：

```text
mean raw target
mean alpha target

std raw target
std alpha target

cross-sectional alpha mean error
```

確認 target implementation 正確。

---

# 23. Config

新增：

```text
research/configs/baseline_lgbm_alpha.json
```

內容只和 JPX2 config 差：

```json
{
  "target_mode": "relative_alpha"
}
```

其他設定應完全一致。

---

# 24. Raw JPX2 Config

舊 baseline 保持：

```json
{
  "target_mode": "raw_return"
}
```

確保同一 code path 可重現之前結果。

---

# 25. Parity Requirement

在跑新實驗前：

使用：

```text
target_mode = raw_return
```

重跑少量已知 episode。

必須與之前 JPX2 baseline：

```text
predictions
weights
episode return
```

一致。

如果不一致：

```text
STOP
```

先修 parity。

---

# 26. Tests

至少新增：

```text
tests/test_lgbm_targets.py
```

並更新：

```text
test_lgbm_dataset.py
test_lgbm_causality.py
test_lgbm_strategy.py
```

---

# 27. Target Tests

## Raw Return

確認舊計算完全相同。

---

## Alpha Mean

每個有效日期：

```text
mean(alpha) ≈ 0
```

---

## Missing Data

如果某些股票 target NaN：

```text
只在 finite labels 上算 market mean
```

---

## Low Coverage

```text
<20 valid labels
→ date invalid
```

---

# 28. Causality Test

修改 decision D 之後資料：

```text
multiply
shuffle
delete
replace
```

必須：

```text
features unchanged
training rows unchanged
alpha labels unchanged
model prediction unchanged
weights unchanged
```

---

# 29. Determinism

同一：

```text
data
config
seed
```

兩次執行：

```text
target identical
prediction identical
portfolio identical
```

---

# 30. Experiment Stage 0 — Tests

執行全部 tests。

要求：

```text
ALL PASS
```

---

# 31. Stage 1 — Parity

用 `raw_return`：

```text
3–6 existing dev episodes
```

確認 refactor 沒改掉 JPX2。

---

# 32. Stage 2 — Smoke

比較：

```text
momentum_20d
JPX2 raw-return LightGBM
Alpha LightGBM
AutoTS
```

使用完全相同：

```text
6 dev episodes
```

目的：

```text
correctness
runtime
compliance
```

不要依 smoke tuning。

---

# 33. Stage 3 — Full Development

使用完全相同上一輪：

```text
70 dev episodes
2019–2021
month_start + mid_month
```

不得挑窗口。

---

# 34. Primary Comparisons

最重要：

```text
Alpha LightGBM
vs
Momentum
```

第二：

```text
Alpha LightGBM
vs
Raw-return LightGBM
```

第三：

```text
Alpha LightGBM
vs
AutoTS
```

---

# 35. Metrics

每個 strategy：

```text
mean 24D return
median 24D return

P25
P10
worst

average MDD

turnover
transaction cost

warnings
disqualifications
```

---

## Paired Metrics

必須：

```text
paired mean Δ
paired median Δ
win rate

bootstrap 95% CI
```

---

# 36. Signal vs Cost Diagnosis

報告必須把兩件事分開：

## Signal

```text
Alpha vs Raw-return
rank stability
paired returns
```

## Cost

```text
turnover difference
cost difference
```

不要直接宣稱：

```text
"沒有成本就會贏"
```

除非真正有對應 counterfactual evidence。

---

# 37. No-Cost Simulation

本輪預設：

```text
DO NOT build new cost-free simulator
```

如果現有 ledger 已經天然支援：

```text
transaction_cost = 0
```

且不需修改 accounting semantics，才允許作：

```text
DIAGNOSTIC ONLY
```

不能用 cost-free result 選策略。

---

# 38. Development Success Gate

Alpha model 要進 Validation，必須：

```text
paired mean Δ vs momentum > 0
AND
paired median Δ vs momentum > 0
AND
no higher disqualification rate
```

如果成立：

```text
PASS_ALPHA_DEV
```

---

# 39. Secondary Signal Gate

另外報：

```text
Alpha LightGBM
vs
Raw-return LightGBM
```

如果：

```text
paired mean > 0
paired median > 0
```

則可支持：

> relative-alpha target 比 raw-return target 更適合此 task。

---

# 40. If Net Return Fails But Signal Looks Better

例如：

```text
Alpha vs raw-return significantly better
BUT
Alpha vs momentum still loses
AND
turnover/cost clearly higher
```

輸出：

```text
ALPHA_SIGNAL_PROMISING_COST_BLOCKED
```

下一版才研究 turnover。

不要本輪直接改 turnover。

---

# 41. If Alpha Fails

如果：

```text
Alpha vs Momentum mean <= 0
AND
median <= 0
```

或 Alpha 也沒有明顯改善 raw-return：

```text
STOP_ALPHA_LGBM
```

不要：

```text
tune LightGBM
加 features
換 horizon
調 portfolio
```

先停止。

---

# 42. Validation

只有：

```text
PASS_ALPHA_DEV
```

才允許跑：

```text
2022–2024 validation
```

設定 freeze。

不得因 validation 結果：

```text
change features
change model
change target
change portfolio
```

---

# 43. Holdout

只有 Validation 通過並 freeze 後：

```text
2025–2026/09
```

跑一次。

Holdout 結果：

```text
report only
```

不能回頭 tuning。

---

# 44. Automation

Agent 應 one-shot 完成：

```text
inspect existing implementation
↓
minimal target refactor
↓
raw-return parity
↓
tests
↓
alpha smoke
↓
70 dev episodes
↓
comparison
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

# 45. Long-Running Work

所有耗時 operation：

```text
shell script / CLI
resumable jobs
```

Agent 不要在 context 中等待。

---

# 46. Token Efficiency

Agent只讀：

```text
summary.json
comparison.csv
model diagnostic summary
failed cases
short logs
```

禁止常態讀：

```text
all ledgers
all predictions
all model dumps
raw per-day logs
```

只有 failure diagnosis 才讀細節。

---

# 47. Code Modification Policy

修改前先搜尋 reuse。

Prefer：

```text
extend existing abstraction
```

over：

```text
duplicate implementation
```

---

# 48. Clean Code Requirements

要求：

```text
single responsibility
small functions
type hints
dataclass configs
explicit arguments
deterministic ordering
explicit failure
no hidden state
no magic fallback
```

---

## Naming

使用：

```text
raw_forward_return
cross_section_market_return
relative_alpha
target_mode
```

不要：

```text
temp
new_target
v2
test2
misc
```

---

# 49. No Unrelated Refactor

不要整理：

```text
AutoTS internals
competition engine
old legacy code
README unrelated sections
```

只動此實驗必要範圍。

---

# 50. Output

建立：

```text
research/results/lgbm_alpha/
├── summary.md
├── summary.json
├── comparison.csv
├── model_diagnostics.csv
└── failures.md
```

---

# 51. `summary.md`

保持短。

## Result

```text
PASS_ALPHA_DEV

or

ALPHA_SIGNAL_PROMISING_COST_BLOCKED

or

STOP_ALPHA_LGBM
```

---

## Main Table

只列：

```text
Momentum
Raw LGBM
Alpha LGBM
AutoTS
```

columns：

```text
Mean
Median
Paired Δ vs momentum
Win rate
P10
Worst
MDD
Turnover
Cost
```

---

## Diagnosis

最多三點：

```text
Target effect
Turnover effect
Next action
```

---

# 52. Git Hygiene

不要 commit：

```text
model binary
run folders
ledgers
large prediction files
cache
long logs
```

只 commit：

```text
source
tests
configs
small summaries
comparison tables
```

---

# 53. Definition of Done

```text
[ ] Same model as JPX2 baseline
[ ] Same features
[ ] Same portfolio
[ ] Same 70 dev episodes
[ ] Only main methodological change = target

[ ] raw_return parity passes
[ ] alpha target tests pass
[ ] cross-sectional alpha mean ≈ 0
[ ] label maturity passes
[ ] causality passes
[ ] determinism passes

[ ] full 70-episode dev complete
[ ] paired Momentum comparison complete
[ ] paired Raw-LGBM comparison complete
[ ] turnover diagnostics complete
[ ] report generated

[ ] validation only if gate passes
[ ] holdout untouched before freeze
[ ] no unrelated refactor
```

---

# 54. Stop Rule

本輪的核心：

```text
Target first.
Model frozen.
Features frozen.
Portfolio frozen.
```

結果若不支持 relative alpha：

```text
STOP
```

不要靠更多 tuning 把負結果調成正結果。

---

# Final Question

這個 spec 最後只能回答一件事：

> **對這個 150 檔、Top-25、24 日台股 competition，預測「相對市場強弱」是否比預測「絕對未來報酬」更有用？**
