# V4 Strategy Research Spec

## 1. Objective

實作並比較：

```text
V4-Momentum
V4-Adaptive
V4-Direct
```

主要問題：

> 哪種方法在 24-session competition horizon 下，能產生最穩健且可執行的 terminal NAV？

不要以長期 CAGR 作 primary objective。

---

# 2. Shared Feature Layer

新增：

```text
src/v4_features.py
```

至少建立以下 causal features。

## Returns

```text
R1
R3
R5
R10
R20
R30
R60
```

## Trend

```text
EMA5
EMA10
EMA20
EMA50
EMA100
EMA200

price / EMA
EMA slope
EMA crossover state
```

## Volume

```text
volume_5 / lagged_volume_20
volume percentile
volume acceleration
```

## Volatility

```text
5D volatility
20D volatility
5D / 20D volatility ratio
ATR-like daily range measure if cleanly available
```

## Momentum quality

例如：

```text
20D return / 20D volatility
positive-day ratio
drawdown from recent high
```

所有 features：

```text
must be available at D-1
```

---

# 3. Strategy A — V4-Momentum

這是 structural baseline，不是 V4 最終答案。

建立 cross-sectional score。

不要只固定一組：

```text
5/10/20
```

search space 至少涵蓋：

```text
short horizon:
3 / 5 / 10

medium:
5 / 10 / 15 / 20

long:
20 / 30 / 60
```

score families：

```text
pure momentum
momentum + trend
momentum + volume
momentum + volatility filter
full multi-factor
```

portfolio structural search：

```text
target_count:
20 / 22 / 25 / 28 / 30

max_replacements_per_day:
0 / 1 / 2 / 3

replacement_margin:
0 / 0.05 / 0.10 / 0.15 / 0.20
```

V4-Momentum 必須測：

```text
equal weight
vs
score-proportional bounded weight
```

---

# 4. Strategy B — V4-Adaptive

核心架構：

```text
alpha experts
↓
walk-forward validation
↓
adaptive weighting
↓
probabilistic confidence
↓
portfolio optimizer
```

---

# 5. Alpha Experts

不要一開始使用大型 deep model。

先建立 heterogeneous but simple experts：

```text
Expert 1:
3–5D short momentum

Expert 2:
10–20D momentum

Expert 3:
30–60D momentum

Expert 4:
trend following

Expert 5:
volume-confirmed momentum

Expert 6:
short-term mean reversion

Expert 7:
risk-adjusted momentum

Expert 8:
cross-sectional linear model
```

可再加入：

```text
Ridge
ElasticNet
Gradient Boosting
Random Forest
```

只有在 walk-forward evidence 顯示有價值時保留。

不要因為模型複雜而預設更好。

---

# 6. Forecast Horizons

至少建立：

```text
1D
5D
10D
20D
```

future return targets。

另外研究 competition-aware horizon：

```text
remaining_session_return
```

例如 episode Day 4：

```text
remaining horizon = 20 sessions
```

Day 20：

```text
remaining horizon = 4 sessions
```

此 branch 必須單獨做 ablation。

禁止任何 future leakage。

---

# 7. Walk-Forward Model Evaluation

模仿 AutoTS 的思想，但保持可控。

每個 decision date：

```text
past training data only
↓
historical walk-forward validation
↓
estimate each expert's recent performance
↓
assign ensemble weight
```

不要：

```text
pick model using current/future episode result
```

adaptive weighting 可測：

```text
inverse validation error
rank IC
recent realized portfolio utility
exponential decay of historical performance
```

模型權重必須：

```text
deterministic
reproducible
bounded
```

---

# 8. Probabilistic Confidence

V4-Adaptive 不只輸出：

```text
expected_return
```

還必須輸出：

```text
uncertainty
confidence
```

至少研究兩種方法：

## Ensemble dispersion

```text
different experts disagree
→ uncertainty high
```

## Historical residual distribution

```text
forecast error history
→ empirical prediction interval
```

每檔至少得到：

```text
expected_return
lower_bound
upper_bound
confidence
```

研究 Catlin-style agreement：

```text
all / most experts positive
→ high confidence

mixed signs
→ lower confidence
```

不要假設這一定有效。

