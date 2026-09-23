# 每天只看這一頁

**目前可本機產單，正式提交仍被阻擋。** 唯一策略為 `fintune_v2.py`，固定候選 `f0019`。本機產檔成功不等於主辦方已收件。

## 每天做什麼

| 時間 | 工作 | 確認 |
|---|---|---|
| 前晚 | 更新資料與官方帳本 | 日期、庫存一致 |
| 早上 | 計算訊號、產單 | 固定版本 |
| 08:30 前 | 執行規則檢查 | 無未知證據 |
| 08:55 前 | 正式平台收件 | 成功回執 |
| 收盤後 | 對帳與封存 | 警告、差異 |

這是操作順序，尚未啟用排程或上傳。即使零筆交易也要產完整 D-Plan；不手改個股或股數。缺資料、AS、帳本或回執就處理原因，不能顯示已成功提交。

## 執行入口

```bash
python fintune_v2.py plan --help
```

從專案根目錄執行完整命令：

```bash
python fintune_v2.py plan \
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

這些是路徑示例，不是真實帳戶資料。詳細欄位、首次空倉啟動、續抱及跨日買賣階段，見 [完整工作流](full_tuned_workflow.md)。

## 需要時再看

| 內容 | 文件 |
|---|---|
| 比賽須知 | [competition_guide](competition_guide.md) |
| 每日檢查 | [daily_checklist](daily_checklist.md) |
| 自動化範圍 | [automation](automation.md) |
| 官方證據缺口 | [official_evidence_status](official_evidence_status.md) |
| 官方文件核對 | [規則稽核](../docs/official_docs_rule_audit.md) |
| 績效限制 | [可信度稽核](../reports/full_tuned_v2_credibility_audit.html) |

帳本、委託和私人紀錄放在 Git 排除的 inbox、private、runs 目錄；不要把真實憑證寫進設定或提交到儲存庫。
