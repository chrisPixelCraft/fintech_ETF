# Production replay：一致性 gate

- 期間：holdout，40 個 24 日窗口、共 960 個交易日
- 每天：T−1 資料 → engine → D-Plan → 驗證 → 從 D-Plan 讀回委託 → shadow ledger 結算
- 同一窗口另跑 `competition.backtest.run_episode`，逐日比對

**CONSISTENCY_PASS**

- ✓ all days normal path
- ✓ every D-Plan validates
- ✓ target weights identical
- ✓ orders identical
- ✓ holdings identical
- ✓ cash and NAV within 1e-06

- NAV 最大差 0 元，現金最大差 0 元
