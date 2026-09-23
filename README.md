# best_finetuned_double_check_v2

目前的研究版本是 [`v2_double_check_fintuned.py`](v2_double_check_fintuned.py)，固定使用候選 `d0034`。它在 2025-01-02 至 2026-09-21 的兩個回測股票池通過**已量測**的逐日交易限制；Active Share、官方帳本與平台收件仍缺可核對的證據，正式 `plan` 因此維持 `BLOCK_SUBMISSION`，不產生可提交的 D-Plan。

## 年度績效比較

三個策略使用相同的官方事後股票池與期間。下表採股利歸屬後淨值計算各年的區間報酬，避免回測帳本的期末股利入帳方式把 2025 年股利算入 2026 年。2026 年只有截至 9 月 21 日的資料，並非全年或年化報酬。

| 期間 | best_finetuned_double_check_v2 | v1 | 0050 |
|---|---:|---:|---:|
| 2025 年 | 79.11% | 66.36% | 33.46% |
| 2026 年截至 9 月 21 日 | 115.79% | 109.28% | 65.81% |

`best_finetuned_double_check_v2` 是本版 `v2_double_check_fintuned` 的展示名稱，使用獲選參數 `d0034`。v1 與 0050 僅供研究對照：v1 有已量測違規，0050 不符合競賽的個股白名單與持股檔數要求。整段資料已用於開發，且 2026 年公布的股票池用於 2025 年有成分股前視偏誤；表中數字不是未見資料績效，也不能證明正式合規或未來可成交報酬。

年度數字依據獲稽核的 [新版月度帳本](outputs/v2_double_check_fintuned/monthly.csv)及[舊版對照月度帳本](outputs/full_tuned_v2/monthly.csv)之 `ending_economic_nav` 計算：2025 年末相對初始本金 10 億元，2026 年 9 月 21 日相對 2025 年末。完整期間的帳面報酬分別為 v2 **286.50%**、v1 **248.15%**、0050 **121.28%**；績效與限制見[完整結果](reports/v2_double_check_fintuned_report.md)。

## 安裝與驗證

使用 Python 3.10：

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/verify_double_check_release.py
python v2_double_check_fintuned.py --verify-release
python v2_double_check_fintuned.py --show-config
```

重播固定參數，輸出目錄須尚未存在：

```bash
python v2_double_check_fintuned.py --track official_ex_post --output outputs/my_double_check_replay
```

正式入口 `python v2_double_check_fintuned.py plan` 目前會回傳阻擋狀態。`research-plan` 只產生標明 `NEVER_SUBMIT` 的研究草稿，不能當正式提交成功。

## 文件與證據

| 內容 | 連結 |
|---|---|
| 新版績效、圖表與限制 | [完整結果](reports/v2_double_check_fintuned_report.md) |
| 官方規則逐條核對及阻斷項 | [規則表](docs/v2_double_check_rules.md) |
| 搜尋範圍及實驗口徑 | [實驗規格](docs/v2_double_check_protocol.md) |
| 獨立驗證與最終帳本 | [新版稽核](outputs/v2_double_check_fintuned/audit.json) |
| 每日操作狀態 | [daily_auto 說明](daily_auto/README.md) |
| 主辦方原始文件 | [official_docs](official_docs/) |
| 舊版 `f0019` 報告 | [原 full tuned v2 報告](reports/full_tuned_v2_report.md) |

本輪完成 836 組不同參數、兩個股票池共 1,672 次回放；只有宣告的 128 組局部網格完整窮舉。獲選版本在已量測門檻下零超限、零警告、零未成交與零成交價格保護超界。完整離散全域與連續參數沒有窮舉；正式使用仍須先補齊[規則表](docs/v2_double_check_rules.md)所列官方證據。
