# Production replay：一致性 gate

**CONSISTENCY_PASS：NAV、現金最大差均 0 元。**

Holdout：40 個 24 日窗口，共 960 交易日。每日以 T−1 資料執行 engine → D-Plan → 驗證 → 讀回委託 → shadow ledger，另跑 `competition.backtest.run_episode` 逐日核對。

- ✓ all days normal path
- ✓ every D-Plan validates
- ✓ target weights / orders / holdings identical
- ✓ cash and NAV within 1e-06
