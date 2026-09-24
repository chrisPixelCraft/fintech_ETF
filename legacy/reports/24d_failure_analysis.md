# Episode failure analysis

`COMPETITION_UNIVERSE_STRESS_TEST`; `BLOCK_READY` / `BLOCK_SUBMISSION`. Return statistics condition on measured-valid complete episodes; valid/requested counts retain failures and incomplete returns stay null. Formal compliance has **0 confirmed episodes**; `ACTIVE_SHARE_NOT_VERIFIED`. Full assumptions and the inherited parameter-selection confound are in the [main report](24d_strategy_summary.md).

A single episode may have multiple failure reasons, so reason counts can exceed failed-episode counts. Failed and incomplete episodes remain in each requested denominator. These are **measured research failures**, not a count of certified official violations. `FAIL_OTHER` can include a breach of the predeclared ±10% research execution-price envelope, which is not an official trading-rule limit.

| Set | Candidate | Failure reason | Episodes | Requested denominator |
| --- | --- | --- | --- | --- |
| development | x0352_daily_baseline | FAIL_CASH | 14 | 119 |
| development | x0352_daily_baseline | FAIL_HOLDING_COUNT | 14 | 119 |
| development | x0352_daily_baseline | FAIL_MISSING_DATA | 4 | 119 |
| development | x0352_daily_baseline | FAIL_OTHER | 3 | 119 |
| development | x0352_daily_baseline | FAIL_ROUND_LOT | 23 | 119 |
| validation | x0352_daily_baseline | FAIL_CASH | 5 | 35 |
| validation | x0352_daily_baseline | FAIL_HOLDING_COUNT | 5 | 35 |
| validation | x0352_daily_baseline | FAIL_MISSING_DATA | 1 | 35 |
| validation | x0352_daily_baseline | FAIL_OTHER | 1 | 35 |
| validation | x0352_daily_baseline | FAIL_ROUND_LOT | 5 | 35 |
| holdout | x0352_daily_baseline | FAIL_CASH | 1 | 23 |
| holdout | x0352_daily_baseline | FAIL_HOLDING_COUNT | 1 | 23 |
| holdout | x0352_daily_baseline | FAIL_OTHER | 1 | 23 |
| holdout | x0352_daily_baseline | FAIL_ROUND_LOT | 3 | 23 |
| oct_nov | x0352_daily_baseline | FAIL_CASH | 4 | 16 |
| oct_nov | x0352_daily_baseline | FAIL_HOLDING_COUNT | 4 | 16 |
| oct_nov | x0352_daily_baseline | FAIL_MISSING_DATA | 1 | 16 |
| rolling | x0352_daily_baseline | FAIL_CASH | 480 | 4060 |
| rolling | x0352_daily_baseline | FAIL_HOLDING_COUNT | 480 | 4060 |
| rolling | x0352_daily_baseline | FAIL_MISSING_DATA | 149 | 4060 |
| rolling | x0352_daily_baseline | FAIL_OTHER | 127 | 4060 |
| rolling | x0352_daily_baseline | FAIL_ROUND_LOT | 745 | 4060 |
| recent_stress | x0352_daily_baseline | FAIL_CASH | 2 | 20 |
| recent_stress | x0352_daily_baseline | FAIL_HOLDING_COUNT | 2 | 20 |
| recent_stress | x0352_daily_baseline | FAIL_MISSING_DATA | 2 | 20 |
| recent_stress | x0352_daily_baseline | FAIL_ROUND_LOT | 4 | 20 |

Baseline development episodes with `FAIL_OTHER`, a research price-envelope breach and no recorded raw-rule breach: 2014-02-05, 2017-10-02, 2018-10-01. They are complete episodes excluded by the stricter research gate.


## Monthly coverage and returns by start year

| Start year | Candidate | Measured valid / requested | Incomplete | Median | P25 | Worst | Median MDD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2010 | x0352_daily_baseline | 9/12 | 2 | 3.14% | -2.23% | -8.95% | 3.76% |
| 2011 | x0352_daily_baseline | 5/12 | 2 | -1.83% | -5.77% | -7.97% | 5.77% |
| 2012 | x0352_daily_baseline | 7/12 | 0 | 0.15% | -4.53% | -6.82% | 4.10% |
| 2013 | x0352_daily_baseline | 9/12 | 0 | 1.99% | 0.24% | -1.47% | 2.67% |
| 2014 | x0352_daily_baseline | 9/12 | 0 | 2.83% | 1.87% | -5.11% | 2.71% |
| 2015 | x0352_daily_baseline | 9/12 | 1 | 2.74% | -0.11% | -2.78% | 3.32% |
| 2016 | x0352_daily_baseline | 7/12 | 2 | 0.80% | -0.65% | -5.75% | 3.70% |
| 2017 | x0352_daily_baseline | 8/12 | 0 | 3.89% | -0.05% | -2.66% | 2.41% |
| 2018 | x0352_daily_baseline | 6/12 | 0 | -0.27% | -3.82% | -5.95% | 3.16% |
| 2019 | x0352_daily_baseline | 10/12 | 0 | 3.08% | 1.31% | -0.50% | 2.43% |
| 2020 | x0352_daily_baseline | 7/12 | 2 | 9.71% | 2.81% | -6.03% | 3.69% |
| 2021 | x0352_daily_baseline | 10/12 | 0 | 4.37% | 0.77% | -7.09% | 5.51% |
| 2022 | x0352_daily_baseline | 8/12 | 2 | -3.57% | -5.72% | -10.57% | 6.49% |
| 2023 | x0352_daily_baseline | 10/12 | 1 | 2.63% | 0.31% | -3.93% | 3.62% |
| 2024 | x0352_daily_baseline | 9/12 | 0 | 7.80% | 0.57% | -3.81% | 3.75% |
| 2025 | x0352_daily_baseline | 8/12 | 1 | 5.38% | -1.91% | -14.96% | 4.28% |
| 2026 | x0352_daily_baseline | 6/8 | 0 | 13.59% | 5.16% | -1.45% | 8.32% |

## Data and execution causes

Daily Open is a research fill proxy; capacity, official actions and source accuracy remain unverified. See [the main report](24d_strategy_summary.md) for data-quality policy, calendar amendments and the 2025–2026 inherited-selection confound.

Evidence: [final_selection.json](../outputs/24d/final_selection.json), [audit.json](../outputs/24d/audit.json). Audit: **EVERY_ATTEMPT_RECONSTRUCTED_BEFORE_SAVE**. [Full provenance and reproduction command](24d_strategy_summary.md).
