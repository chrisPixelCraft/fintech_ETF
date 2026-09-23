# Validation results

`COMPETITION_UNIVERSE_STRESS_TEST`; `BLOCK_READY` / `BLOCK_SUBMISSION`. Return statistics condition on measured-valid complete episodes; valid/requested counts retain failures and incomplete returns stay null. Formal compliance has **0 confirmed episodes**; `ACTIVE_SHARE_NOT_VERIFIED`. Full assumptions and the inherited parameter-selection confound are in the [main report](24d_strategy_summary.md).

Validation uses 2020-01-02–2022-11-01 starts; last episode ends 2022-12-02. The recorded decision is **NO_ELIGIBLE_CANDIDATE**; the required measured pass rate is **100.00%** in both development and validation. Validation ranks the development shortlist; no holdout result may alter that choice.

| Set | Candidate | Measured valid / requested | Incomplete | Median | Mean | P25 | P10 | Worst |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| validation | x0352_daily_baseline | 24/35 | 4 | 3.40% | 3.76% | -1.74% | -6.78% | -10.57% |
| validation | coarse_009_ema_slow_20 | 24/35 | 4 | 3.12% | 3.88% | -0.46% | -6.76% | -11.72% |
| validation | coarse_019_volume_low_0p6 | 24/35 | 4 | 3.38% | 3.71% | -1.74% | -6.78% | -10.35% |
| validation | coarse_024_one_day_chase_return_0p05 | 24/35 | 4 | 3.10% | 3.67% | -2.27% | -5.86% | -10.57% |

| Set | Candidate | Positive | Loss | Median MDD | Worst MDD | Median turnover | All-complete median¹ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| validation | x0352_daily_baseline | 66.67% | 33.33% | 5.02% | 21.65% | 244.08% | 0.60% |
| validation | coarse_009_ema_slow_20 | 70.83% | 29.17% | 4.95% | 21.60% | 244.52% | 1.16% |
| validation | coarse_019_volume_low_0p6 | 70.83% | 29.17% | 5.02% | 21.65% | 243.79% | 2.15% |
| validation | coarse_024_one_day_chase_return_0p05 | 66.67% | 33.33% | 5.18% | 20.46% | 247.74% | 1.10% |

## Distribution diagnostics

| Set | Candidate | Valid / requested | Best | P75 | P90 MDD | Median trades | Mean excluding top 5% | Symmetric trimmed median | Removed per tail |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| validation | x0352_daily_baseline | 24/35 | 17.37% | 9.87% | 9.88% | 114.5 | 2.58% | 3.40% | 2 |
| validation | coarse_009_ema_slow_20 | 24/35 | 18.22% | 10.15% | 9.34% | 116 | 2.66% | 3.12% | 2 |
| validation | coarse_019_volume_low_0p6 | 24/35 | 18.95% | 9.75% | 10.68% | 117 | 2.46% | 3.38% | 2 |
| validation | coarse_024_one_day_chase_return_0p05 | 24/35 | 17.79% | 9.81% | 9.84% | 124 | 2.49% | 3.10% | 2 |

## Cold-start deployment

| Set | Candidate | D1 invested | D3 invested | D5 invested | Holdings D1 / D3 / D5 | Observed D1 / D3 / D5 | Reached valid / requested | MEDIAN_DAYS_TO_VALID_PORTFOLIO |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| validation | x0352_daily_baseline | 90.91% | 89.53% | 89.38% | 20 / 20 / 21 | 35 / 35 / 31 | 31/35 | 1 |
| validation | coarse_009_ema_slow_20 | 90.91% | 90.93% | 89.58% | 20 / 20 / 21 | 35 / 35 / 31 | 31/35 | 1 |
| validation | coarse_019_volume_low_0p6 | 90.87% | 89.53% | 89.26% | 20 / 20 / 21 | 35 / 35 / 31 | 31/35 | 1 |
| validation | coarse_024_one_day_chase_return_0p05 | 90.73% | 90.21% | 89.38% | 20 / 20 / 21 | 35 / 35 / 31 | 31/35 | 1 |

| Set | Candidate | Failure reason | Episodes | Requested denominator |
| --- | --- | --- | --- | --- |
| validation | x0352_daily_baseline | FAIL_CASH | 5 | 35 |
| validation | x0352_daily_baseline | FAIL_HOLDING_COUNT | 5 | 35 |
| validation | x0352_daily_baseline | FAIL_MISSING_DATA | 1 | 35 |
| validation | x0352_daily_baseline | FAIL_OTHER | 1 | 35 |
| validation | x0352_daily_baseline | FAIL_ROUND_LOT | 5 | 35 |
| validation | coarse_009_ema_slow_20 | FAIL_CASH | 5 | 35 |
| validation | coarse_009_ema_slow_20 | FAIL_HOLDING_COUNT | 5 | 35 |
| validation | coarse_009_ema_slow_20 | FAIL_MISSING_DATA | 1 | 35 |
| validation | coarse_009_ema_slow_20 | FAIL_OTHER | 1 | 35 |
| validation | coarse_009_ema_slow_20 | FAIL_ROUND_LOT | 5 | 35 |
| validation | coarse_019_volume_low_0p6 | FAIL_CASH | 4 | 35 |
| validation | coarse_019_volume_low_0p6 | FAIL_HOLDING_COUNT | 4 | 35 |
| validation | coarse_019_volume_low_0p6 | FAIL_OTHER | 2 | 35 |
| validation | coarse_019_volume_low_0p6 | FAIL_ROUND_LOT | 5 | 35 |
| validation | coarse_024_one_day_chase_return_0p05 | FAIL_CASH | 5 | 35 |
| validation | coarse_024_one_day_chase_return_0p05 | FAIL_HOLDING_COUNT | 5 | 35 |
| validation | coarse_024_one_day_chase_return_0p05 | FAIL_MISSING_DATA | 1 | 35 |
| validation | coarse_024_one_day_chase_return_0p05 | FAIL_OTHER | 1 | 35 |
| validation | coarse_024_one_day_chase_return_0p05 | FAIL_ROUND_LOT | 5 | 35 |

Evidence: [final_selection.json](../outputs/24d/final_selection.json), [audit.json](../outputs/24d/audit.json). Audit: **EVERY_ATTEMPT_RECONSTRUCTED_BEFORE_SAVE**. [Full provenance and reproduction command](24d_strategy_summary.md).
