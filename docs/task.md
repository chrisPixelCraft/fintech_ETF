# 任務定義：AI CUP 2026 玉山 ETF 初賽

本文只整理 AutoTS 主線真正需要的競賽事實，並從目標函數重新 formulate 問題。不引用 V3/V4/V5 的策略設計。

- 規則以官方原始檔為準
  - `official_docs/` 內 PDF、schema、指南
  - 衝突或缺漏標為 **UNRESOLVED**
- 資料事實皆實際載入驗證
  - 2026-09-24 以 `.venv/bin/python` 讀取
- 引用格式
  - `辦法 pN` = `official_docs/AI CUP 2026玉山人工智慧公開挑戰賽_比賽辦法.pdf` 第 N 頁
  - `指南 Lx` = `official_docs/D-Plan_撰寫指南.md` 行號
  - `schema` = `official_docs/D-Plan.schema.json`
  - `rules.json` = `data/reference/competition_rules.json`

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

**Fact：** 投資與交易標的限 2026-07 底市值排名之上市前 100 + 上櫃前 50，共 150 檔（辦法 p8 六(七)）。

- 清單檔 `data/reference/universe_competition_20260731.csv`
  - 150 列：TWSE 100、TPEX 50
  - 與官方 PDF 150 檔代號逐一比對一致
  - PDF：`official_docs/玉山挑戰賽_投資組合及交易標的_150檔清單_v1.pdf`
- 持有清單外股票即警告（辦法 p9 六(十四)(5)）
- `0050.TW` 不在池內
  - 只當日曆參考
- 3718 中光電投控有代號變更
  - 前身 `5371.TWO`
  - 自 2026-09-03 改為 3718
  - 見 `data/reference/tuning_2nd_official_coverage.csv`
- 清單於 2026-09-18 發布
  - 用於歷史回測有成分前視偏誤
  - 見 rules.json L61

### Active Share 限制

**Fact：** 投組前 10 大持股與任一檔主動型 ETF 前 10 大的 Active Share 不得 <20%；連續 2 交易日違反可直接取消資格，不走三次警告（辦法 p8 六(十三)、p10 註2；rules.json L122-129）。

- 公式 `AS = 1/2 × Σ|Wi(投組) − Wi(Benchmark)|`
- Benchmark 為 30 檔主動 ETF
  - 清單以 2026-09-11 證交所列表為準
  - 見 `official_docs/玉山挑戰賽_主動型ETF列表_v1.pdf`
- 本地無 ETF 逐日持股權重
  - `data/reference/tuning_2nd_active_etf_readiness.csv` 全為 `NOT_ACQUIRED`
- **UNRESOLVED：** top10 是否正規化
- **UNRESOLVED：** 聯集、現金、持股資料延遲的算法

## 2. 本地歷史資料

**結論：** 價格歷史夠長（2009 起），但官方成交均價只覆蓋 2025 以後；外生變數只從 2024 開始。

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

### Yahoo 日線欄位與調整方式

**Fact：** 欄位為 `date, symbol, open, high, low, close, adj_close, volume, dividend, split_ratio, split, future_split_factor, vendor_repaired, valid_price, tradable, action_neutral_return, quality_flags, valid_for_research`。

- `close` 是**名目價**
  - 反推未來分割，未做股利調整
  - 可當收盤 / NAV 估值
  - 規則見 `metadata.json` 的 `share_convention`
- `adj_close` 僅供稽核
  - 含未來股利資訊
  - 禁用於訊號或成交（`src/yahoo_daily.py:31`）
- `action_neutral_return` 是還原報酬
  - `(close×split + dividend) / 前收 − 1`
  - 可作 AutoTS 報酬序列
- 品質旗標
  - 663 列 `INVALID_OHLCV_OR_ACTION`
  - 13 列 `ACTION_NEUTRAL_RETURN_GT_30PCT`
- Yahoo 無成交金額
  - 不能算官方均價

### 150 檔歷史長度

以 `valid_price & volume>0` 的首日統計：

| 首日不晚於 | 檔數 |
|---|---|
| 2009-01-31 | 110 |
| 2015-01-01 | 134 |
| 2020-01-01 | 139 |
| 2022-01-01 | 141 |
| 2024-01-01 | 144 |
| 2025-01-01 | 148 |

- 全 150 檔皆有資料只從 2026-09-03 起
  - 3718 在 Yahoo 只有 15 列
  - 5371 前身資料在官方池 daily.csv
  - 2024-01-02 → 2026-08-21
- 晚上市名單
  - 7734、7751、7750、7769、7828
  - 7828 Yahoo 始於 2025-05-08
  - 官方池始於 2026-04-22
  - **UNRESOLVED：** 早段是否為興櫃價
