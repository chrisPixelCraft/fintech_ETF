# 台股回測資料稽核

資料涵蓋 **2025-01-02 至 2026-09-21**，其中 2025 年僅供指標暖機。2026 年共有 **174 個實際交易日**。資料已完成結構、日期、單位與重要公司行動檢查，但仍有來源限制；狀態為 `AUDITED_WITH_LIMITATIONS`，不代表逐筆成交或公司行動已全部獨立驗證。

## 可用檔案

| 檔案 | 內容 | 筆數 |
|---|---|---:|
| `data/processed/universe_20251231.csv` | 固定股票池 | 150 |
| `data/processed/daily_canonical.csv` | 日線及公司行動 | 63,229 |
| `data/processed/hourly_canonical.csv` | 原始小時線 | 312,039 |
| `data/processed/canonical_audit.json` | 限制與 SHA256 | — |
| `data/raw/retrieval_manifest.jsonl` | 來源與雜湊 | — |
| `data/processed/source_validation.json` | 回應日期稽核 | — |

日線欄位為 `date,symbol,open,high,low,close,volume,dividend,split,split_restoration_factor,turnover,price_source,source_symbol`。價格單位為新臺幣／股，`volume` 為股數，`dividend` 為**除權息前每一舊股**的現金股利，`split` 為新股／舊股比例。合併除權息時應先按舊股數計算現金權益，再調整股數：`cash += shares_before × dividend`，`shares_after = shares_before × split`。單日總報酬因子為 `(close × split + dividend) / previous_close`，不能把現金股利再乘一次 `split`。`turnover` 有值時為官方成交金額；空白不能當成零，也不能據此製造 VWAP。

小時線增加 `timestamp`，時區為 `Asia/Taipei`。台股交易日不是連續 24 小時；4H 應由完整 09:00、10:00、11:00、12:00 四根小時線組成，13:00 的尾盤半小時不可冒充完整 4H。沒有來源 bar 時不填補。

## 股票池如何避免事後選股

競賽股票池依 2026 年 7 月底市值制定，不能直接作為 2026 年 1 月已知的名單。因此本次以 **2025-12-31** 的上市前 100 大及上櫃前 50 大重建固定股票池，與競賽正式白名單分開保存。

上市股票以 TWSE 當日 `MI_QFIIS` 發行股數乘以 `MI_INDEX` 當日收盤價排序；上櫃股票使用 TPEx 當日市值排名。股票池不依 2026 年報酬、是否仍上市或資料是否完整進行重新挑選。

`known_at_assumption=2025-12-31T19:30:00+08:00` 是收盤資料可供下一交易日決策的操作假設。這些資料在 2026 年重新擷取，**不是當時封存的快照**，因此仍可能存在事後修訂。不得把它描述成完整歷史版本資料庫。

官方來源：

