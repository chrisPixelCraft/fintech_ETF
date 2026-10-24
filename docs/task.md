# 任務定義：AI CUP 2026 玉山 ETF 初賽

> **2026-10-24：本頁為舊規格與歷史紀錄；現行以 [v4.3 核對](rules_v43.md) 為準。**
> 前10大／20%、自行重建官方現金、僅靠 holdings 匯出、UNVERIFIED 可 READY 均失效。已接 DataPack；官方 AS 算法與完整前15大資料未齊，live 阻擋。

本文由目標函數定義 AutoTS 主線，不引用 V3/V4/V5 策略。規則以 `official_docs/` 原始 PDF、schema、指南為準；衝突／缺漏標 **UNRESOLVED**。資料於 2026-09-24 以 `.venv/bin/python` 載入驗證。

引用：`辦法 pN` = `official_docs/AI CUP 2026玉山人工智慧公開挑戰賽_比賽辦法.pdf` 第 N 頁；`指南 Lx` = `official_docs/D-Plan_撰寫指南.md` 行號；`schema` = `official_docs/D-Plan.schema.json`；`rules.json` = `data/reference/competition_rules.json`。

## 一頁摘要

| 項目 | 數值 | 來源 |
|---|---|---|
| 初始本金 | TWD 1,000,000,000，全現金起始 | 辦法 p1；rules.json L22 |
| 賽期 | 2026-10-26 → 2026-11-27，官方稱 24 交易日 | 辦法 p4 四(三)、p11 |
| 股票池 | 固定 150 檔（上市 100 + 上櫃 50） | 辦法 p8 六(七) |
| 持股檔數 | 每日 20–30 檔 | 辦法 p8 六(八) |
| 單股上限 | NAV 10%；2330 為 25% | 辦法 p8 六(八) |
| 現金 | ≥0 且 <25% NAV | 辦法 p8 六(九)、p9 六(十四) |
| 交易單位 | 1,000 股整數倍，現股 long-only | 辦法 p8 六(十)；schema `order.shares` |
| 費稅 | 手續費買賣各 0.1425%；賣出稅 0.3% | 辦法 p8 六(十一) |
| 成交價 | T 日成交均價 = 成交金額 ÷ 成交股數 | 辦法 p8 六(五)；指南 L231 |
| 定股數 | floor(w × 前日NAV ÷ 前日收盤 ÷ 1000) × 1000 − 現持股 | 指南 L225-228 |
| 繳交時窗 | 前一日 19:30 → T 日 08:55（schema 寫 05:00–08:55） | 辦法 p7 六(三)；schema 根描述 |
| 排名 | 11/27 收盤後最終 NAV；同分比 MDD | 辦法 p6 五 |
| 入榜門檻 | 24 日中至少 22 日成功繳交 | 辦法 p4 四(三) |

## 1. 允許股票池

**Fact：** 限 2026-07 底市值上市前 100、上櫃前 50，共 150 檔（辦法 p8 六(七)）。`data/reference/universe_competition_20260731.csv` 150 列（TWSE 100、TPEX 50），與 `official_docs/玉山挑戰賽_投資組合及交易標的_150檔清單_v1.pdf` 逐碼一致。

池外持股警告（辦法 p9 六(十四)(5)）。`0050.TW` 不在池內，僅作日曆參考。3718 中光電投控自 2026-09-03 由 `5371.TWO` 改碼，見 `data/reference/tuning_2nd_official_coverage.csv`。名單 2026-09-18 發布，回測有成分前視偏誤（rules.json L61）。

### Active Share 限制（舊規格）

**Fact：** 投組前 10 大對任一主動 ETF 前 10 大 Active Share 不得 <20%；連續 2 日可取消資格，不走三次警告（辦法 p8 六(十三)、p10 註2；rules.json L122-129）。

`AS = 1/2 × Σ|Wi(投組) − Wi(Benchmark)|`。Benchmark 為 2026-09-11 證交所列表的 30 檔（`official_docs/玉山挑戰賽_主動型ETF列表_v1.pdf`）。本地無逐日權重，`data/reference/tuning_2nd_active_etf_readiness.csv` 全 `NOT_ACQUIRED`。

**UNRESOLVED：** top10 正規化、聯集、現金、持股延遲算法。

## 2. 本地歷史資料

**結論：** 價格自 2009 起；官方均價主要自 2025 起，外生變數自 2024 起。

