# AutoTS-First ETF Competition Spec

## 0. Core Instruction

這次不要延續、整理、修補或比較既有的 V3 / V4 / V5 strategy architecture。

**直接把 AutoTS 當作新的核心方法。**

目標：

> 深入使用 AutoTS，針對本 ETF competition task 做 task-specific adaptation / tuning，建立一套新的 AutoTS-based strategy，最後整理成清楚、可自動執行的 daily workflow。

既有 V3 / V4 / V5：

- 可以讀，了解目前 repo 有哪些資料、規則、工具、ledger、D-Plan、回測資源。
- 可以 reuse 已經正確的 competition infrastructure。
- **不要沿用它們的 strategy design。**
- **不要受它們的 method naming / architecture / assumptions 綁住。**
- 不需要證明新方法是 V3/V4/V5 的延伸。

這是一條新的主線：

```text
AutoTS
→ adapt to ETF competition
→ tune / validate
→ portfolio construction
→ competition constraints
→ D-Plan
→ daily automation
```

---

# 1. First: Understand the Task

先完整閱讀目前 `fintech_ETF` repository 與官方 competition docs。

只整理出本 task 真正需要的：

```text
1. allowed universe
2. available historical data
3. daily information cutoff
4. portfolio constraints
5. transaction costs
6. execution / settlement rules
7. D-Plan requirements
8. evaluation period
9. final competition objective
10. daily submission workflow
```

不要先研究 V3/V4/V5 是怎麼設計策略的。

先重新從 competition objective formulate 問題。

核心問題：

> 給定決策日前可取得的所有資料，如何利用 AutoTS 預測未來相對表現，並建立符合競賽規則、最大化短期 terminal NAV 的 portfolio？

---

# 2. AutoTS Is the Main Model

Upstream:

```text
https://github.com/winedarksea/AutoTS
```

直接使用 AutoTS codebase。

可以：

```text
fork AutoTS
or
clone AutoTS
or
install AutoTS and maintain our patches
```

選擇最適合長期修改與重現的方法。

如果需要改 AutoTS：

- 可以直接改 source
- 可以新增 models
- 可以改 validation
- 可以改 metric
- 可以改 ensemble
- 可以改 model selection
- 可以改 search space
- 可以新增 competition-specific wrapper

AutoTS 是這個新 strategy 的核心，不只是其中一個 expert。

---

# 3. Deeply Read AutoTS

不要只調 API。

深入理解 codebase，至少涵蓋：

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

