# Recent-regime stress test

`COMPETITION_UNIVERSE_STRESS_TEST`; `BLOCK_READY` / `BLOCK_SUBMISSION`. Return statistics condition on measured-valid complete episodes; valid/requested counts retain failures and incomplete returns stay null. Formal compliance has **0 confirmed episodes**; `ACTIVE_SHARE_NOT_VERIFIED`. Full assumptions and the inherited parameter-selection confound are in the [main report](24d_strategy_summary.md).

These post-freeze 2025–2026 episodes are stress evidence only and cannot alter the selected parameters. The inherited x0352 baseline was previously selected using 2025–2026 data, so this is not an untouched holdout for that baseline.

Observed starts: 2025-01-02–2026-08-03 starts; last episode ends 2026-09-03.

| Set | Candidate | Measured valid / requested | Incomplete | Median | Mean | P25 | P10 | Worst |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| recent_regime | x0352_daily_baseline | 14/20 | 1 | 8.70% | 7.71% | -0.30% | -3.27% | -14.96% |
| recent_regime | simple_momentum_reference | 14/20 | 1 | 10.48% | 8.69% | 0.98% | -0.06% | -14.10% |

| Set | Candidate | Positive | Loss | Median MDD | Worst MDD | Median turnover | All-complete median¹ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| recent_regime | x0352_daily_baseline | 71.43% | 28.57% | 6.06% | 15.56% | 228.50% | 7.90% |
| recent_regime | simple_momentum_reference | 85.71% | 14.29% | 5.38% | 15.21% | 220.12% | 9.96% |

| Start year | Candidate | Measured valid / requested | Incomplete | Median | P25 | Worst | Median MDD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2025 | x0352_daily_baseline | 8/12 | 1 | 5.38% | -1.91% | -14.96% | 4.28% |
| 2026 | x0352_daily_baseline | 6/8 | 0 | 13.59% | 5.16% | -1.45% | 8.32% |
| 2025 | simple_momentum_reference | 8/12 | 1 | 6.92% | 0.52% | -14.10% | 4.49% |
| 2026 | simple_momentum_reference | 6/8 | 0 | 13.00% | 4.02% | 0.03% | 8.91% |

## Separate 2024 / 2025 / 2026 regimes

These are descriptive calendar-year groupings of all monthly comparison episodes; they do not replace the purged holdout denominator.

| Set | Candidate | Measured valid / requested | Incomplete | Median | Mean | P25 | P10 | Worst |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2024 | x0352_daily_baseline | 9/12 | 0 | 7.80% | 5.49% | 0.57% | -2.81% | -3.81% |
| 2025 | x0352_daily_baseline | 8/12 | 1 | 5.38% | 3.30% | -1.91% | -7.32% | -14.96% |
| 2026 | x0352_daily_baseline | 6/8 | 0 | 13.59% | 13.60% | 5.16% | 0.48% | -1.45% |

## Cold-start deployment

| Set | Candidate | D1 invested | D3 invested | D5 invested | Holdings D1 / D3 / D5 | Observed D1 / D3 / D5 | Reached valid / requested | MEDIAN_DAYS_TO_VALID_PORTFOLIO |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| recent_regime | x0352_daily_baseline | 90.24% | 89.81% | 89.63% | 20 / 21 / 21 | 20 / 20 / 19 | 19/20 | 1 |
| recent_regime | simple_momentum_reference | 90.16% | 88.87% | 89.72% | 20 / 20 / 21 | 20 / 20 / 19 | 19/20 | 1 |

## Distribution diagnostics

| Set | Candidate | Valid / requested | Best | P75 | P90 MDD | Median trades | Mean excluding top 5% | Symmetric trimmed median | Removed per tail |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| recent_regime | x0352_daily_baseline | 14/20 | 38.29% | 13.69% | 10.26% | 104.5 | 5.36% | 8.70% | 1 |
| recent_regime | simple_momentum_reference | 14/20 | 36.81% | 14.30% | 10.76% | 94.5 | 6.53% | 10.48% | 1 |

| Set | Candidate | Failure reason | Episodes | Requested denominator |
| --- | --- | --- | --- | --- |
| recent_regime | x0352_daily_baseline | FAIL_CASH | 2 | 20 |
| recent_regime | x0352_daily_baseline | FAIL_HOLDING_COUNT | 2 | 20 |
| recent_regime | x0352_daily_baseline | FAIL_MISSING_DATA | 2 | 20 |
| recent_regime | x0352_daily_baseline | FAIL_ROUND_LOT | 4 | 20 |
| recent_regime | simple_momentum_reference | FAIL_CASH | 2 | 20 |
| recent_regime | simple_momentum_reference | FAIL_HOLDING_COUNT | 2 | 20 |
| recent_regime | simple_momentum_reference | FAIL_MISSING_DATA | 2 | 20 |
| recent_regime | simple_momentum_reference | FAIL_ROUND_LOT | 4 | 20 |

Evidence: [final_selection.json](../outputs/24d/final_selection.json), [audit.json](../outputs/24d/audit.json). Audit: **EVERY_ATTEMPT_RECONSTRUCTED_BEFORE_SAVE**. [Full provenance and reproduction command](24d_strategy_summary.md).
