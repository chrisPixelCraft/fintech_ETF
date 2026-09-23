# AI CUP Trading Agent

目前固定版本是 **Full tuned v2，候選 f0019**。已完成 405 組參數、兩個股票池，共 810 次回放與獨立稽核。每日流程使用相同固定設定，不會每天重新挑歷史冠軍。v3 已退出現行方向，原程式與結果保留。

## 從這裡開始

| 用途 | 入口 |
|---|---|
| 結果、月報酬、回撤及限制 | [完整比較報告](reports/full_tuned_v2_report.html)／[Markdown](reports/full_tuned_v2_report.md) |
| 交易策略簡報 | [PowerPoint](reports/full_tuned_v2_deck/full_tuned_v2_strategy.pptx) |
| 固定策略 | [v2_offcial_best_deep_tuning.py](v2_offcial_best_deep_tuning.py) |
| 每天照做 | [daily_auto 入口](daily_auto/README.md)／[固定策略流程](daily_auto/full_tuned_workflow.md) |
| 外部證據缺口 | [官方證據狀態](daily_auto/official_evidence_status.md) |

## 先看結果的邊界

期間：2025-01-02 至 2026-09-21，共 417 個交易日。使用前日訊號、次日官方均價模型，扣除費稅，以帳面 NAV 比較。

| 股票池／版本 | 淨報酬 | 最大回撤 | 已量測超限日 |
|---|---:|---:|---:|
| 正式名單事後池／Full tuned v2 | 302.35% | 25.95% | 0 |
| 正式名單事後池／v1 | 248.15% | 21.81% | 7 |
| 歷史池／同參數 Full tuned v2 | 163.94% | 20.10% | 0 |
| 歷史池／v1 | 173.54% | 19.55% | 9 |
| 同預算 0050 | 121.28% | 25.79% | 不適用 |

現在使用官方 150 檔合法；把 2026 年名單回套 2025 年會有成分股前視偏誤。全部歷史都已參與開發，歷史池亦非未見測試。0050 預留初始資金緩衝，不是 100% 投資的總報酬指數。股利在模擬期末入帳，9 月只到 21 日。

**研究稽核 PASS 不代表正式競賽認證。** Active Share 完整資料與口徑、公司事件委託語意、真實官方帳本及提交回執尚未完整。daily_auto 會阻擋缺少證據的送件；目前未啟用常駐排程或正式上傳。

## 本機執行

```bash
# 顯示已稽核的固定參數
python3 v2_offcial_best_deep_tuning.py --show-config

# 固定版本重播；拒絕覆寫既有輸出
python3 v2_offcial_best_deep_tuning.py --track official_ex_post --output outputs/full_tuned_reproduction

# 每日固定資料計算與產單入口
python3 v2_offcial_best_deep_tuning.py plan --help
```

每日執行的完整參數與官方帳本驗證方式見 [操作指南](daily_auto/full_tuned_workflow.md)。本機產檔、研究重播及本機測試都不會被計作主辦方成功收件。

## 證據與歷史

| 內容 | 入口 |
|---|---|
| 官方文件逐份核對 | [規則稽核](docs/official_docs_rule_audit.md)／[十份文件清單](daily_auto/reference/inventory.json) |
| 本輪搜尋設計 | [研究協定](docs/official_deep_protocol.md)／[完整設定](config/official_deep_study.json) |
| 810 次完整結果 | [trials.csv](outputs/full_tuned_v2/trials.csv) |
| 獨立帳務、公式、前綴稽核 | [audit.json](outputs/full_tuned_v2/audit.json) |
| 前輪官方文件複核 | [原 A 與相容版報告](reports/official_v2_reaudit.html) |
| 更早版本 | [舊導覽快照](docs/archive/README_before_official_review_20260922.md) |

原 v2_A_best、B、C 與 v3 程式不被本輪入口覆寫。舊報告應依其版本口徑閱讀。
