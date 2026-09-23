# 每日交易入口

目前研究策略入口是 [`v2_double_check_refinement.py`](../v2_double_check_refinement.py)。第三輪細搜未找到更好的合格設定，因此保留候選 `x0352`。它在兩個回測股票池通過本地已量測限制，但 Active Share、官方帳本及平台收件證據尚未齊備；正式 `plan` 一律回傳 `BLOCK_SUBMISSION`，不輸出可提交的 D-Plan。

從專案根目錄核對研究版本：

```bash
python scripts/verify_double_check_refinement.py
python v2_double_check_refinement.py --show-config
python v2_double_check_refinement.py plan
```

最後一行預期以狀態碼 2 阻擋；細搜版本的 `research-plan` 也維持阻擋。需要研究重播時使用 `--track` 與新的 `--output` 目錄。本機回測成功或零筆交易，都不能算作主辦方已收件。

## 每日需要核對的事

| 階段 | 所需證據 |
|---|---|
| 訊號與委託 | 固定版本、前日可得資料、完整持股 |
| 交易限制 | 白名單、股數、持股與現金門檻、Active Share |
| 產單前 | 來源、策略一致性、當日官方帳本 |
| 正式提交 | 官方開放時段、配額、平台成功回執 |
| 收盤後 | 官方成交與結算、警告及差異對帳 |

未解阻斷項及逐條來源見[規則表](../docs/v2_double_check_rules.md)；[細搜結果](../reports/v2_double_check_refinement_report.md)與[細搜規格](../docs/v2_double_check_refinement_protocol.md)列出回測、年度比較及搜尋範圍。賽事原文保存在[official_docs](../official_docs/)。

前輪 `x0352` 擴搜見[第二輪報告](../reports/v2_double_check_expansion_report.md)，`d0034` 見[第一輪報告](../reports/v2_double_check_fintuned_report.md)；原 `fintune_v2.py` 的 `f0019` 操作流程保留於[舊版工作流](full_tuned_workflow.md)。這些入口不會被細搜結果覆寫。帳本、委託及私人紀錄仍放在 Git 排除的 `inbox`、`private`、`runs` 目錄，不把真實憑證寫入設定或提交到儲存庫。
