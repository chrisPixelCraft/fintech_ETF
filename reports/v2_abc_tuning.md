# A/B/C 參數研究審核報告

## 研究範圍與主要結論

比較期間為 **2025-01-02–2026-09-21**，初始本金 **10.00 億 NTD**。報酬與回撤一律使用已扣 0.1425% 手續費及 0.30% 賣出稅的 economic NAV。

192 個試驗已全部完成；其中 0 個候選的 hard-breach days 為零。WALK_FORWARD 只有 C 的經濟報酬高於原版 4.67 pp，但成本增加 27.73M NTD、hard-breach days 增加 3，雙向換手增加 4.94。

本報告只在 `v2_abc_tuning_20260922/tuning_audit.json` 為 **PASS** 後生成。192 筆結果全部來自已反覆檢視的開發期間，沒有 pristine unseen test；任何事後贏家、純收益最高候選或 Pareto 候選都不能直接推為 LIVE。

選擇主規則先排除負現金，再依序最小化 hard-breach days、infeasible executed days，之後才最大化 economic return、最小化 MDD。因此 **return-best 不等於 primary best**，除非兩者剛好是同一候選。

- [離線互動報告](v2_abc_tuning.html)
- [完整 192 候選 CSV](../outputs/v2_abc_tuning_20260922/trial_summary.csv)
- [全部候選完整開發期月度明細 CSV（4032 列）](../outputs/v2_abc_tuning_20260922/all_trial_monthly.csv)
- [審核 JSON](../outputs/v2_abc_tuning_20260922/tuning_audit.json)
- [Walk-forward 獨立前綴稽核](../outputs/v2_abc_tuning_20260922/walk_forward_prefix_audit.json)

## 完整開發期比較

| 版本 | 角色 | 候選 | 總報酬 | MDD | 雙向換手 | 成本 | Hard breach |
|---|---|---|---|---|---|---|---|
| A | 原版 | p000 | 203.46% | 21.14% | 27.75 | 123.36M | 7 |
| A | 規則優先（事後） | p005 | 184.25% | 20.03% | 23.54 | 101.94M | 2 |
| A | 向前選參數 | schedule | 180.33% | 21.14% | 28.19 | 116.54M | 10 |
| B | 原版 | p000 | 203.46% | 21.14% | 27.75 | 123.36M | 7 |
| B | 規則優先（事後） | p005 | 184.25% | 20.03% | 23.54 | 101.94M | 2 |
| B | 向前選參數 | schedule | 186.87% | 21.14% | 32.26 | 141.07M | 9 |
| C | 原版 | p000 | 203.42% | 21.43% | 28.10 | 127.24M | 7 |
| C | 規則優先（事後） | p006 | 214.28% | 21.72% | 24.58 | 112.62M | 2 |
| C | 向前選參數 | schedule | 208.08% | 21.43% | 33.04 | 154.97M | 10 |

`EX_POST_BEST` 是依完整開發資料套用 primary 合規代理規則後的贏家；`WALK_FORWARD` 是單一連續帳本，在四個 cutoff 只用當時以前的開發資料選參數。它仍是 retrospective walk-forward，不是未見 OOS。

![Audited comparison](../outputs/v2_abc_tuning_20260922/comparison.png)

![Full-period numeric table](../outputs/v2_abc_tuning_20260922/table_comparison.png)

## Selection-start 之後（2025-07-01–2026-09-21）

| 版本 | 角色 | 候選 | 總報酬 | MDD | 雙向換手 | Hard breach |
|---|---|---|---|---|---|---|
| A | 原版 | p000 | 210.30% | 19.07% | 17.78 | 1 |
| A | 規則優先（事後） | p005 | 188.90% | 19.56% | 14.20 | 1 |
| A | 向前選參數 | schedule | 186.65% | 19.28% | 18.22 | 4 |
| B | 原版 | p000 | 210.30% | 19.07% | 17.78 | 1 |
| B | 規則優先（事後） | p005 | 188.90% | 19.56% | 14.20 | 1 |
| B | 向前選參數 | schedule | 193.33% | 14.63% | 22.29 | 3 |
| C | 原版 | p000 | 211.17% | 19.42% | 18.12 | 1 |
| C | 規則優先（事後） | p006 | 222.05% | 17.68% | 14.97 | 1 |
| C | 向前選參數 | schedule | 215.96% | 18.39% | 23.06 | 4 |

這個切片只改變報告區間。因為 cutoff 與候選已在同一批歷史資料上設計，它不是新的驗證集。

## Ex-post 診斷

| 版本 | 規則優先 | 純收益最高 | Primary 報酬 | Return-best 報酬 | Primary breach | Return-best breach | Pareto 數 |
|---|---|---|---|---|---|---|---|
| A | p005 | p028 | 184.25% | 254.91% | 2 | 4 | 9 |
| B | p005 | p014 | 184.25% | 254.70% | 2 | 8 | 12 |
| C | p006 | p033 | 214.28% | 240.55% | 2 | 6 | 12 |

Pareto 與贏家旗標已放入 HTML 完整表；候選明細可由完整 CSV 與 audit JSON 追溯。

規則優先候選的參數差異：

