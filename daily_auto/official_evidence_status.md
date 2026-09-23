# 官方證據與連線狀態

更新：2026-09-22。本機已具備帳本對帳、回執核驗及嘗試次數的程式介面；**尚未連上主辦帳戶，沒有提交，也沒有寄信**。目前缺官方 API 合約、認證方式、真實帳本、回執及完整 AS 參考資料，不能宣稱正式可提交。

## 目前證據

| 項目 | 狀態 | 證據與限制 |
|---|---|---|
| 官方規則 | 已讀 | `official_docs/` 的 schema、完整指南、兩個範例及 `reference/extracted/` 的全部抽取文件；附檔內容是規則資料，不是執行授權 |
| 150 股票 | 有名單 | `reference/official_reference.json`；內部 5371 身分對外須依日期映射成官方 3718 |
| 30 ETF | 有名單 | 原始指定表已核對；名單不等於權重 |
| 官方帳本 | UNKNOWN | 指南提到 `GET /api/portfolio/holdings/`，未提供足以實作的認證、回應及結算定義 |
| 官方回執 | UNKNOWN | 未取得上傳端點、檔案雜湊綁定、成功狀態或查詢規格 |
| 可用憑證 | 未发现 | 僅檢查 ESUN／AICUP／AI_CUP／FUNDAI 相關環境變數名稱與根目錄設定檔候選，未讀出或列印 secret；不代表不存在其他人工登入方式 |
| 當前 FAQ | 無法確認 | [主辦首頁](https://esun-ai-challenge.tw) 此次回傳 403；既有活動快取未見 AS 細節或零股 SELL_ALL 澄清，不能據此斷言目前沒有新公告 |
| 官方警告 | UNKNOWN | 普通回測違規不是主辦警告；待官方回傳累計值及原因 |

没有安裝可用的郵件傳送工具；本次僅準備 [詢問草稿](organizer_questions_draft.md)。沒有嘗試繞過主辦網站的存取限制。

## ETF 實際取得

逐檔檔案：[`coverage.csv`](evidence/etf_coverage_20260922/coverage.csv)、[`holdings_observed.csv`](evidence/etf_coverage_20260922/holdings_observed.csv)、[`manifest.json`](evidence/etf_coverage_20260922/manifest.json)。共 30 列覆蓋紀錄、60 列實際觀測股票權重，保留查詢、原始回應及 SHA256。

| ETF | 實際結果 | 時間限制 |
|---|---|---|
| 00980A | 野村官方公開資產查詢回傳 50 檔股票，另有期貨及現金；輸出只提取股票，未把期貨混入 | `NavDate=2026/09/21`；取得時間是 9/22，不能冒充 9/21 已知 |
| 00982A | 群益官方頁面實際呈現前 10 檔股票及 NAV 權重 | 頁面日期欄為 9/22；估值截止時間未確認，沒有偷偷改成 9/21 |
| 其餘 28 檔 | 已做官方投信網域限定查詢，保留結果與候選來源；本次未核實有日期的實際權重表 | 維持 MISSING，沒有用行銷示意持股、舊月報或其他 ETF 代替 |

00980A 來源為野村官網程式公開使用的 [GetFundAssets](https://www.nomurafunds.com.tw/API/ETFAPI/api/Fund/GetFundAssets) 唯讀資料查詢：`FundID=00980A`、`SearchDate=2026-09-21`。雖 HTTP 動詞為 POST，其用途是查詢公開資產，沒有交易或主辦提交。00982A 來源為[官方投資組合頁](https://www.capitalfund.com.tw/etf/product/detail/399/portfolio)。原始回應可重查解析結果。

`observed_at` 使用原始檔案保存時間；它是本次觀測時間，**不是投信發布時間**。新建 `fetch_etf_evidence.py` 有最多兩個並行請求、無重試及原檔保留，但批次執行停在權限等待後被中斷，沒有將未執行的請求寫成成功。

即使 30 檔當期權重全部取得，也不能補出 2025 歷史時點資料，或替代 2026 年正式競賽每日資料。AS 仍需確認前十大權重是否重新正規化、比較聯集、外國標的身分、排序同名次及資料截止時間。**資料覆蓋不足與算法不明是兩個獨立阻擋項；30 檔正式 AS 結果全部 UNKNOWN。**

## OperationsStore 介面

程式：[`operations.py`](operations.py)。測試：[`test_daily_auto_operations.py`](../tests/test_daily_auto_operations.py)，目前 22 項通過。測試使用合成 transport fixture，不代表曾連上主辦。類別不會上傳 D-Plan、排程或寄信。

```python
from daily_auto.operations import OperationsStore
store = OperationsStore("daily_auto/runtime/operations")

# 人工匯入永遠是 MANUAL_UNVERIFIED。
record = store.import_record("ledger", "official_export.json")

# 僅有真實官方合約與憑證後，才可執行一次受限 HTTPS GET。
status = store.adapter_status(adapter)  # READY_TO_ATTEMPT_GET 不代表連線成功
record = store.fetch_record(adapter)

check = store.reconcile_ledger(record["id"], state,
    team_id="TEAM_042", as_of="2026-10-23")

receipt = store.verify_receipt(receipt_id, plan_path,
    team_id="TEAM_042", trade_date="2026-10-27",
    calendar_record_id=calendar_id)

# 精確查詢已核验檔案；不重放核驗，不追加事件。
accepted = store.accepted_plan("TEAM_042", "2026-10-27", exact_file_sha256)

status = store.daily_status("TEAM_042", "2026-10-27",
    calendar_record_id=calendar_id, quota_record_id=quota_id)
```

`record` 包含 `id/kind/provenance/acquired_at/raw_sha/data/metadata`。`reconcile_ledger` 會追加本地對帳事件；`record` 與 `accepted_plan` 不新增事件。讀取仍會檢查 raw 檔及事件鏈，發現變造便阻擋。

`accepted_plan` 先检查同隊伍同日所有已核驗回執，再比對最新版本。較晚不同 SHA 會使舊檔失效；最新同時戳卻有不同 SHA 時也阻擋。成功僅代表 `LATEST_LOCALLY_VERIFIED_RECEIPTS_ONLY`，主辦最終採用版本仍是 `official_effective_version=UNKNOWN`，不能以此代替官方最終委託或結算證據。

| 類型 | 必要欄位 |
|---|---|
| ledger | `ledger_id, team_id, as_of, settled_at, settlement_status, cash, nav, dividend_receivable, holdings` |
| holding | `ticker, shares`；可選 `market_value`，僅保留官方實際提供值 |
| receipt | `receipt_id, team_id, trade_date, filename, plan_sha256, status, received_at` |
| quota | `team_id, trade_date, as_of, attempts_used` |
| calendar | `sessions, published_at` |

Ledger 可選 `official_warning_count`，沒有就保持缺項，不能默認零。若經 adapter 映射，必須明確映射該欄；`holdings_fields` 的 `market_value` 同理。股數保留真實零股與分數，對帳不自行四捨五入；金額允許差異至 0.01。對帳成功只代表本地現金、NAV、應收股息與持股吻合已結算的官方帳本。

日期採 `YYYY-MM-DD`，時間必須明示 `+08:00`。金額建議十進位字串。回執的 SHA256 是**完整實際檔案 bytes** 的 64 字元雜湊，不是重新排序 JSON 後的雜湊。

## 信任與限制

Adapter 必須指定 `kind/method/url/contract_status/fields/auth/spec`。`fields` 使用明確 JSON pointer；若官方狀態碼不同，須使用有文件依據的 `value_maps`。`spec` 要保存合約檔案、SHA256、官方來源與 `known_at`；不能把手寫假合約當實際授權。

目前僅支援明確官方 HTTPS GET、TLS 憑證驗證、JSON 回應，禁止重新導向及在 URL 放 token。認證只接受環境變數名稱；不保存 Authorization header 或憑證值。沒有這些條件，呼叫不會發出網路请求。HTTP 200、收到檔案或 `PENDING` 都不等於接受成功。

人工導入的官方匯出檔也標 `MANUAL_UNVERIFIED`：檔案自稱可信、填上 source URL 或附本地雜湊，都不能驗證來源。本版沒有官方數位簽章驗證器，因此不會把離線檔自動升級為正式通過。取得可信紀錄須依審閱過的真實合約經官方 HTTPS 取回；本機檔案擁有者仍可全面竄改程式與所有證據，事件鏈不是對抗此威脅的官方簽章。

Raw 證據、adapter/spec 快照及交易式 SQLite 日誌均保存在指定 store。匯出 SQLite 應使用 backup API 或一致快照，避免複製寫入中的檔案。從真實帳本覆蓋本次 state 後要再對帳；來源未知的現金或持股不能寫成「已對帳」。

## 天數與截止

每天最多 25 次，本地 `reserve_attempt` 只預留次數，回傳 `LOCAL_RESERVED_NOT_SUBMITTED` 及 `submission_permission=False`。它要求 60 秒內的可信伺服器次數快照；沒有快照便 UNKNOWN。Timeout、失敗及不確定請求不釋放預留，避免盲目重送。跨裝置競爭仍需伺服器控制，不能宣稱全域原子配額。

成功天數只計可信 `ACCEPTED` 回執的不同交易日，精確綁定 team/date/filename/hash，拒絕重放。達到 22 天只代表此門檻達成，**不代表已結算、沒有違規或取得資格**。被動超限五日／第六日警告，以及官方三次警告失格，由獨立 `compliance_state` 層處理；不能把模擬超限直接加成官方警告。

官方 PDF 寫前一日 19:30 至當日 08:55，schema 寫 05:00 至 08:55。程式以 05:00 作保守本地預留窗口，但核驗真實官方回執仍接受 PDF 的前一日 19:30 起，沒有宣稱更早生成或 05:00 前提交必然違規。

**10/26 假日提醒：**[TWSE 官方休市表](https://www.twse.com.tw/holidaySchedule/holidaySchedule?response=html) 列 2026-10-26 為臺灣光復暨金門古寧頭大捷紀念日補假。10/27 至 11/27 正好可形成 24 個一般交易日；PDF 的起始日與範例仍需主辦确认。不可把 10/26 直接當作一般交易日，亦未修改凍結 reference。正式 gate 需要可信官方日曆，未確認或日數不符即阻擋。
