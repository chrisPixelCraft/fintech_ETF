# Production 計畫：每天必定安全、合規地提交

先確保每天一定能產生合法 D-Plan，再用一次受控的 Momentum 小實驗決定是否替換 20D baseline。之後策略凍結，每天只滾動更新資料與持股，不再滾動調參。

## 0. 最終目標與正式策略

比賽核心：固定 150 檔台股 → 每天產生 D-Plan → 維持 20–30 檔 → 跑完 24 個交易日 → 最大化期末 NAV。

在新策略通過驗證前，正式 production strategy 是：

| 層 | 設定 |
|---|---|
| Signal | 20D Momentum |
| 組合 | • Top 25，等權<br>• invested = 0.95<br>• keep_rank = 35<br>• rebalance_threshold = 0.10<br>• freeze_last_days = 3 |

依據：[research/results/final_test/summary.md](../research/results/final_test/summary.md)，2025–2026 平均 +8.79%，21 組中最好。

## 1. 滾動方式：Rolling decisions，Frozen strategy

每天都會滾動：

1. 取得最新 T−1 資料
2. 20D window 往前滾一天
3. 重新算 150 檔 momentum 並排名
4. 依目前持股與 keep_rank 決定換股
5. 產生 target weights 與 orders

不能每天變：20D、Top25、0.95、keep_rank 35、換手門檻 10%、最後 3 天不交易。

## 2. 優先級

| 級別 | 內容 |
|---|---|
| **P0** | • D-Plan generator<br>• 每日資料可靠性與備援<br>• 官方持股對帳<br>• Fallback D-Plan<br>• Cold-start fallback<br>• 合規修補 fallback<br>• 主動 ETF 持股取得<br>• Active Share checker 與 repair<br>• End-to-end 驗證基礎建設 |
| **P1** | • Momentum DEV sweep<br>• Freeze candidate<br>• Validation 一次<br>• 定版 Mom20 或 candidate |
| **P2** | • 24D production replay<br>• Backtest / D-Plan 一致性 gate<br>• Daily dry run<br>• 運維強化 |
| **P3** | • 補 2019+ 外部資料<br>• Residual Ranker、Regime Gate<br>• 隔夜、法人、產業訊號 |

P0 是主線，P1 很便宜所以並行，P3 之後再說。

---

## 3. P0-A：每日 Pipeline

正常日流程：

1. 決定交易日 T
2. 抓 T−1 市場資料
3. 驗證新鮮度與完整性
4. 取得主辦方持股
5. 官方 state 與本地帳本對帳
6. 跑凍結的滾動策略
7. 產生 candidate portfolio
8. Active Share 檢查
9. 其他合規檢查
10. 必要時做 constraint repair
11. 產生 target weights
12. 整張股數 planner
13. 產生 orders
14. 產生 D-Plan
15. Schema 驗證
16. 語意驗證
17. 最終合規驗證
18. SUBMIT

Research、backtest、production 共用同一套核心邏輯：

- 訊號：`research/baselines.py` 的 `MomentumStrategy`
- 組合：`autots_strategy/portfolio.py` 的 `target_weights`
- 定股數：`competition/planner.py`（官方公式）
- 結算：`competition/ledger.py`

不另外維護一套 submission strategy。

## 4. P0-B：每日資料可靠性

每個資料來源都要定義：主來源、備援來源、預期發布時間、新鮮度規則、覆蓋率規則、驗證規則、失敗行為。

| 資料 | 主來源 | 備援 | 發布時間 |
|---|---|---|---|
| 上市日行情（收盤、量、成交金額） | TWSE `MI_INDEX` | Yahoo Finance | T−1 盤後 |
| 上櫃日行情 | TPEx `dailyQuotes` | Yahoo Finance | T−1 盤後 |
| 官方成交均價 | 成交金額 ÷ 成交股數（同上） | 無，缺價不捏造 | T−1 盤後 |
| 公司行動 | TWSE / TPEx 除權息公告 | Yahoo dividends / splits | 事前公告 |
| 主辦方持股 | `GET /api/portfolio/holdings/` 或後台庫存明細匯出 | 本地 shadow ledger（只做比對） | T−1 結算後 |
| 主動 ETF 前十大持股 | 各投信官網每日持股 | 其他公開彙整網站 | 每日或每月 |

