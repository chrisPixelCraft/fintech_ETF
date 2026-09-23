# A 策略深度調參｜參數與實驗稽核

結論是 **RUN 這個受限探索，HOLD「A 已穩定」的主張**。新設計正確固定交易硬規則、移除 A 不使用的板塊與隔夜參數，並用相同 nuisance 設定成對比較 4H 模式。它適合找下一個研究候選，不能把 2025-01-02 至 2026-09-21 的事後最佳值當成樣本外證據。

本稽核只讀既有凍結程式與已通過獨立稽核的結果。2026 年 9 月資料只到 9 月 21 日；兩條軌道各有 417 個交易日。主要證據是 [第二輪報告](../reports/tuning_report_2nd_try.md)、[歷史池 audit](../outputs/tuning_report_2nd_try/historical_pit/audit.json)、[正式池事後軌 audit](../outputs/tuning_report_2nd_try/official_ex_post/audit.json)、[新研究規格](a_deep_protocol.md) 與 [新凍結設定](../config/a_deep_study.json)。

## 「A 穩定」目前只能作有限描述

A 在兩條開發軌都能找到高報酬、零已量測硬性違規的候選；這支持 A 值得繼續研究。它不支持參數穩定、未來報酬穩定或正式競賽合規。

| 候選 | 歷史重建池 | 正式名單事後池 |
|---|---|---|
| p000 原始基準 | 報酬 159.40%；MDD 26.30%；排名 18/64；不可行 37 日 | 報酬 207.28%；MDD 25.75%；排名 38/64；不可行 40 日 |
| p052 歷史勝者 | 報酬 192.21%；MDD 23.67%；排名 1/64；不可行 2 日 | 報酬 187.78%；MDD 27.75%；排名 48/64；不可行 17 日 |
| p049 事後池勝者 | 報酬 174.92%；MDD 22.87%；排名 7/64；不可行 39 日 | 報酬 277.51%；MDD 25.36%；排名 1/64；不可行 49 日 |

三組在表內都是零個 measured hard-breach days。排名採各軌 64 個 A 候選的扣成本 economic return，不含 B/C。

穩定性反證比「兩軌都有正報酬」更有辨識力：

- 報酬排名只中度一致：Spearman 0.469；Kendall 0.319。
- Top 5 只重疊 p014，共 1 組。
- p052 從歷史池第 1 降至事後池第 48。
- p049 從歷史池第 7 升至事後池第 1。

因此可說「A 在兩個已看過的情境中都有可行候選」，不能說「A 的最佳參數已穩定」。正式名單軌還含 2026 成分股前視偏誤，不能當獨立驗證集。兩條軌都使用同一段市場歷史，也不能當兩次獨立樣本。

## A 中真正生效的參數

[score_candidates](../src/backtest.py) 建立排名與進出場條件；[compliance_planner](../src/compliance_planner.py) 再把訊號轉成固定股數、規則優先的委託。參數可能先改排名或 eligibility，之後再被現金、檔數、權重與整張限制覆寫。這使「參數改了交易」成立，但不代表單一績效機制已被識別。

| 參數 | 實際路徑 | 判讀限制 |
|---|---|---|
| `target_count` | 改持股目標、等額配置與補倉量 | 仍受 20–30 檔硬限制 |
| `replacement_margin` | 比較新股與最弱持股分差 | 普通換股上限為 0 時結構性失效 |
| `max_replacements_per_day` | 限制普通換股次數 | 不限制強制退出、現金或權重修正 |
| `volatility_spike_ratio` | 只改進場 eligibility | 不直接改退出 |
| `one_day_chase_return` | 只改進場 eligibility | 高值接近關閉過熱濾網 |
| `volume_low/high` | 只改進場 eligibility | 上下界應視為一個條件帶 |
| `four_hour_mode` | `strict` 要求 4H 偏多；`coverage_only` 只要求資料完整 | 兩者都需要 50 根已觀測 4H 棒 |
| `return_short/long` | 改兩個報酬排名；短報酬也進入退出條件 | 兩窗口有交互作用 |
| `ema_fast/slow` | 改 trend；慢 EMA 也改進場與退出 | 不是單純排名參數 |
| `macd_fast/slow/signal` | 改 MACD 正負分數；也改退出 | 三個數值須共同調整 |
| `momentum_weight` | 改動能總權重 | 其餘四項共享剩餘權重 |
| `long_return_fraction` | 在短、長報酬間分配動能權重 | 必須連同總動能權重解讀 |

第二輪 A 的所有 25 個 active OFAT probe 都改變了實際委託路徑。相對 p000，歷史池有 260–416 個 signal days 不同，事後池有 256–411 日不同。歷史池的 margin 0 與 0.05（p001/p002）卻產生完全相同的 orders、trades 與 equity；這證明數值不同不一定增加有效試驗數。64 組中，歷史池有 63 條唯一委託路徑，事後池有 64 條。

A 的 `sector_top_fraction`、`sector_short_weight`、`sector_fallback_mode` 與 `c_alpha` 不生效。[transform_for](../scripts/run_v2_tuning.py) 對 A 直接回傳 `None`，A 不會進入板塊或隔夜 callback。新設定移除這四個維度是正確的。

