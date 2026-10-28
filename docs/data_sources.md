# 資料來源：誰提供、誰抓、決策用到什麼

每天的資料分三層：你提供 3 樣，Agent 自己抓其餘的，最後只有一部分真正進入選股和下單。

```text
你每天提供                 Agent 自己抓                     決策實際使用
─────────────────         ─────────────────────           ─────────────────────
交易日 T          ──┐     股價（官方＋Yahoo）  ──┐         K2 選股（投信、營收、規模）
DataPack（現金持股）─┼──▶  法人買賣超          ──┼──▶      股數計算（現金、收盤價）
組內名次 --rank   ──┘     月營收              ──┤         T2 切換（名次）
                          主動 ETF 前 10 大    ──┘         Active Share 放行
                          固定參考檔（名單、假日）           → 產出 D-Plan JSON
```

> [!NOTE]
> 程式：`./run_daily.sh <T> --datapack <檔案> --rank <名次>`。下方檔名都是相對於 repo 根目錄。

## 1. 你每天提供的資料

這 3 樣只能由你取得，其餘都由 Agent 處理。

| 資料 | 怎麼給 | 用途 | 沒給會怎樣 |
|---|---|---|---|
| 交易日 T | 指令第 1 個參數 | • 決定用哪天的資料<br>• 要填下一個交易日 | 填錯日期，後台回報「交易日不符」 |
| DataPack | `--datapack`，放在 `production_runs/datapacks/` | • 現金、可交易淨值<br>• 持股股數<br>• 違規紀錄 | live 直接擋下 |
| 組內名次 | `--rank`，看前晚排行榜 | • 判斷是否切換 T2<br>• 要連續 2 天前 6 | 維持原模式，不會出錯 |

- DataPack 在後台「文件繳交」下載
	- 主辦方 18:30 取得市場資料、19:00 前結算
	- **19:00 以後再下載**
- DataPack 不含主動 ETF 持股，Agent 另外抓
- 名次每天都要填
	- 第 13 天要用第 12 天的名次

## 2. Agent 自己抓的資料

DataPack 只有帳戶資料，選股需要的市場資料都由 Agent 每次執行時自動下載。

| 資料 | 來源 | 抓哪一天 | 存在哪裡 |
|---|---|---|---|
| 收盤價、成交量 | • 證交所 MI_INDEX<br>• 櫃買中心 dailyQuotes<br>• Yahoo Finance（歷史） | T−1 | `data/yahoo_daily/`、`production_runs/<T>/official_combined.csv` |
| 法人買賣超 | • 證交所 T86<br>• 櫃買中心三大法人明細 | T−1，缺的日子補齊 | `data/production_aux/flows.csv` |
| 月營收 | 公開資訊觀測站 t21sc03 | 已公布的月份 | `data/production_aux/revenue.csv` |
| 主動 ETF 前 10 大 | MoneyDJ 持股狀況頁 | 每次重抓 30 檔 | `production_runs/<T>/etf_top10.csv` |

固定參考檔（不用每天抓）：

- 150 檔標的名單：`data/reference/universe_competition_20260731.csv`
- 30 檔主動 ETF 名單：`data/reference/tuning_2nd_active_etf_readiness.csv`
	- 以證交所 2026/9/11 揭露為準
- 休市日：`production_runs/preflight_20261026/official_holidays.json`
- 比賽規則：`data/reference/competition_rules.json`

### 什麼時候抓得到

| 資料 | 可取得時間 | 證據 |
|---|---|---|
| 法人買賣超 | 收盤當天傍晚 | **Fact**：10/28 17:27 實測，證交所、櫃買中心都已可下載；官方公布時刻 Evidence not found |
| 月營收 | 法規要求次月 10 日前公布 | 程式保守假設 **11 日**才可用 |
| DataPack | 19:00 結算後 | 主辦方公告「市場資訊結算基準時間調整」（10/28） |
| MoneyDJ 前 10 大 | 不固定，多數每天更新 | **Fact**：10/28 有 7 檔停在 10/22 |

我們在 19:00 之後執行，法人和收盤資料都已公布，時間足夠。

## 3. 決策實際使用的資料

第 2 節抓到的資料，只有下面這些真正影響 JSON 內容；外資買賣超有抓，但 K2 目前沒有使用。

### K2 選股（每月新營收後重篩，其他時間持有）

- 篩選條件，三項都要符合
	- 投信近 10 日買超 > 0
	- 3 個月平均營收創 12 個月新高
	- 最新月營收年增 > 20%
- 排序：投信 10 日買超 ÷ 20 日平均成交量
- 不足 22 檔：用規模排名補
	- 規模 = 近 60 日平均成交金額
- 只用近 20 日資料完整的股票

### 下單股數

- 股數 = floor(目標權重 × DataPack 可交易淨值 ÷ T−1 收盤 ÷ 1000) × 1000
	- 依據：官方 D-Plan 撰寫指南 v4.3
- 現金檢查用 DataPack 現金
	- 不含應收股利
	- 買進價以漲停估算，確保不透支

### 切換 T2 與送出檢查

- 切換：第 13 天起，今天和前一交易日名次都 ≤ 6
	- T2 排序：大型股前 40 名中，投信 20 日買超 ÷ 均量
