# Production：現行策略與舊版規格

**2026-10-25：現行策略為凍結 tournament；DataPack 已接入，live AS 可依 MoneyDJ 前10大、最低30%的 heuristic gate 通過。安全閘門以 [v4.3 核對](rules_v43.md) 為準。**

第 0–1 節為現行策略與狀態；第 2 節起為舊規劃及歷史紀錄。前10大／20%、自行重建官方現金、僅靠 holdings 匯出、無條件 UNVERIFIED 可 READY 等舊描述已失效；現行 heuristic 例外見第1節。

## 0. 正式策略：K2 → T2

設定：`production/strategy.json`。Mom20 回滾：`production/strategy_mom20.json`。

| 項目 | K2 TrustRevConc | T2 LargeTrust |
|---|---|---|
| 配置 | 10 核心各 8.5%；12 填充各 10%/12，共 22 檔 | 2330：23.5%；24 檔共 71.5% |
| 選股 | 投信 10 日淨買超 > 0；營收 3 月均值創 12 月高；YoY > 20% | 60 日大型股前 40（排除 2330） |
| 排名 | 投信 10 日淨買超 / 20 日均量 | 投信 20 日淨買超 / 20 日均量，取 24 檔 |
| 補足 | 篩選排名後接大型股排名 | 不補足不足的評分名單 |
| 重篩 | 可用營收月異於 `k2_month`；單向換手 ≥ 10% 才更新目標 | 切換日固定目標 A；分段換倉 |

大型股以 60 日 `close × volume` 均值定義，全期有效；營收篩選需連續 12 個月。

- 權重 > cap − 0.5pp 或現金越界
	- 重試保存目標
- K2 存 `k2_target`、`k2_month`
	- planner HOLD 仍保留更新
- 最後 3 個 session 不交易
- Tournament 不做 Active Share 選股修補
	- INVALID 走既有 fallback

## 1. 切換、資料與保存

```bash
./run_daily.sh <T> --datapack <DataPack.json> --rank N
./run_daily.sh 2026-10-26 --offline
```

`--rank N`：T−1 19:00 組內排名，限正整數；audit 記排名、模式、切換、動作。

- 切換條件：`day_index >= 12` 且 `sessions_remaining > 3` 且 `rank <= 6`。
- 僅 K2 可切換；T2 鎖定至賽末。
- 可切換日缺排名維持模式
	- 記錄 `RANK_MISSING`
	- 仍可產出計畫
- Live：`<runs_dir>/state/tournament.json`。
- Offline：`<runs_dir>/<T>/state/tournament.json`；不寫 live 狀態。

D-Plan 通過驗證才保存策略狀態，含 HOLD。

狀態含 `mode`、`switched_on`、`ranks`，另存：

- `k2_target`：最近 K2 目標
	- 建倉／達換手門檻時更新
- `k2_month`：可用營收月
	- 最近建倉／重篩時更新
- `t2_target`：切換日固定目標 A
- `t2_complete`：決策時持股名稱等於 A

T2 換倉規則：

- 賣單按現重遞減
	- 同重按代號
- 買單按 A 權重遞減
	- 再按 T2 投信比率遞減
	- 2330 優先
- 全部賣出起遞減 k
	- 未賣舊股保留現重
	- A 內持股回到 A 權重
- 依買序補入 A
	- 總重 ≤ 95%，檔數 20–30
- 接受最大可行 k
	- planner 為 OK／REPAIRED
	- 原 Active Share 檢查通過
- Audit 記 `SWITCH_STEP:k/len`
	- 皆不可行則 HOLD
	- 記 `SWITCH_STEP_INFEASIBLE`

名稱到齊即完成；breach 才重試 A，最後 3 日凍結。

| 資料 | 來源與時點 |
|---|---|
| 歷史 | `data/hybrid/flows.csv`、`revenue.csv`；唯讀 |
| 快取 | `data/production_aux/flows.csv`、`revenue.csv`；增量追加 |
| 初始快取 | 複製 2026-09-24..10-22 投信與 2026-09 營收 |
| 投信刷新 | TWSE T86 + TPEx；補缺 session 至 T−1 |
| 營收刷新 | MOPS 已公布月份；月 m 自 m+1/11 可用 |
| Offline | 最新完整本地 vendor 快照（含 preflight）；只讀快取 |

