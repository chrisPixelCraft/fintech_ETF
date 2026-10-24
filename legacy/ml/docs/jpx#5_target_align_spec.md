# LightGBM Target Alignment Study

## 0. Goal

只比較 150 檔 → Top25 的 target formulation：A Raw Return、B Relative Alpha、C Execution-Aligned Relative Alpha。共用 features、LightGBM、training、Top25、portfolio、planner、ledger、episodes，差異歸因於 target。

## 1. Research Questions

依序檢驗 Relative Alpha 是否勝 Raw Return、成交時間對齊是否再改善，以及改善來自 signal quality、rank stability、turnover 或無實質改善。

## 2. Hard Scope

2.1 僅改 target definition / audit / tests、diagnostics/reporting。

2.2 Freeze LightGBM parameters、features、lookback、refit cadence、portfolio、turnover gate、planner、execution、cost model、episode set。

2.3 禁止新增 features、JPX5 features、LightGBMRanker、XGBoost、Optuna、parameter tuning、SOX / ADR / 夜盤、法人、sector features、regime、TopK-drop、score smoothing、portfolio optimizer、ensemble。看結果後不得擴 scope。

## 3. Existing Baseline

分支 `claude/recent-window` 已完成 JPX2 LightGBM Raw Return：70 dev episodes，2019–2021，month_start + mid_month。Momentum +3.41%、Raw LGBM +3.15%、AutoTS +2.99%；沿用 benchmark。

## 4. Architecture

沿用 `lgbm_strategy/`：features.py 只算 features；新增 targets.py 只算 labels；dataset.py 合併 features + matured labels；model.py 不知 target semantics；strategy.py 只 orchestration。不建第二套 LightGBM。

## 5. Target Modes

Config 增加 `target_mode`，僅 raw_return / relative_alpha / execution_alpha；不用散落的 `if alpha_v2...`。

## 6. Target A — Raw Return

CONTROL：完全重現 JPX2 baseline。Feature origin `t`：

```text
r*{i,t}^{(h)} = P*{i,t+h} / P_{i,t} -1
horizon = 10
```

## 7. Target B — Relative Alpha

```text
r_{i,t}^{10} = 個股 forward return
r*{m,t}^{10} = (1 / N_t) × Σ_i r*{i,t}^{10}
alpha*{i,t} = r*{i,t}^{10} - r_{m,t}^{10}
```

7.1 Market mean 只用 competition 150 stocks 中 finite matured forward labels；不用 0050、Top25、feature_ready subset、future holdings。

7.2 Finite labels < 20 時整個 origin date target invalid；

7.3 測試每個有效 origin 的 mean(relative_alpha) ≈ 0。

## 8. Target C — Execution-Aligned Relative Alpha

目前 target 起於 D-1 close；真實 PnL 起於 D 日成交（08:55 前決策）。改為 Feature origin = D-1、Entry = D execution price、Exit = D+10 execution price：

```text
r^{exec}_{i,t} = X_{i,t+1+h} / X_{i,t+1} - 1
t = feature date
t+1 = actual next trading session / execution date
h = 10
X = historical execution price
```

## 9. Execution Price

與 backtest 共用 price source：execution.mode = auto、proxy = hlc3。Official_vwap 存在則 X = official_vwap，否則 X = HLC3 proxy；HLC3 = (High+Low+Close)/3。

## 10. Critical Rule — Single Source of Truth

`targets.py` reuse competition execution helper，不重寫 official VWAP fallback 或 HLC3。必須 backtest execution price == training execution-label price。

## 11. Historical Coverage Limitation

2019–2021 DEV 幾乎無 official VWAP，execution_alpha ≈ HLC3-proxy-aligned target；2025–2026 大部分有，才近似 actual competition execution-aligned target。前段只驗證 simulator execution semantics，不得宣稱驗證官方 VWAP target。

## 12. Label Timeline

Raw / Relative：feature t → t+10 label end。Execution：feature t → trade t+1 → t+1+10 label end；maturity 晚 1 session。

## 13. Label Maturity

Decision day `D` 的 training labels 須 label_end <= D-1；execution target 須 origin + 1 + horizon <= D-1。違反 FAIL HARD；禁止 silent drop/fallback 隱藏 leakage。

