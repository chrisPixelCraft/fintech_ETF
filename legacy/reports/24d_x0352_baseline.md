# x0352 daily baseline

`COMPETITION_UNIVERSE_STRESS_TEST`; `BLOCK_READY` / `BLOCK_SUBMISSION`. Return statistics condition on measured-valid complete episodes; valid/requested counts retain failures and incomplete returns stay null. Formal compliance has **0 confirmed episodes**; `ACTIVE_SHARE_NOT_VERIFIED`. Full assumptions and the inherited parameter-selection confound are in the [main report](24d_strategy_summary.md).

The frozen x0352 parameters are transferred to daily indicators; the 4H availability gate is removed. Signals retain the 10/30-day returns, EMA 10/30 and 100/200, MACD 8/21/5, volume/risk filters and portfolio rules. The approximate score weights are 44% short return, 11% long return, 18% volume and 9% each MACD, medium trend and long trend. These reports describe the saved engine run, not a reproduction of the legacy long-horizon return.

| Set | Candidate | Measured valid / requested | Incomplete | Median | Mean | P25 | P10 | Worst |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| development | x0352_daily_baseline | 78/119 | 7 | 1.92% | 1.67% | -1.18% | -4.81% | -8.95% |
| validation | x0352_daily_baseline | 24/35 | 4 | 3.40% | 3.76% | -1.74% | -6.78% | -10.57% |
| holdout | x0352_daily_baseline | 18/23 | 1 | 5.94% | 5.29% | 0.31% | -2.93% | -3.93% |

| Set | Candidate | Positive | Loss | Median MDD | Worst MDD | Median turnover | All-complete median¹ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| development | x0352_daily_baseline | 67.95% | 32.05% | 3.02% | 11.07% | 239.53% | 1.65% |
| validation | x0352_daily_baseline | 66.67% | 33.33% | 5.02% | 21.65% | 244.08% | 0.60% |
| holdout | x0352_daily_baseline | 77.78% | 22.22% | 3.78% | 9.29% | 236.58% | 2.63% |

## Distribution diagnostics

| Set | Candidate | Valid / requested | Best | P75 | P90 MDD | Median trades | Mean excluding top 5% | Symmetric trimmed median | Removed per tail |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| development | x0352_daily_baseline | 78/119 | 14.28% | 4.19% | 7.24% | 113 | 1.08% | 1.92% | 4 |
| validation | x0352_daily_baseline | 24/35 | 17.37% | 9.87% | 9.88% | 114.5 | 2.58% | 3.40% | 2 |
| holdout | x0352_daily_baseline | 18/23 | 19.36% | 9.09% | 7.81% | 116.5 | 4.47% | 5.94% | 1 |

Top-tail removal discards ceil(5% × valid episodes), capped to leave more than half the sample. Symmetric trimming leaves the sample median unchanged by construction; it is reported for completeness, not evidence of improved robustness.

## Cold-start deployment

| Set | Candidate | D1 invested | D3 invested | D5 invested | Holdings D1 / D3 / D5 | Observed D1 / D3 / D5 | Reached valid / requested | MEDIAN_DAYS_TO_VALID_PORTFOLIO |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| development | x0352_daily_baseline | 90.77% | 90.38% | 89.37% | 20 / 20 / 21 | 119 / 119 / 112 | 112/119 | 1 |
| validation | x0352_daily_baseline | 90.91% | 89.53% | 89.38% | 20 / 20 / 21 | 35 / 35 / 31 | 31/35 | 1 |
| holdout | x0352_daily_baseline | 90.71% | 90.02% | 89.27% | 20 / 20 / 20.5 | 23 / 23 / 22 | 22/23 | 1 |

Invested fractions and holding counts are medians among observations available on each day; observed counts expose early stops. Time to valid portfolio is conditional on reaching all measured portfolio checks, including holding count, cash, caps, stale quotes and odd shares; unreached episodes remain in the denominator. Formal Active Share remains unknown.


## Monthly episodes by start year

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
| 2019 | x0352_daily_baseline | 9/11 | 0 | 2.96% | 1.19% | -0.50% | 2.60% |
| 2020 | x0352_daily_baseline | 7/12 | 2 | 9.71% | 2.81% | -6.03% | 3.69% |
| 2021 | x0352_daily_baseline | 10/12 | 0 | 4.37% | 0.77% | -7.09% | 5.51% |
| 2022 | x0352_daily_baseline | 7/11 | 2 | -2.19% | -6.34% | -10.57% | 5.81% |
| 2023 | x0352_daily_baseline | 10/12 | 1 | 2.63% | 0.31% | -3.93% | 3.62% |
| 2024 | x0352_daily_baseline | 8/11 | 0 | 7.84% | 4.67% | -3.81% | 4.08% |

## Full-period monthly and continuous-book diagnostics

| Set | Candidate | Measured valid / requested | Incomplete | Median | Mean | P25 | P10 | Worst |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_200_monthly_starts | x0352_daily_baseline | 137/200 | 13 | 2.74% | 3.10% | -1.21% | -5.00% | -14.96% |

| Set | Candidate | Valid / requested | Best | P75 | P90 MDD | Median trades | Mean excluding top 5% | Symmetric trimmed median | Removed per tail |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_200_monthly_starts | x0352_daily_baseline | 137/200 | 38.29% | 6.59% | 8.70% | 113 | 2.22% | 2.74% | 7 |

The following book starts once and carries its holdings forward; it is not a compound return of cash-reset episodes. Disqualification stops the book and leaves the full-horizon return null.

| Candidate | Observed / requested sessions | Observed end | Book status | LONG_HORIZON_RETURN | Forensic partial return (not full horizon) |
| --- | --- | --- | --- | --- | --- |
| x0352_daily_baseline | 1113/4083 | 2014-07-09 | DQ | N/A | 38.43% |

Evidence: [final_selection.json](../outputs/24d/final_selection.json), [audit.json](../outputs/24d/audit.json). Audit: **EVERY_ATTEMPT_RECONSTRUCTED_BEFORE_SAVE**. [Full provenance and reproduction command](24d_strategy_summary.md).
