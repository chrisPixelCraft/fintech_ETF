# 文件導覽

保留固定 v2、v3 24 日研究與進行中的 V5。操作見[專案 README](../README.md)；資料、成交假設、期間不同，報酬不可互換。

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
| 主辦方原文 | [official_docs](../../official_docs/) |
| 保留內容與來源 | [發行清單](../config/best_v2_release.json) |

## 整理與恢復

2026-09-24 起不追蹤 `outputs/`；V1–V4 輸出、帳本、官方快取見 [`2769f876`](https://github.com/chrisPixelCraft/fintech_ETF/tree/2769f876ec4b9795ce5d8cc4e64b8274da58099c)。`reports/` 的 `outputs/` 連結須在該版開啟。

整理前： [`d613b75`](https://github.com/chrisPixelCraft/fintech_ETF/tree/d613b75)。含舊入口、設計、報告；Git 不含全部被忽略明細。

本機舊輸出、未完月度搜尋、Yahoo 研究封存於同層 `fintech_ETF_archive_best_v2_20260923/`，`cleanup_manifest.json` 記原路徑、檔數、位元組；非現行可執行版，不推送 Git。

更早研究見 `dfd5f84` 及同層 `fintech_ETF_research_archive_20260923/cleanup_manifest.json` ；封存非新績效證據。

## v2 驗證範圍

保留兩池 `x0352` 全期帳本及 `x0352`、`x0454`、`mx0010` 共 132 月度帳本（含未合格窗）。v1／0050 僅留設定、淨值、年度比較指標，未重建全部交易。

新驗證器重算保留帳本／比較值；舊搜尋完整性與排名仍屬歷史。發行清單留原路徑／雜湊，不將舊實驗改稱新驗證。

`src/` 舊名稱仍有共用依賴；runner 四個共用函式原樣抽至 `src/replay_context.py`。入口：v2 `best_v2.py`、v3 `scripts/run_24d.py`、分階段調參 `scripts/tune_24d.py`、第二輪 `scripts/expand_24d.py`、第三輪 `scripts/expand_24d_round3.py`、最新搜尋 `scripts/expand_24d_round4.py`。

## v3 驗證範圍

v3：Yahoo 日線、獨立重設 24 日帳本、凍結後評估；失敗留分母，原資料／日曆修訂／設定／帳本均有雜湊。帳務重算不能取代官方 Active Share、結算、公司行動、收件證據；正式使用須解除阻斷。
