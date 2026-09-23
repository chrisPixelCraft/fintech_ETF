# Fintune v2

唯一日常策略入口是 **fintune_v2.py**，固定使用 full tuned v2 的 `f0019` 參數。它直接呼叫原本已稽核的實作，不重新選參、不人工改排名。

**可執行研究回測與本機產單；正式提交尚未接通。** 302.35% 是使用全期結果選參後的開發績效，不能當未見資料績效。「最佳」只指既有搜尋與已量測門檻下的獲選配置。

## 安裝與執行

使用 Python 3.10：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/verify_fintune_v2.py
python fintune_v2.py --show-config
python -m unittest discover -s tests -v
```

重播固定策略，輸出目錄必須尚未存在：

```bash
python fintune_v2.py --track official_ex_post --output outputs/my_fintune_v2
python fintune_v2.py --track historical_pit --output outputs/my_historical_sensitivity
```

日常產單：

```bash
python fintune_v2.py plan --help
```

完整輸入與操作順序見 [每日流程](daily_auto/full_tuned_workflow.md)。缺少真實官方帳本、AS 或成功回執時維持 `BLOCK_SUBMISSION`；研究示範不會冒充正式送件。

## 只看這些結果

| 內容 | 入口 |
|---|---|
| 績效與比較 | [最終報告](reports/full_tuned_v2_report.html) |
| 偏誤與可信度 | [可信度稽核](reports/full_tuned_v2_credibility_audit.html) |
| 策略簡報 | [PowerPoint](reports/full_tuned_v2_deck/full_tuned_v2_strategy.pptx) |
| 每日工作 | [daily_auto](daily_auto/README.md) |
| 比賽原文 | [official_docs](official_docs/) |
| 保留檔案說明 | [版本整理](docs/fintune_v2_release.md) |

最終比較仍保留 v1、0050 作對照，但不保留它們的獨立操作入口。正式事後池 v2 報酬 302.35%、回撤 25.95%；歷史重建池報酬 163.94%、回撤 20.10%。兩池都參與過開發，沒有未見驗證。

## 檔案範圍

`data/` 只保留固定回放需要的凍結資料及來源摘要；`outputs/` 只保留本輪最終帳本、比較、選參摘要與稽核。新產生的資料、交易包及回測目錄仍由 `.gitignore` 排除。此版本會將必要的凍結檔明確納入 Git，下載後不依賴本機舊實驗資料夾。

部分共用模組仍使用 `tuning_2nd`、`a_deep` 等舊名稱，因為它們是目前策略或獨立稽核的依賴。不要依名稱再次刪除，也不要任意修改雜湊綁定的原始程式。

整理前的完整程式、文件與報告已推送至提交 `dfd5f84`：**original v1+ v2 + 調參**。大型歷史明細未上傳 Git，已移出專案封存；恢復完整舊輪次研究需另取封存資料，日常固定策略不需要它們。
