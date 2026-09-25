# 24D parameter search

`COMPETITION_UNIVERSE_STRESS_TEST`; `BLOCK_READY` / `BLOCK_SUBMISSION`. Return statistics condition on measured-valid complete episodes; valid/requested counts retain failures and incomplete returns stay null. Formal compliance has **0 confirmed episodes**; `ACTIVE_SHARE_NOT_VERIFIED`. Full assumptions and the inherited parameter-selection confound are in the [main report](24d_strategy_summary.md).

**number_of_candidates_evaluated: 28.** Selected research candidate: `x0352_daily_baseline`. The final choice is limited to this saved neighborhood; the final holdout is not a selection input.

## Development candidates

| Candidate | Measured valid / requested | Median | P25 | P10 | Worst | Median MDD |
| --- | --- | --- | --- | --- | --- | --- |
| coarse_001_target_count_25 | 72/119 | 2.15% | -1.82% | -5.78% | -9.86% | 2.99% |
| coarse_002_target_count_30 | 73/119 | 1.59% | -1.16% | -5.15% | -9.41% | 3.19% |
| coarse_003_return_short_5 | 75/119 | 1.47% | -1.09% | -6.09% | -9.82% | 3.20% |
| coarse_004_return_short_15 | 76/119 | 2.43% | -1.39% | -5.05% | -8.90% | 3.17% |
| coarse_005_return_long_20 | 77/119 | 1.99% | -1.23% | -5.76% | -9.86% | 2.89% |
| coarse_006_return_long_50 | 77/119 | 1.99% | -0.71% | -5.32% | -9.34% | 3.21% |
| coarse_007_ema_fast_8 | 77/119 | 2.04% | -0.95% | -4.85% | -9.07% | 3.15% |
| coarse_008_ema_fast_15 | 76/119 | 2.13% | -0.61% | -5.17% | -8.45% | 3.09% |
| coarse_009_ema_slow_20 | 79/119 | 1.73% | -1.84% | -6.08% | -9.40% | 3.14% |
| coarse_010_ema_slow_50 | 76/119 | 1.82% | -0.57% | -4.97% | -8.81% | 3.20% |
| coarse_011_macd_fast_6 | 76/119 | 1.90% | -1.37% | -5.44% | -9.50% | 3.16% |
| coarse_012_macd_fast_10 | 79/119 | 1.99% | -1.58% | -5.41% | -8.76% | 3.26% |
| coarse_013_macd_signal_3 | 76/119 | 1.76% | -1.38% | -5.25% | -9.50% | 3.13% |
| coarse_014_macd_signal_7 | 79/119 | 1.95% | -1.06% | -5.74% | -8.76% | 3.13% |
| coarse_015_momentum_weight_0p45 | 74/119 | 2.41% | -1.28% | -5.16% | -9.52% | 3.15% |
| coarse_016_momentum_weight_0p65 | 77/119 | 2.15% | -0.81% | -5.84% | -8.96% | 3.08% |
| coarse_017_long_return_fraction_0p1 | 77/119 | 2.08% | -0.95% | -4.96% | -8.79% | 3.11% |
| coarse_018_long_return_fraction_0p3 | 77/119 | 1.91% | -0.51% | -5.14% | -8.71% | 3.06% |
| coarse_019_volume_low_0p6 | 84/119 | 1.91% | -1.18% | -4.67% | -8.58% | 3.41% |
| coarse_020_volume_high_3p0 | 77/119 | 1.93% | -1.09% | -5.37% | -9.24% | 3.15% |
| coarse_021_volatility_spike_ratio_1p5 | 74/119 | 2.01% | -0.58% | -5.90% | -8.71% | 3.03% |
| coarse_022_volatility_spike_ratio_2p5 | 78/119 | 2.03% | -1.05% | -4.95% | -8.95% | 3.02% |
| coarse_023_one_day_chase_return_0p03 | 74/119 | 1.91% | -0.93% | -4.99% | -8.95% | 3.11% |
| coarse_024_one_day_chase_return_0p05 | 80/119 | 1.63% | -1.51% | -5.16% | -9.07% | 3.18% |
| coarse_025_max_replacements_per_day_1 | 77/119 | 1.96% | -0.84% | -4.99% | -9.71% | 3.17% |
| coarse_026_cash_guard_ratio_0p1 | 78/119 | 1.79% | -1.18% | -5.16% | -9.70% | 3.24% |
| coarse_027_cash_guard_ratio_0p14 | 75/119 | 1.92% | -1.14% | -4.78% | -9.02% | 2.96% |
| x0352_daily_baseline | 78/119 | 1.92% | -1.18% | -4.81% | -8.95% | 3.02% |

