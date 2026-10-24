# Holdout results

`COMPETITION_UNIVERSE_STRESS_TEST`; `BLOCK_READY` / `BLOCK_SUBMISSION`. Returns use only measured-valid complete episodes; valid/requested retains failures, incomplete returns stay null. Formal compliance: **0 confirmed episodes**, `ACTIVE_SHARE_NOT_VERIFIED`. Assumptions and inherited selection confound: [main report](24d_strategy_summary.md).

## x0352_daily_baseline metrics

24D returns: median 5.94%, mean 5.29%, P25 0.31%, P10 -2.93%, worst -3.93%, best 19.36%; positive 77.78%, loss 22.22%.

MDD: median 3.78%, worst 9.29%, P90 7.81%. Median turnover 236.58%, trade count 116.5; median days to valid portfolio 1 (among reached).

Measured valid / requested: 18/23 (78.26% rule pass), incomplete 1. Formal compliance confirmed: 0/23; UNKNOWN.

## Boundary purges

Boundary-crossing monthly windows are excluded from selection / holdout scoring but retained in the full-period descriptive panel.

| Start | End | Registry split |
| --- | --- | --- |
| 2019-12-02 | 2020-01-03 | purged |
| 2022-12-01 | 2023-01-04 | purged |
| 2024-12-02 | 2025-01-03 | purged |

## By start year

Candidate throughout: x0352_daily_baseline.

| Start year | Measured valid / requested | Incomplete | Median | P25 | Worst | Median MDD |
| --- | --- | --- | --- | --- | --- | --- |
| 2023 | 10/12 | 1 | 2.63% | 0.31% | -3.93% | 3.62% |
| 2024 | 8/11 | 0 | 7.84% | 4.67% | -3.81% | 4.08% |

## Distribution diagnostics

| Set | Valid / requested | Best | P75 | P90 MDD | Median trades | Mean excluding top 5% | Symmetric trimmed median | Removed per tail |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| holdout | 18/23 | 19.36% | 9.09% | 7.81% | 116.5 | 4.47% | 5.94% | 1 |

## Cold-start deployment

| Set | D1 invested | D3 invested | D5 invested | Holdings D1 / D3 / D5 | Observed D1 / D3 / D5 | Reached valid / requested | MEDIAN_DAYS_TO_VALID_PORTFOLIO |
| --- | --- | --- | --- | --- | --- | --- | --- |
| holdout | 90.71% | 90.02% | 89.27% | 20 / 20 / 20.5 | 23 / 23 / 22 | 22/23 | 1 |

## Rolling stress test

Overlapping 24-session windows are dependent, not independent samples. Rolling stress evidence cannot revise frozen selection.

| Set | Measured valid / requested | Incomplete | Median | Mean | P25 | P10 | Worst |
| --- | --- | --- | --- | --- | --- | --- | --- |
| rolling | 2691/4060 | 302 | 2.16% | 2.72% | -1.31% | -4.68% | -31.93% |

Evidence: [final_selection.json](../outputs/24d/final_selection.json), [audit.json](../outputs/24d/audit.json). Audit: **EVERY_ATTEMPT_RECONSTRUCTED_BEFORE_SAVE**. [Full provenance and reproduction command](24d_strategy_summary.md).