- 抓取程式沿用 `legacy/src/v4_execution.py` 的解析方式
  - 以欄位名稱辨識表格
  - 回應日期不符就拒絕
- 主辦方正式 state 是 source of truth
  - 本地帳本是 shadow ledger
  - 用於稽核、重建、一致性檢查
- 兩者不一致時，不可偷偷改用本地 state

## 5. Data Gate

策略運算前先驗：

- 日期是正確的 T−1
- 150 檔覆蓋率正常
- 價格有限且為正
- 成交量合理
- 公司行動已處理
- 時間戳不晚於決策時點
- 已取得主辦方 state

主來源失敗 → 改用備援。備援也失敗時**不能直接不交**：沒交 D-Plan 本身就會吃警告。

## 6. P0-C：Fallback D-Plan

Production 永遠有兩條路：

| 路徑 | 何時使用 |
|---|---|
| Normal | 資料、策略、驗證都正常 |
| Fallback | 任一環節失敗 |

Fallback 的順序是：產生合法 D-Plan → 驗證 → 提交 → 再通知人工。先保住 submission，再處理降級狀態。

### 一般日 fallback

- 優先沿用主辦方最新確認持股
- 不做 alpha 交易
- D-Plan 仍要完整
  - sources、observations、market_view
  - inferences、no_trade_decisions
  - `orders=[]`、agent_metadata

### Fallback 不等於永遠不交易

只有前一天的組合今天仍合規時，不交易才安全。出現下列情況時要做**最少必要的合規修補**，不做 alpha 最佳化：

- 持股被動超過單股上限
- Active Share 太低
- 公司行動改變權重
- 持股檔數出問題

### Cold-start fallback

Day 1 起始是 0 持股、100% 現金，`orders=[]` 不安全。賽前準備一份 `COLD_START_FALLBACK`：

- 25 檔、等權、投入約 0.95
- 符合所有單股上限
- 符合 Active Share
- 整張交易可執行

並事先凍結、測試、做 24D replay、做 schema 驗證。正式第一天 pipeline 出事，也能機械式建倉。

## 7. P0-D：Active Share 是硬限制

規則：投組前 10 大持股與任一檔主動 ETF 前 10 大的 Active Share 不得低於 20%；連續 2 個交易日違反可直接取消資格，不走三次警告（辦法 p8 六(十三)、p10 註2；`docs/task.md` 第 1 節）。

正式流程：

1. Momentum 產生 candidate portfolio
2. 對每一檔主動 ETF 算 Active Share
3. 通過就繼續；不通過就 repair

它是硬限制，不能只報告 warning。

### 所需資料

- 主動 ETF 清單（30 檔，`official_docs/玉山挑戰賽_主動型ETF列表_v1.pdf`）
- 每檔 ETF 最新適用的前 10 大持股與權重
- 資料時間戳與生效日

目前 `data/reference/tuning_2nd_active_etf_readiness.csv` 全為 `NOT_ACQUIRED`，所以**在這個問題解決前，不能宣稱 production-ready**。

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

Momentum 負責 alpha，合規層負責投影，不把 Active Share 塞進訊號學習：

1. 找出重疊最大的前 10 大持股
2. 從排名下一順位找替代
3. 使 momentum 分數損失最小
4. 重算 Active Share
5. 直到全部通過

### 尚未解決

- 取得 ETF 持股資料
- 確認持股的有效日期
- 確認官方算法細節（top10 是否正規化、聯集、現金、資料延遲）
- 實作 calculator 與 repair
- 寫 unit tests
- 加入 production replay

算法確認前狀態明確標為 `ACTIVE_SHARE_UNVERIFIED`，不可自動視為 PASS。

## 8. End-to-End 一致性 Gate

策略與 D-Plan 完成後，用最近一個完整 24D 歷史窗口做 production replay：每天真的跑「歷史 T−1 資料 → production 資料載入 → 訊號 → 組合 → 合規 → D-Plan → 成交模擬 → 帳本 → 下一天」，跑滿 24 天。

每天和 canonical backtest 比較：