| 資料集 | 路徑 | 期間 | 覆蓋 | 用途 |
|---|---|---|---|---|
| Yahoo 日線快照 | `data/yahoo_daily/v3_20260923/nominal_daily.parquet` | 2009-01-02 → 2026-09-23 | • 151 symbols（150 + 0050）<br>• 587,972 列 | 主要價量歷史 |
| 官方池日線 + 均價 | `data/tuning_2nd/official_universe/processed/daily.csv` | 2024-01-02 → 2026-09-21 | • 151 symbols<br>• 659 sessions | 成交均價、官方收盤 |
| 歷史重建池日線 | `data/v2/market_daily.csv` | 2024-01-02 → 2026-09-21 | • 152 symbols<br>• 非官方 150 池 | legacy 用，不建議 |
| 隔夜市場 | `data/v2_auxiliary/overnight_daily.csv` | 2024-01-02 → 2026-09-21 | • NASDAQ、NVDA、SOX<br>• TSM_ADR、USD_TWD<br>• 台指期夜盤 | 外生變數，含 `available_at` |
| 三大法人 | `data/v2_auxiliary/institutional_pit.csv` | 2024-01-08 → 2026-09-21 | 150 檔 | 外生變數，含 `available_at` |
| 產業指數 | `data/sector/sector_index_daily.csv` | 2024-01-02 → 2026-09-21 | 產業別收盤 | 外生變數 |
| 產業歸屬 | `data/sector/industry_history.csv` | 含 `effective_from/to` | 150 檔 | 分組、中性化 |
| 公司行動 | `data/extended/processed/official_corporate_actions.csv` | 2024-01-04 → 2026-09-21 | 149 檔、505 筆 | 除權息、分割 |
| 日內 1H | `data/extended/processed/hourly_canonical.csv` | 2024-10-01 → 2026-09-21 | 151 symbols | 不建議用 |

### Yahoo 日線與調整

**Fact：** 欄位 `date, symbol, open, high, low, close, adj_close, volume, dividend, split_ratio, split, future_split_factor, vendor_repaired, valid_price, tradable, action_neutral_return, quality_flags, valid_for_research`。

- `close` 為名目價。
	- 反推未來分割。
	- 未調股利。
	- 可估收盤／NAV。
	- 見 `metadata.json` 的 `share_convention`。
- `adj_close` 僅供稽核。
	- 含未來股利。
	- 禁作訊號／成交。
	- 見 `src/yahoo_daily.py:31`。
- `action_neutral_return` 為還原報酬。
	- `(close×split + dividend) / 前收 − 1`，可作 AutoTS 序列。
- 品質旗標：
	- 663 列 `INVALID_OHLCV_OR_ACTION`。
	- 13 列 `ACTION_NEUTRAL_RETURN_GT_30PCT`。

Yahoo 無成交金額，不能算官方均價。

### 歷史長度

依 `valid_price & volume>0` 首日：

| 首日不晚於 | 檔數 |
|---|---|
| 2009-01-31 | 110 |
| 2015-01-01 | 134 |
| 2020-01-01 | 139 |
| 2022-01-01 | 141 |
| 2024-01-01 | 144 |
| 2025-01-01 | 148 |

全 150 檔覆蓋自 2026-09-03 起；Yahoo 3718 僅 15 列，前身 5371 在官方池 daily.csv 有 2024-01-02 → 2026-08-21。晚上市：7734、7751、7750、7769、7828；7828 Yahoo 自 2025-05-08、官方池自 2026-04-22。

**UNRESOLVED：** 早段是否興櫃價。**Inference：** AutoTS 須處理不等長序列。

### 官方成交均價覆蓋

**Fact：** `daily.csv` 的 `execution_vwap` 與 `official_turnover / execution_volume` 重算一致，最大相對誤差 4.4e-16。

| 年 | 覆蓋率 | 來源 |
|---|---|---|
| 2024 | 20.0% | FinMind 對官方交叉驗證 |
| 2025 | 99.3% | TWSE / TPEx / FinMind |
| 2026（至 09-21） | 99.3% | TWSE / TPEx |

2025-01-02 → 2026-09-21 共 417 sessions，約 17 個不重疊 24 日 episode。0.34% 均價在 H–L 外，保留品質旗標。2025 前無本地官方均價，須標 proxy；`outputs/v4`、`outputs/v5` 不存在，舊均價快取未留。

|VWAP/open − 1| 中位數 0.97%，|VWAP/close − 1| 中位數 0.53%。**Inference：** open proxy 誤差不可忽略。

### 交易日曆

