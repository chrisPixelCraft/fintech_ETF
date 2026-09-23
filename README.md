# ETF 策略研究

研究目標是每次從 **10 億元、零持股出發的 24 交易日報酬**。固定 v2 的 **309.16%** 是另一套資料與成交口徑下的長期回測，不能代替短賽期證據。正式使用維持 **`BLOCK_READY`／`BLOCK_SUBMISSION`**。

## 最新 v3 分層搜尋

依 [v3_tuning.md](docs/v3_tuning.md)新增 **512 組不重複參數**：256 組均勻抽樣、256 組分層搜尋。分層涵蓋 5 種目標持股數 × 4 種每日換股數，全部留在原規格範圍。排除前兩輪 575 組，三輪合計 **1,087 組**；並非窮舉全部可能參數。

結果仍為 **`NO_ELIGIBLE_CANDIDATE`**。154 個基準窗口已重現；512 組新增參數均在必要的開發期困難月份失敗，沒有合格替代者。另按預定診斷預算，12 組進入共同開發月份、6 組重跑完整開發與驗證期。所有帳務與合規門檻維持不變。

以下比較相同 **2019–2022 驗證窗口**。代表由各輪開發資料決定，不是合格 winner。

| 策略 | Median 24D | P25 | P10 | 完整／全部 | 已量測通過／全部 |
|---|---:|---:|---:|---:|---:|
| 基準 | 3.08% | -0.23% | -5.71% | 43/47 | 34/47 |
| 前輪開發代表 | 2.71% | -1.09% | -3.22% | 45/47 | 37/47 |
| 本輪開發代表 | 2.05% | -0.47% | -3.82% | 46/47 | 37/47 |

報酬只計完整且通過的窗口，各策略通過樣本可能不同；失敗保留在全部嘗試分母。報告另列三者「共同通過窗口」比較，但它也不能代表全部起始日期的表現。本輪驗證通過數最高為 **38/47**，仍未達完整開發及驗證期皆 **100% 通過**的採用要求；canonical 設定維持原基準。

困難月份中，177 組沒有原始規則超限，但被零股持倉檢查擋下。全部成交委託都是整張；公司行動後的零股持倉，不等於送出零股委託。報告另列20種分層的失敗分布，不能只憑參數增加或單窗改善宣稱更好。

| 內容 | 連結 |
|---|---|
| 最新結果 | [精簡報告](reports/24d_round3.md)／[各期比較](reports/24d_round3_comparison.csv)／[共同窗口](reports/24d_round3_common.csv)／[分層明細](reports/24d_round3_strata.csv) |
| 搜尋與凍結 | [搜尋設定](config/24d_round3_study.json)／[凍結紀錄](outputs/24d_round3/final_selection.json)／[參數包](outputs/24d_round3/competition_24d_candidate.json) |
| 核對證據 | [獨立驗證](outputs/24d_round3/verification.json)／[輸出 SHA256](outputs/24d_round3/result_manifest.json)／[逐日規則](docs/v2_double_check_rules.md) |
| 既有參考設定 | [canonical 設定](configs/competition_24d_final.json)／[舊設定解讀](configs/competition_24d_final_metadata.json) |

開發期為 **2010–2018**，驗證期為 **2019–2022**，凍結後歷史評估為 **2023–2024**；跨邊界窗口排除。另檢查 2025–2026/09、近期 126 個完整滾動起點及 2010–2025 的 10–11 月類比。資料截止 **2026-09-23**，9 月月初尚不足完整 24 日。

2023–2024 與近期結果已在前輪看過，只能說本輪搜尋未使用它們，不能稱首次未見 holdout。驗證期重複使用有適應性選擇偏誤，基準參數也來自後期研究，2026 白名單回套歷史有存活與成分前視偏誤，重疊滾動窗口也不是獨立樣本。

交易採 Yahoo 日線、D−1 決策、次日開盤價近似成交，納入雙邊手續費 0.1425% 與賣出稅 0.3%。Active Share、官方結算、公司行動、最新公告與收件證據仍缺；開盤價不是官方日成交均價，且未建模 10 億元委託的市場衝擊。本地檢查不能代替官方合規認證。

## 重現與驗證

Python 3.10；安裝 `requirements.txt` 與 `requirements-yahoo.txt` 後：

```bash
python scripts/verify_24d_round3.py --rebuild
python scripts/report_24d_round3.py --verify
python -m unittest discover -s tests -q
```

使用凍結快照另建輸出重算；相同指令可接續已完成的組別：

```bash
python scripts/expand_24d_round3.py --workers 4 --output outputs/24d_round3_replay
```

逐日帳本、設定及輸入／輸出皆保存雜湊；來源改變時拒絕沿用舊結果。原始 Parquet 與名目股數換算檔分開保存。[日曆修訂](data/yahoo_daily/v3_20260923/calendar_v2.json)補回 0050 缺資料但股票市場仍交易的日期。`repair=True` 可能由 yfinance 內部使用更細頻資料修復；策略本身只使用日線，沒有 4H 依賴。

## 既有 v3 研究

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

細節見 [v2 比較報告](reports/comparison.md)、[固定帳本](outputs/best_v2/)與[發行清單](config/best_v2_release.json)。v2 仍依賴原始日內資料口徑，兩套研究的報酬不能直接拼接。

```bash
python best_v2.py --verify-release
python best_v2.py --track official_ex_post --output outputs/my_v2_replay
```

`best_v2.py plan` 與 `research-plan` 都維持阻擋，不輸出 D-Plan。官方原文與歷史封存方式見[文件導覽](docs/README.md)。