缺 T−1 任一交易所投信、應有營收月或刷新失敗，記錄 degraded 原因並走既有 fallback。Offline 帳本存當日資料夾，不改 live。

Live 須官方 DataPack。未提供 `--etf-holdings` 時自動抓取 MoneyDJ 前10大；亦可提供完整前15大 CSV。

最終整張化、扣費後持倉及 validation fallback 均適用同一 AS gate：全部 required ETF（目前30檔）齊全、`missing/invalid_data/stale` 為空、未逾7日，每檔至少10筆，`minimum` 有限且 ≥30%。任一抓取失敗或條件未達即阻擋。

門檻讀取 `production/settings.json:live_active_share`（`heuristic_min=0.30`、`allow_top_n=10`）。通過記 `active_share_gate=HEURISTIC_TOP10_PASS` 與 `ACTIVE_SHARE_HEURISTIC_PASS:top10:min=<4dp>:closest=<etf>`，保留 `ACTIVE_SHARE_METHOD_UNCONFIRMED` 作資訊原因；失敗記 `BLOCKED` 與失敗條件。官方方法確認後，既有通過路徑記 `OFFICIAL_PASS`。

AS checker 的前15大、15%官方門檻、20%內部門檻及7日新鮮度不變；其他安全關卡仍須通過才為 `READY_TO_SUBMIT`。Offline 為 `SIMULATION_ONLY`，shell 結束碼 2。

## 2. 優先級

| 級別 | 內容 |
|---|---|
| **P0** | • D-Plan generator<br>• 每日資料可靠性與備援<br>• 官方持股對帳<br>• Fallback D-Plan<br>• Cold-start fallback<br>• 合規修補 fallback<br>• 主動 ETF 持股取得<br>• Active Share checker 與 repair<br>• End-to-end 驗證基礎建設 |
| **P1** | • Momentum DEV sweep<br>• Freeze candidate<br>• Validation 一次<br>• 定版 Mom20 或 candidate |
| **P2** | • 24D production replay<br>• Backtest / D-Plan 一致性 gate<br>• Daily dry run<br>• 運維強化 |
| **P3** | • 補 2019+ 外部資料<br>• Residual Ranker、Regime Gate<br>• 隔夜、法人、產業訊號 |

P0 主線；P1 成本低，可並行；P3 延後。

## 3. P0-A：每日 Pipeline

正常日依序執行：

1. 決定交易日 T
2. 抓 T−1 市場資料
3. 驗新鮮度與完整性
4. 取得主辦方持股
5. 官方 state 對本地帳本
6. 跑凍結滾動策略
7. 產生 candidate portfolio
8. 檢查 Active Share
9. 檢查其他合規
10. 必要時 constraint repair
11. 產生 target weights
12. 整張股數 planner
13. 產生 orders
14. 產生 D-Plan
15. 驗證 Schema
16. 驗證語意
17. 最終合規驗證
18. SUBMIT

Research、backtest、production 共用核心，不另做 submission strategy：

- 訊號：`research/baselines.py` 的 `MomentumStrategy`
- 組合：`autots_strategy/portfolio.py` 的 `target_weights`
- 定股數：`competition/planner.py`（官方公式）
- 結算：`competition/ledger.py`

## 4. P0-B：每日資料可靠性

各來源須定義：主／備援來源、發布時間、新鮮度、覆蓋率、驗證與失敗規則。

| 資料 | 主來源 | 備援 | 發布時間 |
|---|---|---|---|
| 上市日行情（收盤、量、成交金額） | TWSE `MI_INDEX` | Yahoo Finance | T−1 盤後 |
| 上櫃日行情 | TPEx `dailyQuotes` | Yahoo Finance | T−1 盤後 |
| 官方成交均價 | 成交金額 ÷ 成交股數（同上） | 無，缺價不捏造 | T−1 盤後 |
| 公司行動 | TWSE / TPEx 除權息公告 | Yahoo dividends / splits | 事前公告 |
| 主辦方持股 | `GET /api/portfolio/holdings/` 或後台庫存明細匯出 | 本地 shadow ledger（只做比對） | T−1 結算後 |
| 主動 ETF 前十大持股 | 各投信官網每日持股 | 其他公開彙整網站 | 每日或每月 |

