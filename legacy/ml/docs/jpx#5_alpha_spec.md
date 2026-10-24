# LightGBM Relative-Alpha Baseline Spec

**固定 JPX2 model、features、portfolio 與 execution，只把 raw future return target 改為 cross-sectional relative alpha。**本輪檢驗：150 檔、Top-25、24 日台股 competition，預測相對強弱是否更有用；不靠 tuning 挽救負結果。

## 0–4. 假設與範圍

**0.** Raw-return LightGBM 已失敗；本輪只問 target 是否有問題。
**1.** 兩版皆 features → LightGBM → 10D prediction → rank 150 stocks → Top 25。新 target 是股票未來 10 天報酬減同期股票池平均：

\[
\alpha*{i,t}^{10D}=r*{i,t}^{10D}-\frac{1}{N*t}\sum_j r*{j,t}^{10D}
\]

**2.** Scope：只改 training target，以及必要 tests、audit、reporting。

**2.1 可改：**raw return → cross-sectional alpha。

**2.2 凍結：**LightGBM model、feature set、lookback、refit cadence、portfolio、planner、execution、transaction costs、episode set、evaluation protocol。避免同時變動而無法歸因。

**2.3 禁止：**JPX5 tail-only training、SecuritiesCode categorical、R2 mean reversion、131D momentum、new LightGBM params、LightGBMRanker、XGBoost、Optuna、AutoTS ensemble、SOX、NASDAQ、TSM ADR、台指夜盤、三大法人、sector features、regime detection、portfolio optimizer、new weighting method、new turnover threshold；留待後續 spec。

**3.** 沿用 `claude/recent-window` 已完成 baseline 的完全相同 episode IDs：Alpha LightGBM、JPX2、Momentum、AutoTS 逐 episode 配對。DEV = 2019–2021，month_start + mid_month，共 70 episodes；不重定義窗口。通過 DEV 才跑現有 recent-window split 的 2022–2024 Validation；Validation 通過並 freeze 才跑一次 2025–2026/09 Holdout。
**4.** Evaluation episode >= 2019；模型可用決策前歷史。2019 decision 最多回看 750 sessions，約 2016–2018；這不是 pre-2019 evaluation。

## 5–11. 實作與 Target

**5.** Reuse `lgbm_strategy/` 的 features.py、dataset.py、model.py、strategy.py，以及 autots_strategy/portfolio.py、competition/、research/；不建第二套框架。
**6.** 建議新增 targets.py；dataset.py 支援 target_mode，strategy.py 由 config 傳入。features.py 語意與模型不改；禁止複製 lgbm_alpha_strategy/。
**7.** 新增 API：

```python
build_target(returns, horizon, mode)
```

支援 `mode = "raw_return"`、`mode = "relative_alpha"`。

**8.** Raw target 保留舊公式，供 parity test：

\[
r*{i,t}^{h}=\frac{P*{i,t+h}}{P\_{i,t}}-1
\]

**9.** Relative alpha：先算每股 forward return，再逐 origin date 計算 market_return(t)，最後 alpha(i,t) = stock_forward_return(i,t) − market_return(t)。

**9.1 定義：**market_return(t) = competition universe 中 valid matured forward returns 的平均。

**9.2 股票池：**只含 competition 150-stock universe 與 finite forward label；不使用 0050、feature_ready filtering、predicted Top25 或 future portfolio members。市場 target 不應再被 feature eligibility 篩選。

**9.3 覆蓋門檻：**valid_labels < 20，整個 origin date target = invalid；不用極小樣本市場平均。

**10.** 每個有效 training date 必須 mean(alpha_i) ≈ 0；測試 abs(cross_section_mean_alpha) < numerical tolerance。
**11.** 固定 horizon = 10；本輪不測 5 / 20。10D alpha 成立後另開 horizon ablation。

## 12–19. 凍結的訓練與組合

