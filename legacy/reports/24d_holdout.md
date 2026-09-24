# Holdout results

`COMPETITION_UNIVERSE_STRESS_TEST`; `BLOCK_READY` / `BLOCK_SUBMISSION`. Return statistics condition on measured-valid complete episodes; valid/requested counts retain failures and incomplete returns stay null. Formal compliance has **0 confirmed episodes**; `ACTIVE_SHARE_NOT_VERIFIED`. Full assumptions and the inherited parameter-selection confound are in the [main report](24d_strategy_summary.md).

| Metric | x0352_daily_baseline |
| --- | --- |
| Median 24D return | 5.94% |
| Mean 24D return | 5.29% |
| P25 | 0.31% |
| P10 | -2.93% |
| Worst | -3.93% |
| Positive episodes | 77.78% |
| Loss episodes | 22.22% |
| Median MDD | 3.78% |
| Worst MDD | 9.29% |
| Best return | 19.36% |
| P90 MDD | 7.81% |
| Median turnover | 236.58% |
| Measured rule pass / requested | 78.26% |
| Median trade count | 116.5 |
| Median days to valid portfolio (among reached) | 1 |
| Measured valid / requested | 18/23 |
| Incomplete episodes | 1 |
| Formal compliance confirmed | 0/23; UNKNOWN |

## Boundary purges

These monthly windows cross partition boundaries and are excluded from selection and holdout scoring. They remain in the full-period descriptive monthly panel.

| Start | End | Registry split |
| --- | --- | --- |
| 2019-12-02 | 2020-01-03 | purged |
| 2022-12-01 | 2023-01-04 | purged |
| 2024-12-02 | 2025-01-03 | purged |

## By start year

| Start year | Candidate | Measured valid / requested | Incomplete | Median | P25 | Worst | Median MDD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2023 | x0352_daily_baseline | 10/12 | 1 | 2.63% | 0.31% | -3.93% | 3.62% |
| 2024 | x0352_daily_baseline | 8/11 | 0 | 7.84% | 4.67% | -3.81% | 4.08% |

## Distribution diagnostics

| Set | Candidate | Valid / requested | Best | P75 | P90 MDD | Median trades | Mean excluding top 5% | Symmetric trimmed median | Removed per tail |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| holdout | x0352_daily_baseline | 18/23 | 19.36% | 9.09% | 7.81% | 116.5 | 4.47% | 5.94% | 1 |

## Cold-start deployment

| Set | Candidate | D1 invested | D3 invested | D5 invested | Holdings D1 / D3 / D5 | Observed D1 / D3 / D5 | Reached valid / requested | MEDIAN_DAYS_TO_VALID_PORTFOLIO |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| holdout | x0352_daily_baseline | 90.71% | 90.02% | 89.27% | 20 / 20 / 20.5 | 23 / 23 / 22 | 22/23 | 1 |

## Rolling stress test

Overlapping 24-session windows are dependent; their row count is not an independent sample size. Rolling results are secondary stress evidence and must not revise the frozen selection.

| Set | Candidate | Measured valid / requested | Incomplete | Median | Mean | P25 | P10 | Worst |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| rolling | x0352_daily_baseline | 2691/4060 | 302 | 2.16% | 2.72% | -1.31% | -4.68% | -31.93% |

Evidence: [final_selection.json](../outputs/24d/final_selection.json), [audit.json](../outputs/24d/audit.json). Audit: **EVERY_ATTEMPT_RECONSTRUCTED_BEFORE_SAVE**. [Full provenance and reproduction command](24d_strategy_summary.md).
