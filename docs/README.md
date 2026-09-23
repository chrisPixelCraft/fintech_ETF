# 文件導覽

目前的策略入口與績效以[專案 README](../README.md)為準；研究結果集中在 `reports/`，這裡保留規則、資料說明及實驗規格。

## 先看這些

| 要查什麼 | 文件 |
|---|---|
| 規則與阻斷項 | [現行規則表](v2_double_check_rules.md) |
| 既有回測資料 | [2025 至今資料稽核](data_audit_2025_to_now.md) |
| 每日操作 | [daily_auto 說明](../daily_auto/README.md) |
| 單月策略結果 | [單月擴搜報告](../reports/monthly_expansion_report.md) |
| 月度研究依據 | [研究與來源](monthly_strategy_research.md) |
| 主辦方原文 | [official_docs](../official_docs/) |

規則表說明本地可檢查的範圍與仍缺的官方證據；研究回測通過不等於正式合規。

## 重現實驗時再看

這些規格記錄各輪當時的搜尋範圍與判定方法，已綁定實驗來源雜湊。它們不是多個可任意切換的正式策略，也不應合併後回填成舊實驗的原始輸入。

| 實驗 | 凍結規格 |
|---|---|
| 原 full tuned v2 | [原始搜尋](official_deep_protocol.md) |
| Double-check 第一輪 | [搜尋與驗證](v2_double_check_protocol.md) |
| Double-check 第二輪 | [局部擴搜](v2_double_check_expansion_protocol.md) |
| Double-check 第三輪 | [局部細搜](v2_double_check_refinement_protocol.md) |
| Double-check 第四輪 | [結構搜尋](v2_double_check_structural_protocol.md) |
| 月度比較 | [量測規格](monthly_strategy_protocol.md) |
| 單月參數擴搜 | [搜尋規格](monthly_expansion_protocol.md)／[建倉診斷](monthly_expansion_design.md) |

## 已移除的舊說明

下列文件已退出目前文件集。需要追溯時，可讀取整理前固定版本；它們的舊入口、測試數或規則文字不作現行依據。

| 舊文件 | 現在看哪裡 | 歷史全文 |
|---|---|---|
| 舊版發行說明 | [專案 README](../README.md) | [Git 版本](https://github.com/chrisPixelCraft/fintech_ETF/blob/f4334ff27d4f3929e0ac2b83ed72c5973082d3c0/docs/fintune_v2_release.md) |
| 第二輪規劃器筆記 | [現行規則表](v2_double_check_rules.md) | [Git 版本](https://github.com/chrisPixelCraft/fintech_ETF/blob/f4334ff27d4f3929e0ac2b83ed72c5973082d3c0/docs/compliance_planner_2nd.md) |
| 舊官方規則摘要 | [現行規則表](v2_double_check_rules.md) | [Git 版本](https://github.com/chrisPixelCraft/fintech_ETF/blob/f4334ff27d4f3929e0ac2b83ed72c5973082d3c0/docs/official_docs_rule_audit.md) |

更早的整理前程式版本為 [`dfd5f84`](https://github.com/chrisPixelCraft/fintech_ETF/tree/dfd5f84)。當時排除的大型實驗明細另存於專案同層 `fintech_ETF_research_archive_20260923/`，清單為 `cleanup_manifest.json`；Git 歷史不包含完整的 810 次逐筆帳本。`verify_fintune_v2.py` 檢查目前留存的精簡版證據，不代表重驗全部封存試驗。