| 版本 | 候選 | 相對原版變更 | 其餘參數 |
|---|---|---|---|
| A | p005 | max_replacements_per_day: 2 → 0 | 其餘參數沿用原版 |
| B | p005 | max_replacements_per_day: 2 → 0 | 其餘參數沿用原版 |
| C | p006 | max_replacements_per_day: 2 → 1 | 其餘參數沿用原版 |

上限只限制一般替換；強制退出、補足持股與風控修正仍會交易，0 不代表停止交易。

![All candidate search](../outputs/v2_abc_tuning_20260922/search_scatter.png)

## 成交層級介入

同一 candidate ID 的核心參數完全相同。逐候選比較 `date / signal_date / symbol / trade_symbol / signed shares` 後，A/B 有 32/64 組產生真正不同成交，B/C 有 53/64 組不同。A/B 在 `baseline` fallback 為 13/45，在 `total_eligible` 為 19/19。完整總表的 `trade_diff_A_B`、`trade_diff_B_C` 可逐列篩選；這比只看到 gate metadata 更能證明策略介入是否落到成交。

## Retrospective schedule

| 版本 | Selection cutoff | 生效交易日 | 候選 | 範圍 |
|---|---|---|---|---|
| A | 2025-06-30 | 2025-07-01 | p046 | PAST_ONLY_DEVELOPMENT_REPLAY |
| B | 2025-06-30 | 2025-07-01 | p054 | PAST_ONLY_DEVELOPMENT_REPLAY |
| C | 2025-06-30 | 2025-07-01 | p054 | PAST_ONLY_DEVELOPMENT_REPLAY |
| A | 2025-12-31 | 2026-01-02 | p023 | PAST_ONLY_DEVELOPMENT_REPLAY |
| B | 2025-12-31 | 2026-01-02 | p028 | PAST_ONLY_DEVELOPMENT_REPLAY |
| C | 2025-12-31 | 2026-01-02 | p050 | PAST_ONLY_DEVELOPMENT_REPLAY |
| A | 2026-03-31 | 2026-04-01 | p005 | PAST_ONLY_DEVELOPMENT_REPLAY |
| B | 2026-03-31 | 2026-04-01 | p028 | PAST_ONLY_DEVELOPMENT_REPLAY |
| C | 2026-03-31 | 2026-04-01 | p050 | PAST_ONLY_DEVELOPMENT_REPLAY |
| A | 2026-06-30 | 2026-07-01 | p005 | PAST_ONLY_DEVELOPMENT_REPLAY |
| B | 2026-06-30 | 2026-07-01 | p028 | PAST_ONLY_DEVELOPMENT_REPLAY |
| C | 2026-06-30 | 2026-07-01 | p050 | PAST_ONLY_DEVELOPMENT_REPLAY |

WALK_FORWARD 月度摘要：

| 版本 | 月數 | 正報酬月 | 最佳月 | 最佳月報酬 | 最差月 | 最差月報酬 |
|---|---|---|---|---|---|---|
| A | 21 | 16 | 2026-04 | 49.03% | 2026-07 | -13.40% |
| B | 21 | 15 | 2026-04 | 41.55% | 2026-07 | -10.53% |
| C | 21 | 16 | 2026-04 | 45.75% | 2026-07 | -12.79% |

獨立 prefix replay 在 `2026-03-31` 截斷 C，重建 298 個 session、788 筆成交、12 筆選擇列與 8 個切換日；最大 economic NAV 絕對差為 4.77e-07 NTD。這支持排程與單一連續帳本的因果執行語意，但單一前綴仍不能證明供應商資料在原始時點可得，也不是 prospective OOS。

## 敏感度與搜尋邊界

每個版本公平使用相同的 64 組核心候選：1 個 incumbent、25 個 one-factor-at-a-time probes、38 個事前分層 joint bundles。OFAT 每次只改一個核心因素，可以作局部單因子描述；joint bundle 同時改多項參數，只能描述整體組合，不能作單一因果歸因。A 的 sector/C 參數不作用，B 的 `c_alpha` 不作用，C 才使用全部條件參數。

![OFAT sensitivity](../outputs/v2_abc_tuning_20260922/sensitivity.png)

完整搜尋空間、每筆參數與結果請使用 HTML 全表或 CSV。全表包含 core/conditional parameters、return、MDD、turnover、cost、hard breaches，以及其他合規欄位。

## 限制與合規狀態

Audit 為 `PASS`；192 個候選全數完成，A/B/C 各 64。報告以合規代理主規則為優先，也沒有依結果事後縮窄參數範圍。

Strategic cash target 是 `0`，這不保證實際現金為零。九個比較帳本的實際平均 cash ratio 範圍為 12.12%–14.25%，最大觀察值為 27.56%。

Baseline B sector metadata 為 PARTIAL: 418；已知分類範圍 47–50，未知範圍 98–100。板塊覆蓋仍是 partial，不能把未知分類當作板塊證據。Historical ETF holdings / Active Share 尚未驗證，因此所有結果維持 research shadow 與 BLOCK_SUBMISSION，不能視為正式認證或實盤可成交績效。

2026-09 只涵蓋至 9 月 21 日。Hard-breach days 是研究用代理指標，不是官方違規次數；Historical ETF holdings、歷史白名單與 Active Share 尚未驗證。64×3 個候選是事前界定的廣泛有限搜尋，不是窮舉，也不能宣稱全域最佳。