## 舊搜尋的邊界仍未解決

| 參數 | 舊範圍 | p052 | p049 | 稽核結果 |
|---|---|---:|---:|---|
| 目標持股 | 20 / 25 / 30 | 20 | 20 | 兩者都在合法下界 |
| 換股分差 | 0–0.30 | 0 | 0.20 | p052 在物理下界 |
| 普通換股上限 | 0 / 1 / 2 / 4 | 4 | 0 | 兩端各有勝者 |
| 波動比上限 | 1.5–3.0 | 3.0 | 2.0 | p052 在上界 |
| 單日追價上限 | 4.0%–9.5% | 9.5% | 4.0% | 兩端各有勝者 |
| 量能帶 | 0.3–0.8 / 2–5 | 0.5–3 | 0.8–3 | p049 的下界在上端 |
| 4H | strict / coverage | coverage | coverage | 兩者同向，仍非未見證據 |
| 報酬窗 | 10/30、20/50、30/90 | 20/50 | 10/30 | p049 在快端 |
| EMA | 10/30、20/50、30/90 | 30/90 | 10/30 | 兩端各有勝者 |
| MACD | 8/21/5、12/26/9、19/39/9 | 12/26/9 | 8/21/5 | p049 在快端 |
| 評分 | fast / base / slow / balanced | base | fast | 配置依股票池改變 |

p052 同時命中五個邊界，p049 也命中多個相反邊界。這不是「最佳值已找完」的證據，而是需要軸向探測與交互作用搜尋的訊號。

既有 cross-section 也顯示某些舊上界幾乎等於關閉濾網。以 repaired pilot 的 62,240 筆 ready、非零量資料列作診斷：

| Gate | 通過比例 |
|---|---:|
| 波動比 ≤2.0 | 98.39% |
| 波動比 ≤3.0 | 99.87% |
| 單日報酬 ≤4.0% | 91.84% |
| 單日報酬 ≤9.5% | 97.94% |
| 量能 0.3–5.0 | 99.29% |
| 量能 0.5–3.0 | 94.07% |
| 量能 0.8–2.0 | 64.12% |
| 4H strict，在 coverage 已就緒列中 | 40.50% |

這些比例只是 eligibility 診斷，不是報酬機制估計。它們支持保留「近乎無濾網」的 sentinel，也說明 4H 模式很可能是高影響變數。

## 變數角色與固定邊界

本輪唯一明確的科學比較是：相同 nuisance tuple 下，4H 方向確認是否增加價值。日線窗口、評分與進場門檻則是 A 的可調配套；聯合候選只能作整體工程比較。

| 角色 | 變數 | 處理 |
|---|---|---|
| 科學 | `four_hour_mode` | 64 組 nuisance tuple 各跑 strict／coverage |
| Nuisance | 持股數、換股、波動、追價、量能 | OFAT、Sobol、局部深化 |
| Nuisance | 報酬窗、EMA、MACD、評分權重 | 使用明示數值，不再只寫 fast/base/slow |
| 固定 | 20–30 檔、權重上限、現金 <25% | 不以績效放寬 |
| 固定 | VWAP、費稅、整張、公司事件 | 沿用已稽核資料路徑 |
| 固定 | cash guard 12%、headroom 2% | 只作事前工程緩衝，不當 alpha |
| 固定 | 價格壓力 0.9/1.1、local allocation | 不調參 |
| 固定 | 200 日暖機、長 EMA100/200 | 不調參 |
| 固定 | 4H EMA20、MACD12/26/9、50 bars | 只切 gate，不改 4H 指標 |
| 排除 | `sector_*`、`c_alpha` | A 路徑不使用 |

cash guard 與 headroom 不是競賽額外規則，而是因果規劃器的工程緩衝。它們曾用事前可行性規則固定，這輪不應再依報酬選值。`cash_target=0` 也不表示實際現金為零；整張、價格壓力與規則修正會留下現金。

## 已凍結的深度搜尋空間

[a_deep_study.json](../config/a_deep_study.json) 有 12 個 Sobol 軸；4H 另作第 13 個成對組別。它們展開成 17 個 scalar fields。這個計數不能混成「17 維 Sobol」。