## Validation comparison

| Set | Candidate | Measured valid / requested | Incomplete | Median | Mean | P25 | P10 | Worst |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| validation | x0352_daily_baseline | 24/35 | 4 | 3.40% | 3.76% | -1.74% | -6.78% | -10.57% |
| validation | coarse_009_ema_slow_20 | 24/35 | 4 | 3.12% | 3.88% | -0.46% | -6.76% | -11.72% |
| validation | coarse_019_volume_low_0p6 | 24/35 | 4 | 3.38% | 3.71% | -1.74% | -6.78% | -10.35% |
| validation | coarse_024_one_day_chase_return_0p05 | 24/35 | 4 | 3.10% | 3.67% | -2.27% | -5.86% | -10.57% |

The validation shortlist contains diagnostic fallback entries when development eligibility fails; shortlist membership does not mean a candidate passed the development gate.


The exact candidate definitions, selection ordering, thresholds, purged episodes and recorded chronology are in [candidates.csv](../outputs/24d/candidates.csv) and [final_selection.json](../outputs/24d/final_selection.json). No unpublished refinement or holdout-driven parameter change is implied.

Recorded decision: **NO_ELIGIBLE_CANDIDATE**. Required measured pass threshold: 100.00%. A diagnostic candidate is not an eligible winner when no candidate meets this threshold.

The frozen implementation did not use P10 as a ranking key; P10 is shown as a descriptive sensitivity metric. The report preserves that recorded ordering and does not retroactively claim the recommended P10 ordering was applied.

## Cold-start behavior of every searched candidate

| Set | Candidate | D1 invested | D3 invested | D5 invested | Holdings D1 / D3 / D5 | Observed D1 / D3 / D5 | Reached valid / requested | MEDIAN_DAYS_TO_VALID_PORTFOLIO |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| development | x0352_daily_baseline | 90.77% | 90.38% | 89.37% | 20 / 20 / 21 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_001_target_count_25 | 90.21% | 89.35% | 89.47% | 25 / 25 / 25 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_002_target_count_30 | 89.94% | 89.55% | 89.81% | 30 / 28 / 27 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_003_return_short_5 | 90.81% | 87.43% | 88.26% | 20 / 20 / 21 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_004_return_short_15 | 90.72% | 90.49% | 89.87% | 20 / 20 / 20 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_005_return_long_20 | 90.75% | 90.44% | 89.36% | 20 / 20 / 20.5 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_006_return_long_50 | 90.73% | 90.28% | 89.39% | 20 / 20 / 21 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_007_ema_fast_8 | 90.72% | 90.40% | 89.41% | 20 / 20 / 21 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_008_ema_fast_15 | 90.78% | 90.45% | 89.32% | 20 / 20 / 20 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_009_ema_slow_20 | 90.80% | 90.31% | 89.38% | 20 / 20 / 21 | 119 / 119 / 115 | 115/119 | 1 |
| development | coarse_010_ema_slow_50 | 90.78% | 90.45% | 89.42% | 20 / 20 / 21 | 119 / 119 / 111 | 111/119 | 1 |
| development | coarse_011_macd_fast_6 | 90.75% | 90.37% | 89.39% | 20 / 20 / 20.5 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_012_macd_fast_10 | 90.77% | 90.40% | 89.37% | 20 / 20 / 20 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_013_macd_signal_3 | 90.77% | 90.38% | 89.41% | 20 / 20 / 21 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_014_macd_signal_7 | 90.77% | 90.40% | 89.38% | 20 / 20 / 20 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_015_momentum_weight_0p45 | 90.77% | 90.43% | 89.42% | 20 / 20 / 20 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_016_momentum_weight_0p65 | 90.72% | 90.35% | 89.36% | 20 / 20 / 21 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_017_long_return_fraction_0p1 | 90.75% | 90.46% | 89.38% | 20 / 20 / 21 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_018_long_return_fraction_0p3 | 90.78% | 90.31% | 89.37% | 20 / 20 / 21 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_019_volume_low_0p6 | 90.85% | 90.71% | 89.54% | 20 / 20 / 21 | 119 / 119 / 117 | 117/119 | 1 |
| development | coarse_020_volume_high_3p0 | 90.79% | 90.41% | 89.41% | 20 / 20 / 21 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_021_volatility_spike_ratio_1p5 | 90.72% | 89.89% | 89.34% | 20 / 20 / 21 | 119 / 119 / 110 | 110/119 | 1 |
| development | coarse_022_volatility_spike_ratio_2p5 | 90.75% | 90.40% | 89.38% | 20 / 20 / 21 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_023_one_day_chase_return_0p03 | 90.72% | 90.13% | 89.43% | 20 / 20 / 21 | 119 / 119 / 110 | 110/119 | 1 |
| development | coarse_024_one_day_chase_return_0p05 | 90.81% | 90.47% | 89.40% | 20 / 20 / 21 | 119 / 119 / 113 | 113/119 | 1 |
| development | coarse_025_max_replacements_per_day_1 | 90.77% | 86.72% | 85.33% | 20 / 20 / 21 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_026_cash_guard_ratio_0p1 | 90.79% | 91.04% | 91.22% | 20 / 20 / 21 | 119 / 119 / 112 | 112/119 | 1 |
| development | coarse_027_cash_guard_ratio_0p14 | 90.77% | 90.37% | 87.58% | 20 / 20 / 21 | 119 / 119 / 112 | 112/119 | 1 |

