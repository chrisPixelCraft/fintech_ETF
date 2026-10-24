# V4 Master Spec — Competition Architecture

## 1. Goal

在 `fintech_ETF` 新增 V4 research + execution framework：遵守比賽規則與資訊時點，從 NT$1B、零持股最大化約 24 個交易日 terminal NAV。

公平比較 V4-Momentum、V4-Adaptive、V4-Direct；由 development / validation evidence 決定架構，不預設 `x0352`、V3 momentum 或外部方法最佳。

## 2. Preserve Existing Work

V3 artifacts 為 immutable baseline evidence；不得破壞、覆寫或重新定義：

```text
best_v2.py
src/strategy_24d.py
configs/competition_24d_final.json
outputs/best_v2/
outputs/24d*/
reports/24d*
```

V4 使用獨立 namespace：

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

## 3. Competition Rules — Hard Constraints

以 repo 官方文件為準；不得改合規規則提升報酬。

```text
initial_cash = NT$1,000,000,000
allowed universe = official 150 securities
20 <= count <= 30
0 <= cash_ratio < 25%
normal stock <= 10%; 2330 <= 25%
board lot only; shares % 1000 == 0
no short selling; no day trading
commission = 0.1425% on buy / sell
sell tax = 0.3%
target_shares = floor(target_weight * previous_day_NAV / previous_day_close / 1000) * 1000
```

## 4. Causality

交易日 `t` 決策只能用 `information timestamp <= t-1 market close`。

禁止 t-day close / high / low / volume / turnover、future constituents / corporate actions / labels 進入決策。`t` 日資料僅供 execution、settlement、mark-to-market、future training label。所有 feature / model / regime / portfolio code 須通過 causal tests。

## 5. Correct Execution Model

Canonical execution：

```text
official_daily_average_price = official_trading_value / official_trading_volume
D-1 information → t-day order → t-day official average-price execution → fees/tax → settlement
```

V3 `DAILY_OPEN_RESEARCH_PROXY` 不能當 canonical。缺官方成交金額或成交量時，不得默換 Open / Close；必須 explicit proxy mode + separate status + BLOCK_CANONICAL_V4。Proxy research 與 official-average-price track 分開標示。

## 6. V4 Strategy Families

### A. V4-Momentum

簡單、可解釋 baseline：3/5/10/20/30/60D returns、trend、volume、MACD、volatility、controlled rotation。以 official execution 重新搜尋，不沿用 V3 參數。

### B. V4-Adaptive

主要研究方向：

```text
multiple alpha experts → walk-forward model evaluation → adaptive ensemble
→ expected return + uncertainty/confidence → market regime → portfolio optimizer → official execution
```

參考 M6 AutoTS / Colin Catlin 的 multiple candidate forecasting models、adaptive selection、ensemble、probabilistic uncertainty、recent-regime-aware validation，不照搬 AutoTS library。

### C. V4-Direct

獨立研究 features/history → direct portfolio objective → target weights。參考 M6 single-stage portfolio optimization work，檢驗 portfolio optimization 是否需要 accurate individual-return prediction；不強制 features → individual return forecast → optimizer。

## 7. Architecture

新增 `src/` 模組：

- `v4_features.py`：causal reusable features。
- `v4_alpha.py`：deterministic alpha experts。
- `v4_forecast.py`：forecast models、walk-forward selection、ensemble、uncertainty。
- `v4_regime.py`：market breadth / volatility / momentum regime。
- Optimizer 分工：
	- `v4_portfolio_optimizer.py`：prediction → constrained portfolio。
	- `v4_direct_optimizer.py`：single-stage/direct branch。
- Execution 與 interface：
	- `v4_execution.py`：official average-price execution；`v4_ledger.py`：competition-compatible accounting。
	- `v4_strategy.py`：統一 interface。

## 8. Unified Strategy Interface

三族共用：

```python
strategy.generate_target(
    decision_date,
    history,
    portfolio_state,
    competition_state,
)
```

輸出 target symbols、target weights、expected return / score、confidence、reason、strategy family、model/ensemble identity、regime。共用 portfolio → D-Plan planner → execution → ledger，不得採不同 accounting rules。

## 9. Alpha vs Compliance

分開判定：

- Strategy quality：ALPHA_PASS / ALPHA_FAIL；看 terminal return、MDD、turnover、lower-tail performance。
- Competition validity：COMPLIANCE_PASS / BLOCK_SUBMISSION；看 odd holding ambiguity、missing official price、Active Share uncertainty、submission contract unknown。

可同時 ALPHA_PASS、BLOCK_SUBMISSION。未解歷史 corporate-action 問題不抹除 alpha 統計；alpha success 也不代表正式合規。

## 10. External Method Inspirations

- Existing repository：competition ledger、D-Plan logic、compliance planner、bounded tuning、chronological evaluation、audit、hashing、fail-closed design。
- M6 AutoTS winner：adaptive model selection、多模型、ensemble、prediction uncertainty、horizon alignment。
- M6 Ai / Liu / Lin：分開 prediction 與 portfolio allocation；robust features、portfolio optimizer、Differential Evolution-style search。
- Single-stage M6：direct features → portfolio。
- ETF Global Portfolio Challenge：短賽受 regime、sector/theme leadership 影響；不複製 leveraged ETF concentration、short positions 或 contest-rule-specific tricks。

## 11. Research Philosophy

Hypothesis → bounded experiment → validation → freeze → holdout。優先 architecture improvement，不以數千參數追逐 2025–2026 或 tiny parameter improvement。

## 12. Required Final Families

至少產生 v4_momentum、v4_adaptive、v4_direct、v3_baseline；四者共用 data cutoff、episode windows、transaction costs、execution model、portfolio constraints、ledger、evaluation metrics。

## 13. Definition of Done

Spec 1 完成須有：

1. 清楚 V4 module boundaries。
2. 保留 V3 frozen artifacts。
3. Canonical official-average-price execution interface。
4. Unified strategy interface。
5. Three V4 strategy families。
6. Separate alpha/compliance status。
7. Reproducible config-driven experiment design。
8. Automated tests。

Spec 2、Spec 3 完成前：DO NOT declare a V4 winner.
