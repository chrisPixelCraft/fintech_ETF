# 未來價格因果性審查

## 結論

在本次指定的凍結程式與兩個廉價反例中，**沒有發現執行日 VWAP 直接回流，改寫同一批已送出委託股數的證據**；日線、4 小時線與深度調參特徵也未見 `bfill`、負向 `shift`、置中視窗或跨未來日期的全期正規化。

這不代表整體績效具有未見樣本的因果效力。研究已確認兩個事後偏差：`official_ex_post` 使用 2026 年官方成分池回看 2025 年起的歷史，且最終參數由完整 417 個交易日的報酬選出。兩者屬於**成分資格與模型選擇的未來資訊**，不是特徵公式直接偷看隔日價格；仍足以讓官方軌績效不能解讀為未見樣本或可上線績效。

| 判定 | 項目 | 結論 |
|---|---|---|
| **NO_EVIDENCE** | 隔日 VWAP 改委託量 | 反例未觸發 |
| **NO_EVIDENCE** | 特徵直接偷看未來 | 指定程式未見 |
| **CONFIRMED** | 官方池成分前視 | 已明示放寬 |
| **CONFIRMED** | 全期參數選優 | 屬事後開發 |
| **CONFIRMED** | 凍結面板 prefix | 兩軌皆通過 |
| **UNKNOWN** | vendor 歷史 as-of | 現證據不足 |
| **UNKNOWN** | 除權息掛單規則 | 仍是模型假設 |

## 委託、VWAP 與企業事件

**NO_EVIDENCE：隔日 VWAP 沒有回流到同一批委託股數。**

引擎先在交易日套用股利與拆股，再用當日 `turnover / execution_volume` 成交前一訊號日留下的固定 `pending` 股數；註解也明定執行日不重算股數。當日成交完成後，才用當日收盤訊號產生下一交易日的委託，且 `sizing_price` 明確取當日 `close`。證據位於：

- `src/backtest_v2.py:275-321`
- `src/backtest_v2.py:351-392`
- `scripts/audit_official_deep.py:108-128`

新增反例把第一個執行日的 `turnover` 放大 8%。結果是該日成交價同步增加 8%，但前一日委託表與成交股數逐值不變：`tests/test_tuning_credibility_audit.py:42-63`。這只驗證「已送出委託不因執行價重算」；VWAP 改變現金與 NAV，因而影響之後的決策，是正常的序列狀態更新。

**CONFIRMED：企業事件先調整舊持股，未調整已排定委託。**

拆股與股利在成交前套用到既有持股；若拆股後仍不足以完成賣單，該筆會被標成未成交。第二個反例在賣出日安排 2 倍拆股，固定賣單仍是 1,000 股，舊庫存先由 1,000 變成 2,000 股，成交後留下 1,000 股：

- `src/backtest_v2.py:282-321`
- `tests/test_tuning_credibility_audit.py:65-90`

**UNKNOWN：此企業事件掛單語義是否等同主辦與券商。**

程式內帳務一致不等於外部制度已證實。獨立 auditor 也保留「企業事件日固定股數掛單仍是假設」：`scripts/audit_official_deep.py:287-292`。

## 特徵與成熟期

**NO_EVIDENCE：指定特徵路徑未見直接未來價格操作。**

日線特徵按個股排序後使用當期或過去資料：報酬為 `pct_change`，均線與 MACD 為向後 EWM，量價比使用 trailing rolling，分母另有 `shift()`。首次報酬的 `fillna(1)` 是序列初值，不是向後填補未來值。4 小時線只收 09、10、11、12 時四根完整 bar：

- `src/backtest.py:42-48`
- `src/backtest.py:51-69`
- `src/backtest.py:72-125`

候選分數只對當日橫截面排名，成熟度用已觀測列數判定；未成熟個股不會由未來值補齊：

- `src/backtest.py:128-149`
- `src/tuning_features.py:98-133`
- `src/tuning_features.py:174-189`
- `src/tuning_a_deep.py:97-141`

快取還要求輸入值與原列雜湊相同，且每檔只能使用無缺口的完整歷史 prefix；排程參數僅從生效訊號日向前套用：

- `src/tuning_features.py:139-172`
- `src/tuning_features.py:191-227`

程式搜尋未見 `bfill`、負向 `shift`、`center=True` 或用完整時間序列統計量正規化當日特徵。這是指定程式範圍內的 **NO_EVIDENCE**，不是所有上游資料處理均已證明因果。

## 已確認的研究偏差

**CONFIRMED：`official_ex_post` 放寬成分已知時間。**

一般引擎會拒絕第一決策日後才知道的成分池：`src/backtest_v2.py:242-264`。官方軌以動態改寫，當 `ex_post_fixed_universe=True` 時略過這項拒絕：`src/official_deep_tuning.py:139-156`。獨立 auditor 也明確檢查官方軌不是 PIT 成分，而歷史軌才是：`scripts/audit_official_deep.py:74-90`。

因此，官方軌包含 2026 成分回看 2025 歷史的存活者／資格偏差。這不是隔日價格直接進入技術指標，但會影響哪些股票能被排名與交易。

**CONFIRMED：最終 winner 由全期報酬事後選出。**

選擇器以合規代理門檻後的完整期間 `total_return` 排序：`src/official_deep_tuning.py:176-183`。auditor 用 810 個完整 trial 重建 winner，並要求標籤是 `EX_POST_DEVELOPMENT`：`scripts/audit_official_deep.py:247-254`。auditor 也明示 417 個交易日全都已用於開發，沒有未見 OOS 證據：`scripts/audit_official_deep.py:287-288`。

## Prefix 證據與邊界

**CONFIRMED：凍結日線面板的時間不變性通過。**

auditor 物理截斷 daily 與 4H 面板，重建快取並重新執行已選 winner，再逐表比較訊號、委託、成交、持股與 NAV：`scripts/audit_official_deep.py:194-214`。結果如下：

- `historical_pit` 截至 2025-12-31
  - 243 sessions
  - 1,122 trades
  - economic NAV 最大誤差 0
- `official_ex_post` 截至 2026-06-30
  - 359 sessions
  - 1,490 trades
  - economic NAV 最大誤差 0

來源：`outputs/full_tuned_v2/physical_prefix_audit.json`。該檔也明確把範圍標成 `TEMPORAL_INVARIANCE_NOT_UNSEEN_OOS`。

**UNKNOWN：vendor 的歷史值在當時是否已可得。**

Prefix PASS 證明的是：在已凍結的 processed panel 中，刪除 cutoff 後的列不會改變 cutoff 前結果。它不能證明 vendor 後來回補或修正的歷史 OHLCV、股利、拆股資料，在每個歷史決策時間都以相同值存在。現有雜湊與 readiness 證明本次輸入未變，並不等於 point-in-time publication archive。這項上游 as-of 風險應維持 **UNKNOWN**。

## 新增測試與適用範圍

新增檔案：`tests/test_tuning_credibility_audit.py`。

兩個新測試連同既有 feature、deep 與 independent audit 測試共 45 項通過。這些測試是共享 `run_v2` 合成引擎的廉價因果反例；它們不是完整 deep tuning 重跑，也不是 vendor point-in-time 可得性的證明。

最可支持的表述是：**凍結程式內未發現 next-day VWAP 或未來列直接改寫先前委託與技術特徵；但官方成分池與參數選擇已確認使用事後資訊，vendor 歷史 as-of 與企業事件掛單外部規則仍未證實。**