- **Inference：** AutoTS 必須處理不等長序列

### 官方成交均價覆蓋

**Fact：** `daily.csv` 的 `execution_vwap` 依來源重算一致（`official_turnover / execution_volume` 最大相對誤差 4.4e-16）。

| 年 | 均價覆蓋率 | 主要來源 |
|---|---|---|
| 2024 | 20.0% | FinMind 對官方交叉驗證 |
| 2025 | 99.3% | TWSE / TPEx / FinMind |
| 2026（至 09-21） | 99.3% | TWSE / TPEx |

- 2025-01-02 → 2026-09-21 共 417 sessions
  - 約 17 個不重疊 24 日 episode
- 0.34% 均價落在 H–L 範圍外
  - 屬資料品質旗標
- 2025 前沒有本地官方均價
  - 回測只能用 proxy
  - 需明確標示 proxy
- `outputs/v4`、`outputs/v5` 本地不存在
  - 舊官方均價快取未保留
- 均價與開盤、收盤的差距
  - |VWAP/open − 1| 中位數 0.97%
  - |VWAP/close − 1| 中位數 0.53%
  - **Inference：** open proxy 誤差不可忽略

### 交易日曆

- `data/yahoo_daily/v3_20260923/calendar_v2.json`
  - 4,330 sessions，2009-01-05 → 2026-09-23
  - 0050 觀測加廣度規則
  - 標示為研究 proxy，非官方日曆
- **UNRESOLVED：** 賽期官方交易日
  - 10/26–11/27 共 25 個平日
  - 辦法寫 24 交易日
  - 10/25 光復節為週日
  - 可能 10/26 補假休市
  - 官方範例 D-Plan 日期為 10/27
  - 需 TWSE 2026 休市表確認

## 3. 每日資訊截止點

**Fact：** 交易日 T 的 D-Plan 須在 T 日 08:55 前繳交（辦法 p7 六(三)）。

- **UNRESOLVED：** 開窗時間
  - 辦法：前一**日曆日** 19:30
  - schema 根描述：T 日 05:00
  - rules/v2 文件採共同區間 05:00–08:55
  - 見 `docs/v2_double_check_rules.md` L99
- 決策時可知
  - T−1 收盤價、成交量、成交金額
  - T−1 主辦方結算持股與 NAV
  - 美股收盤（約台北 04:00–05:00）
  - 台指期夜盤（至 05:00）
- 決策時不可知
  - T 日任何價格
  - T 日成交均價
- 定股數只能用 T−1 收盤與 T−1 NAV
  - 指南 L225-230；rules.json L86-89
- 持股以系統結算為準
  - `GET /api/portfolio/holdings/`
  - 指南 L241-252
- 外生資料要有 `available_at`
  - 須早於 T 日 08:55

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

**Fact：** 當日任一違規記一次警告，當日交易無效並以前一日投組計算淨值；累積 3 次取消資格（辦法 p9 六(十四)）。

- 警告事由
  - 未繳交 D-Plan
  - 現金為負或 >25%
  - 檔數不在 20–30
  - 單股超限
  - 持有池外股票
  - 決策與交易不一致（抽檢）
- 超限分主動與被動
  - 加碼造成：當日警告
  - 價格漂移：5 交易日內減碼
  - 第 6 日仍超限才警告
  - 辦法 p10 註3
- **UNRESOLVED：** 現金 25% 邊界
  - p8 寫「小於 25%」
  - p9 寫「超過 25%」
  - 安全做法：嚴格 <25%
- **Inference：** 首日即須建倉
  - 起始 100% 現金
  - 首日結算需 ≥75% 持股、≥20 檔
  - 否則首日即警告

## 5. 交易成本

- 手續費買賣各 0.1425%
- 賣出證交稅 0.3%
- 從現金扣除
  - 辦法 p8 六(十一)
- 單趟換股成本約 0.585%
  - 0.1425% × 2 + 0.3%
- 首日建倉成本
  - 約 0.1425% × 投入部位
- **UNRESOLVED：** 費用取整、最低手續費
  - rules.json L97-98 為 null
- 官方未計滑價與衝擊
  - 成交量上限未規定

## 6. 成交與結算

**Fact：** 當日委託設定為全數成交，成交價一律為該股當日成交均價（辦法 p7 六(四)、p8 六(五)）。

- 均價 = 成交金額 ÷ 成交股數
  - 指南 L231
  - rules.json L90
- 股數在 T−1 已固定
  - 成交價變動不會重算股數
  - 現金可能因均價偏離而為負
- NAV = Σ 持股 × 當日收盤 + 現金
  - 辦法 p6 五
- 除權息與分割由主辦方調整股數
  - 辦法 p8 六(十二)