**12.** Features 完全沿用 JPX2：Open、High、Low、Close、Volume；return_20、return_40、return_60；volatility_20、volatility_40、volatility_60；ma_gap_20、ma_gap_40、ma_gap_60。全部 per-symbol、chronological、causal、D-1 only。
**13.** 沿用 `model.py`，其他參數保持 library default：

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

**14.** lookback = 750 sessions、refit_every = 5 sessions。每次 Decision D：AsOfView through D-1 → matured labels only → latest ≤750 dates → chronological train / validation split → LightGBM。
**15.** Hard rule：label_end <= D-1；保留 assertion：

```python
latest_label_end <= view.date
```

違反即 FAIL，禁止 fallback。

**16.** Earlier ~80% train、latest ~20% validation，中間保留 horizon purge；禁止 random split、shuffle split、future normalization。
**17.** Decision D：D-1 data → features → current LightGBM → predicted alpha → cross-sectional sort。score = predicted alpha，不另作 normalization。
**18.** 沿用 Momentum / JPX2 portfolio，不能為救 turnover 改設定：

```text
n_holdings = 25
keep_rank = 35
weighting = equal
invested = 0.88
cap_scale = 0.9
rebalance_threshold = 0.10
freeze_last_days = 3
```

**19.** 前版 Momentum turnover = 3.41、JPX2 = 6.01，LightGBM 每 episode 多約 +0.77% transaction cost。本輪先測 alpha target 能否自然改善資訊與穩定度；同時改 turnover 會無法歸因。

## 20–29. 診斷、設定與測試

**20.** 可新增、不影響策略的每日 diagnostics：Top25 / Top35 overlap、entries、exits、one-way turnover、transaction cost。建議 top25_overlap_ratio = |Top25_t ∩ Top25_t-1| / 25；只診斷 ranking 穩定度，不 tuning。
**21.** 每次 refit 記錄 best_iteration、validation_pearson、train_pearson、target_std、prediction_std、n_train_rows、n_validation_rows、n_symbols、latest_label_end。
**22.** 每個 training window 加記 mean raw target、mean alpha target、std raw target、std alpha target、cross-sectional alpha mean error，確認實作。
**23.** 新增 research/configs/baseline_lgbm_alpha.json；只與 JPX2 config 差一項：

```json
{"target_mode": "relative_alpha"}
```

**24.** 舊 JPX2 config 維持同一 code path：

```json
{"target_mode": "raw_return"}
```

**25.** 新實驗前以 target_mode = raw_return 重跑少量已知 episodes；predictions、weights、episode return 必須與舊 JPX2 相同。否則 STOP，先修 parity。
**26.** 新增 tests/test_lgbm_targets.py；更新 test_lgbm_dataset.py、test_lgbm_causality.py、test_lgbm_strategy.py。
**27.** 測 raw return 與舊計算完全相同；有效日期 mean(alpha) ≈ 0；NaN 只用 finite labels 算 mean；<20 valid labels → date invalid。
**28.** 修改 decision D 之後資料（multiply、shuffle、delete、replace），features、training rows、alpha labels、model prediction、weights 必須不變。
**29.** 相同 data、config、seed 跑兩次，target、prediction、portfolio 必須 identical。

## 30–43. 實驗與決策

