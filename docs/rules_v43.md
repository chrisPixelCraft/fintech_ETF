# v4.3 規則與操作

2026-10-25：策略維持凍結；live AS 新增最低30%的 heuristic gate，其他安全關卡不變。

## 現行限制

| 項目 | 條件 |
|---|---|
| 股票池 | 官方150檔 |
| 持股數 | 20–30檔；只計 `shares > 0` |
| 台積電權重 | ≤25% |
| 其他個股權重 | ≤10% |
| 現金比 | `0 ≤ cash / nav_tradable < 25%` |
| 權重分母 | `nav_tradable`，不含應收權益 |
| 下單單位 | 1,000股正整數倍 |
| 交易種類 | 現股；禁融資券、借券、放空、當沖 |
| 買賣手續費 | 各0.1425% |
| 賣出證交稅 | 另收0.3% |
| Active Share | 前15大對各 ETF 前15大；不得低於15% |
| D-Plan | v4.3；上傳後須送出繳交 |

前 15 大／15% 來自本次使用者更新，並非已確認的新版正式規章。舊辦法為10大／20%；其連續兩日失格條款也未確認適用新版。

## 每日真值

後台「文件繳交」下載 DataPack：正式輸入為 `account.cash`、`account.nav_tradable`、`holdings[].shares`。本地帳本僅供離線研究；`aum`、應收配息／配股不增加交易額度。

- `null` 代表未齊
	- 不當作空手
- 首日可為空手
	- 依交易日曆辨識
- 官方日期須相符
	- 隊號也須相符
- 價格衝突則阻擋
	- 不混用舊價格

DataPack 獨立版本；範例 `3.0` 不因 D-Plan 為 `4.3` 而被拒絕。

目標股數與委託：

```text
target_shares = floor(target_weight × account.nav_tradable / previous_close / 1000) × 1000
order_shares = target_shares − official_current_shares
```

前日官方收盤價估算、當日均價成交；費用扣現金，成交後比例可能改變。

## 執行與狀態

```bash
./run_daily.sh <T> --datapack <DataPack.json>

# 僅用歷史快取試跑
./run_daily.sh 2026-09-24 --offline
```

ETF CSV：`etf,as_of,ticker,weight`，weight 為基金權重。可提供完整前15大；未提供時 live 自動抓取 MoneyDJ 前10大。全部 required ETF（目前30檔）須齊全、數值有效、未逾7日，每檔至少10筆；任一抓取失敗即阻擋。

| 狀態 | 意義 |
|---|---|
| `SIMULATION_ONLY` | 離線測試；不能上傳 |
| `EMERGENCY_REVIEW_REQUIRED` | 缺資料或未確認；不輸出可提交檔 |
| `READY_TO_SUBMIT` | 正式真值及所有關卡通過 |

AS 的 `min(raw, normalised)` 與 near-tie band 僅為篩選假設。最終持倉 `minimum` 有限且 ≥30%，並符合上述資料條件時，live gate 記 `HEURISTIC_TOP10_PASS`，保留 `ACTIVE_SHARE_METHOD_UNCONFIRMED` 作資訊原因；否則記 `BLOCKED` 與失敗條件。設定見 `production/settings.json:live_active_share`。官方方法確認後，既有通過路徑記 `OFFICIAL_PASS`。

planner 整張化／估費後、validation 轉 fallback 後皆重驗 AS。失敗重跑將前次管線 D-Plan 移至 `superseded/`，避免誤用。

## 原始依據

- 新門檻：使用者更新：前15大／15%
- 帳戶真值：[DataPack規格](../official_docs/DataPack_規格書_v4.3.md) §2.1–2.6
- 公式與提交：[D-Plan指南](../official_docs/D-Plan_撰寫指南_v4.3.md) §⑤–⑦
- 結構驗證：[官方schema](../official_docs/D-Plan.schema_v4.3.json)
- 操作流程：[後台說明](../official_docs/2026%20Agent%20基金經理人競賽後台操作說明.pdf) §4–5
- 本輪驗證：[測試報告](../research/results/rules_v43_validation/summary.md)

策略說明書：10/21–10/26；名稱以「主動」開頭、20字內，主題50字內、理念100–300字。要求及150檔／30檔ETF名單的新舊PDF文字一致；v1-1 檔名不代表新增名單。
