# AutoTS-First ETF Competition Spec

**以 AutoTS 建立新策略主線，對齊 ETF competition，再形成可自動執行的 daily workflow。**可讀取及 reuse 正確的 V3 / V4 / V5 資料、規則、工具、ledger、D-Plan 與回測設施；不延續或修補其 strategy design，也不受舊 naming、architecture、assumptions 限制。

## 0–3. 任務與 AutoTS

**0.** 主線：AutoTS → task adaptation / tuning / validation → portfolio construction → competition constraints → D-Plan → daily automation。
**1.** 先讀 `fintech_ETF` 與官方文件，依 competition objective 重新定義問題，不先研究舊策略設計。釐清 10 項：1. allowed universe；2. available historical data；3. daily information cutoff；4. portfolio constraints；5. transaction costs；6. execution / settlement；7. D-Plan requirements；8. evaluation period；9. final objective；10. daily submission workflow。問題是：如何用決策前資料及 AutoTS 預測相對表現，建立合規、最大化短期 terminal NAV 的 portfolio？
**2.** 直接使用 upstream codebase：https://github.com/winedarksea/AutoTS。可 fork、clone，或 install 後維護 patches，選擇利於長期修改與重現的方式。AutoTS 是 primary model，非單一 expert；必要時可改 source、models、validation、metric、ensemble、model selection、search space 或 competition-specific wrapper。
**3.** 不只調 API；先讀以下路徑、relevant transforms / preprocessing，再追 input → preprocessing → candidates → fit → validation → scoring → selection → ensemble → interval → forecast：

```text
autots/evaluator/auto_ts.py
autots/evaluator/auto_model.py
autots/evaluator/validation.py
autots/evaluator/metrics.py
autots/models/model_list.py
autots/models/ensemble.py
autots/models/mlensemble.py
autots/models/sklearn.py
autots/models/statsmodels.py
autots/models/basics.py
autots/templates/general.py
production_example.py
```

## 4–9. Task Adaptation

**4.** Fine-tune 指完整調整 search、validation、selection、scoring、ensemble，不只是改參數。

**4.1 Target：**比較 price、next-day return、3-day return、5-day return、10-day return、24-day return、relative return、cross-sectional rank、excess return vs market，依 portfolio decision 選擇，不預設 raw price 最好。

**4.2 Horizon：**比較 1D、3D、5D、10D、24D；可 multi-horizon forecast 再組成 portfolio score。

**4.3 Input：**研究 close price、adjusted close、returns、log returns、relative strength、volume-derived signals、market-relative series。若有幫助，可加 market / sector index、US market overnight、SOX、Nasdaq、FX、volatility、breadth、volume、other causal signals；所有資料遵守 decision-time causality。

**5.** 建 competition-aware walk-forward：historical cutoff → train → predict → portfolio → competition execution → result。必須 strictly chronological、no leakage、多 regimes、多 24-trading-day episodes；不能只用 generic validation 或 MAE / RMSE / SMAPE 決定模型。
**6.** 設計 task-specific scoring，評估 terminal NAV、return、drawdown、turnover、transaction cost、ranking / direction quality、forecast uncertainty、portfolio stability。例如 forecast + cross-sectional rank + direction + portfolio return − instability − turnover penalty；形式由實驗決定，禁止 final holdout tuning。
**7.** 先讀 model zoo，再建立有界金融日線 pool；比較 naive baselines、statistical、regression / sklearn、multivariate、ensemble、relevant deep models，依 evidence 篩選。Pool 須能每日執行、有 regime 多樣性，並勝過簡單 baseline；不無限暴搜。
**8.** 測 single best、weighted、horizontal、mosaic ensemble、per-series best model。150 檔未必適合同一模型；檢驗每股最佳模型與隨 regime 自動選模是否有用。
**9.** 每股輸出 expected return、forecast uncertainty、confidence；使用 prediction interval 或其他 uncertainty estimate。配置考慮 risk-adjusted forecast strength，而非只取 highest forecast。

## 10–14. Portfolio、研究與 Daily Workflow

**10.** AutoTS forecasts → expected return / rank / confidence → candidates → weights → constraints → target portfolio。以 repo 最新官方文件為準，包括 allowed universe、20–30 holdings、cash constraint、single-name caps、board-lot orders、no short、no day trading；不得為績效放寬規則。
**11.** 最終 evaluation 必須 end-to-end：historical data → fit → forecast → portfolio → orders → execution → fees / tax → ledger → NAV，不能只測 forecast accuracy。
**12.** 可自動探索 forecast target / horizon、lookback、model / transform list、validation method / count、ensemble type、metric weighting、portfolio mapping、confidence / rebalance threshold；必須 bounded search、chronological validation、experiment logging、reproducible configs，不能無限搜尋直到某段歷史最好。
**13.** Autonomous loop：read latest state → propose experiment → train/search → backtest → compare → reject/keep → update registry。每次保存 config、data cutoff、git commit、AutoTS version / fork commit、metrics、portfolio result、runtime、result status。Agent 可大量執行；Human 保留 objective、high-level method choice、exceptions、final acceptance。
**14.** 每日一條 canonical command，例如：

```bash
python daily_agent.py --date YYYY-MM-DD
```

自動 update causal data → validate data → load/update models → forecast → portfolio → constraints → decisions → D-Plan → schema / portfolio validation → final files。重要資訊缺失 fail closed；結果只能 READY_TO_SUBMIT 或 BLOCK_SUBMISSION。

## 15–18. Repository 與文件

**15.** Active code 以新 workflow 為中心，不圍繞 V3 / V4 / V5、round1 / round2 / round3。目錄可依 codebase 調整：autots_strategy/、competition/、data/、research/、daily/、tests/、docs/。舊版留 archive / legacy，保留 evidence，不當 README 主角。
**16.** 建 `docs/method.md`，按 Problem → Data → Prediction target → AutoTS adaptation → Validation → Model search → Ensemble → Uncertainty → Portfolio construction → Competition execution → Daily workflow 描述現行方法，不寫版本流水帳。
**17.** 若改 AutoTS source，建 docs/autots_changes.md，記 upstream commit、our fork commit、files changed、why changed、task-specific behavior、license / attribution。
**18.** Root `README.md` 優先順序：1. Project goal；2. Current AutoTS-based method；3. Architecture；4. Research execution；5. Daily agent execution；6. Competition constraints；7. Verification；8. Legacy archive。歷史一句即可：Previous strategy generations are retained under legacy/archive for reproducibility.

## 19–20. 驗收與 Git

**19.** Done：AutoTS 為 primary forecasting method，已深入讀碼及 task adaptation；target 經實驗選擇，validation 對齊 horizon；bounded search、ensemble 實驗、uncertainty 輸出完成；能轉成合規 portfolio、重現 end-to-end backtest、執行 daily workflow；不放寬規則；V3/V4/V5 不限制新 architecture，legacy evidence 保留；method、README 完整，tests / verification 已執行。
**20.** 完成 implementation、tests、method、README 後先驗證：

```bash
git diff --check
git status
python -m unittest discover -s tests -q
```

確認無 secrets、API keys、temporary artifacts、unrelated changes，再依原規格執行：

```bash
git add -A
git commit -m "feat: build AutoTS-based ETF strategy workflow"
git push
```

禁止：

```bash
git push --force
git reset --hard
git clean -fd
```

完成回報：1. AutoTS changes；2. Final method；3. Best validated configuration；4. Backtest / validation summary；5. Daily workflow；6. Known limitations；7. branch；8. commit；9. push status。
