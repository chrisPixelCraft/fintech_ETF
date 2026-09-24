# Legacy 策略封存（V2–V5）

這裡放 AutoTS 之前的各代策略、測試、設定與報告。內容只搬移位置，沒有改寫；結果全是歷史研究證據，不是新的績效。正式提交一律維持 `BLOCK_SUBMISSION`。

## 各代最終狀態

| 世代 | 入口 | 最終狀態 |
|---|---|---|
| v1／v2 `x0352` | `best_v2.py` | 長期回放 309.16%，另一套資料口徑，不能代表 24 日賽期 |
| V3 24 日四輪（1,471 組） | `scripts/run_24d.py`、`tune_24d.py`、`expand_24d*.py` | `NO_ELIGIBLE_CANDIDATE` |
| V4 Stage 2 | `scripts/v4_*.py` | `NO_V4_WINNER`；保留 Stage 1 官方均價模擬器 |
| V5 四族研究 | `scripts/v5_*.py` | 程式與測試完成；官方成交資料未齊，沒有跑出任何結果 |

規格與報告在 [docs/](docs/README.md) 與 [reports/](reports/)。

## 目錄

- `src/`、`scripts/`、`tests/`：舊程式與測試
- `config/`、`configs/`：研究設定與凍結候選
- `reports/`：各輪報告
- `outputs/`：只追蹤測試要用的少數檔案，其餘輸出不進 Git
- `data`：指向 `../data` 的相對 symlink
  - 程式內的 `data/...` 路徑與雜湊清單鍵名因此不必更動
- `requirements*.txt`：舊版固定套件（Python 3.10）

## 執行測試

程式以 `legacy/` 為根目錄，指令需在此資料夾執行：

```bash
cd legacy
python -m unittest discover -s tests -q
```

搬移前後都是 295 個測試：292 通過、2 略過（V5 資料快取未建）、1 失敗。這個失敗在搬移前就存在：macOS 暫存目錄 `/var` 與 `/private/var` 路徑不一致（`test_report_24d`）。

## 已知限制

- 完整重驗 v2 發行清單或 V3 各輪結果需要原始輸出，這些輸出不在目前版本
- 發行清單以 `legacy/` 為根，`official_docs/`、`docs/data.md` 等路徑已不在原位
- 驗證器會拒絕解析到根目錄外的 symlink，所以透過 `data` 讀取的雜湊檢查會直接判定失敗，不會被放寬

## 完整證據

V1–V4 完整輸出、帳本與官方原始快取保存在提交 [`2769f876`](https://github.com/chrisPixelCraft/fintech_ETF/tree/2769f876ec4b9795ce5d8cc4e64b8274da58099c)。報告中指向 `outputs/` 的連結請在該版本查看。
