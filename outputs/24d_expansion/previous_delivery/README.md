# ETF 策略研究

研究目標是每次從 **10 億元、零持股出發的 24 交易日報酬**。固定 v2 的 **309.16%** 是另一套資料與成交口徑下的長期回測，不能代替短賽期證據。正式使用維持 **`BLOCK_READY`／`BLOCK_SUBMISSION`**。

## 最新 v3 調參

已依 [v3_tuning.md](docs/v3_tuning.md)完成 A–F 分階段搜尋、局部 Cartesian 網格及有界隨機搜尋，共 **247 組不重複參數**。先重現基準的 154 個開發／驗證窗口，再用相同 12 個開發期月份篩選，入圍者重跑完整開發與驗證期。

結果為 **`NO_ELIGIBLE_CANDIDATE`**。以下是相同 **2019–2022 驗證窗口**上的診斷比較；搜尋代表沒有通過全部已量測限制，不能稱作可提交的 winner。

| 策略 | Median 24D | P25 | P10 | 完整窗口 | 已量測通過／全部 |
|---|---:|---:|---:|---:|---:|
| x0352 baseline | 3.08% | -0.23% | -5.71% | 91.49% | 34/47 |
| staged 診斷代表 | 3.08% | -0.23% | -5.71% | 91.49% | 34/47 |
| local 診斷代表 | 3.46% | -0.71% | -3.94% | 91.49% | 35/47 |
| global 診斷代表 | 3.58% | -0.89% | -3.37% | 100.00% | 36/47 |

報酬只描述完整且通過的窗口，各策略通過樣本可能不同；失敗窗口保留在分母。完整跑完不等於逐日合規，不能把条件式高報酬當作可實現績效。採用門檻要求完整開發與驗證期皆為 **100% 已量測通過**；本輪沒有合格替代者，因此 canonical 設定保留基準原位元組。

| 內容 | 連結 |
|---|---|
| 本輪結果 | [精簡報告](reports/24d_tuning.md)／[各期比較 CSV](reports/24d_tuning_comparison.csv)／[冷啟動明細](reports/24d_tuning_cold_start.csv) |
| 搜尋與凍結 | [搜尋設定](config/24d_tuning_study.json)／[凍結紀錄](outputs/24d_tuning/final_selection.json)／[本輪參數](outputs/24d_tuning/competition_24d_candidate.json) |
| 可核對證據 | [驗證結果](outputs/24d_tuning/verification.json)／[輸出 SHA256](outputs/24d_tuning/result_manifest.json)／[逐日規則](docs/v2_double_check_rules.md) |
| 既有參考設定 | [canonical 設定](configs/competition_24d_final.json)／[舊設定解讀](configs/competition_24d_final_metadata.json) |

本輪切分為開發 **2010–2018**、驗證 **2019–2022**、凍結後歷史評估 **2023–2024**；跨邊界窗口不混入選參。2025–2026/09、近期六個完整月份、滾動窗口及 2010–2025 的 10–11 月類比只作凍結後檢查。資料截止 **2026-09-23**，9 月月初尚不足完整 24 日。

2023–2024 與近期結果已在上一輪看過，只能說本輪搜尋沒有使用它們，不能宣稱首次未見 holdout。原基準亦有後期選參暴露；2026 白名單回套歷史有存活與成分前視偏誤。滾動窗口會重疊，不視為獨立樣本。

交易採 Yahoo 日線、D−1 決策、次日開盤價近似成交，納入雙邊手續費 0.1425% 與賣出稅 0.3%。逐日持股、權重、現金與委託檢查沿用凍結帳本。Active Share、官方結算、公司行動、最新公告與收件證據仍缺；開盤價不是官方日成交均價，且未建模 10 億元委託的市場衝擊，因此不能保證所有官方規則已通過。

## 重現與驗證

Python 3.10；安裝 `requirements.txt` 與 `requirements-yahoo.txt` 後：

```bash
python scripts/verify_24d_tuning.py --rebuild
python scripts/report_24d_tuning.py --verify
python -m unittest discover -s tests -q
```

使用凍結快照另建輸出重算，執行中可用相同指令接續已完成的組別：

```bash
python scripts/tune_24d.py --workers 4 --output outputs/24d_tuning_replay
```

每組保存逐日帳本、設定與輸入／輸出雜湊；來源或設定改變時拒絕沿用舊結果。原始 Parquet 與名目股數換算檔分開保存。[日曆修訂](data/yahoo_daily/v3_20260923/calendar_v2.json)補回 0050 缺資料但股票市場仍交易的日期。`repair=True` 可能由 yfinance 內部使用更細頻資料修復；策略本身只使用日線，沒有 4H 依賴。

## 上一輪 v3

上一輪 [v3_spec.md](docs/v3_spec.md)採 2010–2019／2020–2022 切分，28 組搜尋也沒有合格候選。原始結果保留於[研究摘要](reports/24d_strategy_summary.md)、[參數搜尋](reports/24d_parameter_search.md)、[失敗分析](reports/24d_failure_analysis.md)及[凍結候選](reports/24d_final_candidate.md)，不可與本輪混稱同一切分。

```bash
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