Medians use observed days; time-to-valid uses reached episodes, with reached/requested shown separately. A 20-name count alone does not establish a valid portfolio.

## Zero, one and two daily replacements

| Set | Max replacements/day | Valid / requested | Median return | P25 | Median MDD | Median turnover | Median fees + tax / initial cash |
| --- | --- | --- | --- | --- | --- | --- | --- |
| development | 0 | 78/119 | 1.92% | -1.18% | 3.02% | 239.53% | 0.57% |
| development | 1 | 77/119 | 1.96% | -0.84% | 3.17% | 246.78% | 0.60% |
| development | 2 | 77/119 | 1.96% | -1.10% | 3.15% | 250.92% | 0.61% |
| validation | 0 | 24/35 | 3.40% | -1.74% | 5.02% | 244.08% | 0.59% |
| validation | 1 | not evaluated | N/A | N/A | N/A | N/A | N/A |
| validation | 2 | 23/35 | 3.09% | -1.59% | 5.24% | 249.79% | 0.61% |

Replacement 2 is an additive post-freeze diagnostic, not a revision of the frozen candidate search. Return, risk and fee medians condition on each candidate’s valid subset; coverage is compared on all requested windows. Replacement 1 was not evaluated on validation, so no 1-versus-2 validation claim is available. Two replacements did not improve validation coverage or its conditional median relative to the baseline; it is not adopted.

## Full-denominator penalty diagnostic

The saved policy assigns a fixed negative score to each failed episode. These penalty scores are **not realized portfolio returns**; they make failed-episode exclusions visible.

| Set | Candidate | All requested | Score assigned per failure | Mean score | Median score |
| --- | --- | --- | --- | --- | --- |
| development | x0352_daily_baseline | 119 | -100.00% | -33.36% | -1.47% |
| validation | x0352_daily_baseline | 35 | -100.00% | -28.85% | -1.60% |
| holdout | x0352_daily_baseline | 23 | -100.00% | -17.60% | 1.69% |

## Walk-forward evidence

The walk-forward policy may choose different candidates by year using prior-year episodes only; this is family robustness evidence, not the fixed final strategy’s return.

| Measured valid / requested | Median | P25 | P10 | Worst | Median MDD |
| --- | --- | --- | --- | --- | --- |
| 81/121 | 2.86% | -0.50% | -4.04% | -14.96% | 3.43% |

| Start year | Candidate | Measured valid / requested | Incomplete | Median | P25 | Worst | Median MDD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2015 | walk_forward_policy | 8/11 | 1 | 3.20% | -0.66% | -2.78% | 3.31% |
| 2016 | walk_forward_policy | 6/11 | 2 | 1.19% | -1.23% | -5.75% | 3.82% |
| 2017 | walk_forward_policy | 7/11 | 0 | 4.20% | 1.87% | -2.66% | 2.13% |
| 2018 | walk_forward_policy | 5/11 | 0 | 0.72% | -1.25% | -4.68% | 3.15% |
| 2019 | walk_forward_policy | 9/11 | 0 | 2.96% | 1.19% | -0.50% | 2.60% |
| 2020 | walk_forward_policy | 6/11 | 2 | 6.84% | 2.24% | -6.03% | 3.95% |
| 2021 | walk_forward_policy | 9/11 | 0 | 4.45% | 0.08% | -7.09% | 5.69% |
| 2022 | walk_forward_policy | 7/11 | 2 | -2.19% | -6.34% | -10.57% | 5.81% |
| 2023 | walk_forward_policy | 9/11 | 1 | 3.56% | 0.51% | -3.93% | 3.43% |
| 2024 | walk_forward_policy | 8/11 | 0 | 7.84% | 4.67% | -3.81% | 4.08% |
| 2025 | walk_forward_policy | 7/11 | 1 | 2.86% | -2.62% | -14.96% | 4.31% |