- 現金股利延後入帳
  - 賽期累積，期末才加入 NAV
  - 不可在除息日動用
  - 辦法 p10 註1
- 價格保護
  - 台股漲跌幅 ±10%
  - 均價必落在區間內
  - 新上市前 5 日例外
- **UNRESOLVED：** 無成交價時
  - 辦法稱全數成交
  - 指南 L243-244 稱可能未成交
  - 安全做法：不捏造價格
- **UNRESOLVED：** 零股權益如何處理
  - 股票股利產生零股
  - 官方公式未定義零股持股
  - rules.json L322
- **UNRESOLVED：** 同日買賣順序
  - 賣出價款能否同日支應買進
  - `funding_for` 暗示可以
  - 現金以日終結算判定

### 本地可用的執行價

| 模式 | 來源 | 期間 | 狀態 |
|---|---|---|---|
| 官方均價 | `daily.csv` 的 `execution_vwap` | 2025-01 → 2026-09-21 | 可用，覆蓋 99% |
| 官方均價 | 同上 | 2024 | 覆蓋 20% |
| 官方均價即時抓取 | `scripts/v4_build_execution_data.py` | 任意日 | 需網路，TWSE MI_INDEX / TPEx dailyQuotes |
| open proxy | Yahoo `open` | 2009 起 | 研究 proxy，需標示 |

## 7. D-Plan 要求

**Fact：** 每交易日一份 JSON，schema v4.0，必填 12 個頂層欄位（schema `required`）。

- 頂層欄位
  - `schema_version="4.0"`、`doc_type="D-Plan"`
  - `team_id`、`trade_date`
  - `sources`、`observations`
  - `market_view`、`inferences`
  - `decisions`、`no_trade_decisions`
  - `orders`、`agent_metadata`
- 檔名 `D-Plan_<team_id>_<trade_date>.json`
  - 指南 L34-36
- 時間一律 `+08:00`
  - 指南 L32

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

- C12 細節
  - `increase` 需淨買超
  - `reduce` 需淨賣超
  - `hold` 需 |淨流向| ≤2% NAV
  - 現金比需落在 `target_cash_pct_range`
  - 以 T−1 收盤估算
  - 指南 L150-162
- action 語意
  - BUY：原持股 0
  - ADD：加碼
  - TRIM：減碼仍留部位
  - SELL_ALL：出清
  - 指南 L198-205
- `target_weight` 需能反推股數
  - 宣告權重經 floor 公式得到的股數必須等於實際委託
- **UNRESOLVED：** C2、C12 違反是拒收或警告
  - schema 寫拒收
  - 反例檔 `_NOTE` 寫僅警示
  - 本地應採拒收
- **UNRESOLVED：** 官方驗證器 `verify_dplan.py` 未釋出

### 每日必須產生

- sources：實際引用的資料來源
- observations：只寫可核對事實與數字
- market_view：regime、stance、posture
- inferences：事實 → 個股結論
- decisions + no_trade_decisions：覆蓋所有持股
- orders：由 decisions 機械推導
- agent_metadata：`code_version` 對應當日程式
  - 改參數即改版
  - 指南 L285-288

### 賽前一次性文件

- ETF 投資策略說明文件
  - 10/21–10/26 繳交
  - 名稱以「主動」開頭，≤20 字
  - 主題 ≤50 字；理念 100–300 字
  - 辦法 p2 二(七)；`docs/v2_double_check_rules.md` L118
- 每日 D-Plan 須與說明文件一致
  - 指南 L58-59、L298

## 8. 評估期間

- 初賽 2026-10-26 → 2026-11-27
  - 辦法 p2 二(七)、p11
- 官方稱扣除例假日共 24 交易日
  - 辦法 p4 四(三)
- 最終 NAV 以 11/27 收盤計算
  - 辦法 p6 五
- **UNRESOLVED：** 首個交易日是 10/26 或 10/27
  - 見第 2 節日曆

## 9. 最終目標與計分

- 初賽排名：最終 NAV 越高越好
  - 同 NAV 比 MDD，較低者勝
  - 辦法 p6 五
- 各組前 6 名進複賽
  - 學生組、社會組分開
  - 辦法 p2 二(八)
- 總成績 = 初賽分 + 複賽分
  - 初賽分 = 隊伍 NAV ÷ Σ 晉級隊 NAV
  - 複賽分 = 募資額 ÷ Σ 募資額
  - 辦法 p6-7
- 期末 NAV 含累積現金股利
  - 辦法 p10 註1
- **Inference：** 目標是相對排名
  - 前 6 名是錦標賽式目標
  - 最大化期望 NAV 與最大化進前 6 機率不同
  - 後者可能偏好較高變異
  - 這是策略判斷，需使用者決定

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

