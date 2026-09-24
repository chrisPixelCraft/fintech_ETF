# ETF 策略研究

研究目標是每次從 **10 億元、零持股出發的 24 交易日報酬**。固定 v2 的 **309.16%** 是另一套資料與成交口徑下的長期回測，不能代替短賽期證據。正式使用維持 **`BLOCK_READY`／`BLOCK_SUBMISSION`**。

## 目前狀態（2026-09-24）

| 研究 | 結果 |
|---|---|
| V3 四輪 1,471 組 | `NO_ELIGIBLE_CANDIDATE` |
| V4 Stage 2 | `NO_V4_WINNER`；策略層已移除，保留 Stage 1 官方均價模擬器與帳本 |
| **V5** | **程式與測試完成；官方成交資料下載中，尚無任何 V5 績效** |

## Clone 與歷史證據

`outputs/` 已不再追蹤（只保留 `src/strategy_24d.py` 會讀的 v2 設定，以及 V3 測試重建候選譜系所需的三輪 `candidates.json`／`development_ranking.csv`），研究執行結果一律留在本機，摘要放 `reports/`。V1–V4 的完整輸出、帳本與官方原始快取保留在提交 [`2769f876`](https://github.com/chrisPixelCraft/fintech_ETF/tree/2769f876ec4b9795ce5d8cc4e64b8274da58099c)，下方標示的 V4 Stage 1、第四輪與 v2 發行驗證指令都要在該版本執行。

Git 歷史仍含這些大檔，建議用部分 clone，只下載目前版本需要的檔案：

```bash
git clone --filter=blob:none https://github.com/chrisPixelCraft/fintech_ETF.git
git checkout 2769f876ec4b9795ce5d8cc4e64b8274da58099c   # 需要舊證據時才切換，會按需下載
```

## V5：冠軍策略啟發的四族研究（進行中）

[規格](docs/v5_spec.md)在任何結果出現前預先宣告，依 [champion.md](docs/champion.md) 與 V4 失敗分析設計。修改門檻、切分或參數網格須另立版本並記錄理由。

| 策略族 | 核心機制 | 組數 |
|---|---|---:|
| A `momentum` | R5／R10／R20／R60 排名混合，EMA20 趨勢濾網 | 12 |
| B `ensemble` | AutoTS 式專家池，walk-forward 加權，L／P／U 區間與方向一致信心 | 11 |
| C `rank` | ATA 式排名頻率，預測進入前五分之一的機率 | 11 |
| E `direct` | 直接擬合近期投組效用的線性分數，L2 懲罰 | 11 |

- **窗口**：每月第一個交易日起算 24 日；開發 2010–2018（108）、驗證 2019–2022（48）、回顧 holdout 2023–2024（24），另有 10/26 季節與 2025 後近期診斷，共 216 個窗口、4,070 個交易日。
- **成交**：沿用 V4 Stage 1 模擬器，官方成交金額÷股數為成交價、官方前日收盤定股數。缺官方資料不以 Open／Close 補值。
- **流程**：S1 各族選優 → S2 投組層消融（score-bounded、risk-aware、regime overlay）→ 驗證期對 `A0_V3` 配對比較 → 凍結 → holdout 只能否決、不能認證。
- **已完成**：79 項單元測試通過，各訊號族通過未來資料破壞的位元一致因果測試。
- **待完成**：TWSE／TPEx 官方日報共 8,140 份。資料不齊時 `v5_run.py` 以 `BLOCK_CANONICAL_V5` 拒絕執行。

### 執行

官方網站有限流，下載約需 3–4 小時；中斷後重跑相同指令會從斷點接續。

```bash
nohup python scripts/v5_data.py all > fetch.log 2>&1 &   # 下載並建官方成交資料
python scripts/v5_data.py status                         # 兩市場 raw_present 接近 4,070 即完成
python scripts/v5_run.py --stage scores                  # 只需日線，可與下載同時先算
python scripts/v5_run.py --stage all && python scripts/v5_verify.py && python scripts/v5_report.py
python -m unittest discover -s tests -p 'test_v5_*.py' -q
```

原始回應約 1GB，留在本機不推送。結果寫入 `outputs/v5/study/`，通過全部門檻才產生 `configs/v5_final.json`，否則為 `NO_V5_WINNER`；即使通過，正式提交仍維持 `BLOCK_SUBMISSION`。

已知限制：2026-07-31 股票池回套至 2010 有存活偏誤，動能族受影響最大；387 筆未記錄減資造成的價格跳動影響所有策略；未建模 10 億元市場衝擊。

## V4 Stage 1：成交與帳本驗證（V5 沿用）

本階段只重現凍結 V3，不建立或調整 V4 策略。新增 TWSE／TPEx 官方成交金額÷成交股數資料管線，與 Open proxy 配對回放；另以官方前日收盤價定股數與估值，檢查完整官方價格路徑。所有結果仍為研究證據，正式提交維持 `BLOCK_SUBMISSION`。

21 次回放已完成，三軌各完成 5/7 個窗口；量測合規通過數依序為 Open **3/7**、官方均價對照 **1/7**、完整官方價格路徑 **3/7**。帳務驗證通過不代表策略合規；未宣告 V4 winner。

[執行比較報告](reports/v4_execution_comparison.md)列出預先指定的 7 個 24 日窗口、全部失敗、逐筆價差及成交假設影響。這是涵蓋不同時期的有限診斷，並非完整歷史驗證；近期重疊窗口不是獨立樣本。官方資料缺漏不以 Open／Close 補值，缺證據時維持 `BLOCK_CANONICAL_V4`。

| 內容 | 連結 |
|---|---|
| 範圍與設定 | [Stage 1 設定](config/v4_study.json)／[主規格](docs/v4_master_spec.md)／[驗證規格](docs/v4_execution_validation_spec.md) |
| 結果與來源 | [配對結果](https://github.com/chrisPixelCraft/fintech_ETF/blob/2769f876ec4b9795ce5d8cc4e64b8274da58099c/outputs/v4/stage1/results.csv)／[來源與雜湊](https://github.com/chrisPixelCraft/fintech_ETF/blob/2769f876ec4b9795ce5d8cc4e64b8274da58099c/outputs/v4/stage1/manifest.json)／[Open 重現](https://github.com/chrisPixelCraft/fintech_ETF/blob/2769f876ec4b9795ce5d8cc4e64b8274da58099c/outputs/v4/stage1/open_reproduction.json) |
| 官方價格 | [正規化快取](https://github.com/chrisPixelCraft/fintech_ETF/blob/2769f876ec4b9795ce5d8cc4e64b8274da58099c/outputs/v4/execution_data.csv)／[下載紀錄](https://github.com/chrisPixelCraft/fintech_ETF/blob/2769f876ec4b9795ce5d8cc4e64b8274da58099c/outputs/v4/execution_data.manifest.json) |

使用既有 Yahoo 快照與官方快取重現（需先切到證據版本 `2769f876`），輸出必須選新的 V4 目錄：

```bash
python scripts/v4_run_baseline.py --output outputs/v4/stage1_replay
python scripts/v4_verify.py --output outputs/v4/stage1_replay
python scripts/v4_report.py --output outputs/v4/stage1_replay
python -m unittest discover -s tests -p 'test_v4_*.py' -q
```

官方快取可用 `python scripts/v4_build_execution_data.py --dates outputs/v4/execution_requested_dates.csv` 重建；交易所原始回應以壓縮檔保留。策略、委託與成交資料分開處理，因果測試確認 t 日與未來資料不改寫已提交委託；獨立稽核重算費稅、持股、現金與 NAV。

## 最新 v3 可行性搜尋

新增 **384 組不重複參數**，四輪合計 **1,471 組**。從前三輪各自的開發期代表出發，每個來源128組，只調整訊號期間、評分權重與換股參數，保持其餘設定與所有逐日門檻不變。

結果仍為 **`NO_ELIGIBLE_CANDIDATE`**。154個基準窗口已重現；新增384組均未通過必要開發窗口 **2015-08-03 起的24個交易日**。依預先固定的提前停止條件，本輪不再替已失去入選資格的組合跑完整期間排名。

| 新參數來源 | 組數 | 完成24日 | 無原始超限 | 全項通過 |
|---|---:|---:|---:|---:|
| 第一輪代表鄰域 | 128 | 128 | 128 | 0 |
| 第二輪代表鄰域 | 128 | 128 | 128 | 0 |
| 第三輪代表鄰域 | 128 | 128 | 128 | 0 |

「無原始超限」不等於全項通過，仍可能被公司行動後的零股持倉檢查擋下。全部新組合的成交委託都是整張，零股持倉不能誤稱為零股委託；官方處理契約仍缺證據，不能刪除門檻換取通過。

新參數的完整開發期、驗證期、歷史評估與近期報酬均標記 **NOT_RUN**，沒有補出未執行的績效。這是完成可行性篩選，不是找到報酬最佳組合；也不代表全部可能參數均無解。原 canonical 設定不變。

| 內容 | 連結 |
|---|---|
| 最新結果 | [精簡報告](reports/24d_round4.md)／[比較 CSV](reports/24d_round4_summary.csv)／[窗口明細](https://github.com/chrisPixelCraft/fintech_ETF/blob/2769f876ec4b9795ce5d8cc4e64b8274da58099c/outputs/24d_round4/bottleneck.csv) |
| 搜尋與凍結 | [搜尋設定](config/24d_round4_study.json)／[凍結紀錄](https://github.com/chrisPixelCraft/fintech_ETF/blob/2769f876ec4b9795ce5d8cc4e64b8274da58099c/outputs/24d_round4/final_selection.json)／[參數包](https://github.com/chrisPixelCraft/fintech_ETF/blob/2769f876ec4b9795ce5d8cc4e64b8274da58099c/outputs/24d_round4/competition_24d_candidate.json) |
| 核對證據 | [獨立驗證](https://github.com/chrisPixelCraft/fintech_ETF/blob/2769f876ec4b9795ce5d8cc4e64b8274da58099c/outputs/24d_round4/verification.json)／[輸出 SHA256](https://github.com/chrisPixelCraft/fintech_ETF/blob/2769f876ec4b9795ce5d8cc4e64b8274da58099c/outputs/24d_round4/result_manifest.json)／[逐日規則](docs/v2_double_check_rules.md) |
| 最近完整比較 | [第三輪報告](reports/24d_round3.md)／[第三輪比較表](reports/24d_round3_comparison.csv)；不是本輪新參數成績 |
| 既有參考設定 | [canonical 設定](configs/competition_24d_final.json)／[舊設定解讀](configs/competition_24d_final_metadata.json) |

資料快照截至 **2026-09-23**，開發2010–2018、驗證2019–2022邊界不變。既有驗證與後期結果已在前輪看過，不能再稱首次未見 holdout；基準參數來自後期研究，2026白名單回套歷史亦有前視及存活偏誤。

交易使用 Yahoo 日線、D−1 決策與次日開盤價近似成交，納入雙邊手續費0.1425%與賣出稅0.3%。Active Share、官方結算、公司行動及平台收件證據仍缺；未建模10億元委託的市場衝擊。本地帳本稽核不能代替官方合規認證。

## 重現與驗證

Python 3.10；安裝 `requirements.txt` 與 `requirements-yahoo.txt` 後執行單元測試。前兩行驗證第四輪保留輸出，需在證據版本 `2769f876` 執行：

```bash
python scripts/verify_24d_round4.py --rebuild
python scripts/report_24d_round4.py --verify
python -m unittest discover -s tests -q
```

使用凍結快照另建輸出；相同指令可接續已完成組別：

```bash
python scripts/expand_24d_round4.py --workers 4 --output outputs/24d_round4_replay
python scripts/verify_24d_round4.py --output outputs/24d_round4_replay --rebuild
```

帳本、設定、程式及資料皆有雜湊；來源改變時拒絕沿用舊結果。原始 Parquet 與名目股數換算分開保存。[日曆修訂](data/yahoo_daily/v3_20260923/calendar_v2.json)補回0050缺價但市場仍交易的日期。`repair=True`可能由 yfinance 內部使用較細頻資料修復；策略本身只使用日線，沒有4H依賴。

## 既有 v3 研究

第三輪512組的[分層搜尋報告](reports/24d_round3.md)及[分層明細](reports/24d_round3_strata.csv)完整保留。

第二輪328組的[追加搜尋報告](reports/24d_expansion.md)、[比較表](reports/24d_expansion_comparison.csv)與[共同窗口表](reports/24d_expansion_common.csv)完整保留。

第一輪 247 組的[分階段調參報告](reports/24d_tuning.md)、[比較表](reports/24d_tuning_comparison.csv)與[冷啟動明細](reports/24d_tuning_cold_start.csv)完整保留，採相同 2010–2018／2019–2022 切分。

更早的 [v3_spec.md](docs/v3_spec.md)研究採 2010–2019／2020–2022 切分，28 組搜尋也沒有合格候選；見[研究摘要](reports/24d_strategy_summary.md)、[參數搜尋](reports/24d_parameter_search.md)、[失敗分析](reports/24d_failure_analysis.md)及[凍結候選](reports/24d_final_candidate.md)。不可混稱同一切分。

```bash
python scripts/verify_24d_tuning.py --rebuild
python scripts/report_24d_tuning.py --verify
python scripts/verify_24d.py
python scripts/report_24d.py --verify
```

## 固定 v2 長期參考

[`best_v2.py`](best_v2.py) 保留原 `x0352`，期間為 2025-01-02–2026-09-21。年度欄採股利歸屬後的經濟淨值；2026 年並非全年或年化報酬。

| 期間 | v2 `x0352` | v1 | 0050 |
|---|---:|---:|---:|
| 2025 年 | 78.95% | 66.36% | 33.46% |
| 2026 年至 9/21 | 128.65% | 109.28% | 65.81% |
| 全期帳面報酬 | 309.16% | 248.15% | 121.28% |

細節見 [v2 比較報告](reports/comparison.md)、[固定帳本](https://github.com/chrisPixelCraft/fintech_ETF/tree/2769f876ec4b9795ce5d8cc4e64b8274da58099c/outputs/best_v2/)與[發行清單](config/best_v2_release.json)。v2 仍依賴原始日內資料口徑，兩套研究的報酬不能直接拼接。發行驗證需要完整帳本，請在證據版本 `2769f876` 執行。

```bash
python best_v2.py --verify-release
python best_v2.py --track official_ex_post --output outputs/my_v2_replay
```

`best_v2.py plan` 與 `research-plan` 都維持阻擋，不輸出 D-Plan。官方原文與歷史封存方式見[文件導覽](docs/README.md)。
