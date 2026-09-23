# best_finetuned_double_check_v2

唯一研究入口為 [`best_v2.py`](best_v2.py)，固定使用 `x0352`。2025-01-02–2026-09-21 的帳面總報酬為 **309.16%**；這是開發期回測，正式提交仍為 **`BLOCK_SUBMISSION`**。

## 績效比較

年度欄採股利歸屬後的經濟淨值；2026 年只到 9 月 21 日，非全年或年化報酬。

| 期間 | v2 `x0352` | v1 | 0050 |
|---|---:|---:|---:|
| 2025 年 | 78.95% | 66.36% | 33.46% |
| 2026 年至 9/21 | 128.65% | 109.28% | 65.81% |
| 全期帳面報酬 | 309.16% | 248.15% | 121.28% |

完整年度、單月重新建倉與賽期類比見[比較報告](reports/comparison.md)。v1 與 0050 只作對照；月度候選 `x0454`、`mx0010` 未提供足以替換主策略的證據，相關失敗窗口仍完整保留。

`x0352` 的兩池全期回放通過已量測的逐日門檻，但不保證任意單月起點都合格。Active Share、官方結算與平台收件證據仍缺。官方事後股票池存在前視偏誤，模擬成交未計滑價與市場衝擊，不能視為未見資料或可實現績效。

## 使用與驗證

Python 3.10：

```bash
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python best_v2.py --verify-release
python best_v2.py --show-config
```

重播固定策略，輸出目錄必須尚未存在：

```bash
python best_v2.py --track official_ex_post --output outputs/my_v2_replay
python best_v2.py --track historical_pit --output outputs/my_v2_historical_replay
```

獨立重算保留帳本與比較數值：

```bash
python scripts/verify_best_v2.py --rebuild
python scripts/report_best_v2.py --verify
python -m unittest discover -s tests -q
```

`python best_v2.py plan` 與 `research-plan` 均回傳阻擋，不輸出 D-Plan。這個版本保留原始日內資料需求；它不是純日線策略。

## 保留內容

| 內容 | 位置 |
|---|---|
| 固定策略入口 | [best_v2.py](best_v2.py) |
| 參數與完整帳本 | [官方事後池](outputs/best_v2/official_ex_post/)／[歷史池](outputs/best_v2/historical_pit/) |
| 年度與月度對照 | [比較報告](reports/comparison.md)／[比較資料](outputs/comparisons/) |
| 發行雜湊與來源 | [保留清單](config/best_v2_release.json) |
| 獨立稽核 | [稽核結果](outputs/best_v2/audit.json) |
| 規則與資料限制 | [文件導覽](docs/README.md) |
| 主辦方原始文件 | [official_docs](official_docs/) |

舊策略入口、搜尋工具、重複報告及未採用候選的搜尋明細已退出目前版本。整理前的 Git 版本與本機封存方式見[整理說明](docs/README.md)。精簡版只驗證保留內容，不宣稱重跑完整參數搜尋或重新證明全域最佳。
