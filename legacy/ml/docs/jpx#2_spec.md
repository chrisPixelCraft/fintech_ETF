# JPX #2 Clean-Transfer LightGBM Baseline

## 0. Goal

驗證：

> **JPX #2 式簡單 LightGBM Regression，在 2019–2026 台股環境，能否穩定勝過 Momentum 與 AutoTS。**

只研究 signal，不改 execution / portfolio infrastructure。

---

# 1. Scope

## 必做

```text
causal features
→ LightGBM regression
→ cross-sectional ranking
→ Top 25
→ existing planner / ledger
→ 2019–2026 walk-forward evaluation
```

## 禁止

```text
AutoTS ensemble
LightGBMRanker
XGBoost
Optuna / massive tuning
overnight / SOX / ADR
三大法人
sector model
regime detection
relative-alpha target
tail-only training
deep learning
portfolio optimizer
```

---

# 2. Evaluation Period

**所有 strategy evaluation episode 僅限：**

```text
2019-01-01
↓
2026-09 latest available complete episode
```

完全不要跑：

```text
2009–2018 episodes
```

---

## 2.1 Split

使用：

```text
DEV
2019-01 → 2022-12

VALIDATION
2023-01 → 2024-12

HOLDOUT
2025-01 → 2026-09
```

用途：

```text
2019–2022
→ development / debugging / design

2023–2024
→ validation / model selection

freeze

2025–2026/09
→ final holdout
```

### Hard Rule

看到 holdout 後：

```text
不得修改
features
target
hyperparameters
portfolio rules
```

---

# 3. Historical Training Data

「只測 2019–2026」指的是：

> **只建立 2019–2026 的 competition episodes。**

模型仍可使用 decision day 之前的 historical data。

例如：

```text
2019-01 decision

training lookback
≈ preceding 750 sessions
≈ 2016–2018+
```

這是正常 historical context，不算測試 2016–2018。

否則 2019 初期模型會沒有足夠 warm-up。

---

# 4. Source Method

參考 JPX Tokyo Stock Exchange Prediction #2。

核心：

```text
OHLCV
+
20 / 40 / 60D return
+
20 / 40 / 60D volatility
+
20 / 40 / 60D MA gap
↓
LightGBM Regression
↓
Predicted return
↓
Cross-sectional rank
```

公開設定：

```text
objective = regression
boosting = gbdt
learning_rate = 0.005
num_boost_round <= 3000
early_stopping = 300
```

修正原 notebook 不乾淨部分：

```text
所有 rolling / pct_change
必須 per-symbol
必須 chronological
必須 causal
```

---

# 5. Target

Baseline：

```text
10-session forward action-neutral return
```

\[
y*{i,t}=\frac{P*{i,t+10}}{P\_{i,t}}-1
\]

不得使用：

```text
adj_close
future dividend
future split
```

---

## Label Maturity

Decision D：

```text
features <= D-1
```

Training row `t` 必須：

```text
t + 10 <= D-1
```

Hard assertion：

```text
latest_label_end <= D-1
```

---

# 6. Features

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

逐股票：

```text
P(t) / P(t-h) - 1
```

## Volatility

```text
volatility_20
volatility_40
volatility_60
```

```text
rolling std(log return)
```

## MA Gap

```text
MA_gap_20
MA_gap_40
MA_gap_60
```

保持 JPX #2 formulation：

```text
Close / SMA_h(Close)
```

---

# 7. Model

使用：

```python
lightgbm.LGBMRegressor
```

固定：

```text
objective = regression
boosting_type = gbdt

learning_rate = 0.005
n_estimators = 3000

random_state = 2026
n_jobs = 1
verbosity = -1
force_col_wise = true
```

其他 parameter 優先 library defaults。

不要 tuning。

---

# 8. Walk-Forward Training

每個 decision point：

```text
D-1 available data
↓
latest matured labels
↓
last ≤750 sessions
↓
chronological train / validation
↓
LightGBM
```

設定：

```text
lookback = 750 sessions
refit_every = 5 sessions
```

One global model：

```text
all dates × all eligible stocks
```

不要 one-model-per-stock。

---

# 9. Internal Validation

每個 training window：

```text
earlier ~80%
→ train

latest ~20%
→ validation
```

禁止 random split。

Early stopping metric：

```text
Pearson(prediction, target)
```

但最終策略選擇不能看 RMSE/Pearson。

最終 judge：

```text
24D NAV
```

---

# 10. Daily Decision

```text
D-1 data
↓
features
↓
LightGBM prediction
↓
sort 150 stocks
↓
predicted return = score
↓
Top 25
↓
existing portfolio
```

---

