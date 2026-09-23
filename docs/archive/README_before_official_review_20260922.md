# 台股趨勢動能回測

最新交付為 **v3 弱市實驗**：按[使用者規格](docs/AI_CUP_Trading_Agent_v3.md)實作 A 固定對照、B 低 Beta／低波動、C 殘差動能、D 等百分位融合；兩軌八次固定回放完成，沒有新增調參或自動策略選擇。查看[完整圖表報告](reports/v3_weak_market_report.html)、[Markdown](reports/v3_weak_market_report.md)、[每月比較](reports/v3_weak_market_assets/monthly_comparison.csv)及[凍結規格](docs/v3_protocol.md)。

歷史池 A／B／C／D 報酬 **192.21%／166.57%／171.34%／150.54%**，MDD **23.67%／21.08%／22.30%／20.43%**。B／D 平均弱月少跌，但四版均只有 **1／6 個弱月正報酬**；2026 年 7 月各新版反而比 A 差。2026 年 3 月没有事前弱市交易日，7 月只有 4 日，因此不能把問題全歸因於選股排名。

v3 兩軌独立因子／帳務稽核、六次實體截斷與 161 項測試通過。八策略零已量測硬性違規，Active Share 仍 UNKNOWN／BLOCK_SUBMISSION。正式名單事後情境分開報告；不作未見驗證或實盤容量認證。A 對照固定公開 `v2_A_best.py` 的 p052／p049，並非 deep A 冠軍。

```bash
python3 scripts/run_v3_study.py --output outputs/v3_weak_market_reproduction
python3 scripts/audit_v3.py --output outputs/v3_weak_market_reproduction/historical_pit
python3 scripts/audit_v3.py --output outputs/v3_weak_market_reproduction/official_ex_post
```

上一輪為 **A 策略深入調參**：13 個搜尋軸，兩個股票池各 265 組，共 **530 次正式回測**。期間 2025-01-02 至 2026-09-21，417 個完整交易日。查看[完整圖表報告](reports/a_deep_tuning_report.html)、[Markdown 報告](reports/a_deep_tuning_report.md)、[參數檢查](docs/a_deep_parameter_audit.md)與[全部 530 組比較表](reports/a_deep_tuning_assets/all_trials.csv)。

歷史池報酬最佳 a0036：**200.33%／MDD 24.95%**；回撤不高於原 A 的最佳 a0016：**196.19%／23.06%**；嚴格 4H 最佳 a0139：**195.05%／22.32%**。原 A 為 192.21%／23.67%，所以最高報酬版並未降低回撤。正式名單事後情境 r0050 為 360.04%／23.69%，另有成分股前視偏誤，不能當成 2025 年可部署績效。

新入口 [v2_A_deep_best.py](v2_A_deep_best.py) 固定已選參數；原 A／B／C 入口保持原設定。兩軌獨立帳務與截斷回放、132 項測試、六次公開入口重播均通過。新候選零已量測硬性違規，但 Active Share 仍 UNKNOWN／BLOCK_SUBMISSION；全期已用於開發，沒有未見樣本外績效證據。

```bash
python3 v2_A_deep_best.py --show-config
python3 v2_A_deep_best.py --choice risk_controlled --output outputs/a_deep_risk_reproduction
python3 v2_A_deep_best.py --choice strict_winner --output outputs/a_deep_strict_reproduction
python3 v2_A_deep_best.py --track official_ex_post --output outputs/a_deep_official_reproduction
```

前一輪規則優先搜尋：兩個股票池分開，各 A／B／C 64 組，共 **384 次正式回測**。完整[報告與圖表](reports/tuning_report_2nd_try.html)、[Markdown 報告](reports/tuning_report_2nd_try.md)、[384 組候選總表](outputs/tuning_report_2nd_try/all_candidate_results.csv)。

[A](v2_A_best.py)、[B](v2_B_best.py)、[C](v2_C_best.py) 預設採歷史股票池的 p052／p014／p024，扣成本報酬為 **192.21%／186.07%／183.55%**。正式名單事後情境另固定 p049／p049／p033，報酬為 **277.51%／277.51%／269.66%**；此情境有成分股前視偏誤。兩份結果均通過獨立帳務稽核與公開入口重播驗證。

入選資格要求零已量測硬性違規，但 Active Share 仍為 UNKNOWN，全部維持 `BLOCK_SUBMISSION`。所有最佳參數都是 EX_POST_DEVELOPMENT；不能稱為未見樣本外績效。A 的歷史最佳組合換股分差為 0、4H 只檢查覆蓋，415／417 日有交易，不能視為原始低換手嚴格確認版本。先前 p005／p006 的交付及原結果保留在[第一輪固定報告](reports/v2_rule_first_final.md)。