## 14. Features

沿用 JPX2，不新增 R2、R5、ADV rank、stock ID、sector：

```text
Open High Low Close Volume
return_20 return_40 return_60
volatility_20 volatility_40 volatility_60
MA_gap_20 MA_gap_40 MA_gap_60
```

## 15. Model

`model.py` semantics 不變，不 tuning：

```text
LightGBMRegressor
objective = regression; boosting = gbdt
learning_rate = 0.005; n_estimators = 3000
early_stopping = 300; validation metric = Pearson
random_state = 2026; n_jobs = 1
```

## 16. Training

Lookback = 750 sessions、refit_every = 5。每次 D−1 cutoff → matured labels → latest ≤750 origins → chronological train/validation → purge horizon overlap → fit。

## 17. Validation Split

維持 Earlier ~80% train → purge horizon gap → Latest ~20% validation，禁止 random split。

## 18. Prediction

所有 mode：D-1 features → LightGBM score → descending cross-sectional rank → Top25。不加 score normalization、rank transform、calibration。

## 19. Portfolio

Freeze，不因 LGBM turnover 高而改：

```text
n_holdings = 25; keep_rank = 35
weighting = equal; invested = 0.88; cap_scale = 0.9
rebalance_threshold = 0.10; freeze_last_days = 3
```

## 20. Configs

建立 research/configs/baseline_lgbm_raw.json、research/configs/baseline_lgbm_alpha.json、research/configs/baseline_lgbm_execution_alpha.json。除 `target_mode` 外，LightGBM / portfolio config identical。

## 21. Parity Gate

新 experiment 前用 target_mode = raw_return 重跑 3–6 個既有 episode；training rows、predictions、target weights、episode return 須與舊 JPX2 一致，否則 STOP，先修 parity。

## 22. Unit Tests

新增 tests/test_lgbm_targets.py；更新 test_lgbm_dataset.py、test_lgbm_causality.py、test_lgbm_strategy.py。

## 23. Required Target Tests

- Raw target：bit-identical / numerical-identical。
- Alpha：mean(alpha_t) ≈ 0。
- 抽樣日期：label execution price == backtest execution price helper。
- Timeline：raw label_end = t+h；execution label_end = t+1+h。
- Official_vwap NaN：exactly same HLC3 fallback logic。

## 24. Causality

Dataset B 僅 multiply / shuffle / delete / replace dataset A 的 D 之後資料；features、matured labels、training rows、predictions、weights 須 identical。

## 25. Future Execution Corruption Test

改 D-1 之後 execution price 不得影響 D decision：相關 labels 尚未 mature。

## 26. Determinism

同 data / config / seed 的 targets、best_iteration、predictions、weights 須 identical。

## 27. Diagnostics — Target

每個 refit 記 target_mode、target_mean、target_std、cross_section_count、label_start、label_end、official_vwap_share、proxy_hlc3_share。

## 28. Diagnostics — Model

沿用 best_iteration、train_pearson、validation_pearson、n_train_rows、n_validation_rows；不作最終 selection。

## 29. Diagnostics — Rank Stability

每日記 Top25 overlap previous day、Top35 overlap、entries、exits、one-way turnover。Overlap*t = |Top25_t ∩ Top25*{t-1}| / 25，只供 diagnosis。

## 30. Experiment Stage 0

ALL unit / causality / parity tests PASS 才 backtest。

## 31. Stage 1 — Smoke

同 6 dev episodes 跑 Momentum、Raw LGBM、Alpha LGBM、Execution Alpha LGBM、AutoTS；僅查 correctness、runtime、no warnings、no disqualification，不 tuning。

## 32. Stage 2 — Full DEV

固定 2019–2021、70 episodes、month_start + mid_month，不增減窗口。

## 33. Primary Comparison

依序：Q1 Relative Alpha vs Raw Return（去 market component）；Q2 Execution Alpha vs Relative Alpha（成交區間對齊）；Q3 Execution Alpha vs Momentum（能否勝 baseline）。

## 34. Metrics

每策略報 Mean / Median 24D return、P25、P10、Worst、Mean MDD、Turnover、Transaction cost、Warnings、Disqualification。

