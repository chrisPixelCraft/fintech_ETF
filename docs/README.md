# 文件導覽

專案保留固定 v2、v3 的 24 交易日研究，以及進行中的 V5 研究。操作看[專案 README](../README.md)；兩套研究的資料、成交假設與期間不同，不能直接互換報酬數字。

| 內容 | 文件 |
|---|---|
| V5 研究規格（進行中） | [四族研究規格](v5_spec.md)／[冠軍策略參考](champion.md) |
| 本輪調參規格 | [24 日調參](v3_tuning.md) |
| 最新可行性搜尋 | [384 組參數結果](../reports/24d_round4.md) |
| 第三輪分層搜尋 | [512 組參數結果](../reports/24d_round3.md) |
| 前輪追加搜尋 | [328 組參數結果](../reports/24d_expansion.md) |
| 前輪分階段調參 | [247 組比較報告](../reports/24d_tuning.md) |
| v3 研究規格 | [24 日規格](v3_spec.md) |
| 逐日規則與未知項 | [完整規則](v2_double_check_rules.md) |
| v3 研究結果 | [24 日摘要](../reports/24d_strategy_summary.md) |
| v2 規則與阻斷項 | [規則說明](rules.md) |
| v2 資料與偏誤 | [資料說明](data.md) |
| v2 績效比較 | [比較報告](../reports/comparison.md) |
| 主辦方原文 | [official_docs](../official_docs/) |
| 保留內容與來源 | [發行清單](../config/best_v2_release.json) |

## 整理與恢復

整理前版本為 [`d613b75`](https://github.com/chrisPixelCraft/fintech_ETF/tree/d613b75)。舊入口、研究設計與歷次報告可從該版本取得。Git 不包含當時被忽略的全部實驗明細。

本機舊輸出、未完成月度搜尋及 Yahoo 日線研究另封存在專案同層 `fintech_ETF_archive_best_v2_20260923/`，其中 `cleanup_manifest.json` 記錄原路徑、檔案數與位元組。封存不屬於目前可執行版本，也不會推送至 Git。

更早的歷史研究仍可由版本 `dfd5f84` 與既有同層 `fintech_ETF_research_archive_20260923/cleanup_manifest.json` 追溯。這些封存都不是新的績效證據。

## v2 驗證範圍

保留兩池 `x0352` 全期完整帳本，以及 `x0352`、`x0454`、`mx0010` 共 132 個月度比較帳本，包含未合格窗口。v1 與 0050 留存設定、淨值與指標供年度比較，未聲稱重建其全部交易。

新驗證器重算保留帳本與比較數字；舊搜尋的完整性及排名只屬歷史紀錄。發行清單保存整理前來源路徑及雜湊，不改寫舊實驗成新的驗證結果。

`src/` 中部分名稱源自舊研究，但仍是固定策略的共用依賴。原搜尋 runner 需要的四個共用函式已抽至 `src/replay_context.py`，函式內容保持一致。v2 入口為 `best_v2.py`；v3 原研究入口為 `scripts/run_24d.py`；分階段調參入口為 `scripts/tune_24d.py`；第二輪入口為 `scripts/expand_24d.py`；第三輪入口為 `scripts/expand_24d_round3.py`；最新可行性搜尋入口為 `scripts/expand_24d_round4.py`。

## v3 驗證範圍

v3 使用 Yahoo 日線、獨立重設的 24 日帳本及凍結後評估；失敗窗口保留在分母。原始資料、日曆修訂、設定與帳本均有雜湊。每個窗口的帳務重算不能代替官方 Active Share、結算、公司行動或平台收件證據；正式使用仍須通過這些阻斷項。