| test_year | candidate_id | training_end | train_episodes | test_episodes | decision |
| --- | --- | --- | --- | --- | --- |
| 2015 | x0352_daily_baseline | 2014-12-31 | 59 | 11 | NO_ELIGIBLE_BASELINE_DIAGNOSTIC |
| 2016 | x0352_daily_baseline | 2015-12-31 | 71 | 11 | NO_ELIGIBLE_BASELINE_DIAGNOSTIC |
| 2017 | x0352_daily_baseline | 2016-12-31 | 83 | 11 | NO_ELIGIBLE_BASELINE_DIAGNOSTIC |
| 2018 | x0352_daily_baseline | 2017-12-31 | 95 | 11 | NO_ELIGIBLE_BASELINE_DIAGNOSTIC |
| 2019 | x0352_daily_baseline | 2018-12-31 | 107 | 11 | NO_ELIGIBLE_BASELINE_DIAGNOSTIC |
| 2020 | x0352_daily_baseline | 2019-12-31 | 119 | 11 | NO_ELIGIBLE_BASELINE_DIAGNOSTIC |
| 2021 | x0352_daily_baseline | 2020-12-31 | 131 | 11 | NO_ELIGIBLE_BASELINE_DIAGNOSTIC |
| 2022 | x0352_daily_baseline | 2021-12-31 | 143 | 11 | NO_ELIGIBLE_BASELINE_DIAGNOSTIC |
| 2023 | x0352_daily_baseline | 2022-12-31 | 155 | 11 | NO_ELIGIBLE_BASELINE_DIAGNOSTIC |
| 2024 | x0352_daily_baseline | 2023-12-31 | 167 | 11 | NO_ELIGIBLE_BASELINE_DIAGNOSTIC |

The additive 2025 walk-forward diagnostic uses training ending 2024-12-31; frozen-family choice `x0352_daily_baseline`, decision **NO_ELIGIBLE_BASELINE_DIAGNOSTIC**. It is included in the policy summary above and cannot revise the fixed selection.


## Full-family post-freeze diagnostics

**30 configurations × 200 starts = 6000 attempted episodes**. This includes 28 searched configurations, the additive replacement-2 parameter set and the simple-momentum sanity reference; it does not expand the frozen search or authorize a new winner. The periods include previously exposed development, validation and recent data.

