# v2_double_check_fintuned：規則與阻斷項

**正式送件仍為 `BLOCK_SUBMISSION`：僅能認證指定歷史輸入的已量測限制。** Active Share、主辦方結算契約、來源事實、策略一致性仍缺證據；零回測警告不代表未知項通過。

本文保留 v2 規則與阻斷項。舊實作／測試連結固定於 `d613b75`，不代證 v3；以 [v3 規格](v3_spec.md)及執行稽核核對。

2026-09-23 核對十份原始檔 SHA256，均符 [inventory](https://github.com/chrisPixelCraft/fintech_ETF/blob/d613b75/daily_auto/reference/inventory.json)。重抽辦法 p7–10，核對指南、schema、分頁抽取及程式。下列為覆蓋與測試位置，結果以本版驗證報告為準；保留舊報告及口徑差異。

## 來源與優先順序

[競賽辦法](<../official_docs/AI CUP 2026玉山人工智慧公開挑戰賽_比賽辦法.pdf>) p9 §六(十六)將最終解釋權留給主辦方，最新平台公告優先。本次未取得可驗證的最新平台公告，本地文件不能代證。

| 原始來源 | 核對範圍 | 規則用途 |
|---|---|---|
| 競賽辦法 | p1–12、15–16 | 交易及行政規範 |
| [D-Plan 指南](../official_docs/D-Plan_撰寫指南.md) | ⓪–⑦、送件檢查 | 每日決策格式 |
| [schema v4.0](../official_docs/D-Plan.schema.json) | 根物件、`$defs` | 結構及語意約束 |
| [正例](../official_docs/D-Plan_TEAM_042_2026-10-27.json) | 全檔 | 格式示例 |
| [反例](../official_docs/D-Plan_TEAM_043_2026-10-27.json) | 全檔 | 錯誤示例 |
| [策略範本](<../official_docs/玉山挑戰賽_ETF投資策略說明文件_參賽隊伍繳交格式_v1.docx>) | 欄位表 | 字數限制 |
| [策略範例](<../official_docs/玉山挑戰賽_ETF投資策略說明文件_範例文件_v1.pdf>) | p1–2 | 命名、策略一致性 |
| [ETF 清單](<../official_docs/玉山挑戰賽_主動型ETF列表_v1.pdf>) | p1、30 檔 | Active Share 集合 |
| [股票清單](<../official_docs/玉山挑戰賽_投資組合及交易標的_150檔清單_v1.pdf>) | p1–3、150 檔 | 股票白名單 |
| [Token 說明](<../official_docs/玉山挑戰賽_Google Token Credit領取方式_v1.pdf>) | p1–2 | 選用資源流程 |

範例不放寬辦法、指南、schema。正例兩檔不代表可少於 20 檔；反例稱 C2/C12 不符為警告，schema 要求拒收，本地採拒絕。

## 交易與帳務覆蓋

`LOCAL`＝本地檢查／模擬，非官方認證；`UNKNOWN`＝關鍵契約／輸入不足，須擋正式送件。

| 規則／門檻 | 官方位置 | 實作與測試 | 狀態 |
|---|---|---|---|
| 本金 TWD 10 億 | 辦法 p1 | `double_check_tuning.FIXED` | LOCAL |
| 前日 NAV／收盤定股數 | 指南⑥、schema `orders` | `derive_orders`；公式邊界測試 | LOCAL |
| 整張 1,000 股 | 辦法 p8 六(十)、schema `order` | `derive_orders`；非整張拒收測試 | LOCAL |
| 禁止超賣、同股當沖 | 指南⑥ C13 | `validate_plan`；action／order 測試 | LOCAL |
| 禁融資融券借券放空 | 辦法 p8 六(十) | 現股帳本；現金非負檢查 | LOCAL |
| 當日成交均價 | 辦法 p8 六(五)、指南⑥ | `double_check_ledger.run_v2` | LOCAL |
| 缺價不得假成交 | 指南⑥ | `UNFILLED` 記錄；候選剔除 | LOCAL |
| 手續費雙邊 0.1425% | 辦法 p8 六(十一)、指南⑥ | `project_orders`、新帳本 | LOCAL |
| 賣出稅 0.3% | 同上 | 費稅重算測試 | LOCAL |
| 費用取整／最低費 | 文件未定義 | 不補造平台規則 | UNKNOWN |
| 每日 20–30 檔 | 辦法 p8 六(八) | `rule_check`、`validate_plan` | LOCAL |
| 單股 ≤10% | 同上 | cap／現金邊界測試 | LOCAL |
| 2330 ≤25% | 同上 | TSMC cap 測試 | LOCAL |
| 現金 ≥0 且 <25% | 辦法 p8 六(九)、p9 六(十四) | 嚴格現金邊界測試 | LOCAL |
| 官方 150 檔 | 辦法 p8 六(七)、股票清單 | exact whitelist 測試 | LOCAL |
| 現行代號 `3718` | 股票清單 p3 | 官方 ticker 映射 | LOCAL |
| 日 NAV＝市值＋現金 | 辦法 p6 五 | 帳本獨立重算 | LOCAL |
| 股利於期末加入 NAV | 辦法 p10 註1 | terminal credit 測試 | LOCAL |
| 公司行動按市場調整 | 辦法 p8 六(十二) | 股數、股利記錄 | UNKNOWN |
| 加碼超限即警告 | 辦法 p10 註3 | `cap_state`；day-one 測試 | LOCAL |
| 被動超限第六日警告 | 同上 | grace／交易日測試 | LOCAL |
| 每日多違規僅一警告 | 辦法 p9 六(十四) | `warning_reasons`；multi-breach 測試 | LOCAL |
| 整日交易無效回退 | 同上 | rollback／費稅還原測試 | LOCAL |
| 累積三警告失格 | 同上 | 第三次停止測試 | LOCAL |
| 漏交或決策不一致警告 | p9 六(十四)(1)(6) | 歷史成交無法證明 | UNKNOWN |
| AS 每檔均 ≥20% | p8 六(十三)、p10 註2 | `_active_share`；30 ETF 測試 | UNKNOWN |
| AS 連兩日可失格 | 同上 | 歷史比較缺值即阻擋 | UNKNOWN |

實作入口：[新帳本](../src/double_check_ledger.py)、[新選參器](../src/double_check_tuning.py)、[語意驗證器](https://github.com/chrisPixelCraft/fintech_ETF/blob/d613b75/daily_auto/validate.py)、[超限歷史](https://github.com/chrisPixelCraft/fintech_ETF/blob/d613b75/daily_auto/compliance_state.py)。對應測試：[新帳本反例](../tests/test_double_check.py)、[每日語意](https://github.com/chrisPixelCraft/fintech_ETF/blob/d613b75/tests/test_daily_auto.py)、[超限交易日](https://github.com/chrisPixelCraft/fintech_ETF/blob/d613b75/tests/test_daily_compliance_state.py)。測試非完整性證明，須核對逐日輸出。

**推論與限制：** ±10% 僅為研究成交邊界，官方未保證股票、公司行動、缺價皆適用。事前可行不保證事後可行；超界／未成交另記並剔除選參，禁用當日價格改前日委託。

**帳務假設：** 回退當日成交／費稅，保留公司行動權利，按當日收盤計價。`terminal_dividend_credit` 僅於期末入 NAV，不可再投資。原文未定現金比例、警告順序及回退／公司行動交互語意；解讀未經主辦方確認。

**零超限選參：** 帳本／反例保留官方五交易日被動寬限；候選另須 `raw_rule_breach_days=0`，不靠寬限。這是更嚴的本地門檻，非官方罰則。

**工程修復：** 首搜因超限歸因錯誤暫停並隔離產物。賣別股的費稅會縮 NAV、推高原持股權重，未買該股也可能交易誘發超限。修正後以同日收盤不交易組合作反事實，不給交易誘發超限被動寬限。官方邊界解讀未明，選參以零超限避開。

## D-Plan 與每日營運覆蓋

| 規則 | 官方位置 | 程式／測試 | 狀態 |
|---|---|---|---|
| v4.0 結構與長度 | schema 全部定義 | `Draft202012Validator` | LOCAL |
| ID 唯一、從 1 連號 | 指南 ID 規則 | `_references`／sequential-ID 測試 | LOCAL |
| 跨層引用完整 | 指南整體結構、schema C1 | reference-chain 測試 | LOCAL |
| 昨日持股完整涵蓋 | 指南⑤ | coverage／duplicate 測試 | LOCAL |
| BUY／ADD／TRIM／SELL_ALL | 指南⑤ | action-consistency 測試 | LOCAL |
| 委託與公式完全相等 | 指南⑥、schema C2 | Decimal 邊界測試 | LOCAL |
| posture 淨流向一致 | 指南③、schema C12 | posture-flow 測試 | LOCAL |
| hold 容差 2% NAV | 同上 | 容差邊界測試 | LOCAL |
| 宣告現金區間一致 | 同上 | 費用後現金測試 | LOCAL |
| funding 引用及真缺口 | 指南⑤、schema C14 | funding-gap 測試 | LOCAL |
| 來源機構、時間、事實 | 指南①② | 時間及 hash 檢查 | UNKNOWN |
| 推論合理、主題一致 | 指南④、策略範例 p2 | 完整外部佐證未具備 | UNKNOWN |
| 檔名及 team/date 相符 | 指南整體結構 | `validate_plan`、receipt 測試 | LOCAL |
| 台北時間與完成時點 | 指南⑦、schema C6 | time-order／server receipt 測試 | LOCAL |
| 參數、提示詞皆屬版本 | 指南⑦ | 程式及設定 SHA256 | LOCAL |
| 每日最多 25 次 | 辦法 p2 二(八) | quota／timeout 測試 | LOCAL |
| ≥22 個成功交易日 | 辦法 p4 四(三) | distinct-date receipt 測試 | LOCAL |
| server 收件與身分 | 指南⑦、schema `team_id` | `verify_receipt` | UNKNOWN |
| 當日真實結算持股 | 指南⑥ | `reconcile_ledger` | UNKNOWN |
| 每日19:00淨值更新 | 辦法 p8 六(六) | 仍須實際結算佐證 | UNKNOWN |

營運與證據測試見 [operations tests](https://github.com/chrisPixelCraft/fintech_ETF/blob/d613b75/tests/test_daily_auto_operations.py)。預留次數≠提交；HTTP 成功≠平台接受；accepted D-Plan≠結算。逐環節核對 exact bytes、日期、隊伍、官方來源與收件時間。

辦法 p7：交易日「前一日」19:30 起（週日 10/25 →週一 10/26），非前一交易日。Schema：當日 05:00–08:55。本地採共同區間 05:00–08:55；可提早產製，不宣稱可提早提交，舊摘要不作依據。

指南⑥要求自行記帳及查系統持股；須官方結算對帳，不假設全成交。`funding_for` 依 schema C14 移除全部已宣告調度賣出後重算，非每筆獨立缺口。來源存檔為強烈建議，非官方必填。

## 其餘規定不能由回測代證

| 規定範圍 | 官方位置 | 需要的證據 |
|---|---|---|
| 自主決策、禁止人工干預 | p7 六(一)(三) | Agent 真實執行紀錄 |
| 禁共享抄襲作弊及攻擊 | p7 六(二)、p9 六(十五) | 程式來源及存取範圍 |
| 報名、組別、1–4 人 | p2 二、p4 三、p15 FAQ | 帳號與隊伍資料 |
| 單一隊伍、資格、指導老師 | p4 三、p5 四 | 報名身分證明 |
| 領獎、出席、名單與補件 | p3–5 二／四 | 官方行政紀錄 |
| 說明文件及期限 | p2 二(七)、指南⓪ | 正式文件與收件證明 |
| 複賽簡報、程式碼繳交 | p3 二、p11–12 八 | 版本及收件證明 |
| 肖像個資及行為規範 | p9–10 六(十八) | 當事人與活動紀錄 |
| 獎項、計分及模擬用途 | p3 二、p6–7 五、p11 七 | 主辦方判定 |
| 選用 Token／Slack | p1 註、Token p1–2、p16 | 不作策略資格門檻 |

正式策略名稱採「主動」開頭，20 字內；主題 50 字內；理念 100–300 字。2026-10-21 至 10-26 提交。初賽 10-26 至 11-27；晉級程式與文件 12-02 15:00 前；出席名單 12-09 前；簡報 12-16 23:59 前；複賽 12-19，報告 10 分鐘及問答 5 分鐘。參賽、領獎與行政資格在現有回測輸入中均為 `UNKNOWN`，程式測試不能代證。

Token 文件說序號約三個工作日後於競賽頁面提供，並非 email 寄送；任一隊員可在 09-20 至 10-19 以註冊信箱寄課程證明，11-30 前啟用，12-31 後失效。這修正舊摘要文字，不影響策略交易限制。

## 正式啟用仍缺什麼

Active Share 缺 30 ETF 逐日 top10 權重、可用時間、官方算法（正規化、聯集、並列、連續兩日）。另缺零股／分割 pending order 契約、費用取整、警告回退順序，以及最新公告、官方帳本、正式 calendar、提交配額、來源及策略一致性證據。

保存研究候選，阻擋正式提交；阻擋僅防未驗證政策送出，賽日漏交仍會警告，不交易不保證合規。開賽前須補齊證據，以官方驗證器／平台契約逐項對帳。

[本版每日入口](https://github.com/chrisPixelCraft/fintech_ETF/blob/d613b75/daily_auto/double_check.py)的 `plan` 研究稽核通過仍回傳 `BLOCK_SUBMISSION`，不輸出 D-Plan。`research-plan` 存 `RESEARCH-DRAFT_…json`：候選包於 `candidate_plan`，頂層 `RESEARCH_DRAFT_NEVER_SUBMIT`，不可官方收件。[每日入口測試](https://github.com/chrisPixelCraft/fintech_ETF/blob/d613b75/tests/test_double_check_daily.py)覆蓋強制阻擋、研究草稿包裝、參數及原始碼遭修改等情境。