- signal 排名
- 選出的股票
- 目標權重
- orders、股數
- 費用、現金、持股
- NAV、周轉

核心要求：D-Plan orders 等於 backtest orders，production ledger 等於 backtest ledger，只允許事先定義的浮點容差。不一致即 `CONSISTENCY_FAIL`，不能上 production。

最終必須成立：Research strategy = Backtest = Production = D-Plan。

---

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

Top25、等權、0.95、keep 35、換手門檻 10%、最後 3 天不交易。只測 signal，避免「signal validation 後再調 portfolio、再看一次 validation」。

### DEV 比較方式

主要看配對差距 `Δe = R(candidate, e) − R(Mom20, e)`：

- Mean Δ、Median Δ、勝率
- P10、最差窗口
- 周轉、成本、警告

DEV 最後最多留 1–3 個 candidate，最好只有 1 個。

### Freeze

進 validation 前鎖死：訊號公式、lookback、skip、blend、quality filter、組合參數、評估指標、通過門檻。留下 config、manifest、git commit SHA。

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

不能「只差 0.1%，再調一下，再偷看一次」。

---

## 10. P2：定版後

- 24D production replay 與一致性 gate（第 8 節）
- Daily dry run：每天照正式流程跑，即使不提交也保存 inputs、outputs、logs、D-Plan、驗證結果、對帳結果
- 提前找出：資料延遲、API 失敗、日曆錯誤、公司行動錯誤、整張股數 bug、時區 bug、ETF 持股更新問題、schema 失敗

## 11. 正式比賽操作

每天只跑一個入口 `./run_daily.sh`，系統自己決定模式：

| 模式 | 意義 |
|---|---|
| `NORMAL` | 正常策略 |
| `FALLBACK_HOLD` | 沿用持股，不交易 |
| `FALLBACK_COMPLIANCE_REPAIR` | 只做必要合規修補 |
| `COLD_START_FALLBACK` | Day 1 機械式建倉 |
| `EMERGENCY_REVIEW_REQUIRED` | 連合法 fallback 都無法產生 |

輸出：驗證過的 D-Plan、audit report、submission status。

### Fail-safe 邏輯

舊邏輯「有問題 → BLOCK → 不交」改成「有問題 → 先試 safe fallback → 產生合法 D-Plan → 提交 → 人工處理」。只有連合法 fallback 都無法安全產生時，才進 `EMERGENCY_ESCALATION`，而不是默默不提交。

---

## 12. Repo 現況（2026-09-25）

| 項目 | 狀態 |
|---|---|
| 策略、組合、定股數、帳本 | 已有，`research/`、`autots_strategy/`、`competition/` |
| 官方日行情解析 | legacy 有，`legacy/src/v4_execution.py`；網路可連 TWSE |
| D-Plan generator、validator | 沒有 |
| Fallback、cold start | 沒有 |
| 主辦方持股 API | 路徑與認證未知；`/api/portfolio/holdings/` 直接請求回 404 |
| 提交管道 | 文件未寫，推測為後台上傳 |
| 主動 ETF 持股 | `NOT_ACQUIRED` |
| 2026 賽期交易日曆 | 未確認（`docs/task.md` U1） |

## 13. 需要外部提供（UNRESOLVED）

| ID | 問題 | 在確認前的做法 |
|---|---|---|
| P-U1 | 主辦方持股 API 的網址與認證 | 讀使用者匯出的持股檔（JSON/CSV） |
| P-U2 | 提交方式 | 產生檔案，人工上傳 |
| P-U3 | 主動 ETF 前十大持股來源 | 嘗試公開網站；取不到就 `ACTIVE_SHARE_UNVERIFIED` |
| P-U4 | Active Share 官方算法細節 | 採保守算法並標示假設 |
| P-U5 | 賽期交易日曆（10/26 或 10/27 起） | 從 TWSE 休市表確認 |
| P-U6 | `team_id` | 需主辦方配發值 |
| P-U7 | 非 LLM 管線是否算 AI Agent | 需主辦方確認（`docs/task.md` U10） |

其餘規則類 UNRESOLVED 見 [docs/task.md](task.md) 的 UNRESOLVED 總表。
