# 文件導覽

目前只保留 `x0352` 固定策略與相關比較。操作看[專案 README](../README.md)，數字看[比較報告](../reports/comparison.md)。

| 內容 | 文件 |
|---|---|
| 規則與阻斷項 | [規則說明](rules.md) |
| 資料與偏誤 | [資料說明](data.md) |
| 主辦方原文 | [official_docs](../official_docs/) |
| 保留內容與來源 | [發行清單](../config/best_v2_release.json) |

## 整理與恢復

整理前版本為 [`d613b75`](https://github.com/chrisPixelCraft/fintech_ETF/tree/d613b75)。舊入口、研究設計與歷次報告可從該版本取得。Git 不包含當時被忽略的全部實驗明細。

本機舊輸出、未完成月度搜尋及 Yahoo 日線研究另封存在專案同層 `fintech_ETF_archive_best_v2_20260923/`，其中 `cleanup_manifest.json` 記錄原路徑、檔案數與位元組。封存不屬於目前可執行版本，也不會推送至 Git。

更早的歷史研究仍可由版本 `dfd5f84` 與既有同層 `fintech_ETF_research_archive_20260923/cleanup_manifest.json` 追溯。這些封存都不是新的績效證據。

## 驗證範圍

保留兩池 `x0352` 全期完整帳本，以及 `x0352`、`x0454`、`mx0010` 共 132 個月度比較帳本，包含未合格窗口。v1 與 0050 留存設定、淨值與指標供年度比較，未聲稱重建其全部交易。

新驗證器重算保留帳本與比較數字；舊搜尋的完整性及排名只屬歷史紀錄。發行清單保存整理前來源路徑及雜湊，不改寫舊實驗成新的驗證結果。

`src/` 中部分名稱源自舊研究，但仍是固定策略的共用依賴。原搜尋 runner 需要的四個共用函式已抽至 `src/replay_context.py`，函式內容保持一致；僅保留一個公開策略入口。