- Active Share 放行：對 30 檔主動 ETF 前 10 大
	- 最低值 ≥ 30% 才送出
	- 規則門檻是前 15 大、15%
	- 資料超過 7 天視為過期，直接擋下

## 4. 資料出問題時

前三節任何一項缺漏，程式都會停在安全的一邊，而不是用舊資料硬做決策。

| 狀況 | 程式行為 |
|---|---|
| T−1 收盤缺很多檔 | 改走保守路線，沿用前日持股 |
| T−1 法人資料抓不到 | 同上，記錄原因 |
| 該公布的月營收缺 | 同上 |
| MoneyDJ 任一檔過期或抓不到 | live 擋下，不輸出可提交 JSON |
| 休市日 | 主辦方作廢當日 D-Plan，不延到下一日 |

- 網站無法上傳時：08:55 前寄 JSON 到 intelligent-finance@esunbank.com，附錯誤畫面
	- 累計 2 次無正當理由使用信箱，可能取消資格

> [!WARNING]
> MoneyDJ 有 7 檔資料停在 10/22（00402A、00409A、00983A、00986A、00988A、00989A、00997A）。若 10/30 前仍未更新，live 會因資料過期被擋。

## 5. 參考資料

### 主辦方官方文件

- [競賽活動頁](https://esun-ai-challenge.tw/events/fundaiagent)、[活動 API](https://esun-ai-challenge.tw/api/events/fundaiagent/)
- [比賽辦法（10/05 更新）](https://esun-ai-challenge.tw/api/events/fundaiagent/attachments/24/download/)
	- Active Share 改為前 15 大、低於 15% 違規
- [DataPack 規格書 v4.3](https://esun-ai-challenge.tw/api/events/fundaiagent/attachments/29/download/)，本機：[official_docs/DataPack_規格書_v4.3.md](../official_docs/DataPack_規格書_v4.3.md)
- [D-Plan 撰寫指南 v4.3](https://esun-ai-challenge.tw/api/events/fundaiagent/attachments/28/download/)，本機：[official_docs/D-Plan_撰寫指南_v4.3.md](../official_docs/D-Plan_撰寫指南_v4.3.md)
- [D-Plan schema v4.3](https://esun-ai-challenge.tw/api/events/fundaiagent/attachments/27/download/)，本機：[official_docs/D-Plan.schema_v4.3.json](../official_docs/D-Plan.schema_v4.3.json)
- [後台操作說明](https://esun-ai-challenge.tw/api/events/fundaiagent/attachments/30/download/)
- [150 檔標的清單](https://esun-ai-challenge.tw/api/events/fundaiagent/attachments/5/download/)、[主動型 ETF 列表](https://esun-ai-challenge.tw/api/events/fundaiagent/attachments/4/download/)
- 公告（活動頁「公告」區）：結算基準時間調整、臨時休市處理、異常繳交機制、未繳交資格認定
- 本機與官網 v4.3 規格三份檔案 SHA256 逐位元相同（10/28 檢查）

### 資料端點（程式實際呼叫）

- 證交所收盤：`https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=YYYYMMDD&type=ALLBUT0999`
- 櫃買中心收盤：`https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date=YYYY/MM/DD&id=&response=json`
- 證交所法人：`https://www.twse.com.tw/rwd/zh/fund/T86?date=YYYYMMDD&selectType=ALLBUT0999&response=json`
- 櫃買中心法人：`https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&se=EW&t=D&d=民國年/MM/DD&o=json`
- 月營收：`https://mopsov.twse.com.tw/nas/t21/{sii|otc}/t21sc03_民國年_月_{0|1}.html`
- MoneyDJ：`https://www.moneydj.com/ETF/X/Basic/Basic0007.xdjhtm?etfid=<代號>.TW`
- Yahoo Finance：透過 `yfinance` 下載，[0050 歷史頁](https://finance.yahoo.com/quote/0050.TW/history)
- [政府資料開放平台：上櫃三大法人買賣明細](https://data.gov.tw/dataset/11856)

### 策略依據

- [FinLab：投信買超＋營收動能實測](https://finlab.finance/blog/institutional-strategy)
- 營收動能：[Sales momentum（IJFR 2014）](https://ideas.repec.org/a/jfr/ijfr11/v5y2014i1p71-80.html)、[月營收與財報公告（2024）](https://ideas.repec.org/a/wsi/tijaxx/v59y2024i01ns1094406024500021.html)
- 動能崩跌：[Daniel & Moskowitz, Momentum Crashes](https://business.columbia.edu/sites/default/files-efs/pubfiles/4607/mom11.pdf)
- 研究結果：[conc_tournament](../research/results/conc_tournament/CODEX_REPORT.md)、[k3_weights](../research/results/k3_weights/CODEX_REPORT.md)、[benchmark](../research/results/benchmark_20261025/CODEX_REPORT.md)

### 程式位置

- 每日入口：[production/run_daily.py](../production/run_daily.py)
- 股價下載：[production/market_data.py](../production/market_data.py)
- 法人與營收下載、K2/T2 選股：[production/tournament.py](../production/tournament.py)
- 主動 ETF 下載：[production/etf_holdings.py](../production/etf_holdings.py)
- D-Plan 產生：[production/dplan.py](../production/dplan.py)