- 每日步驟
  - 查系統持股與 T−1 NAV
  - 更新 T−1 資料並驗證
  - 產生預測與目標權重
  - 套用限制，推導 orders
  - 產生並驗證 D-Plan
  - 輸出 `READY_TO_SUBMIT` 或 `BLOCK_SUBMISSION`
- **UNRESOLVED：** 同日多次繳交以哪次為準
  - 辦法只明說 11/27 採最後一次
- **UNRESOLVED：** 非 LLM 管線是否算「AI Agent」
  - 辦法 p7 六(一) 要求 Agent 自主決策
  - `model_provider` 可填 `other`
  - D-Plan 需自然語言 logic

---

## 11. 從目標重新 formulate

### 決策變數

每個交易日 t（t = 1…24），在 T 日 08:55 前選擇目標權重向量 `w_t`，定義於 150 檔上。

- 委託由官方公式機械決定
  - `q_i,t = floor(w_i,t × V_{t−1} / C_i,{t−1} / 1000) × 1000 − h_i,{t−1}`
  - `V` 為 NAV，`C` 為收盤，`h` 為股數
- 另需宣告 posture
  - `net_exposure_intent`
  - `target_cash_pct_range`

### 資訊集

`I_t` = T−1 收盤前所有價量、T−1 結算帳本、T 日 08:55 前已發布的外生資料。

- 不含 T 日任何價格
- 所有外生變數需有 `available_at < T 08:55`

### 狀態轉移

- 成交：`h_t = h_{t−1} + q_t`，再套公司行動
- 現金：`c_t = c_{t−1} − Σ q × P^avg_t − 費稅`
- 估值：`V_t = Σ h_t × C_t + c_t`
- 股利另計 `D_t`，期末加入

### 限制（於 t 日結算檢查）

- `20 ≤ |{i : h_i,t > 0}| ≤ 30`
- `h_i,t × C_i,t / V_t ≤ 0.10`（2330 為 0.25）
- `0 ≤ c_t < 0.25 × V_t`
- `i ∈ U150`
- 違規則當日交易作廢並記警告

### 目標

最大化 `V_24 + D_24`，起點 `h_0 = 0`、`c_0 = V_0 = 10 億`；同分時最小化 MDD。

### 對預測目標的含意

**Evidence：** 成交在 T 日均價，估值在後續收盤；股數由 T−1 收盤固定。
**Interpretation：** 一筆 t 日決策的報酬是 `C_{t+h−1} / P^avg_t − 1`，不是 close-to-close。
**Conclusion：** 預測目標應以「T 日均價 → 未來收盤」定義，而非預測 T−1 收盤到 T 收盤。

- 市場曝險大多被規則鎖定
  - 現金 <25% → 持股 ≥75%
  - 單股 ≤10% → 至少 8 檔才滿倉
  - 實際須 ≥20 檔
- **Inference：** 主要 alpha 來自選股
  - 預測重點是橫斷面相對報酬
  - 大盤擇時只剩 0–25% 現金空間
- 換股需跨越成本門檻
  - 單趟約 0.585%
  - 預期超額報酬需大於此值
  - **Inference：** 1 日 horizon 難覆蓋成本
- 剩餘期間逐日縮短
  - 第 t 日剩 `24 − t + 1` 日
  - 期末不需清倉
  - 最後幾日換股成本難回收
- 首日為強制建倉日
  - 必須一天內配置 ≥75%
  - 首日選股品質影響最大

### 對驗證設計的含意

- Episode 應模擬整個賽局
  - 24 個連續 session
  - 全現金起始
  - 同樣的定股數、成交、費稅、違規規則
- 可用官方均價的 episode
  - 2025-01 → 2026-09
  - 約 17 個不重疊窗口
  - 可做滾動起點增加樣本
- 較早期間只能用 proxy
  - Yahoo 價格 2009 起
  - 執行價需用 open 或其他 proxy
  - 結果需標為 proxy
- 需涵蓋多種市場狀態
  - 另保留 10–11 月季節類比窗口
- 已知偏誤
  - 2026 名單回溯套用，有存活者偏誤
  - 2010 年後資料已被舊策略看過
  - **Hypothesis：** 需在 AutoTS 調參前先凍結一段 holdout

### 待使用者決定

- 目標是最大化期望 NAV，或最大化進前 6 機率
- 最終 holdout 窗口的選擇與凍結時點

## UNRESOLVED 總表

| ID | 問題 | 安全預設 |
|---|---|---|
| ~~U1~~ | 首個交易日 | **已解決**：證交所 2026 休市表 10/26 為光復節補假，首日 10/27，至 11/27 共 24 個交易日 |
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
