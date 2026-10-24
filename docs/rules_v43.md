# v4.3 規則與操作

2026-10-24 已接入新版文件。正式候選維持凍結的 Mom20；目前不能宣稱可以正式提交，因完整 ETF 前15大資料與 Active Share 官方算法仍缺。

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

Active Share 的「15大／15%」採用本次使用者提供的新規則。儲存庫舊辦法仍寫10大／20%，因此新門檻的來源明記為本次更新，沒有冒充新版正式規章。舊規章的連續兩日失格條款也未被當作新版已確認條款。

## 每日真值

從後台「文件繳交」下載 DataPack。`account.cash`、`account.nav_tradable`、`holdings[].shares` 為正式輸入；本地帳本只供離線研究。`aum`、應收配息與應收配股不可增加可交易額度。

- `null` 代表未齊
	- 不當作空手
- 首日可為空手
	- 依交易日曆辨識
- 官方日期須相符
	- 隊號也須相符
- 價格衝突則阻擋
	- 不混用舊價格

DataPack 有自己的版本，規格書範例為 `3.0`；不能因 D-Plan 是 `4.3` 就拒絕它。

目標股數與委託：

```text
target_shares = floor(target_weight × account.nav_tradable / previous_close / 1000) × 1000
order_shares = target_shares − official_current_shares
```

估算使用前日官方收盤價；成交價是當日均價。費用從現金扣除，成交後的實際比例仍可能變動。

## 執行與狀態

```bash
./run_daily.sh <T> --datapack <DataPack.json> --etf-holdings <etf_top15.csv>

# 僅用歷史快取試跑
./run_daily.sh 2026-09-24 --offline
```

ETF CSV 欄位為 `etf,as_of,ticker,weight`；weight 為基金權重。每檔 required ETF 至少須有完整15檔，還須檢查日期及有效數值。既有 MoneyDJ 抓取器只有前10大，保留作診斷，不能滿足新版覆蓋需求。

| 狀態 | 意義 |
|---|---|
| `SIMULATION_ONLY` | 離線測試；不能上傳 |
| `EMERGENCY_REVIEW_REQUIRED` | 缺資料或未確認；不輸出可提交檔 |
| `READY_TO_SUBMIT` | 正式真值及所有關卡通過 |

目前 AS 的 `min(raw, normalised)` 與 near-tie band 只是內部篩選假設。報告即使顯示 heuristic `PASS`，`official_method_confirmed` 仍為 false，live 不會放行。沒有新增可繞過此關卡的旗標。

AS 會在 planner 整張化與費用估算後重算，validation 轉 fallback 後也再次檢查。失敗重跑會把前次由管線產生的 D-Plan 移到 `superseded/`，避免誤用舊輸出。

## 原始依據

| 來源 | 位置 |
|---|---|
| 新門檻 | 本次使用者訊息：前15大／15% |
| 帳戶真值 | [DataPack規格](../official_docs/DataPack_規格書_v4.3.md) §2.1–2.6 |
| 公式與提交 | [D-Plan指南](../official_docs/D-Plan_撰寫指南_v4.3.md) §⑤–⑦ |
| 結構驗證 | [官方schema](../official_docs/D-Plan.schema_v4.3.json) |
| 操作流程 | [後台說明](../official_docs/2026%20Agent%20基金經理人競賽後台操作說明.pdf) §4–5 |
| 本輪驗證 | [測試報告](../research/results/rules_v43_validation/summary.md) |

策略說明書期間為10/21–10/26；名稱須以「主動」開頭且20字內，主題50字內、理念100–300字。此要求與150檔、30檔ETF名單的新舊PDF抽取文字一致，沒有因檔名 v1-1 就假定名單新增。
