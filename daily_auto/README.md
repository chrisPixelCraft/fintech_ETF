# 每天只看這一頁

**目前：準備期。正式提交尚未接通；本機產出成功不等於主辦方收件成功。**

新的固定研究入口使用完整調參 A `full_tuned_v2_f0019`；它只從原始日線、時線和帳本計算訊號，不接受手工排名。舊 `v2_A_best`／`A_dplan_guard` 回放仍保留作比較。兩者都尚未開放正式交易。v3 已退出現行流程，歷史資料保留。本輪凍結並核驗的 417 個交易日行情截止 2026-09-21；不把 9/22 未核驗資料混入已完成研究。

## 今天先做

| 狀態 | 事情 | 完成標準 |
|---|---|---|
| 待本人辦理 | 完成報名 | 全員兩個平台使用同一信箱；10/19 前完成 |
| 選做 | 領模型額度 | 任一隊員完成課程；10/19 前送完成證明 |
| 已完成研究 | 看 A 的複核 | [回測報告](../reports/official_v2_reaudit.md) |
| 已完成研究產單 | 看完整調參 A 的阻擋示範 | [最終核驗 2026-09-21 receipt](runs/full_tuned_demo_verified_final_2026-09-21/receipt.json) |
| 待串接 | 正式每日運行 | ETF 持股、官方帳本、平台回執接通後才可放行 |

報名、課程和登入涉及個人帳號，這次沒有代辦、寄信或申請額度。程式也沒有替你提交任何交易。

## 比賽時每天做

| 時間 | Agent 負責 | 你只需確認 |
|---|---|---|
| 前晚 19:35 | 同步當日結算、行情、ETF 持股 | 資料是否完成 |
| 當日 06:30 | 產生排名與完整 D-Plan | 程式是否成功 |
| 當日 08:30 | 驗證格式、引用、訂單與風險 | 是否全數通過 |
| 當日 08:40 | 上傳同日 JSON；保留回執 | 有主辦方成功回執 |
| 當日 08:45 | 再查隊號、日期、最新版本 | 收到的是正確檔案 |
| 當晚 19:00 後 | 對帳、記錄、封存版本 | 有無差異或警告 |

上表是建議作業時間，**不是已啟用的排程**。真正截止是台北時間 **08:55，依主辦方伺服器收件時間**。即使零筆交易，也必須送完整 D-Plan。

每天不必重讀技術指標。只看四格：`主辦方已收件`、`警告累計`、`資料完整`、`帳本已對上`。任何一格未知，都不能顯示綠燈。

## 紅燈就照做

| 紅燈 | 下一步 |
|---|---|
| 資料缺漏 | 補抓並重跑，不套用舊日期 |
| 帳本不符 | 用官方庫存核對，不靠推算超賣 |
| Active Share 未知 | 補完整 ETF 證據；維持禁止提交 |
| D-Plan 不通過 | 修正程式後由 Agent 重生，不手改買賣 |
| 沒有成功回執 | 查平台結果，再決定是否重送 |
| 快到截止 | 不用錯誤檔案硬送；留存錯誤並處理 |

「全部續抱」也要重新驗證，不能把它當保證合規的備援。禁止自動改成全現金，因為現金必須低於 25%。

## 本機入口

執行位置為專案根目錄。可先查看說明：

```bash
python3 -m daily_auto.cli --help
```

不用真實隊號即可試跑完整的歷史產檔示範：

```bash
python3 -m daily_auto.replay --output daily_auto/runs/my_replay
```

它會自動建立 D-Plan、研究帳本、檢查報告與來源雜湊。[本次示範結果](runs/replay_2025-01-02/README.md) 已實際跑完：schema、股數公式及預估部位通過，但送件狀態維持 `BLOCK_SUBMISSION`，因為這是歷史資料。沒有偽造當年擷取時間或模型呼叫。

用真正的 D-Plan 與前日帳本進行本機驗證：

```bash
python3 -m daily_auto.cli validate \
  --plan daily_auto/inbox/D-Plan_YOUR_TEAM_2026-10-27.json \
  --state daily_auto/inbox/state.json \
  --output daily_auto/runs/2026-10-27/preflight.json
```

上述路徑是使用方式，不是已存在的真實送件檔。帳本格式見 [state.template.json](examples/state.template.json)。模板的空值不應填成猜測數字；缺失資料會被擋下。

從原始日線、時線與固定參數產生新的完整 D-Plan：

```bash
python3 v2_offcial_best_deep_tuning.py plan \
  --state daily_auto/inbox/state.json \
  --daily daily_auto/inbox/daily.csv \
  --hourly daily_auto/inbox/hourly.csv \
  --source-manifest daily_auto/inbox/source_manifest.json \
  --operations-root daily_auto/private/operations \
  --ledger-id RECORD_ID_FROM_OPERATIONS_STORE \
  --calendar-record-id OFFICIAL_CALENDAR_RECORD_ID \
  --previous-packet daily_auto/runs/PREVIOUS_DATE/full_tuned \
  --output daily_auto/runs/DATE/full_tuned
```

完整欄位、跨日買賣階段、證據與輸出說明見 [完整調參 A 產單流程](full_tuned_workflow.md)。沒有 OperationsStore 的可信結算 record 與前一日已接受 D-Plan 時，手寫 `reconciled: true` 或 `buy_phase` 都不會被採信。

舊的 `runs/full_tuned_demo_2026-09-21` 與中間版 `runs/full_tuned_demo_verified_2026-09-21` 都保留供稽核；它們的 code closure 已由 [superseded 說明](runs/full_tuned_demo_2026-09-21.SUPERSEDED.md) 標示，不應作為目前示範入口。

## 需要時再看

| 內容 | 文件 |
|---|---|
| 比賽重點 | [比賽須知](competition_guide.md) |
| 每日排錯 | [每日操作](daily_checklist.md) |
| 完整調參 A 產單 | [固定入口與證據格式](full_tuned_workflow.md) |
| 自動化範圍 | [自動化說明](automation.md) |
| 原文核對 | [官方文件稽核](../docs/official_docs_rule_audit.md) |
| 全部資料來源 | [十份文件清單](reference/inventory.json) |

[原有每日 SOP](玉山_AI_CUP_每日交易_SOP.md) 保留不改，並非目前程式入口。該稿的法人、財報、新聞與隔夜資訊屬可擴充構想；固定 A 尚未使用這些因子。實際自動化範圍與阻擋條件以本頁及自動化說明為準。
