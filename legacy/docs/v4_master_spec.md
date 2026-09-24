# V4 Master Spec — Competition Architecture

## 1. Goal

在現有 `fintech_ETF` repository 中新增 V4 research + execution framework。

V4 的唯一主要研究目標：

> 在完全遵守比賽規則、只使用決策時可取得資訊的前提下，最大化從 NT$1B、零持股開始的約 24 個交易日 terminal NAV。

不要假設現有 `x0352`、V3 momentum 或任何外部競賽方法一定是最佳解。

V4 必須公平比較三個 strategy families：

```text
V4-Momentum
V4-Adaptive
V4-Direct
```

最後由歷史 development / validation evidence 決定是否有值得採用的架構。

---

# 2. Preserve Existing Work

不得破壞、覆寫或重新定義：

```text
best_v2.py
src/strategy_24d.py
configs/competition_24d_final.json
outputs/best_v2/
outputs/24d*/
reports/24d*
```

V3 所有 artifacts 保留為 immutable baseline evidence。

V4 使用新的 namespace：

```text
src/v4_*
scripts/v4_*
config/v4_*
configs/v4_*
outputs/v4/
reports/v4_*
docs/v4_*
tests/test_v4_*
```

---

# 3. Competition Rules — Hard Constraints

沿用 repo 中官方文件為 authoritative source。

至少包含：

```text
initial_cash = NT$1,000,000,000

allowed universe = official 150 securities

holdings:
20 <= count <= 30

cash:
0 <= cash_ratio < 25%

single-name cap:
normal stock <= 10%
2330 <= 25%

orders:
board lot only
shares % 1000 == 0

no short selling
no day trading

commission:
0.1425% on buy
0.1425% on sell

sell tax:
0.3%
```

D-Plan target shares 必須依官方公式：

```text
target_shares
=
floor(
    target_weight
    * previous_day_NAV
    / previous_day_close
    / 1000
) * 1000
```

V4 不得靠修改合規規則提升回測報酬。

---

# 4. Causality

交易日 `t` 的 decision 只能使用：

```text
information timestamp <= t-1 market close
```

禁止：

```text
t-day close
t-day high
t-day low
t-day volume
t-day turnover
future constituents
future corporate actions
future labels
```

進入 `t` 日決策。

`t` 日資料只能用來：

```text
execution
settlement
mark-to-market
future training label
```

所有 feature / model / regime / portfolio code 必須通過 causal tests。

---

# 5. Correct Execution Model

V3 的：

```text
DAILY_OPEN_RESEARCH_PROXY
```

不能作為 V4 canonical execution。

V4 canonical execution：

```text
official_daily_average_price
=
official_trading_value
/
official_trading_volume
```

亦即：

```text
D-1 information
→ t-day order
→ t-day official average-price execution
→ fees/tax
→ settlement
```

如果某歷史日期沒有官方成交金額或成交量：

```text
DO NOT silently substitute Open
DO NOT silently substitute Close
```

必須：

```text
explicit proxy mode
+
separate status
+
BLOCK_CANONICAL_V4
```

允許保留 proxy research track，但不得與 official-average-price track 混稱。

---

# 6. V4 Strategy Families

## A. V4-Momentum

目的：

建立簡單、可解釋、高品質 baseline。

核心：

```text
3/5/10/20/30/60D returns
trend
volume
MACD
volatility
controlled rotation
```

不要假設 V3 參數。

重新在 official execution 下搜尋。

---

## B. V4-Adaptive

主要研究方向。

架構：

```text
multiple alpha experts
↓
walk-forward model evaluation
↓
adaptive ensemble
↓
expected return + uncertainty/confidence
↓
market regime
↓
portfolio optimizer
↓
official execution
```

方法思想參考：

```text
M6 AutoTS / Colin Catlin
```

重點不是複製 AutoTS library，而是：

```text
multiple candidate forecasting models
adaptive selection
ensemble
probabilistic uncertainty
recent-regime-aware validation
```

---

## C. V4-Direct

作為 independent research branch。

架構：

```text
features/history
↓
direct portfolio objective
↓
target weights
```

不要強迫：

```text
features
→ individual return forecast
→ optimizer
```

