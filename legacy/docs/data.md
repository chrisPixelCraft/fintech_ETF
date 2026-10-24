# 固定資料與解讀界線

沿用 `x0352` 309.16% 的凍結回放輸入，不重下載或換價；[發行清單](../config/best_v2_release.json)記錄所有保留檔案 SHA256。

| 股票池 | 主要資料 |
|---|---|
| 官方事後池 | `data/tuning_2nd/official_universe/processed/` |
| 歷史重建池 | `data/v2/market_daily.csv` |
| 歷史日內觀測 | `data/extended/processed/hourly_canonical.csv` |
| 歷史股票池 | `data/extended/processed/universe_20241231.csv` |
| 公司行動與品質 | 各資料夾的稽核與覆蓋紀錄 |

兩池均回放 2025-01-02–2026-09-21、417 個交易日；保留 200 日暖機與真實日內觀測，不合成四小時資料、不改日線策略。

## 偏誤與限制

2026 年公布的官方 150 檔回套 2025 年有成分前視偏誤；歷史池亦受資料可用時間與公司事件假設影響。兩者均非無偏交易績效，整段已參與選參。

含費稅，但未計滑價、市場衝擊或排隊；部分模擬成交超過當日市場成交量，容量未證明。

日內觀測始於 2024 年晚期，無法同規則重現完整 2010–2024 年報酬。未完成的 Yahoo 日線研究已封存，不納入或補造歷史。

修復與來源見[整理前資料稽核](https://github.com/chrisPixelCraft/fintech_ETF/blob/d613b75/docs/data_audit_2025_to_now.md)；凍結輸入的完整性驗證不保證外部行情正確。