做 ablation：

```text
expected return only
vs
expected return × confidence
```

---

# 9. Regime Module

新增：

```text
src/v4_regime.py
```

V4 Core 先只使用 Taiwan-market causal data。

features：

```text
universe breadth
median R5
median R20
cross-sectional dispersion
market volatility
percentage above EMA20
percentage above EMA50
```

輸出：

```text
risk_on
neutral
risk_off
```

以及可選：

```text
sector leadership
```

Regime 第一版只允許調整：

```text
ensemble weights
cash target
risk penalty
rotation budget
```

不要做大量 hand-written regime rules。

---

# 10. Optional V4.1 Overnight Branch

不要混入 V4 Core winner selection。

獨立 branch 可研究：

```text
SOX
NASDAQ
S&P500
TSM ADR
Taiwan futures overnight
USD/TWD
```

要求：

```text
timestamp-valid before submission cutoff
```

比較：

```text
V4 Core
vs
V4 + overnight
```

只有 validation evidence 成立才採用。

---

# 11. Portfolio Optimizer

新增：

```text
src/v4_portfolio_optimizer.py
```

input：

```text
expected returns
confidence
covariance / risk estimate
current holdings
cash
transaction costs
competition constraints
```

核心 utility：

\[
U(w)
=
\mu^\top w
-
\lambda w^\top \Sigma w
-
\gamma Turnover(w,w_{prev})
-
EstimatedTradingCost
\]

subject to：

```text
long only

20–30 names

normal position <= 10%

2330 <= 25%

cash < 25%

no negative cash
```

不要直接讓 optimizer 假設 fractional shares 最後一定可行。

必須經過：

```text
target weights
↓
official lot-sizing
↓
board-lot rounding
↓
compliance repair
```

---

# 12. Portfolio Optimization Algorithms

至少公平比較：

### Baseline

```text
Top-N equal weight
```

### Score weighted

```text
bounded normalized score
```

### Risk-adjusted continuous optimization

### Differential-Evolution-style optimizer

DE 是 research candidate，不是 mandatory winner。

搜尋變數可以是：

```text
candidate subset
risk penalty
target weights
```

但必須有限制：

```text
deterministic random seed
bounded evaluations
no future data
```

---

# 13. Strategy C — V4-Direct

新增：

```text
src/v4_direct_optimizer.py
```

研究問題：

> 是否需要先準確預測 individual stock returns，才能得到好的 24D portfolio？

建立 direct branch：

```text
features
↓
portfolio score / allocation parameters
↓
historical portfolio utility
```

可以使用：

```text
regularized linear scoring
penalized regression
bounded nonlinear search
```

直接 optimize walk-forward portfolio objective。

禁止：

```text
optimize current validation period directly
```

必須 development fit → validation evaluate。

---

# 14. Turnover

V4 必須明確付出 turnover cost。

每次換股不只是 signal improvement。

必須比較：

\[
ExpectedImprovement
>
TradingCost + Margin
\]

因此研究：

```text
rotation = 0 / 1 / 2 / 3 names per day
```

不要 full portfolio daily rebalance，除非 evidence 顯示收益足以覆蓋成本。

---

# 15. Initial Deployment

因為 competition 只有 24 sessions：

記錄：

```text
Day-1 invested ratio
Day-3 invested ratio
Day-5 invested ratio
```

避免策略因過度保守導致前幾天大量 cash idle。

---

# 16. Required Ablations

至少產生：

```text
A0 V3 baseline

A1 V4 momentum only

A2 + rotation

A3 + confidence

A4 + regime

A5 + optimized weights

A6 full V4-Adaptive

B1 V4-Direct

B2 V4-Direct + regime
```

所有版本：

```text
same execution
same ledger
same windows
same costs
```

---

# 17. Output

每次 experiment 保存：

```text
config
features/model identity
episode results
daily ledger
orders
holdings
predictions
confidence
regime
optimizer output
failures
SHA256
```

最終必須能回答：

```text
Which component actually improved performance?

Was improvement caused by:
forecasting?
confidence?
regime?
rotation?
portfolio optimization?
execution assumption?
```

不允許只回報：

```text
full V4 beats baseline
```

而不知道原因。
