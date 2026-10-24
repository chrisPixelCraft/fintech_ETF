# Frozen 24D research candidate

SELECTED STRATEGY: `x0352_daily_baseline` (diagnostic fallback; **NO_ELIGIBLE_CANDIDATE**, not adopted)

SELECTION DATA: Development 2010-01-04–2019-11-01 starts; last episode ends 2019-12-04; validation 2020-01-02–2022-11-01 starts; last episode ends 2022-12-02.

HOLDOUT DATA: 2023-01-03–2024-11-01 starts; last episode ends 2024-12-04; selection chronology and boundary purges are recorded in final_selection.json.

`COMPETITION_UNIVERSE_STRESS_TEST`; `BLOCK_READY` / `BLOCK_SUBMISSION`. Returns use measured-valid complete episodes; counts retain failures, incomplete returns stay null. Formal compliance: **0 confirmed episodes**, `ACTIVE_SHARE_NOT_VERIFIED`. Assumptions and inherited selection confound: [main report](24d_strategy_summary.md).

Research artifact only; no trading/submission authorization. Results apply to this family, snapshot, execution proxy and episodes. Daily updates cannot silently change parameters.

[Frozen configuration](../configs/competition_24d_final.json) · [Configuration metadata](../configs/competition_24d_final_metadata.json) · [final_selection.json](../outputs/24d/final_selection.json)

Metadata records actual cutoff and separates inactive legacy dates/selection labels from active 24D settings.

## Frozen parameters

```text
cash_guard_ratio = 0.12
ema_fast = 10
ema_slow = 30
four_hour_mode = disabled
long_return_fraction = 0.2
macd_fast = 8
macd_signal = 5
macd_slow = 21
max_replacements_per_day = 0
momentum_weight = 0.55
one_day_chase_return = 0.04
replacement_margin = 0.2
return_long = 30
return_short = 10
target_count = 20
volatility_spike_ratio = 2.0
volume_high = 2.5
volume_low = 0.8
```

4H mode above is active; nested `coverage_only` is inactive metadata. Daily execution: `use_4h=False`, `match_4h_coverage=False`.

Base candidate configuration SHA256 (`outputs/24d/configs/x0352_daily_baseline.json`): `83d1930ee44932fcf7092dc8d2d7160426aa26bddb402631583017cb868ca926`. Selection freeze: `2026-09-23T15:32:31.728225+00:00`.

Final configuration SHA256 (`configs/competition_24d_final.json`): `a2e79e53e27e9cd09ffd476174f9f68fd6e4599fa1739afc925ab08709f7016c`.

Holdout measured-valid complete episodes: **18/23**, incomplete: 1; measured rule pass: 78.26%. Formal compliance confirmed: **0/23; UNKNOWN**.

- Returns: median 5.94%, mean 5.29%, P25 0.31%, P10 -2.93%, worst -3.93%, best 19.36%.
- Positive/loss: 77.78% / 22.22%; MDD median 3.78%, P90 7.81%, worst 9.29%.
- Median turnover 236.58%, trades 116.5; median days to valid portfolio among reached: 1.


Daily Open is a research proxy; capacity, official actions and source accuracy are unverified. Data-quality policy, calendar amendments and 2025–2026 selection confound: [main report](24d_strategy_summary.md).

Evidence: [final_selection.json](../outputs/24d/final_selection.json), [audit.json](../outputs/24d/audit.json). Audit: **EVERY_ATTEMPT_RECONSTRUCTED_BEFORE_SAVE**. [Full provenance and reproduction command](24d_strategy_summary.md).
