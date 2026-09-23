# best_finetuned_double_check_v2

目前的研究入口是 [`v2_double_check_structural.py`](v2_double_check_structural.py)。第四輪調整持股數、現金緩衝與訊號期間後，80 組新設定在官方事後股票池均未通過已量測門檻，因此仍固定使用 `x0352`。它在 2025-01-02 至 2026-09-21 的兩個回測股票池通過**已量測**的逐日交易限制；Active Share、官方帳本與平台收件仍缺可核對的證據，正式 `plan` 維持 `BLOCK_SUBMISSION`，不產生可提交的 D-Plan。

## 年度績效比較

v2 與 v1 使用官方事後股票池，0050 是同期間 ETF 基準。下表採股利歸屬後淨值計算各年的區間報酬，避免回測帳本的期末股利入帳方式把 2025 年股利算入 2026 年。2026 年只有截至 9 月 21 日的資料，並非全年或年化報酬。

| 期間 | best_finetuned_double_check_v2 | v1 | 0050 |
|---|---:|---:|---:|
| 2025 年 | 78.95% | 66.36% | 33.46% |
| 2026 年截至 9 月 21 日 | 128.65% | 109.28% | 65.81% |

`best_finetuned_double_check_v2` 是研究候選 `x0352` 的展示名稱。第四輪保留了此候選，沒有改寫前三輪的發行檔。v1 與 0050 僅供研究對照：v1 有已量測違規，0050 不符合競賽的個股白名單與持股檔數要求。整段資料已用於開發，且 2026 年公布的股票池用於 2025 年有成分股前視偏誤；表中數字不是未見資料績效，也不能證明正式合規或未來可成交報酬。

年度數字依據[第四輪獲選月度帳本](outputs/v2_double_check_structural/monthly.csv)及[舊版對照月度帳本](outputs/full_tuned_v2/monthly.csv)之 `ending_economic_nav` 計算：2025 年末相對初始本金 10 億元，2026 年 9 月 21 日相對 2025 年末。完整期間的帳面報酬分別為 v2 **309.16%**、v1 **248.15%**、0050 **121.28%**；績效與限制見[第四輪結果](reports/v2_double_check_structural_report.md)。

2010–2024 年無法用相同參數與逐日規則算出完整年度報酬；逐年缺口見[2010–2024 年度查核](reports/x0352_2010_2024_report.md)。

一個月持有期另完成 88 次回放：月度目標候選 `x0454` 全期報酬為 **283.44%**，未勝過 `x0352`。官方事後池的 20 個重新建倉窗口，兩者分別通過 18 個與 15 個，不能宣稱任意起點都逐日合格。逐月報酬、10/26–11/27 歷史類比與 2010–2024 共 180 月的資料缺口見[月度比較報告](reports/monthly_horizon_report.md)；本輪維持 `x0352` 研究入口與正式提交阻擋。

最新單月擴搜新增 **72 組參數、665 次回放**。7 組通過全部 11 個訓練窗口，選出的固定候選 `mx0010` 訓練窗報酬中位數 **6.69%**、最差 **-17.99%**。它在兩池各通過 19/20 個冷啟動窗口，但兩池的全期診斷及 2026-07 窗口未通過研究門檻，因此**不替換 `x0352`**。參數差異、逐月比較與失敗原因見[單月擴搜報告](reports/monthly_expansion_report.md)；結果不代表每月最佳或官方合規認證。

## 安裝與驗證

使用 Python 3.10：

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/verify_double_check_structural.py
python v2_double_check_structural.py --verify-release
python v2_double_check_structural.py --show-config
```

重播固定參數，輸出目錄須尚未存在：

```bash
python v2_double_check_structural.py --track official_ex_post --output outputs/my_double_check_structural_replay
```

正式入口 `python v2_double_check_structural.py plan` 目前會回傳阻擋狀態；`research-plan` 也維持阻擋。本地研究重播不等於正式提交成功。

## 文件與證據

| 內容 | 連結 |
|---|---|
| 第四輪績效、年度比較與限制 | [結構搜尋結果](reports/v2_double_check_structural_report.md) |
| 2010–2024 年逐年報酬可行性 | [年度查核報告](reports/x0352_2010_2024_report.md) |
| 一個月持有期、逐月報酬與缺口 | [月度比較報告](reports/monthly_horizon_report.md) |
| 最新 72 組單月參數搜尋與採用判定 | [單月擴搜報告](reports/monthly_expansion_report.md)／[獨立稽核](outputs/monthly_expansion_20260923/audit.json) |
| 月策略的文獻與有限比較設計 | [研究依據](docs/monthly_strategy_research.md)／[實驗規格](docs/monthly_strategy_protocol.md) |
| 官方規則逐條核對及阻斷項 | [規則表](docs/v2_double_check_rules.md) |
| 81 組局部網格及停損規格 | [結構搜尋規格](docs/v2_double_check_structural_protocol.md) |
| 獨立驗證與最終帳本 | [結構搜尋稽核](outputs/v2_double_check_structural/audit.json) |
| 每日操作狀態 | [daily_auto 說明](daily_auto/README.md) |
| 主辦方原始文件 | [official_docs](official_docs/) |
| 前輪 `x0352` 細搜報告 | [第三輪結果](reports/v2_double_check_refinement_report.md) |
| 前輪 `x0352` 擴搜報告 | [擴搜結果](reports/v2_double_check_expansion_report.md) |
| 前輪 `d0034` 報告 | [前輪結果](reports/v2_double_check_fintuned_report.md) |
| 舊版 `f0019` 報告 | [原 full tuned v2 報告](reports/full_tuned_v2_report.md) |

本輪完整覆蓋四軸局部網格 81 個位置；1 組沿用已稽核的 `x0352`，80 組新設定各在兩個股票池回放，共 160 次新試驗。連同前三輪累計 1,569 組不同設定。官方事後池沒有任何新候選通過全部已量測門檻，故未換參；其中 79 組有無有效交易計畫的日子、78 組有持股價格資料過期的日子，原因可能重疊。`x0352` 兩池皆為零超限、零警告、零未成交與零成交價格保護超界。官方池仍有 13 筆模擬成交超過該股票當日總成交量；文件未訂成交量上限，但這些報酬不能當作真實市場可成交績效。完整全域與連續參數沒有窮舉；正式使用仍須先補齊[規則表](docs/v2_double_check_rules.md)所列官方證據。
