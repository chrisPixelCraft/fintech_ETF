# 每日交易入口

目前研究策略入口是 [`v2_double_check_fintuned.py`](../v2_double_check_fintuned.py)，固定使用候選 `d0034`。獲選參數通過本地已量測限制，但 Active Share、官方帳本及平台收件證據尚未齊備；正式 `plan` 一律回傳 `BLOCK_SUBMISSION`，不輸出可提交的 D-Plan。

從專案根目錄核對研究版本：

```bash
python scripts/verify_double_check_release.py
python v2_double_check_fintuned.py --show-config
python v2_double_check_fintuned.py plan
```

最後一行預期以狀態碼 2 阻擋。需要檢查候選產單流程時，可使用 `research-plan`；它只保存標明 `NEVER_SUBMIT` 的研究草稿。本機草稿、回測成功或零筆交易，都不能算作主辦方已收件。

## 每日需要核對的事

| 階段 | 所需證據 |
|---|---|
| 訊號與委託 | 固定版本、前日可得資料、完整持股 |
| 交易限制 | 白名單、股數、持股與現金門檻、Active Share |
| 產單前 | 來源、策略一致性、當日官方帳本 |
| 正式提交 | 官方開放時段、配額、平台成功回執 |
| 收盤後 | 官方成交與結算、警告及差異對帳 |

未解阻斷項及逐條來源見[新版規則表](../docs/v2_double_check_rules.md)；[新版研究結果](../reports/v2_double_check_fintuned_report.md)列出回測與搜尋範圍。賽事原文保存在[official_docs](../official_docs/)。

原 `fintune_v2.py` 的 `f0019` 操作流程保留於[舊版工作流](full_tuned_workflow.md)與[舊版報告](../reports/full_tuned_v2_report.md)，不是目前獲選的研究入口。帳本、委託及私人紀錄仍放在 Git 排除的 `inbox`、`private`、`runs` 目錄，不把真實憑證寫入設定或提交到儲存庫。