抓取沿用 `legacy/src/v4_execution.py`：依欄名辨識表格，日期不符即拒絕。

主辦方 state 是 source of truth；本地 shadow ledger 僅供稽核、重建、一致性檢查。不一致時不得改用本地 state。

## 5. Data Gate

策略運算前先驗：

- 日期是正確的 T−1
- 150 檔覆蓋率正常
- 價格有限且為正
- 成交量合理
- 公司行動已處理
- 時間戳不晚於決策時點
- 已取得主辦方 state

主來源失敗改用備援；備援也失敗仍須走 fallback，未交 D-Plan 會記警告。

## 6. P0-C：Fallback D-Plan

Production 分兩路：

| 路徑 | 何時使用 |
|---|---|
| Normal | 資料、策略、驗證都正常 |
| Fallback | 任一環節失敗 |

Fallback：合法 D-Plan → 驗證 → 提交 → 通知人工處理降級。

### 一般日 fallback

- 優先沿用最新官方持股
	- 須經主辦方確認
- 不做 alpha 交易
- D-Plan 仍要完整
	- sources、observations、market_view
	- inferences、no_trade_decisions
	- `orders=[]`、agent_metadata

### Fallback 不等於永遠不交易

前日組合仍合規才可不交易；以下情況須做最少合規修補，不做 alpha 最佳化：

- 持股被動超過單股上限
- Active Share 太低
- 公司行動改變權重
- 持股檔數出問題

### Cold-start fallback

Day 1 為 0 持股、100% 現金，`orders=[]` 不安全。賽前凍結 `COLD_START_FALLBACK`：

- 25 檔、等權、投入約 0.95
- 符合所有單股上限
- 符合 Active Share
- 整張交易可執行

須先測試、做 24D replay 與 schema 驗證，首日 pipeline 失敗仍可機械式建倉。

## 7. P0-D：Active Share 是硬限制

規則：投組前 10 大持股與任一檔主動 ETF 前 10 大的 Active Share 不得低於 20%；連續 2 個交易日違反可直接取消資格，不走三次警告（辦法 p8 六(十三)、p10 註2；`docs/task.md` 第 1 節）。

流程：Momentum 產生 candidate portfolio → 對每檔主動 ETF 算 Active Share → 通過才繼續，否則 repair。這是硬限制，不能只報 warning。

### 所需資料

- 主動 ETF 清單（30 檔，`official_docs/玉山挑戰賽_主動型ETF列表_v1.pdf`）
- 各 ETF 前 10 大持股與權重
	- 須為最新適用資料
- 資料時間戳與生效日

`data/reference/tuning_2nd_active_etf_readiness.csv` 全為 `NOT_ACQUIRED`；解決前不得稱 production-ready。

### Checker 輸出

- `minimum_active_share`
- 最接近的 ETF
- 距門檻的距離
- 資料日期與新鮮度

內部門檻：

| Active Share | 狀態 |
|---|---|
| > 25% | healthy |
| 20–25% | caution |
| < 20% | invalid |

25% 只是內部安全邊際，官方門檻是 20%。

### Repair

Momentum 做 alpha；合規層做投影，Active Share 不進訊號學習：

1. 找前 10 大中重疊最大持股
2. 從排名下一順位找替代
3. 使 momentum 分數損失最小
4. 重算 Active Share
5. 直到全部通過

### 尚未解決

- 取得 ETF 持股資料
- 確認持股的有效日期
- 確認官方算法
	- top10 是否正規化
	- 聯集、現金、資料延遲
- 實作 calculator 與 repair
- 寫 unit tests
- 加入 production replay

算法未確認不能視為官方 PASS；現行 live heuristic gate 另見第1節。

## 8. End-to-End 一致性 Gate

策略與 D-Plan 完成後，以最近完整 24D 歷史窗口重播 24 天：歷史 T−1 資料 → production 載入 → 訊號 → 組合 → 合規 → D-Plan → 成交模擬 → 帳本 → 下一天。

每天和 canonical backtest 比較：

- signal 排名
- 選出的股票
- 目標權重
- orders、股數
- 費用、現金、持股
- NAV、周轉