# 11. Portfolio

完全沿用 momentum baseline：

```text
n_holdings = 25
keep_rank = 35

weighting = equal
invested = 0.88
cap_scale = 0.9

rebalance_threshold = 0.10
freeze_last_days = 3
```

不要讓 LightGBM 控制 position sizing。

---

# 12. Code Structure

新增：

```text
lgbm_strategy/
├── __init__.py
├── features.py
├── dataset.py
├── model.py
└── strategy.py
```

責任：

```text
features.py
→ feature computation

dataset.py
→ matured labels / train split

model.py
→ fit / predict

strategy.py
→ orchestration
```

Reuse：

```text
competition/
autots_strategy/portfolio.py
research/
existing data loaders
```

禁止重寫第二套 planner / ledger。

---

# 13. Clean Code

要求：

```text
small functions
single responsibility
type hints
dataclass configs
explicit input/output
deterministic sorting
explicit NaN handling
no hidden globals
no duplicated logic
```

避免：

```text
magic numbers
huge functions
deep nesting
silent fallback
copy-paste feature code
unrelated refactor
```

---

# 14. Tests

至少：

```text
test_lgbm_features.py
test_lgbm_dataset.py
test_lgbm_causality.py
test_lgbm_strategy.py
```

必驗：

### Feature correctness

```text
20 / 40 / 60 returns
volatility
MA gap
per-symbol rolling
```

### Causality

修改 D 之後資料：

```text
shuffle
multiply
delete
corrupt
```

必須：

```text
features unchanged
training data unchanged
predictions unchanged
weights unchanged
```

### Label maturity

```text
latest_label_end <= D-1
```

### Determinism

相同 input：

```text
prediction identical
portfolio identical
```

---

# 15. Experiment Protocol

## Stage 0 — Tests

全部 unit / causality tests PASS。

---

## Stage 1 — Smoke

只從 **2019–2022 DEV** 中抽：

```text
6 episodes
```

比較：

```text
Momentum 20D
AutoTS
LightGBM JPX2
```

只檢查：

```text
pipeline
runtime
causality
rule compliance
```

不根據 6 episodes tuning。

---

## Stage 2 — Full Development

只跑：

```text
2019-01 → 2022-12
```

全部可用 24D episodes。

---

## Stage 3 — Validation

模型設計固定後：

```text
2023-01 → 2024-12
```

與：

```text
Momentum
AutoTS
```

公平比較。

Validation 後 freeze。

---

## Stage 4 — Holdout

只跑一次：

```text
2025-01 → 2026-09
```

不得用結果回頭修改策略。

---

# 16. Metrics

Primary：

```text
paired 24D return vs momentum_20d
```

報告：

```text
mean
median

paired mean Δ
paired median Δ

P25
P10
worst

MDD
turnover
transaction cost

warnings
disqualification
runtime
```

---

# 17. Success Gate

進 Stage 2 research extension 前必須：

```text
paired median Δ vs momentum > 0
AND
paired mean Δ vs momentum > 0
AND
no worse disqualification
```

否則：

```text
STOP_LGBM_BASELINE
```

不要暴力調參救模型。

---

# 18. Automation

Agent one-shot：

```text
inspect repo
↓
implement
↓
tests
↓
2019–2022 smoke/full dev
↓
2023–2024 validation
↓
freeze
↓
2025–2026 holdout
↓
generate comparison report
```

耗時工作寫成：

```text
.sh / CLI / resumable jobs
```

Agent不要等待並浪費 token。

只讀：

```text
summary.json
comparison.csv
failures
short logs
```

---

# 19. Output

```text
research/results/lgbm_jpx2/
├── summary.md
├── summary.json
├── comparison.csv
└── failures.md
```

`summary.md` 必須清楚分：

```text
2019–2022 DEV
2023–2024 VALIDATION
2025–2026 HOLDOUT
```

最後只給：

```text
PASS_TO_STAGE_2
```

或：

```text
STOP_LGBM_BASELINE
```

---

# 20. Definition of Done

```text
[ ] No evaluation episodes before 2019
[ ] DEV = 2019–2022
[ ] Validation = 2023–2024
[ ] Holdout = 2025–2026/09

[ ] LightGBM implemented
[ ] causal features
[ ] mature labels only
[ ] deterministic
[ ] tests PASS

[ ] Momentum comparison
[ ] AutoTS comparison
[ ] full report generated

[ ] holdout accessed only after freeze
[ ] no unrelated refactor
[ ] no unnecessary large files committed
```

## Final Principle

```text
2019–2026 only for evaluation.

Pre-2019 data may only serve as
historical lookback for early-2019 decisions.
```