| 軸 | 值 | 理由 |
|---|---|---|
| `target_count` | 20, 22, 25, 28, 30 | 涵蓋合法範圍的代表點，未窮舉 20–30 的全部整數；30 是合法上界 |
| `replacement_margin` | 0, .025, .05, .10, .20, .30, .40 | 細分 0–.20；延伸舊上界 |
| `max_replacements_per_day` | 0, 1, 2, 4, 6, 8 | 同時覆蓋 p049 與 p052 方向 |
| `volatility_spike_ratio` | 1.5, 2, 2.5, 3, 4, 5 | 3 以上接近 no-filter sentinel |
| `one_day_chase_return` | 3%, 4%, 5.5%, 7%, 8.5%, 9.5% | 覆蓋有約束到接近漲停 |
| `volume_low` | .2, .3, .5, .8, 1.0 | 探測低量容忍度 |
| `volume_high` | 2, 3, 5, 8 | 2 有約束；8 接近 no-filter |
| 報酬窗 | 5/20, 10/30, 15/40, 20/50, 30/90, 40/120 | 一週／一月至約兩月／半年 |
| EMA | 5/20, 10/30, 15/40, 20/50, 30/90, 50/150 | 包含兩個舊勝者並延伸邊界 |
| MACD | 6/13/4, 8/21/5, 12/26/9, 16/35/9, 19/39/9, 24/52/12 | 明示快慢三元組 |
| `momentum_weight` | .55, .65, .75, .85, .95 | 控制動能總權重 |
| `long_return_fraction` | .15, .20, .35, .4667, .50, .70, .85, .90 | 控制長報酬在動能內占比 |
| `four_hour_mode` | strict / coverage_only | 每一 Sobol tuple 成對比較 |

新的評分權重為：

```text
short_return = momentum_weight × (1 − long_return_fraction)
long_return  = momentum_weight × long_return_fraction
volume/macd/trend/long_trend = (1 − momentum_weight) × 0.4/0.2/0.2/0.2
```

p052 對應 `momentum_weight=.75`、`long_return_fraction=.4667`；p049 對應 `.75/.20`。舊 `slow` 的精確 `.15/.60/.10/.05/.05/.05` 與 `balanced` 的 `.30/.30/.15/.10/.10/.05` 不在新離散格點中，只能被鄰近值近似。這不使研究失效，但限制「已完整覆蓋舊評分家族」的說法；最終報告應列為 search-space caveat。

## 實驗設計稽核

凍結設計為每條軌道 201 個探索候選，再依該軌完整開發結果生成 64 個局部候選，共 265 組；兩條軌共 530 組。

| 階段 | 數量／軌 | 能回答的問題 |
|---|---:|---|
| 兩個 anchor | 2 | 精確重播 p052、p049 |
| 雙 anchor OFAT | 71 | 找局部敏感度、plateau 與邊界 |
| 配對 Sobol | 128 | 64 個 nuisance tuple 的 4H 成對差異 |
| 局部深化 | 64 | 在已知開發資料內尋找更好工程組合 |

這個設計的優點：

- 4H 預算完全配對
  - 64 對逐參數相同
- 搜尋 seed 分離
  - 20260922 與 20260923
- inactive 維度已刪除
  - 不浪費 A 的試驗數
- 邊界預先列出
  - 不在看結果後臨時加值
- 失敗不作合格
  - 不使用 least-violating fallback

仍需保留三個限制。第一，12 軸只由每個 seed 32 個 nuisance vectors 覆蓋，主要用途是廣探索，不能宣稱空間飽和。第二，64 個局部候選由整段探索結果生成，是 adaptive full-development search。第三，p049 由 membership-lookahead 軌得到；把它作另一個 anchor 會增加既有開發資訊，但不構成資料前視之外的新獨立證據。

## 結果應如何判讀

主要選擇規則可沿用：完整回測、零 measured hard-breach days，然後最大化扣成本 economic return，以較低 economic MDD 和 candidate ID 破同分。回撤受限版本應另列，不能事後替換主要規則。

報告不能只列最大值。至少需要：

- 畫完整軸向圖
  - 報酬、MDD、成本、不可行日
- 分析 64 個 4H pair
  - 用 paired delta，不比兩邊 maxima
- 分開兩個 Sobol seed
  - 檢查 top region 是否一致
- 公開有效路徑數
  - 去除 observational aliases
- 檢查所有邊界
  - 邊界勝者不稱 interior optimum
- 顯示時間切片
  - 2025 H1/H2、2026 H1/Q3

時間切片只能叫 retrospective stress。先前研究已看過全部月份，任何 2025–2026 子區間都不是新的 holdout。若需要「穩定」證據，最小可信條件是兩個 Sobol seed 找到相近區域、paired 4H 結論方向一致、候選在多個時間切片不只靠單一月份，並在未參與本次選參的未來期間通過。即使達成，也只可升級為新的 research incumbent。

## 決策門檻

**ADOPT（研究版）**：零已量測硬性違規，精確重播通過，兩個搜尋 seed 指向相近參數區域，時間切片沒有單一區間壟斷全部優勢，且效益足以覆蓋新增換手與成本。

**HOLD**：只有 full-period 最大值改善、最佳值仍落在可擴張邊界、兩 seed 或兩軌選到相反區域，或不可行日明顯增加。這是目前對「A 穩定」最合適的狀態。

**REJECT**：候選無法保持零硬性違規、重播不一致、paired 4H 效果消失或反轉，或改善只來自 membership-lookahead 軌。

Active Share、歷史 ETF 持股與正式提交一致性仍是 `UNKNOWN/BLOCK_SUBMISSION`。這些缺口不會因 530 次回測而消失。真正的未見確認必須使用本輪凍結後才出現的資料，並且在看到結果前固定候選與判定規則。