D-Plan orders 須等於 backtest orders，production ledger 須等於 backtest ledger；僅允許預定浮點容差。不一致即 `CONSISTENCY_FAIL`，禁止上 production。

必須成立：Research strategy = Backtest = Production = D-Plan。

## 9. P1：Momentum 小實驗（研究旁支）

### 資料切分

| 期間 | 用途 | 次數 |
|---|---|---|
| 2019–2021 DEV | 試、比較、搜尋、淘汰 | 不限 |
| 2022–2024 VALIDATION | 凍結後確認 | 只開一次 |
| 2025–2026 | 已看過 | 不能再調策略 |

- 窗口：每月月初、月中
- 評估期間在 2019 起
	- 只影響「哪些窗口」
	- 不影響 lookback 取資料
- 理由：momentum 不訓練
	- 60D lookback 需要窗口前 60 天
	- 2019 初窗口因此會讀到 2018 價格

### 只測這些

| 類別 | 候選 |
|---|---|
| Lookback | 15、20、25、30 |
| Skip recent | 0、1、3、5 |
| Multi-horizon | 20、10+20、20+60、10+20+60 |
| Risk-adjusted | momentum ÷ volatility |
| Quality（少量） | • 接近近期高點<br>• 上漲天數比例<br>• 成交量確認 |

不做：100 個技術特徵、XGBoost、Transformer、RL、更大型 AutoTS、Residual Ranker、Regime Gate、隔夜訊號、法人訊號。

### 組合完全固定

固定 Top25、等權、0.95、keep 35、換手門檻 10%、最後 3 天不交易。只測 signal；禁止 validation 後再調 portfolio、重看 validation。

### DEV 比較方式

主要看配對差距 `Δe = R(candidate, e) − R(Mom20, e)`：

- Mean Δ、Median Δ、勝率
- P10、最差窗口
- 周轉、成本、警告

DEV 留 1–3 個 candidate，以 1 個為佳。

### Freeze

validation 前凍結訊號公式、lookback、skip、blend、quality filter、組合參數、指標、門檻；保存 config、manifest、git commit SHA。

### Validation Gate（2022–2024，只看一次）

- 主要門檻：`Mean(Δ) > +0.5%`
	- 這是實務門檻
	- 不宣稱統計顯著
- 另外檢查
	- median 不明顯惡化
	- 勝率合理
	- P10、最差窗口不惡化太多
	- 周轉不爆、成本不吃掉 edge
	- 沒有新增合規風險

結果只有兩種：

| 結果 | 動作 |
|---|---|
| PASS | Candidate 取代 Mom20，策略凍結 |
| FAIL | Mom20 維持 production，停止策略搜尋 |

即使只差 0.1%，也不得調整後重看。

## 10. P2：定版後

- 24D production replay 與一致性 gate（第 8 節）
每日照正式流程 dry run；未提交也保存 inputs、outputs、logs、D-Plan、驗證與對帳結果。

提前排查資料延遲、API 失敗、日曆與公司行動錯誤、整張股數與時區 bug、ETF 持股更新問題、schema 失敗。

## 11. 正式比賽操作

每日入口 `./run_daily.sh` 自動選模式：

| 模式 | 意義 |
|---|---|
| `NORMAL` | 正常策略 |
| `FALLBACK_HOLD` | 沿用持股，不交易 |
| `FALLBACK_COMPLIANCE_REPAIR` | 只做必要合規修補 |
| `COLD_START_FALLBACK` | Day 1 機械式建倉 |
| `EMERGENCY_REVIEW_REQUIRED` | 連合法 fallback 都無法產生 |

輸出：驗證過的 D-Plan、audit report、submission status。

### Fail-safe 邏輯

舊流程「有問題 → BLOCK → 不交」改為「safe fallback → 合法 D-Plan → 提交 → 人工處理」。僅無法安全產生合法 fallback 時進 `EMERGENCY_ESCALATION`，不得默默不交。

## 12. 執行狀態（2026-09-25）

