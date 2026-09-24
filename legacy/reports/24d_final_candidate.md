# Frozen 24D research candidate

SELECTED STRATEGY: `x0352_daily_baseline` (diagnostic fallback; **NO_ELIGIBLE_CANDIDATE**, not adopted)

SELECTION DATA: Development 2010-01-04–2019-11-01 starts; last episode ends 2019-12-04; validation 2020-01-02–2022-11-01 starts; last episode ends 2022-12-02.

HOLDOUT DATA: 2023-01-03–2024-11-01 starts; last episode ends 2024-12-04; selection chronology and boundary purges are recorded in final_selection.json.

24D MEDIAN RETURN: 5.94% (measured-valid 18/23 holdout)

24D P25 RETURN: 0.31% (measured-valid 18/23 holdout)

24D WORST RETURN: -3.93% (measured-valid 18/23 holdout)

POSITIVE EPISODE RATE: 77.78% (measured-valid 18/23 holdout)

COMPLIANCE PASS RATE: 0/23 formally confirmed; UNKNOWN. Measured rule pass: 18/23 (78.26%).

MEDIAN MDD: 3.78% (measured-valid 18/23 holdout)

`COMPETITION_UNIVERSE_STRESS_TEST`; `BLOCK_READY` / `BLOCK_SUBMISSION`. Return statistics condition on measured-valid complete episodes; valid/requested counts retain failures and incomplete returns stay null. Formal compliance has **0 confirmed episodes**; `ACTIVE_SHARE_NOT_VERIFIED`. Full assumptions and the inherited parameter-selection confound are in the [main report](24d_strategy_summary.md).

The selected fixed parameter set is a research artifact, not authorization to trade or submit. Its current result applies only to the tested candidate family, data snapshot, execution proxy and episode definitions. Daily data updates must not silently change the parameters.

[Frozen configuration](../configs/competition_24d_final.json) · [Configuration metadata](../configs/competition_24d_final_metadata.json) · [final_selection.json](../outputs/24d/final_selection.json)

The metadata companion records the actual data cutoff and distinguishes inactive legacy dates and selection labels from the active 24D configuration.

Recorded selection decision: **NO_ELIGIBLE_CANDIDATE**; ready status: **BLOCK_READY**.

## Frozen parameters

| Parameter | Value |
| --- | --- |
| cash_guard_ratio | 0.12 |
| ema_fast | 10 |
| ema_slow | 30 |
| four_hour_mode | disabled |
| long_return_fraction | 0.2 |
| macd_fast | 8 |
| macd_signal | 5 |
| macd_slow | 21 |
| max_replacements_per_day | 0 |
| momentum_weight | 0.55 |
| one_day_chase_return | 0.04 |
| replacement_margin | 0.2 |
| return_long | 30 |
| return_short | 10 |
| target_count | 20 |
| volatility_spike_ratio | 2.0 |
| volume_high | 2.5 |
| volume_low | 0.8 |

The active 4H mode is shown above. Nested `coverage_only` values preserve inactive legacy metadata; daily execution has `use_4h=False` and `match_4h_coverage=False`.

Base candidate configuration SHA256 (`outputs/24d/configs/x0352_daily_baseline.json`): `83d1930ee44932fcf7092dc8d2d7160426aa26bddb402631583017cb868ca926`. Selection freeze: `2026-09-23T15:32:31.728225+00:00`.

Final configuration SHA256 (`configs/competition_24d_final.json`): `a2e79e53e27e9cd09ffd476174f9f68fd6e4599fa1739afc925ab08709f7016c`.

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

Daily Open is a research fill proxy; capacity, official actions and source accuracy remain unverified. See [the main report](24d_strategy_summary.md) for data-quality policy, calendar amendments and the 2025–2026 inherited-selection confound.

Evidence: [final_selection.json](../outputs/24d/final_selection.json), [audit.json](../outputs/24d/audit.json). Audit: **EVERY_ATTEMPT_RECONSTRUCTED_BEFORE_SAVE**. [Full provenance and reproduction command](24d_strategy_summary.md).
