# v2_double_check_fintuned：規則與阻斷項

本文保留 v2 規則與未解阻斷項。舊版實作與測試連結指向整理前版本 `d613b75`；它們不代表目前 v3 已完成對應功能。v3 的實際覆蓋與研究假設以 [v3 規格](v3_spec.md)及執行後的稽核報告核對。

本版只能認證「指定歷史資料上的已量測限制」，不能認證所有競賽規則。正式送件維持 `BLOCK_SUBMISSION`。Active Share、主辦方結算契約、來源事實與策略一致性尚缺完整證據；零回測警告不能把這些未知項轉成通過。

2026-09-23 核對十份原始檔的 SHA256，均與 [inventory](https://github.com/chrisPixelCraft/fintech_ETF/blob/d613b75/daily_auto/reference/inventory.json) 相符。本輪重新抽取競賽辦法 p7–10，並核對原始指南、schema、既有分頁抽取及程式。本文列的是規則覆蓋與測試位置；測試執行結果及實際回測數字以本版驗證報告為準。舊檔保留，不以改寫舊報告掩蓋口徑差異。

## 來源與優先順序

[競賽辦法](<../official_docs/AI CUP 2026玉山人工智慧公開挑戰賽_比賽辦法.pdf>) p9 §六(十六)將最終解釋權留給主辦方，最新平台公告優先。現有本地文件不是最新公告已確認的證據；本次環境未取得可驗證的最新平台內容。

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

範例不能放寬辦法、指南或 schema。正例只列兩檔，不代表正式持股可以少於 20 檔。反例把 C2/C12 不符稱為警告，schema 則寫拒收；本地檢查採拒絕。

## 交易與帳務覆蓋

下表的 `LOCAL` 表示本地有檢查或模擬，不代表官方認證。`UNKNOWN` 表示關鍵契約或輸入不足，正式送件必須阻擋。

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

實作入口：[新帳本](../src/double_check_ledger.py)、[新選參器](../src/double_check_tuning.py)、[語意驗證器](https://github.com/chrisPixelCraft/fintech_ETF/blob/d613b75/daily_auto/validate.py)、[超限歷史](https://github.com/chrisPixelCraft/fintech_ETF/blob/d613b75/daily_auto/compliance_state.py)。對應測試：[新帳本反例](../tests/test_double_check.py)、[每日語意](https://github.com/chrisPixelCraft/fintech_ETF/blob/d613b75/tests/test_daily_auto.py)、[超限交易日](https://github.com/chrisPixelCraft/fintech_ETF/blob/d613b75/tests/test_daily_compliance_state.py)。各條測試並非完整性證明，須連同逐日實際輸出驗證。

**推論與限制：** ±10% 成交價格情境是規劃器的研究安全邊界，官方文件沒有承諾所有股票、公司行動或缺價情境均落在此區間。事前可行並不保證事後可行；超界與未成交另行記錄，選參時剔除，不能利用當日價格改寫前日委託。

**帳務假設：** 新帳本回退當日成交與費稅，但保留公司行動權利，再用當日收盤計價。期末股利另列 `terminal_dividend_credit`，不變成可再投資現金。原文支持期末加入 NAV，但未明訂其現金比例、警告判定順序及回退與公司行動交互語意；這些解讀不能聲稱已獲主辦方確認。

**零超限選參：** 官方五交易日被動超限寬限保留於帳本模擬及反例測試；本版可入選候選另外要求 `raw_rule_breach_days=0`，不能依賴任何超限寬限。這是比官方更嚴格的本地門檻，不是新增官方罰則。

**工程修復：** 首次搜尋因超限歸因錯誤暫停，相關產物隔離。賣出別股的費稅可縮小 NAV 並推高既有股票權重，故「未買進該股」不能直接判為價格被動超限。修正規格以同日收盤的不交易組合作反事實比較；交易誘發的超限不給價格被動寬限。該邊界情境的官方細部解讀仍未確認，最終選參以零超限日避開此不確定性。

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

營運與證據測試見 [operations tests](https://github.com/chrisPixelCraft/fintech_ETF/blob/d613b75/tests/test_daily_auto_operations.py)。本地預留次數不是提交；HTTP 成功不是平台接受；accepted D-Plan 不是已完成結算。每個環節須核對 exact bytes、日期、隊伍、官方來源與收件時間。

辦法 p7 的送件起點是交易日的「前一日」19:30，例子是週日 10/25 至週一 10/26；不能改寫成「前一交易日」。Schema 描述則為當日 05:00–08:55。本地採共同區間 05:00–08:55，較早產製合法但不在較早時段宣稱可提交。舊摘要的「前一交易日」文字不作本版依據。

指南⑥同時寫自行記帳與查詢系統持股；採官方結算對帳，不能自行假設全數成交。`funding_for` 按 schema C14 移除「全部已宣告調度賣出」後重算，不把每筆都須獨立造成缺口誤寫成規定。來源存檔在文件中是強烈建議，不能誤寫為官方必填。

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

正式策略名稱採「主動」開頭，20 字內；主題 50 字內；理念 100–300 字。2026-10-21 至 10-26 提交。初賽 10-26 至 11-27；晉級程式與文件 12-02 15:00 前；出席名單 12-09 前；簡報 12-16 23:59 前；複賽 12-19，報告 10 分鐘及問答 5 分鐘。參賽、領獎與行政資格在現有回測輸入中均為 `UNKNOWN`，不能因程式測試通過就省略。

Token 文件說序號約三個工作日後於競賽頁面提供，並非 email 寄送；任一隊員可在 09-20 至 10-19 以註冊信箱寄課程證明，11-30 前啟用，12-31 後失效。這修正舊摘要文字，不影響策略交易限制。

## 正式啟用仍缺什麼

Active Share 尚缺 30 檔 ETF 的逐日 top10 權重、可用時間與官方算法確認，包括正規化、聯集、並列名次及連續兩日判定。公司行動尚缺零股／分割 pending order 的正式處理，費用取整與警告回退順序也未確認。另須取得最新公告、官方帳本、正式 calendar、提交配額、來源與策略一致性證據。

目前安全行為是保存研究候選並阻擋正式提交。阻擋只能避免送出未驗證政策；若到了比賽日仍未排除阻斷，漏交本身也會警告，不能稱為「不交易即可合規」。真正可用版本必須在競賽開始前補齊證據並以官方驗證器／平台契約逐項對帳。

[本版每日入口](https://github.com/chrisPixelCraft/fintech_ETF/blob/d613b75/daily_auto/double_check.py)的 `plan` 即使研究稽核通過，仍回傳 `BLOCK_SUBMISSION`，不輸出 D-Plan。`research-plan` 僅保存 `RESEARCH-DRAFT_…json`，將候選放進 `candidate_plan` 包裝，頂層標記 `RESEARCH_DRAFT_NEVER_SUBMIT`；這不是官方可接收的 D-Plan。[每日入口測試](https://github.com/chrisPixelCraft/fintech_ETF/blob/d613b75/tests/test_double_check_daily.py)覆蓋強制阻擋、研究草稿包裝、參數及原始碼遭修改等情境。