**30.** Stage 0：全部 tests 必須 ALL PASS。
**31.** Stage 1：`raw_return` 跑 3–6 existing dev episodes，確認 refactor parity。
**32.** Stage 2：momentum_20d、JPX2 raw-return LightGBM、Alpha LightGBM、AutoTS 使用相同 6 dev episodes；只驗 correctness、runtime、compliance，不依 smoke tuning。
**33.** Stage 3：相同 70 dev episodes（2019–2021、month_start + mid_month），不得挑窗口。
**34.** 比較優先序：Alpha vs Momentum → Alpha vs Raw-return LightGBM → Alpha vs AutoTS。
**35.** 每策略報 mean / median 24D return、P25、P10、worst、average MDD、turnover、transaction cost、warnings、disqualifications；配對報 mean Δ、median Δ、win rate、bootstrap 95% CI。
**36.** 分開診斷 signal（Alpha vs Raw-return、rank stability、paired returns）與 cost（turnover / cost difference）。沒有對應 counterfactual evidence，不得宣稱「沒有成本就會贏」。
**37.** 不建新 cost-free simulator。僅當現有 ledger 天然支援 transaction_cost = 0，且不用改 accounting semantics，才可 DIAGNOSTIC ONLY；不能用於選策略。
**38.** DEV gate：paired mean Δ vs momentum > 0、paired median Δ vs momentum > 0，且 no higher disqualification rate → PASS_ALPHA_DEV，才可進 Validation。
**39.** Secondary gate：Alpha vs Raw-return paired mean > 0 且 paired median > 0，可支持 relative-alpha target 較適合 task。
**40.** 若 Alpha vs raw-return significantly better，但仍輸 Momentum，且 turnover/cost 明顯較高：ALPHA_SIGNAL_PROMISING_COST_BLOCKED；下一版才研究 turnover。
**41.** 若 Alpha vs Momentum mean <= 0 且 median <= 0，或未明顯改善 raw-return：STOP_ALPHA_LGBM。停止，不 tune LightGBM、加 features、換 horizon、調 portfolio。
**42.** 僅 PASS_ALPHA_DEV 可跑 2022–2024 validation；設定 freeze，不得依結果 change features、model、target、portfolio。
**43.** Validation 通過且 freeze 才跑一次 2025–2026/09；Holdout report only，不能回頭 tuning。

## 44–54. 自動化與交付

**44.** One-shot：inspect existing implementation → minimal target refactor → raw-return parity → tests → alpha smoke → 70 dev episodes → comparison → gate → PASS 才 validation → freeze → 允許才 holdout → report。
**45.** 耗時工作用 shell script / CLI、resumable jobs；Agent 不在 context 等待。
**46.** 平常只讀 summary.json、comparison.csv、model diagnostic summary、failed cases、short logs。只有 failure diagnosis 才讀 all ledgers、all predictions、all model dumps、raw per-day logs。
**47.** 修改前先搜尋 reuse；extend existing abstraction 優先於 duplicate implementation。
**48.** 要求 single responsibility、small functions、type hints、dataclass configs、explicit arguments、deterministic ordering、explicit failure、no hidden state、no magic fallback。命名用 raw_forward_return、cross_section_market_return、relative_alpha、target_mode；不用 temp、new_target、v2、test2、misc。
**49.** 只改必要範圍，不整理 AutoTS internals、competition engine、old legacy code、README unrelated sections。
**50.** 輸出：

```text
research/results/lgbm_alpha/
├── summary.md
├── summary.json
├── comparison.csv
├── model_diagnostics.csv
└── failures.md
```

**51.** `summary.md` 保持短：Result = PASS_ALPHA_DEV / ALPHA_SIGNAL_PROMISING_COST_BLOCKED / STOP_ALPHA_LGBM；Main Table 只列 Momentum、Raw LGBM、Alpha LGBM、AutoTS，欄位 Mean、Median、Paired Δ vs momentum、Win rate、P10、Worst、MDD、Turnover、Cost。Diagnosis 最多三點：Target effect、Turnover effect、Next action。
**52.** Git hygiene：不 commit model binary、run folders、ledgers、large prediction files、cache、long logs；只 commit source、tests、configs、small summaries、comparison tables。
**53.** Done：同 JPX2 model / features / portfolio / 70 dev episodes，唯一主要變動為 target；raw_return parity、alpha tests、cross-sectional mean ≈ 0、maturity、causality、determinism 全通過；完成 full dev、paired Momentum / Raw-LGBM comparison、turnover diagnostics、report。Validation 依 gate；holdout freeze 前不碰；無 unrelated refactor。
**54.** Target first；Model / Features / Portfolio frozen。不支持 relative alpha 就 STOP，不把負結果調成正結果。