| 項目 | 狀態 | 證據 |
|---|---|---|
| P1 Momentum 小實驗 | • 完成：Mom20 維持<br>• mom30 validation +0.37% 未達 +0.5% | [momentum_sweep/summary.md](../research/results/momentum_sweep/summary.md) |
| 每日 pipeline | • 完成，`./run_daily.sh` | `production/run_daily.py` |
| 每日資料 | • Yahoo 更新＋官方 T−1 交叉比對<br>• 2026-09-24 對 150 檔 0 差異 | `production/market_data.py` |
| 官方持股對帳 | • 讀匯出檔；不一致即 FALLBACK_HOLD | `production/state.py` |
| Fallback 四種模式 | • HOLD、合規修補、cold start、EMERGENCY | `production/engine.py`、`tests/test_production.py` |
| Cold-start 名單 | • 25 檔，Active Share PASS 25.2% | `production/cold_start.json` |
| 主動 ETF 持股 | • MoneyDJ 前十大，30/30 檔 | `production/etf_holdings.py` |
| Active Share | • checker＋repair；2026-09-25 Mom20 最低 29.5% | `production/active_share.py` |
| D-Plan 與驗證 | • schema＋C1/C2/C12/C13/覆蓋 | `production/dplan.py`、`production/validate.py` |
| 一致性 gate | • 40 窗口、960 天 CONSISTENCY_PASS | [production_replay/summary_holdout.md](../research/results/production_replay/summary_holdout.md) |
| 即時 dry run | • 2026-09-25 READY_TO_SUBMIT | `production_runs/dryrun/`（不進 git） |

## 13. 需要外部提供（UNRESOLVED）

| ID | 問題 | 目前做法 |
|---|---|---|
| P-U1 | 主辦方持股 API 的網址與認證 | 每天用 `--holdings` 傳入後台匯出檔 |
| P-U2 | 提交方式 | 產生檔案，人工上傳 |
| P-U3 | 主動 ETF 持股來源 | MoneyDJ（vendor），非投信原始揭露 |
| P-U4 | Active Share 官方算法細節 | 保守：raw 與正規化取低；近似同權取最壞情況 |
| P-U5 | 賽期交易日曆 | • 主辦方指定 10/26 首日<br>• 證交所列 10/26 補假 |
| ~~P-U6~~ | `team_id` | **已解決**：主辦方配發 `TEAM_11076`，已填入 `production/settings.json` |
| P-U7 | 非 LLM 管線是否算 AI Agent | 需主辦方確認（`docs/task.md` U10） |

P-U5：`contest_start` = 10/26，休市自動跳過；若當天開盤，賽期為 25 天，最後 3 天不交易會提早 1 天，偏保守。

其餘 UNRESOLVED 見 [docs/task.md](task.md) 總表。

## 14. 每日操作手冊

賽前一次：

1. `production/settings.json` 已填 `team_id`
	- `TEAM_11076` 已完成
	- `contest_start`：2026-10-26
	- 10/26 照常送件
2. 重新凍結 cold-start 名單
	- `python -m production.etf_holdings --out <etf.csv>`
	- `python -m production.cold_start --etf-holdings <etf.csv> --yahoo-dir <最新 Yahoo 快取>`
3. commit 乾淨版本（D-Plan 的 `code_version` 不可帶 `-dirty`）
4. 清掉 dry run 狀態，正式狀態放在 `production_runs/state/`

每個交易日 T（台北 05:00–08:55）：

1. 後台匯出前日結算持股
2. 執行 `./run_daily.sh <T> --holdings <匯出檔>`
3. 結束碼 0：上傳 `production_runs/<T>/D-Plan_*.json`
4. 結束碼 2：讀 `audit.md`
	- 仍有 D-Plan 就先上傳
	- 再處理原因

| 狀態 | 意思 | 要做什麼 |
|---|---|---|
| `NORMAL` | 凍結策略正常執行 | 上傳 |
| `FALLBACK_HOLD` | 資料或對帳有問題，沿用持股 | 上傳，再查原因 |
| `FALLBACK_COMPLIANCE_REPAIR` | 只做最少合規修補 | 上傳，再查原因 |
| `COLD_START_FALLBACK` | 首日用凍結名單建倉 | 上傳 |
| `EMERGENCY_REVIEW_REQUIRED` | 連合法 fallback 都產生不了 | 08:55 前人工處理 |
