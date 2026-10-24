# Legacy 封存

正式策略已改為 K2＋排行榜切換（見 [docs/strategy.md](../docs/strategy.md)），由根目錄 `./run_daily.sh` 執行 `production/`；20 日動能 Mom20 為前一版正式策略。本區封存 V1–V5 規則策略與 ML（AutoTS、LightGBM、Hybrid、動能改良），結果僅為歷史證據。封存時只搬位置，未改內容。

## 各時期一覽

| 時期 | 做了什麼 | 結果 | 說明 |
|---|---|---|---|
| v1／v2 `x0352` | • 固定參數長期回放 | • 全期 309.16%<br>  ↳ 另一套資料口徑<br>• 不代表 24 日賽期 | `best_v2.py` |
| V3 | • 改做 24 日賽期<br>• 搜尋 1,471 組 | • 無一組全合規<br>  ↳ `NO_ELIGIBLE_CANDIDATE` | [docs/](docs/README.md) |
| V4 | • 官方均價管線<br>• 帳本驗證 | • `NO_V4_WINNER`<br>• 現行帳本源自此版 | [docs/](docs/README.md) |
| V5 | • 參考冠軍策略<br>• 設計四族策略 | • 官方資料沒下載齊<br>• 沒跑出結果 | [docs/](docs/README.md) |
| ML | • AutoTS、LightGBM<br>• Hybrid、每日 v2<br>• 動能改良 8 項 | • 全部沒贏 Mom20<br>  ↳ 最終測試最佳 −1.17%<br>• 動能改良也沒換掉 | [ml/README.md](ml/README.md) |

V1–V5 正式提交均為 `BLOCK_SUBMISSION`。規格／報告：[docs/README.md](docs/README.md)、[reports/](reports/)；v2 回放：[規則](docs/rules.md)、[資料](docs/data.md)。

## V1–V5

### 目錄

- `src/`、`scripts/`、`tests/`
	- V3：`scripts/run_24d.py`、`tune_24d.py`、`expand_24d*.py`
	- V4：`scripts/v4_*.py`
	- V5：`scripts/v5_*.py`
- `config/`、`configs/`：設定與凍結候選
- `reports/`：報告
- `outputs/`：測試必要檔案
- `data` → `../data` symlink
	- 保留舊路徑與雜湊鍵
- `requirements*.txt`：Python 3.10 套件

### 執行測試

程式以 `legacy/` 為根目錄：

```bash
cd legacy
python -m unittest discover -s tests -q
```

295 個測試：292 通過、2 略過（V5 快取未建）、1 搬移前既有失敗 `test_report_24d`（macOS `/var`、`/private/var` 差異）。

### 限制

重驗所需 v2 發行清單與 V3 各輪原始輸出不在目前版本。發行清單以 `legacy/` 為根，`official_docs/` 等已搬離；經 `data` 的雜湊檢查會因根外 symlink 被拒絕，刻意不放寬。

### 完整證據

`main` 僅一個起始提交；舊歷史存於 tags：

- `archive/pre-cleanup`：整理前完整歷史
- `archive/v1-v4-evidence`：`2769f876`
- `archive/docs-index-d613b75`：`d613b75`

取得：`git fetch origin tag archive/pre-cleanup`。

V1–V4 輸出、帳本與官方快取見 [`2769f876`](https://github.com/chrisPixelCraft/fintech_ETF/tree/2769f876ec4b9795ce5d8cc4e64b8274da58099c)；`outputs/` 連結須在該版查看。

## ML 時期

程式要回舊提交執行，步驟見 [ml/README.md](ml/README.md#怎麼重跑)。

- AutoTS、LightGBM、Hybrid：`05745bf`
- 動能改良 `momv2/`：`6d2a7c4`