- [TWSE 歷史發行股數](https://www.twse.com.tw/fund/MI_QFIIS?response=json&date=20251231&selectType=ALLBUT0999)
- [TWSE 歷史收盤行情](https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date=20251231&type=ALLBUT0999)
- [TPEx 歷史市值排名](https://www.tpex.org.tw/web/stock/aftertrading/daily_mktval/mkt_result.php?l=zh-tw&o=json&d=114/12/31&s=0,asc,0)

## 股價與公司行動

Yahoo chart API 提供日線、小時線及事件。程式不用 `Adj Close`。對有回傳拆分事件的股票，將拆分調整後 OHLC 還原到各歷史日期的每股單位，並另存 `split`，供投組引擎在事件生效日調整股數。已取得的 2026 年官方 OHLCV 優先於 Yahoo；其餘保留 Yahoo，並由 `price_source` 區分。

所有 2026 年 TPEx 現金及股票股利均以官方計算表校正。TWSE 純現金除息亦以官方表校正，但**表上排定日期不一定是實際除息交易日**，必須先套用實際交易日曆。TWSE 的 16 筆除權或權息合併事件，事件日期與類型已核對官方表，但現金／股票分解仍主要依 Yahoo，逐筆列於 `canonical_audit.json` 的 `unverified_twse_combined_actions`。不得宣稱全部公司行動完全驗證。

現金增資認購不等於免費配股。本資料沒有把理論除權跌幅轉成免費股票，也未假設出售或行使認購權。相關事件保存在 `official_corporate_actions.csv`，回測應揭露不參與認購的設定。股利入帳日未全面取得，若引擎在除息日立即增加可動用現金，屬額外近似。

官方來源：

- [TPEx 除權息計算表](https://www.tpex.org.tw/www/zh-tw/bulletin/exDailyQ?startDate=2026%2F01%2F01&endDate=2026%2F09%2F21&response=json)
- [TWSE 除權息計算表](https://www.twse.com.tw/rwd/zh/exRight/TWT49U?response=json&startDate=20260101&endDate=20260921)

## 中光電代號變更

原 `5371.TWO` 的 Yahoo 長期歷史資料缺失，因此補入官方 2025 年月報及 2026 年日報。2026-08-21 為最後交易日；2026-08-24 至 09-02 不製造交易 bar。2026-09-03 起將 `3718.TWO` 視為同一投資實體的 1:1 股份轉換，`symbol` 保持 `5371.TWO`，`source_symbol` 保存實際代號。

轉換依據是[臺灣期貨交易所 2026-08-20 公文](https://www.taifex.com.tw/file/taifex/CHINESE/11/attach/5371_20260903.pdf)，第 1 頁第二點明列 1:1 比例及 09-03 生效日期。此處是追蹤原持股權利，並非提前將新股加入選股池。

Yahoo 不提供 `5371.TWO` 在 2026-07-17 前的小時資料，分段查詢亦明確回覆不存在。未使用日線偽造小時線。4H 指標暖機不足時必須依預先設定的缺值政策處理；不可默默把它從 150 檔基準刪除。

## 完成的檢查與剩餘限制

日線與小時線主鍵沒有重複。OHLC 皆為有限正數，最高／最低價關係有效，成交量非負，股份比例為正。股份轉換日與停牌區間已檢查。所有擷取回應保留來源 URL、擷取時間及 SHA256。

獨立驗證找出 **2026-07-10 因颱風休市**，Yahoo 卻提供全市場零成交量的平盤日線。已依[臺灣期貨交易所 7 月 9 日公告](https://www.taifex.com.tw/enl/eng11/newsDetail?idx=10470&newsType=1)與證券商公會公告確認休市，刪除該日 150 筆假交易日線；該日本來沒有小時線。其餘 65 筆個股零成交量紀錄完整保留，沒有把個股停牌誤當全市場休市。

這次驗證也抓到一項資料整合錯誤：官方除息計算表仍保留原排定的 7 月 10 日，但 Yahoo 已將華航、中信金、聯詠股利放在 7 月 13 日。直接按官方表日期補入股利會重複計算。[TWSE 天災問答](https://www.twse.com.tw/zh/about/suspended_faq.html)說明，原定休市日除權息的價格基準適用於下一個實際交易日。因此三筆股利都只保留 **7 月 13 日的一次權益**；`official_corporate_actions.csv` 同時記錄 `scheduled_date` 與 `effective_date`。修正前的初步回測不得使用。

回歸稽核逐筆比對修正前後資料，確認除了刪除休市日與三筆股利的生效日處理，其餘 canonical 資料不變。2025 暖機及 2026 回測皆通過全市場零成交量檢查。未經官方證實的全市場零量日期會阻止執行，不能自動當作休市刪除。檢查結果保存在 `market_calendar_audit.json`。

2026 年可比的 25,981 筆 09:00 小時開盤價與日線開盤價中，18 筆差異超過 0.5%。大部分資料支持拆分還原單位一致，但零星盤初／集合競價或認購權調整差異仍存在。`1815`、`6023` 的 2025 年長段系統差異已改用官方月報修正。

`0050` 的 2025 年拆分未出現在 Yahoo 事件表；其早期日線與小時線單位不同。本次僅使用 2026 年 `0050` 作基準，不能將它的 2025 年資料當成已完整校正的回測序列。

官方日報僅部分完成：TPEx 覆蓋 2026 年全段，TWSE 因來源限流只覆蓋部分日期。舊 TPEx 路徑會忽略歷史日期；日期斷言攔截了這些回應，並標為 `REJECTED`，它們沒有進入 canonical 檔。**官方 VWAP 全期回測尚不成立**；主回測可以採隔日開盤成交，但必須揭露與競賽撮合方式的差別。

## 重現

執行入口為 `scripts/fetch_market_data.py`。`--mode all` 擷取股票池與 Yahoo 資料，`--mode official --twse-cache-only` 擷取 TPEx 並使用已快取的 TWSE 日報，`--mode repair` 產生 canonical 資料。快取存在時不重新下載；變更原始資料後應重新核對雜湊及所有稽核結果。

固定輸入的 SHA256 已存入 `canonical_audit.json`。回測結果應保存相同雜湊，避免資料校正後與旧回測結果混用。