| Set | Candidate | Measured valid / requested | Incomplete | Median | Mean | P25 | P10 | Worst |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2010–2026 monthly diagnostic | x0352_daily_baseline | 137/200 | 13 | 2.74% | 3.10% | -1.21% | -5.00% | -14.96% |
| 2010–2026 monthly diagnostic | coarse_001_target_count_25 | 130/200 | 13 | 2.43% | 2.91% | -0.82% | -5.00% | -10.03% |
| 2010–2026 monthly diagnostic | coarse_002_target_count_30 | 130/200 | 13 | 2.26% | 2.59% | -1.27% | -5.09% | -13.51% |
| 2010–2026 monthly diagnostic | coarse_003_return_short_5 | 131/200 | 13 | 1.92% | 2.78% | -1.09% | -5.12% | -13.21% |
| 2010–2026 monthly diagnostic | coarse_004_return_short_15 | 136/200 | 13 | 2.74% | 3.17% | -1.05% | -4.94% | -14.29% |
| 2010–2026 monthly diagnostic | coarse_005_return_long_20 | 136/200 | 13 | 2.45% | 3.13% | -0.99% | -4.57% | -14.01% |
| 2010–2026 monthly diagnostic | coarse_006_return_long_50 | 136/200 | 13 | 2.67% | 3.22% | -0.72% | -5.13% | -14.96% |
| 2010–2026 monthly diagnostic | coarse_007_ema_fast_8 | 136/200 | 13 | 2.86% | 3.32% | -0.58% | -4.55% | -10.24% |
| 2010–2026 monthly diagnostic | coarse_008_ema_fast_15 | 134/200 | 13 | 2.75% | 3.32% | -0.70% | -4.48% | -10.53% |
| 2010–2026 monthly diagnostic | coarse_009_ema_slow_20 | 137/200 | 10 | 2.06% | 2.94% | -0.70% | -5.67% | -14.40% |
| 2010–2026 monthly diagnostic | coarse_010_ema_slow_50 | 134/200 | 13 | 2.64% | 2.97% | -0.59% | -5.07% | -14.96% |
| 2010–2026 monthly diagnostic | coarse_011_macd_fast_6 | 136/200 | 13 | 2.55% | 3.14% | -1.10% | -4.77% | -11.73% |
| 2010–2026 monthly diagnostic | coarse_012_macd_fast_10 | 140/200 | 13 | 2.77% | 3.02% | -1.27% | -5.23% | -14.79% |
| 2010–2026 monthly diagnostic | coarse_013_macd_signal_3 | 136/200 | 13 | 2.47% | 3.17% | -1.10% | -4.91% | -10.70% |
| 2010–2026 monthly diagnostic | coarse_014_macd_signal_7 | 139/200 | 13 | 2.68% | 3.16% | -0.60% | -5.01% | -10.47% |
| 2010–2026 monthly diagnostic | coarse_015_momentum_weight_0p45 | 134/200 | 13 | 2.45% | 3.15% | -0.94% | -4.97% | -9.59% |
| 2010–2026 monthly diagnostic | coarse_016_momentum_weight_0p65 | 137/200 | 13 | 2.72% | 3.07% | -0.62% | -4.91% | -15.85% |
| 2010–2026 monthly diagnostic | coarse_017_long_return_fraction_0p1 | 135/200 | 13 | 2.71% | 3.29% | -0.91% | -4.65% | -9.19% |
| 2010–2026 monthly diagnostic | coarse_018_long_return_fraction_0p3 | 135/200 | 13 | 2.22% | 3.29% | -0.35% | -4.68% | -11.00% |
| 2010–2026 monthly diagnostic | coarse_019_volume_low_0p6 | 144/200 | 6 | 2.64% | 2.97% | -0.99% | -4.56% | -14.96% |
| 2010–2026 monthly diagnostic | coarse_020_volume_high_3p0 | 137/200 | 13 | 2.66% | 3.20% | -1.09% | -5.17% | -14.96% |
| 2010–2026 monthly diagnostic | coarse_021_volatility_spike_ratio_1p5 | 132/200 | 16 | 2.36% | 3.27% | -0.65% | -4.92% | -10.13% |
| 2010–2026 monthly diagnostic | coarse_022_volatility_spike_ratio_2p5 | 137/200 | 13 | 2.74% | 3.12% | -1.10% | -5.00% | -14.96% |
| 2010–2026 monthly diagnostic | coarse_023_one_day_chase_return_0p03 | 132/200 | 16 | 2.16% | 3.19% | -0.88% | -4.54% | -9.10% |
| 2010–2026 monthly diagnostic | coarse_024_one_day_chase_return_0p05 | 139/200 | 12 | 2.46% | 3.05% | -1.23% | -5.08% | -15.41% |
| 2010–2026 monthly diagnostic | coarse_025_max_replacements_per_day_1 | 136/200 | 13 | 2.24% | 3.00% | -1.00% | -4.99% | -14.96% |
| 2010–2026 monthly diagnostic | coarse_026_cash_guard_ratio_0p1 | 138/200 | 13 | 2.07% | 3.01% | -1.00% | -5.24% | -15.15% |
| 2010–2026 monthly diagnostic | coarse_027_cash_guard_ratio_0p14 | 134/200 | 13 | 2.27% | 3.01% | -1.04% | -4.44% | -14.66% |
| 2010–2026 monthly diagnostic | diagnostic_replacement_2 | 134/200 | 13 | 2.52% | 3.04% | -1.18% | -4.92% | -14.96% |
| 2010–2026 monthly diagnostic | simple_momentum_reference | 137/200 | 13 | 2.32% | 2.93% | -1.23% | -5.19% | -14.10% |

Every configuration’s full mean/median/tails, best/worst, positive/loss rates, MDD, turnover, trade counts, validity and cold-start denominators are available in [full_period_summary.csv](../outputs/24d_diagnostics/full_period_summary.csv); per-episode fees, evidence and original ledger lineage are in [monthly_all_candidates.csv](../outputs/24d_diagnostics/monthly_all_candidates.csv). Top-tail removal and symmetric-trim medians remain conditional on valid episodes; a symmetric trim leaves the median unchanged by construction.

## Continuous books across all fixed configurations