先前調參研究已完成 **A／B／C 各 64 組，共 192 組**。查看[完整互動比較表](reports/v2_abc_tuning.html)、[調參報告](reports/v2_abc_tuning.md)與[全部候選 CSV](outputs/v2_abc_tuning_20260922/trial_summary.csv)。事後最高報酬不等於可採用策略；向前選參數未改善硬性超限，仍維持 HOLD／BLOCK_SUBMISSION。原始 v2 報告及參數保留。

原始 v2 交付為 [v2 策略](docs/strategy_v2_implemented.md)、[最終比較報告](reports/backtest_v2_final.md) 與 [PDF](reports/backtest_v2_final.pdf)。研究期間為 2025-01-02 至 2026-09-21，共 417 個交易日，本金 10 億元，跨年連續持倉。

A／B／C／D、同口徑 v1 和 0050 已完成相同日曆、均價、費稅與資金預算下的回測。A／B 淨報酬 +203.46%、C +203.42%、D +119.97%、同口徑 v1 +173.54%、0050 +121.28%。這些是扣費研究帳務；部分訊號資料不完整、Active Share 未認證且存在原始規則超限，全部維持 `BLOCK_SUBMISSION`。

A 的成交筆數較同口徑 v1 由 4,340 降為 818，但仍有 393／417 日交易。板塊與多訊號功能不能僅憑存在就宣稱有效。全部每月報酬、月內最大回撤、年度與 24 日區塊見報告及明細。

## 重現

三個入口共用已稽核引擎與固定資料，執行時不重新選參數；輸出目錄非空時會拒絕覆寫。`--show-config` 顯示完整設定，移除該選項可單獨回測。

```bash
python3 v2_A_best.py --show-config
python3 v2_B_best.py --show-config
python3 v2_C_best.py --show-config
python3 scripts/run_v2_best.py --output outputs/v2_best_reproduction
python3 scripts/run_v2_best.py --track official_ex_post --output outputs/v2_official_reproduction
# 此入口會自動比對已獨立稽核的成交、持股、淨值、設定與指標。
```

原始 v2 全策略重現：

```bash
python3 -m unittest discover -s tests -v
python3 scripts/run_v2_study.py --output outputs/backtest_v2_reproduction
python3 scripts/audit_v2_study.py --output outputs/backtest_v2_reproduction
python3 run_daily.py --date 2026-09-21 --mode paper
```

完整輸出拒絕覆寫。離線重跑使用本機凍結輸入；程式、設定及資料 SHA-256 見 [provenance](outputs/backtest_v2_2025_to_now/provenance.json)。獨立稽核重建六版全部帳務、126 列月表、12 列年表及 108 列區塊表。每日命令是歷史 paper 回放，會生成 14 個固定檔案，尚非即時資料服務或正式交易提交。

## 證據入口

| 內容 | 入口 |
|---|---|
| 使用者四版規格 | [AI_CUP_Trading_Agent_v2.md](docs/AI_CUP_Trading_Agent_v2.md) |
| 實作策略與參數 | [strategy_v2_implemented.md](docs/strategy_v2_implemented.md) |
| 總比較 | [summary.csv](outputs/backtest_v2_2025_to_now/summary.csv) |
| 每月報酬及最大回撤 | [monthly_comparison.csv](outputs/backtest_v2_2025_to_now/monthly_comparison.csv) |
| 獨立帳務與時序稽核 | [audit.json](outputs/backtest_v2_2025_to_now/audit.json) |
| 每日回放重現性 | [paper_export_audit.json](outputs/backtest_v2_2025_to_now/paper_export_audit.json) |
| 最新 paper 包 | [comparison.md](outputs/paper_daily/2026-09-21/comparison.md) |
| 競賽規則 | [competition_rules.md](docs/competition_rules.md) |
| 基礎行情的限制 | [data_audit_2025_to_now.md](docs/data_audit_2025_to_now.md) |
| 歷史產業來源 | [provenance.json](data/sector/provenance.json) |

## 保留的歷史版本

[舊 v1 報告](reports/backtest_2025_to_now_v1.md) 的 +133.16% 使用 15% 策略現金、次日開盤加 5 bps，與本輪主表不是同一條 v1 曲線；原輸出保留於 `outputs/backtest_2025_to_now_v1/`。原 2026 單年度報告亦保留，來源修正見 `outputs/backtest_2026_v1/SOURCE_CORRECTION_NOTICE.md`。

所有 `outputs/failed_*` 與 `tmp/v2_baseline_smoke/` 都是隔離的失效候選，不能引用其績效。PDF 唯一文字來源為 `reports/backtest_v2_final.md`，由 `scripts/render_v2_report.py` 產生；圖表只使用已獨立稽核通過的輸出。