`data/yahoo_daily/v3_20260923/calendar_v2.json`：4,330 sessions，2009-01-05 → 2026-09-23；0050 觀測加廣度規則，為研究 proxy，非官方日曆。

**已解決（U1）：** 主辦指定 10/26；10/26–11/27 有 25 平日，辦法稱 24 交易日，休市表列 10/26 補假。首日設 10/26，休市自動跳過；見 [UNRESOLVED 總表](#unresolved-總表) U1。

## 3. 每日資訊截止點

**Fact：** T 日 D-Plan 於 08:55 前交（辦法 p7 六(三)）。**UNRESOLVED：** 開窗為前一日曆日 19:30（辦法）或 T 日 05:00（schema）；採共同區間 05:00–08:55（`docs/v2_double_check_rules.md` L99）。

決策可用 T−1 價量、成交金額、主辦結算持股／NAV、美股收盤（台北約 04:00–05:00）、台指期夜盤（至 05:00）；禁用 T 日任何價格及均價。外生資料須 `available_at` 早於 T 日 08:55。

定股數僅用 T−1 收盤與 NAV（指南 L225-230；rules.json L86-89）；持股以系統 `GET /api/portfolio/holdings/` 為準（指南 L241-252）。

## 4. 投資組合限制

| 限制 | 條件 | 判定時點 | 來源 |
|---|---|---|---|
| 股票池 | 150 檔內 | 當日結算 | 辦法 p8 六(七)、p9 (5) |
| 檔數 | 20 ≤ n ≤ 30 | 當日結算 | 辦法 p8 六(八)、p9 (3) |
| 單股上限 | ≤10% NAV | 當日收盤 NAV | 辦法 p8 六(八)、p9 (4) |
| 2330 上限 | ≤25% NAV | 當日收盤 NAV | 同上 |
| 現金 | 0 ≤ cash < 25% NAV | 當日結算 | 辦法 p8 六(九)、p9 (2) |
| 交易型態 | 現股、整股、無融資融券放空當沖 | 下單 | 辦法 p8 六(十) |
| Active Share | 對每檔主動 ETF ≥20% | 不定期檢查 | 辦法 p8 六(十三) |

### 違規處理

**Fact：** 每日任一違規記一次警告，當日交易無效、沿用前日投組算 NAV；累積 3 次取消資格（辦法 p9 六(十四)）。事由：漏交 D-Plan、現金負或 >25%、檔數不在 20–30、單股超限、池外股票、抽檢決策與交易不一致。

主動加碼超限當日警告；價格漂移超限有 5 交易日減碼，第 6 日才警告（辦法 p10 註3）。**UNRESOLVED：** p8 要現金 <25%，p9 寫 >25% 才警告；安全採嚴格 <25%。

**Inference：** 首日 100% 現金，結算即須 ≥75% 持股、≥20 檔，否則警告。

## 5. 交易成本

買賣各 0.1425% 手續費，賣出稅 0.3%，扣現金（辦法 p8 六(十一)）。換股約 0.585% = 0.1425% × 2 + 0.3%；首日成本約 0.1425% × 部位。

**UNRESOLVED：** 費用取整、最低費（rules.json L97-98 為 null）。官方未計滑價／衝擊，無成交量上限。

## 6. 成交與結算

**Fact：** 委託全數按 T 日均價成交（辦法 p7 六(四)、p8 六(五)）；均價 = 成交金額 ÷ 股數（指南 L231；rules.json L90）。股數於 T−1 固定，不隨成交價重算，均價偏離可能使現金為負。

NAV = Σ 持股 × 當日收盤 + 現金（辦法 p6 五）。主辦調整除權息／分割股數（辦法 p8 六(十二)）；現金股利期末才加 NAV，除息日不能動用（辦法 p10 註1）。台股 ±10% 保護涵蓋均價，新上市前 5 日例外。

**UNRESOLVED：** 無價時辦法稱全成交、指南 L243-244 稱可能未成交，採不捏造價格。股票股利零股與官方持股公式未定（rules.json L322）。同日買賣順序、賣款支應買進未明；`funding_for` 暗示可用，現金按日終判定。

### 本地執行價

| 模式 | 來源 | 期間 | 狀態 |
|---|---|---|---|
| 官方均價 | `daily.csv` 的 `execution_vwap` | 2025-01 → 2026-09-21 | 可用，覆蓋 99% |
| 官方均價 | 同上 | 2024 | 覆蓋 20% |
| 即時官方均價 | `scripts/v4_build_execution_data.py` | 任意日 | 需網路；TWSE MI_INDEX / TPEx dailyQuotes |
| open proxy | Yahoo `open` | 2009 起 | 研究 proxy，須標示 |

## 7. D-Plan 要求

**Fact：** 每日 JSON、schema v4.0，12 個頂層必填（schema `required`）：`schema_version="4.0"`、`doc_type="D-Plan"`、`team_id`、`trade_date`、`sources`、`observations`、`market_view`、`inferences`、`decisions`、`no_trade_decisions`、`orders`、`agent_metadata`。

檔名 `D-Plan_<team_id>_<trade_date>.json`（指南 L34-36）；時間 `+08:00`（指南 L32）。

### 語意檢查

| 代號 | 規則 | 後果 | 來源 |
|---|---|---|---|
| C1 | 引用鏈完整 | 拒收 | 指南 L29-30、L51-52 |
| ID | S/O/I/D 從 1 連號 | 格式錯誤 | 指南 L49-50 |
| C2 | orders 逐位元等於官方公式 | 見下方 UNRESOLVED | schema `orders` |
| C6 | `run_completed_at` ≤ 收件時間 | 拒收 | schema `agent_metadata` |
| C9/C11 | 現金、檔數、權重 server 重算 | 違規 | schema `decision.target_weight` |
| C12 | posture 與淨流向一致 | 拒收 | schema `market_view.posture` |
| C13 | 賣超持股 | 整份拒收 | 指南 L241 |
| C14 | `funding_for` 必須真有缺口 | 拒收 | schema `decision.funding_for` |
| 覆蓋 | 昨日每檔持股出現在 decisions 或 no_trade 擇一 | 拒收 | 指南 L180-182 |

C12：`increase` 淨買超、`reduce` 淨賣超、`hold` |淨流向| ≤2% NAV；以 T−1 收盤估，現金比須在 `target_cash_pct_range`（指南 L150-162）。Action：BUY 原持股 0、ADD 加碼、TRIM 減碼留倉、SELL_ALL 出清（指南 L198-205）。`target_weight` 經 floor 必須反推出實際委託股數。

**UNRESOLVED：** C2/C12，schema 寫拒收、反例 `_NOTE` 寫警示；本地採拒收。官方 `verify_dplan.py` 未釋出。

### 每日產物與一次性文件

Sources 實際引用；observations 僅可核對事實／數字；market_view 含 regime、stance、posture；inferences 從事實推個股結論；decisions + no_trade_decisions 覆蓋持股；orders 機械推導。`code_version` 對應當日程式，改參數須改版（指南 L285-288）。

ETF 策略說明於 10/21–10/26 交；名稱以「主動」起、≤20 字，主題 ≤50 字、理念 100–300 字（辦法 p2 二(七)；`docs/v2_double_check_rules.md` L118）。每日 D-Plan 須一致（指南 L58-59、L298）。

## 8. 評估期間

初賽 2026-10-26 → 2026-11-27（辦法 p2 二(七)、p11），官方稱 24 交易日（p4 四(三)）；11/27 收盤計最終 NAV（p6 五）。首日按 10/26（U1）。

## 9. 最終目標與計分

最終 NAV 高者勝，同分比低 MDD（辦法 p6 五）；學生組、社會組各前 6 名晉級（p2 二(八)）。總分 = 初賽分 + 複賽分；初賽分 = 隊伍 NAV ÷ Σ 晉級隊 NAV，複賽分 = 募資額 ÷ Σ 募資額（p6-7）。期末 NAV 含累積股利（p10 註1）。

**Inference：** 進前 6 是相對排名目標，與最大化期望 NAV 不同，可能偏好高變異；需使用者決定。

## 10. 每日繳交流程

| 時點（台北） | 事件 | 來源 |
|---|---|---|
| T−1 13:30 | 收盤 | 市場慣例 |
| T−1 盤後 | TWSE / TPEx 公布日行情與成交金額 | `src/v4_execution.py` URL |
| T−1 19:00 | 主辦方更新 NAV 與排行榜 | 辦法 p8 六(六) |
| T−1 19:30 或 T 05:00 | 繳交時窗開啟（UNRESOLVED） | 辦法 p7；schema |
| T 約 05:00 | 美股收盤、夜盤結束 | `overnight_daily.csv` |
| T 08:55 前 | 繳交 D-Plan；每日最多 25 次 | 辦法 p2 二(八)、p7 六(三) |
| T 09:00–13:30 | 以 T 日均價全數成交 | 辦法 p8 六(五) |
| T 晚間 | 結算、檢查違規、更新 NAV | 辦法 p8-9 |

步驟：查持股／T−1 NAV → 更新驗證 T−1 資料 → 預測／權重 → 限制／orders → 產生驗證 D-Plan → `READY_TO_SUBMIT` 或 `BLOCK_SUBMISSION`。

**UNRESOLVED：** 同日多次繳交僅明定 11/27 用最後一次。非 LLM 管線是否算 AI Agent 需確認：辦法 p7 六(一) 要求自主決策，`model_provider` 可 `other`，D-Plan 仍需自然語言 logic。

## 11. 從目標重新 formulate

### 變數、資訊與轉移

每日 t = 1…24，於 T 日 08:55 前在 150 檔選 `w_t`。官方委託公式：`q_i,t = floor(w_i,t × V_{t−1} / C_i,{t−1} / 1000) × 1000 − h_i,{t−1}`；`V` = NAV、`C` = 收盤、`h` = 股數；另宣告 `net_exposure_intent`、`target_cash_pct_range`。

`I_t` = T−1 價量／結算帳本及 T 日截止前外生資料，禁含 T 日價格；須 `available_at < T 08:55`。

- 成交 `h_t = h_{t−1} + q_t`，再套公司行動。
- 現金 `c_t = c_{t−1} − Σ q × P^avg_t − 費稅`。
- 估值 `V_t = Σ h_t × C_t + c_t`。
- 股利另計 `D_t`，期末加入。

結算限制：`20 ≤ |{i : h_i,t > 0}| ≤ 30`、`h_i,t × C_i,t / V_t ≤ 0.10`（2330 為 0.25）、`0 ≤ c_t < 0.25 × V_t`、`i ∈ U150`。違規當日交易作廢、記警告。

目標：最大化 `V_24 + D_24`；`h_0 = 0`、`c_0 = V_0 = 10 億`，同分最小化 MDD。

### 預測含意

**Evidence：** T 日均價成交、未來收盤估值、T−1 收盤定股數。**Interpretation：** 決策報酬為 `C_{t+h−1} / P^avg_t − 1`。**Conclusion：** 預測 T 均價 → 未來收盤，非 T−1 → T close-to-close。

現金 <25% 鎖定 ≥75% 曝險；普通單股 ≤10% 至少需 8 檔才滿倉，實際規則 ≥20 檔。**Inference：** alpha 主要來自橫斷面選股，擇時僅 0–25% 現金空間。

換股預期超額須跨 0.585% 成本；**Inference：** 1 日 horizon 難覆蓋。剩餘 `24 − t + 1` 日，期末不清倉、末段成本難回收。首日須一天配置 ≥75%，選股影響最大。

### 驗證含意與待決事項

Episode 模擬完整 24 session：全現金、相同定股數／成交／費稅／違規。官方均價 2025-01 → 2026-09 約 17 個不重疊窗口，可增滾動起點；2009 起較早期須標 open／其他 proxy。涵蓋多市場狀態及 10–11 月類比。

2026 名單回套有存活偏誤，2010 後資料已被舊策略看過。**Hypothesis：** AutoTS 調參前應凍結 holdout。使用者須選期望 NAV 或前 6 機率，並決定 holdout 窗口與凍結時點。

## UNRESOLVED 總表

| ID | 問題 | 安全預設 |
|---|---|---|
| U1 | 首個交易日 | **以 10/26 為準**：主辦方指定首日 10/26（辦法範例「10/25 19:30～10/26 08:55 繳交」）；證交所休市表 10/26 為補假。`contest_start` 設 10/26，兩種情況計數都正確；10/26 照常產生並上傳 D-Plan |
| U2 | 繳交開窗 19:30 或 05:00 | 只在 05:00–08:55 送件 |
| U3 | 現金 25% 邊界 | 嚴格 <25% |
| U4 | Active Share 算法與資料 | 狀態 UNKNOWN，不宣稱通過 |
| U5 | 費用取整、最低費 | 不取整，標為假設 |
| U6 | 無成交價是否成交 | 不捏造價格，以系統持股為準 |
| U7 | 股票股利零股處理 | 零股持有不動，標為假設 |
| U8 | C2/C12 違反是拒收或警告 | 視為拒收 |
| U9 | 同日多次繳交採哪次 | 視為最後一次，且每次都需完整合規 |
| U10 | 非 LLM 管線是否算 AI Agent | 需主辦方確認 |
| U11 | 7828 等早段是否興櫃價 | 以上市日後資料為主 |
| U12 | 官方 `verify_dplan.py` | 未釋出，用本地驗證器 |