Each book has one initial cash allocation. DQ or incomplete books have no full-horizon return; forensic partial values do not establish long-horizon performance.

| Candidate | Observed / requested sessions | Observed end | Book status | LONG_HORIZON_RETURN | Forensic partial return (not full horizon) |
| --- | --- | --- | --- | --- | --- |
| coarse_001_target_count_25 | 1224/4083 | 2014-12-16 | DQ | N/A | 20.64% |
| coarse_002_target_count_30 | 1625/4083 | 2016-08-12 | DQ | N/A | 66.02% |
| coarse_003_return_short_5 | 1111/4083 | 2014-07-07 | DQ | N/A | 26.47% |
| coarse_004_return_short_15 | 1108/4083 | 2014-07-02 | DQ | N/A | 57.66% |
| coarse_005_return_long_20 | 1892/4083 | 2017-09-18 | DQ | N/A | 124.65% |
| coarse_006_return_long_50 | 1031/4083 | 2014-03-12 | DQ | N/A | 23.85% |
| coarse_007_ema_fast_8 | 3855/4083 | 2025-10-16 | DQ | N/A | 1295.53% |
| coarse_008_ema_fast_15 | 2085/4083 | 2018-07-06 | DQ | N/A | 164.73% |
| coarse_009_ema_slow_20 | 3314/4083 | 2023-07-26 | DQ | N/A | 386.89% |
| coarse_010_ema_slow_50 | 1278/4083 | 2015-03-13 | DQ | N/A | 39.83% |
| coarse_011_macd_fast_6 | 1570/4083 | 2016-05-24 | DQ | N/A | 37.22% |
| coarse_012_macd_fast_10 | 3579/4083 | 2024-08-27 | DQ | N/A | 585.43% |
| coarse_013_macd_signal_3 | 1113/4083 | 2014-07-09 | DQ | N/A | 18.08% |
| coarse_014_macd_signal_7 | 1105/4083 | 2014-06-27 | DQ | N/A | 38.43% |
| coarse_015_momentum_weight_0p45 | 1513/4083 | 2016-03-01 | DQ | N/A | 43.02% |
| coarse_016_momentum_weight_0p65 | 1093/4083 | 2014-06-11 | DQ | N/A | 25.39% |
| coarse_017_long_return_fraction_0p1 | 897/4083 | 2013-08-20 | DQ | N/A | 24.67% |
| coarse_018_long_return_fraction_0p3 | 1420/4083 | 2015-10-08 | DQ | N/A | 36.76% |
| coarse_019_volume_low_0p6 | 2357/4083 | 2019-08-16 | DQ | N/A | 172.80% |
| coarse_020_volume_high_3p0 | 2460/4083 | 2020-01-16 | DQ | N/A | 300.03% |
| coarse_021_volatility_spike_ratio_1p5 | 1583/4083 | 2016-06-14 | DQ | N/A | 24.98% |
| coarse_022_volatility_spike_ratio_2p5 | 1105/4083 | 2014-06-27 | DQ | N/A | 39.94% |
| coarse_023_one_day_chase_return_0p03 | 1097/4083 | 2014-06-17 | DQ | N/A | 35.47% |
| coarse_024_one_day_chase_return_0p05 | 1113/4083 | 2014-07-09 | DQ | N/A | 73.09% |
| coarse_025_max_replacements_per_day_1 | 1091/4083 | 2014-06-09 | DQ | N/A | 67.02% |
| coarse_026_cash_guard_ratio_0p1 | 1146/4083 | 2014-08-26 | DQ | N/A | 37.81% |
| coarse_027_cash_guard_ratio_0p14 | 1889/4083 | 2017-09-13 | DQ | N/A | 170.56% |
| diagnostic_replacement_2 | 1284/4083 | 2015-03-23 | DQ | N/A | 51.10% |
| simple_momentum_reference | 1263/4083 | 2015-02-11 | DQ | N/A | 36.93% |
| x0352_daily_baseline | 1113/4083 | 2014-07-09 | DQ | N/A | 38.43% |

Evidence: [long_horizon.csv](../outputs/24d_diagnostics/long_horizon.csv) and [diagnostics_audit.json](../outputs/24d_diagnostics/diagnostics_audit.json).


Evidence: [final_selection.json](../outputs/24d/final_selection.json), [audit.json](../outputs/24d/audit.json). Audit: **EVERY_ATTEMPT_RECONSTRUCTED_BEFORE_SAVE**. [Full provenance and reproduction command](24d_strategy_summary.md).