relevant transforms / preprocessing
production_example.py
```

追完整流程：

```text
input series
→ preprocessing / transform
→ model candidates
→ fitting
→ validation
→ scoring
→ model selection
→ ensemble
→ prediction interval
→ forecast
```

理解之後再決定哪些地方要針對 ETF task 修改。

---

# 4. Task-Specific Adaptation

這裡的「fine-tune AutoTS」不是只改幾個參數。

意思是：

> 把 AutoTS 的 search / validation / model selection / scoring / ensemble 針對我們的 competition objective 做完整 task adaptation。

至少研究以下部分。

---

## 4.1 Prediction Target

不要預設 forecast raw stock price 就是最好。

系統性比較：

```text
price
next-day return
3-day return
5-day return
10-day return
24-day return
relative return
cross-sectional rank
excess return vs market
```

找到最適合 portfolio decision 的 target。

---

## 4.2 Forecast Horizon

AutoTS 原本是 generic forecasting。

我們要針對短期 ETF competition。

比較：

```text
1D
3D
5D
10D
24D
```

可以是：

```text
multi-horizon forecast
```

再組合成 portfolio score。

---

## 4.3 Input Series

研究 AutoTS 是否應預測：

```text
close price
adjusted close
returns
log returns
relative strength
volume-derived signals
market-relative series
```

如果 exogenous regressors 有幫助，也可加入：

```text
market index
sector index
US market overnight
SOX
Nasdaq
FX
volatility
breadth
volume
other causal signals
```

所有資料必須符合 decision-time causality。

---

# 5. Validation Must Match the Competition

這是最重要的 adaptation 之一。

不要使用 generic forecasting validation 就直接決定模型。

建立 competition-aware walk-forward validation：

```text
historical cutoff
→ train
→ predict
→ construct portfolio
→ simulate competition execution
→ measure result
```

validation 必須：

```text
strictly chronological
no leakage
multiple market regimes
multiple 24-trading-day episodes
```

最終模型選擇不能只看：

```text
MAE
RMSE
SMAPE
```

而要看 forecasting 是否真的改善 portfolio。

---

# 6. Competition-Aware Objective

AutoTS 原本主要優化 forecast accuracy。

我們真正 care：

```text
terminal NAV
return
drawdown
turnover
transaction cost
ranking quality
direction quality
forecast uncertainty
portfolio stability
```

設計一個 task-specific scoring system。

例如研究：

```text
forecast score
+
cross-sectional rank score
+
direction score
+
portfolio return score
-
instability penalty
-
turnover penalty
```

實際形式交給實驗決定。

不要在 final holdout 上 tuning。

---

# 7. AutoTS Search Space

不要直接讓 AutoTS 無限制暴力搜尋所有模型。

先深入理解 model zoo，再針對金融日線建立 task-specific model pool。

至少比較：

```text
naive baselines
statistical models
regression / sklearn models
multivariate models
ensemble models
relevant deep models
```

然後用 evidence 決定保留哪些。

目標：

```text
small enough to run daily
diverse enough to adapt to regime
strong enough to outperform simple baseline
```

---

# 8. Ensemble

AutoTS 的 ensemble 是核心。

深入測試：

```text
single best model
weighted ensemble
horizontal ensemble
mosaic ensemble
per-series best model
```

因為 150 檔股票可能完全不適合同一個 forecasting model。

需要回答：

> 是否應該每支股票都有自己的最佳模型？

以及：

> 是否可以讓 model selection 隨時間 regime 自動改變？

---

# 9. Uncertainty

使用 AutoTS prediction interval 或其他 uncertainty estimate。

每支股票至少輸出：

```text
expected return
forecast uncertainty
confidence
```

portfolio 不應只看：

```text
highest forecast
```

而應考慮：

```text
risk-adjusted forecast strength
```

---

# 10. Portfolio Construction

AutoTS 負責 forecasting。

接著把 forecast 轉成正式 portfolio。

基本流程：

```text
AutoTS forecasts
→ expected return / rank / confidence
→ select candidates
→ determine weights
→ apply competition constraints
→ target portfolio
```

必須遵守官方規則。

例如目前官方限制若為：

```text
allowed universe
20–30 holdings
cash constraint
single-name caps
board-lot orders
no short
no day trading
```

全部以 repo 中最新官方文件為準。

**不得為了讓 AutoTS 表現更好而放寬 competition rules。**

---

# 11. End-to-End Backtest

不能只測 forecast accuracy。

必須建立完整：

```text
historical data
→ AutoTS fit
→ forecast
→ portfolio
→ orders
→ execution
→ fees / tax
→ ledger
→ NAV
```

backtest。

這才是最終 method evaluation。

---

# 12. Automated Tuning

Agent 可以自動探索：

```text
forecast target
forecast horizon
lookback
model list
transform list
validation method
number of validations
ensemble type
metric weighting
portfolio mapping
confidence threshold
rebalance threshold
```

但必須：

```text
bounded search
chronological validation
experiment logging
reproducible configs
```

不要無限暴力搜尋直到某段歷史資料看起來最好。

---

# 13. Build an Autonomous Research Loop

建立 Agent 可以反覆執行的研究流程：

```text
read latest experiment state
→ propose next experiment
→ run AutoTS training / search
→ backtest
→ compare
→ reject or keep
→ update experiment registry
```

每個 experiment 要保存：

```text
config
data cutoff
git commit
AutoTS version / fork commit
metrics
portfolio result
runtime
result status
```

Agent 可以自行跑大量實驗。

Human 主要處理：

```text
objective
high-level method choice
exceptions
final acceptance
```

---

# 14. Daily Production Workflow

最終目標是每天只有一條 canonical command，例如：

```bash
python daily_agent.py --date YYYY-MM-DD
```

自動執行：

```text
update causal data
→ validate data
→ load / update AutoTS models
→ generate forecast
→ generate portfolio
→ apply competition constraints
→ generate decisions
→ generate D-Plan
→ validate schema
→ validate portfolio
→ output final files
```

最後只能得到：

```text
READY_TO_SUBMIT
```

或：

```text
BLOCK_SUBMISSION
```

任何重要資訊缺失都 fail closed。

---

# 15. Repository Cleanup

整理 repo 時，以新的 AutoTS-first workflow 為中心。

不要再讓目錄結構圍繞：

```text
V3
V4
V5
round1
round2
round3
```

新的 active code 應該清楚呈現：

```text
autots_strategy/
competition/
data/
research/
daily/
tests/
docs/
```

具體名稱可以依現有 codebase 調整。

舊 V3/V4/V5：

```text
保留
archive / legacy
不要刪 evidence
不要讓它們成為 README 主角
```

---

# 16. Method Documentation

整理一份新的：

```text
docs/method.md
```

不要用版本流水帳寫。

直接描述目前真正的方法：

```text
Problem
↓
Data
↓
Prediction target
↓
AutoTS adaptation
↓
Validation
↓
Model search
↓
Ensemble
↓
Uncertainty
↓
Portfolio construction
↓
Competition execution
↓
Daily workflow
```

讓任何人只讀這份就知道現在策略是怎麼工作的。

---

# 17. AutoTS Modification Log

如果改 AutoTS source，建立：

```text
docs/autots_changes.md
```

記錄：

```text
upstream commit
our fork commit
files changed
why changed
task-specific behavior
license / attribution
```

避免之後不知道我們到底改過什麼。

---

# 18. README

最後重新整理 root `README.md`。

README 不要再用 V3/V4/V5 歷史當主結構。

首頁優先呈現：

```text
1. Project goal
2. Current AutoTS-based method
3. Architecture
4. How to run research
5. How to run daily agent
6. Competition constraints
7. Verification
8. Legacy archive
```

歷史策略只需要一句：

```text
Previous strategy generations are retained under legacy/archive for reproducibility.
```

---

# 19. Definition of Done

完成時應該具備：

- [ ] AutoTS 是新的 primary forecasting method
- [ ] 已深入讀 AutoTS codebase
- [ ] 已針對 ETF task 修改 / tune AutoTS
- [ ] prediction target 經過實驗選擇
- [ ] validation 對齊 competition horizon
- [ ] model search 有 bounded task-specific space
- [ ] ensemble 已實驗
- [ ] uncertainty 可輸出
- [ ] forecast 可轉成 competition portfolio
- [ ] end-to-end backtest 可重現
- [ ] daily workflow 可執行
- [ ] competition rules 不被放寬
- [ ] V3/V4/V5 不再限制新 architecture
- [ ] legacy evidence 保留
- [ ] `docs/method.md` 完整
- [ ] README 已整理
- [ ] tests / verification 已執行

---

# 20. Git

完成所有 implementation、tests、method documentation 與 README 後：

```bash
git diff --check
git status
python -m unittest discover -s tests -q
```

確認沒有：

```text
secrets
API keys
temporary artifacts
unrelated changes
```

然後：

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

完成後回報：

```text
1. AutoTS changes
2. Final method
3. Best validated configuration
4. Backtest / validation summary
5. Daily workflow
6. Known limitations
7. branch
8. commit
9. push status
```
