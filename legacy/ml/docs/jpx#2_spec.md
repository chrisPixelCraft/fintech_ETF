# JPX #2 Clean-Transfer LightGBM Baseline

**驗證簡單 JPX #2 LightGBM Regression，能否在 2019–2026 台股穩定勝過 Momentum 與 AutoTS。**只研究 signal，沿用 execution / portfolio infrastructure；失敗即停止，不暴力調參。

## 0–5. 範圍與資料

**0.** Goal：causal features → LightGBM regression → cross-sectional ranking → Top 25 → existing planner / ledger → walk-forward evaluation。
**1.** 禁止 AutoTS ensemble、LightGBMRanker、XGBoost、Optuna / massive tuning、overnight / SOX / ADR、三大法人、sector model、regime detection、relative-alpha target、tail-only training、deep learning、portfolio optimizer。
**2.** 所有 evaluation episodes 限 2019-01-01 至 2026-09 latest available complete episode；不跑 2009–2018 episodes。

**2.1 Split：**DEV 2019-01 → 2022-12（2019–2022 development / debugging / design）；VALIDATION 2023-01 → 2024-12（2023–2024 validation / model selection）；freeze 後才跑 HOLDOUT 2025-01 → 2026-09（2025–2026/09）。看到 holdout 後，不得改 features、target、hyperparameters 或 portfolio rules。

**3.** 模型可用 decision day 前歷史。2019-01 decision 回看約 preceding 750 sessions，約 2016–2018+，供 warm-up；不算評估 2016–2018。
**4.** JPX Tokyo Stock Exchange Prediction #2：OHLCV + 20 / 40 / 60D return、volatility、MA gap → regression → predicted return → rank。公開設定 objective = regression、boosting = gbdt、learning_rate = 0.005、num_boost_round <= 3000、early_stopping = 300。修正 notebook：rolling / pct_change 必須 per-symbol、chronological、causal。
**5.** Baseline target = 10-session forward action-neutral return：

\[
y*{i,t}=\frac{P*{i,t+10}}{P\_{i,t}}-1
\]

不得使用 adj_close、future dividend、future split。Decision D 的 features <= D-1；training row `t` 必須 t + 10 <= D-1，hard assertion：latest_label_end <= D-1。

## 6–11. 固定模型與組合

**6.** Features：Open、High、Low、Close、Volume；return_20、return_40、return_60 = 逐股票 P(t) / P(t-h) - 1；volatility_20、volatility_40、volatility_60 = rolling std(log return)；MA_gap_20、MA_gap_40、MA_gap_60 保留 JPX #2 的 Close / SMA_h(Close)。
**7.** 使用固定模型，其他參數優先 library defaults，不 tuning：

```python
lightgbm.LGBMRegressor
```

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

**8.** 每 decision point：D-1 data → latest matured labels → last ≤750 sessions → chronological train / validation → LightGBM。lookback = 750 sessions；refit_every = 5 sessions。One global model 使用 all dates × all eligible stocks，不做 one-model-per-stock。
**9.** Internal split：earlier ~80% train、latest ~20% validation，禁止 random split。Early stopping metric = Pearson(prediction, target)；最終策略選擇看 24D NAV，不能只看 RMSE/Pearson。
**10.** Decision D：D-1 data → features → prediction → sort 150 stocks → predicted return = score → Top 25 → existing portfolio。
**11.** 沿用 momentum baseline；LightGBM 不控制 position sizing：

```text
n_holdings = 25
keep_rank = 35
weighting = equal
invested = 0.88
cap_scale = 0.9
rebalance_threshold = 0.10
freeze_last_days = 3
```

## 12–14. 實作與測試

**12.** 新增 lgbm_strategy/：__init__.py；features.py 計算特徵；dataset.py 管理 matured labels / train split；model.py fit / predict；strategy.py orchestration。Reuse competition/、autots_strategy/portfolio.py、research/、existing data loaders；禁止第二套 planner / ledger。
**13.** 要求 small functions、single responsibility、type hints、dataclass configs、explicit input/output、deterministic sorting、explicit NaN handling、no hidden globals、no duplicated logic。避免 magic numbers、huge functions、deep nesting、silent fallback、copy-paste feature code、unrelated refactor。
**14.** 至少 test_lgbm_features.py、test_lgbm_dataset.py、test_lgbm_causality.py、test_lgbm_strategy.py。驗證 20 / 40 / 60 returns、volatility、MA gap、per-symbol rolling；修改 D 之後資料（shuffle、multiply、delete、corrupt），features、training data、predictions、weights 必須不變；label maturity assertion 通過；相同 input 得 identical prediction / portfolio。

## 15–17. 實驗與 Gate

**15.** 依序執行，holdout 只能一次：

- Stage 0：unit / causality tests 全 PASS。
- Stage 1：2019–2022 DEV 抽 6 episodes，比 Momentum 20D、AutoTS、LightGBM JPX2；只驗 pipeline、runtime、causality、rule compliance，不依 smoke tuning。
- Stage 2：2019-01 → 2022-12 全部可用 24D episodes。
- Stage 3：固定設計，2023-01 → 2024-12。
	- 公平比較 Momentum、AutoTS。
	- 之後 freeze。
- Stage 4：2025-01 → 2026-09 一次，不依結果修改策略。

**16.** Primary = paired 24D return vs momentum_20d。報 mean、median、paired mean Δ、paired median Δ、P25、P10、worst、MDD、turnover、transaction cost、warnings、disqualification、runtime。
**17.** 進 Stage 2 research extension 前，paired median Δ vs momentum > 0、paired mean Δ vs momentum > 0 且 no worse disqualification；否則 STOP_LGBM_BASELINE，不暴力調參。

## 18–20. 自動化與交付

**18.** One-shot：inspect repo → implement → tests → 2019–2022 smoke/full dev → 2023–2024 validation → freeze → 2025–2026 holdout → comparison report。耗時工作用 .sh / CLI / resumable jobs；Agent 不在 context 等待，只讀 summary.json、comparison.csv、failures、short logs。
**19.** 輸出：

```text
research/results/lgbm_jpx2/
├── summary.md
├── summary.json
├── comparison.csv
└── failures.md
```

`summary.md` 分列 2019–2022 DEV、2023–2024 VALIDATION、2025–2026 HOLDOUT；最終判定只用 PASS_TO_STAGE_2 或 STOP_LGBM_BASELINE。

**20.** Done：無 2019 前 evaluation；split 如上；LightGBM、causal features、mature labels、determinism、tests PASS；完成 Momentum / AutoTS 比較與完整報告；holdout 僅 freeze 後使用；無 unrelated refactor 或不必要大檔 commit。

2019–2026 僅限制 evaluation；pre-2019 data 只能作決策前 historical lookback。