方法思想參考：

```text
M6 single-stage portfolio optimization work
```

用來驗證：

> portfolio optimization 是否真的需要 accurate individual-return prediction。

---

# 7. Architecture

新增：

```text
src/
├── v4_features.py
├── v4_alpha.py
├── v4_forecast.py
├── v4_regime.py
├── v4_portfolio_optimizer.py
├── v4_direct_optimizer.py
├── v4_execution.py
├── v4_ledger.py
└── v4_strategy.py
```

Responsibilities：

### `v4_features.py`

只建立 causal reusable features。

### `v4_alpha.py`

建立 deterministic alpha experts。

### `v4_forecast.py`

forecast models、walk-forward selection、ensemble、uncertainty。

### `v4_regime.py`

market breadth / volatility / momentum regime。

### `v4_portfolio_optimizer.py`

prediction → constrained portfolio。

### `v4_direct_optimizer.py`

single-stage/direct branch。

### `v4_execution.py`

official average-price execution。

### `v4_ledger.py`

competition-compatible accounting。

### `v4_strategy.py`

統一 interface。

---

# 8. Unified Strategy Interface

三個 families 必須共用：

```python
strategy.generate_target(
    decision_date,
    history,
    portfolio_state,
    competition_state,
)
```

輸出至少：

```text
target symbols
target weights
expected return / score
confidence
reason
strategy family
model/ensemble identity
regime
```

再由同一個：

```text
portfolio → D-Plan planner → execution → ledger
```

處理。

不得讓三種策略使用不同 accounting rules。

---

# 9. Alpha vs Compliance

V4 必須把兩個概念分開：

## Strategy quality

```text
ALPHA_PASS
ALPHA_FAIL
```

例如：

```text
terminal return
MDD
turnover
lower-tail performance
```

## Competition validity

```text
COMPLIANCE_PASS
BLOCK_SUBMISSION
```

例如：

```text
odd holding ambiguity
missing official price
Active Share uncertainty
submission contract unknown
```

一個 strategy 可以：

```text
ALPHA_PASS
BLOCK_SUBMISSION
```

不得因尚未釐清的歷史 corporate-action 問題，把 alpha research 統計全部丟掉。

但也不得把 alpha success 說成正式比賽已合規。

---

# 10. External Method Inspirations

V4 可以借鑑，但不得盲目複製：

### Existing repository

```text
competition ledger
D-Plan logic
compliance planner
bounded tuning
chronological evaluation
audit
hashing
fail-closed design
```

### M6 AutoTS winner

```text
adaptive model selection
multiple forecasting models
ensemble
prediction uncertainty
horizon alignment
```

### M6 Ai / Liu / Lin

```text
separate:
prediction
from
portfolio allocation

robust features
portfolio optimizer
Differential Evolution-style search
```

### Single-stage M6 portfolio work

```text
direct features → portfolio
```

### ETF Global Portfolio Challenge

只採用 high-level lesson：

```text
short contests are regime-sensitive
sector/theme leadership matters
```

不要複製：

```text
leveraged ETF concentration
short positions
contest-rule-specific tricks
```

---

# 11. Research Philosophy

不要：

```text
search thousands of parameters
until 2025–2026 looks good
```

要：

```text
hypothesis
→ bounded experiment
→ validation
→ freeze
→ holdout
```

優先找：

```text
architecture improvement
```

而不是：

```text
tiny parameter improvement
```

---

# 12. Required Final Families

至少產生：

```text
v4_momentum
v4_adaptive
v4_direct
v3_baseline
```

四者使用完全相同：

```text
data cutoff
episode windows
transaction costs
execution model
portfolio constraints
ledger
evaluation metrics
```

---

# 13. Definition of Done

Spec 1 完成後，repository 必須具備：

```text
1. 清楚 V4 module boundaries
2. 不修改 V3 frozen artifacts
3. canonical official-average-price execution interface
4. unified strategy interface
5. three V4 strategy families
6. separate alpha/compliance status
7. reproducible config-driven experiment design
8. automated tests
```

在 Spec 2、Spec 3 完成以前：

```text
DO NOT declare a V4 winner.
```