## 35. Paired Metrics

每組報 paired mean Δ、paired median Δ、win rate、bootstrap 95% CI，不只 aggregate mean。

## 36. Signal / Cost 分開

Signal 報 paired gross/net result、rank stability、validation Pearson、Top25 overlap；Cost 報 turnover、cost、entries/exits。未跑 counterfactual，不得因 cost difference > net gap 宣稱「沒成本一定贏」。

## 37. Success Logic

Relative Alpha vs Raw 的 mean Δ > 0 且 median Δ > 0 → RELATIVE_ALPHA_SUPPORTED；Execution Alpha vs Alpha 同門檻 → EXECUTION_ALIGNMENT_SUPPORTED。

## 38. Final DEV Gate

最佳 target 對 Momentum 的 paired mean Δ > 0、paired median Δ > 0、no worse disqualification 全成立，才 PASS_TARGET_STUDY → Validation。

## 39. Interesting Failure Case

Execution Alpha > Raw LGBM 但 < Momentum → TARGET_IMPROVED_MODEL_STILL_WEAK。JPX5 features / Learning-to-Rank 留待下階段，不立即 tuning。

## 40. If All Fail

三 targets 均無明顯改善 → STOP_TARGET_FORMULATION，問題不只 target；下一方向為 JPX5 behavioral features / direct ranking objective。

## 41. Validation

僅 PASS_TARGET_STUDY 才跑 2022–2024；設定 frozen，不因 validation 改 target。

## 42. Holdout

Freeze 後才正式跑 2025–2026/09 一次；特別報 official execution-label share（VWAP coverage 高）。

## 43. Automation

One-shot：inspect target/execution helpers → minimal target abstraction → raw parity → tests → smoke → 70 DEV episodes × 3 target modes → paired comparison → gate → if PASS validation → freeze → if allowed holdout → report。

## 44. Long Jobs

耗時 backtest 用 CLI / shell、resumable、安全處 parallel；不長時間 tail logs、隔幾秒 poll 或將 full ledgers 載入 context。

## 45. Token Efficiency

正常只讀 summary.json、comparison.csv、target_diagnostics.csv、model_diagnostics.csv、failures.md；failure 才查 ledger、orders、per-day prediction。

## 46. Clean Code

Single responsibility、盡量 pure target functions、explicit target_mode enum、type hints、dataclass config、deterministic sorting、explicit NaN handling、fail loudly。

禁止 duplicated execution logic、magic offsets、target_v2 / final_target names、large orchestrator functions、hidden future access、silent fallback、unrelated refactor。

## 47. Naming

用 RawReturnTarget、RelativeAlphaTarget、ExecutionAlphaTarget、entry_session、exit_session、execution_price、market_forward_return、relative_alpha；避免 v2、new、tmp、special_case。

## 48. Output

research/results/target_alignment/ 內輸出 summary.md、summary.json、comparison.csv、target_diagnostics.csv、model_diagnostics.csv、failures.md。

## 49. summary.md

僅四段：Decision（PASS_TARGET_STUDY / TARGET_IMPROVED_MODEL_STILL_WEAK / STOP_TARGET_FORMULATION）；Main Table（Momentum / Raw LGBM / Alpha LGBM / Execution Alpha LGBM / AutoTS，欄位 Mean、Median、Δ vs Momentum、Win Rate、P10、Worst、MDD、Turnover、Cost）；Target Comparison（Raw → Alpha、Alpha → Execution Alpha）；Next Action 最多 3 行。

## 50. Git Hygiene

只 commit source、tests、configs、small reports、comparison tables；不 commit run folders、model binaries、full ledgers、prediction dumps、cache、long logs。

## 51. Definition of Done

Target abstraction、raw parity、alpha zero mean、execution-price parity、maturity timeline、causality、determinism PASS；同 features / LightGBM / portfolio / 70 DEV episodes；Raw / Alpha / Execution Alpha 完成；paired comparisons、turnover / target diagnostics、summary 完成；holdout 不早於 freeze，無 unrelated refactor。

## 52. Final Principle

只問 What should the model predict?，不問 How do we make LightGBM more complicated?。固定 Raw Return → remove market component → align with actual execution → 才改 features/model/portfolio。
