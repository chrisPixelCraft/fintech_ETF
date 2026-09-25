# Legacy 封存

這裡放正式策略以外的所有舊研究。正式策略是 20 日動能（Mom20），每天由根目錄 `./run_daily.sh` 跑，程式在 `production/`。

- 這裡的結果都是歷史證據
  - 不是新的績效
- 內容只搬位置，沒改寫
- 分兩個時期
  - V1–V5：規則式策略
  - ML：AutoTS、LightGBM、Hybrid

## 各時期一覽

| 時期 | 做了什麼 | 結果 | 說明 |
|---|---|---|---|
| v1／v2 `x0352` | • 固定參數長期回放 | • 全期 309.16%<br>  ↳ 另一套資料口徑<br>• 不代表 24 日賽期 | `best_v2.py` |
| V3 | • 改做 24 日賽期<br>• 搜尋 1,471 組 | • 無一組全合規<br>  ↳ `NO_ELIGIBLE_CANDIDATE` | [docs/](docs/README.md) |
| V4 | • 官方均價管線<br>• 帳本驗證 | • `NO_V4_WINNER`<br>• 現行帳本源自此版 | [docs/](docs/README.md) |
| V5 | • 參考冠軍策略<br>• 設計四族策略 | • 官方資料沒下載齊<br>• 沒跑出結果 | [docs/](docs/README.md) |
| ML | • AutoTS、LightGBM<br>• Hybrid、每日 v2 | • 全部輸 Mom20<br>  ↳ 最佳 −1.17% | [ml/README.md](ml/README.md) |

- V1–V5 規格與報告
  - [docs/README.md](docs/README.md)、[reports/](reports/)
- v2 回放規則與資料
  - [docs/rules.md](docs/rules.md)、[docs/data.md](docs/data.md)
- 正式提交一律 `BLOCK_SUBMISSION`
  - 指 V1–V5 各版

## V1–V5

### 目錄

- `src/`、`scripts/`、`tests/`：程式與測試
  - V3：`scripts/run_24d.py`、`tune_24d.py`、`expand_24d*.py`
  - V4：`scripts/v4_*.py`
  - V5：`scripts/v5_*.py`
- `config/`、`configs/`：設定與凍結候選
- `reports/`：各輪報告
- `outputs/`：只留測試要用的檔案
- `data`：指向 `../data` 的 symlink
  - 舊路徑與雜湊鍵名不必改
- `requirements*.txt`：Python 3.10 套件

### 執行測試

程式以 `legacy/` 為根目錄：

```bash
cd legacy
python -m unittest discover -s tests -q
```

- 共 295 個測試
  - 292 通過
  - 2 略過：V5 快取未建
  - 1 失敗：搬移前就有
- 失敗的是 `test_report_24d`
  - macOS `/var` 與 `/private/var` 不一致

### 已知限制

- 完整重驗需要原始輸出
  - v2 發行清單、V3 各輪
  - 這些輸出不在目前版本
- 發行清單以 `legacy/` 為根
  - `official_docs/` 等已不在原位
- 經 `data` 的雜湊檢查會失敗
  - 驗證器拒絕根目錄外 symlink
  - 刻意不放寬

### 完整證據

V1–V4 完整輸出、帳本與官方原始快取在提交 [`2769f876`](https://github.com/chrisPixelCraft/fintech_ETF/tree/2769f876ec4b9795ce5d8cc4e64b8274da58099c)。報告中指向 `outputs/` 的連結請在該版本查看。

## ML 時期

程式只能在提交 `05745bf` 執行，步驟見 [ml/README.md](ml/README.md#怎麼重跑)。
