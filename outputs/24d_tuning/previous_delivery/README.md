# ETF 策略研究

v3 研究的是每次從 **10 億元現金、零持股出發的 24 交易日報酬**。固定 v2 的 **309.16%** 是另一套資料與成交口徑下的長期回測，不能代替短賽期證據。正式使用維持 **`BLOCK_READY`／`BLOCK_SUBMISSION`**。

## v3：24 交易日研究

依 [v3 規格](docs/v3_spec.md)與[逐日規則](docs/v2_double_check_rules.md)，從 `x0352_daily_baseline` 做有限鄰域搜尋。先驗證基準，僅使用開發與驗證窗口選參，再凍結設定並評估保留資料。

本輪 28 組參數沒有候選達到開發與驗證窗口 **100% 已量測通過**的採用門檻，結論為 `NO_ELIGIBLE_CANDIDATE`。凍結的基準僅作診斷參考，不能直接提交比賽。

| 基準用途 | 年份 | 已量測通過／全部 | 通過窗口報酬中位數 |
|---|---|---:|---:|
| 開發 | 2010–2019 | 78/119 | 1.92% |
| 驗證選參 | 2020–2022 | 24/35 | 3.40% |
| 凍結後評估 | 2023–2024 | 18/23 | 5.94% |

報酬欄僅描述通過且完整的窗口，不能忽略其餘失敗。另有 3 個跨切分邊界窗口，僅作診斷。切分採規格第 1 節；第 19 節的另一組建議切分只作補充描述，不重新選參。

2010–2024 保留 180 個月初窗口，加上 2025–2026 年 8 月的 20 個近期窗口，共 200 個。滾動測試延伸至 2026 年 8 月，另有 16 個歷年 10–11 月類比。窗口會重疊，不能當作獨立樣本。近期 2025–2026 資料僅作凍結後壓力測試。每日替換 2 檔與延伸窗口的補充測試在主選參凍結後加入，結果不回流選參。

| 內容 | 連結 |
|---|---|
| 結論與比較 | [研究摘要](reports/24d_strategy_summary.md) |
| 基準與搜尋 | [日線基準](reports/24d_x0352_baseline.md)／[參數搜尋](reports/24d_parameter_search.md) |
| 驗證與近期市場 | [驗證結果](reports/24d_validation.md)／[近期結果](reports/24d_recent_regime.md) |
| 保留資料與季節類比 | [凍結後結果](reports/24d_holdout.md)／[10–11 月類比](reports/24d_oct_nov_analogs.md) |
| 失敗與使用限制 | [失敗分析](reports/24d_failure_analysis.md)／[凍結候選](reports/24d_final_candidate.md) |
| 設定與證據 | [最終設定](configs/competition_24d_final.json)／[設定解讀](configs/competition_24d_final_metadata.json)／[稽核](outputs/24d/audit.json)／[輸出雜湊](outputs/24d/run_manifest.json) |

研究採 Yahoo 日線，D−1 決策、次日開盤價近似成交，納入雙邊手續費 0.1425% 與賣出稅 0.3%。每日檢查持股數、權重、現金、整張與禁止超賣／當沖；失敗窗口保留在分母，未完成窗口不填造 24 日報酬。採用候選須通過全部已量測門檻，不能只挑高報酬窗口。

本研究標記為 `COMPETITION_UNIVERSE_STRESS_TEST`：2026 白名單回套歷史，存在存活與成分前視偏誤；原 x0352 參數也曾由 2025–2026 資料選出，因此 2023–2024 僅對**本輪搜尋**保留，並非整個策略家族從未接觸後期資訊的前瞻證據。

Active Share 仍為 `ACTIVE_SHARE_NOT_VERIFIED`；官方結算、公司行動、最新公告與收件證據仍缺。開盤價模擬沒有市場衝擊模型，部分委託超過當日成交量。Yahoo 修訂與拆股資料完整性亦未獲官方核對。這些限制都不能由內部稽核通過取代。

## 重現與驗證

Python 3.10：

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-yahoo.txt
python scripts/verify_24d.py
python scripts/report_24d.py --verify
python -m unittest discover -s tests -q
```

使用凍結的 Yahoo 快照另建輸出重算；以下指令依序執行：

```bash
python scripts/run_24d.py all --workers 4 --cache data/yahoo_daily/v3_20260923 --output outputs/24d_replay
python scripts/supplement_24d.py --workers 2 --main outputs/24d_replay --output outputs/24d_replay_supplement
python scripts/complete_24d_diagnostics.py --workers 2 --main outputs/24d_replay --supplement outputs/24d_replay_supplement --output outputs/24d_replay_diagnostics
```

完整重算已保存的帳本可執行 `python scripts/verify_24d.py --rebuild`。原始 Parquet 與獨立的名目股數換算檔分開保存；[日曆修訂](data/yahoo_daily/v3_20260923/calendar_v2.json)補回 0050 缺資料但股票市場仍交易的日期。`repair=True` 可能由 yfinance 內部使用更細頻資料修復；策略本身只使用日線，沒有 4H 依賴。

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
