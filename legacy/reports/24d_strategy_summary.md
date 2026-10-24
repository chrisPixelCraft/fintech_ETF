# 24D strategy: no eligible candidate

**NO_ELIGIBLE_CANDIDATE**; `x0352_daily_baseline` is the unchanged diagnostic fallback, not adopted. Formal status: `BLOCK_READY` / `BLOCK_SUBMISSION`; `ACTIVE_SHARE_NOT_VERIFIED`. Formally confirmed: 0/23, UNKNOWN. Measured holdout pass: 18/23 (78.26%).

Selection starts: development 2010-01-04–2019-11-01 (last end 2019-12-04); validation 2020-01-02–2022-11-01 (last end 2022-12-02). Holdout starts 2023-01-03–2024-11-01 (last end 2024-12-04). final_selection.json records chronology and boundary purges.

**Interpretation:** `COMPETITION_UNIVERSE_STRESS_TEST`: retrospectively applying the 2026 official 150-stock whitelist creates survivorship/composition look-ahead; no point-in-time deployability claim. Episodes start at TWD 1 billion, zero holdings, for 24 observed sessions.

**Metric scope:** Returns, drawdowns, win/loss and turnover use complete, measured-valid episodes only. Checks include competition constraints and stricter data/execution rules; measured failure need not mean official violation. Coverage/pass rates retain all requests; incomplete 24D returns stay null, never replaced by partial forensic returns. ¹ All-complete median includes completed rule failures and is diagnostic, not compliant performance. Turnover = gross traded notional / initial cash. Arithmetic/measured PASS cannot verify official Active Share, platform settlement, company actions, current announcements or submission.

## Holdout comparison

Returns: median 5.94%, mean 5.29%, P25 0.31%, P10 -2.93%, worst -3.93%, best 19.36%. Positive/loss episodes: 77.78% / 22.22%.

Risk: median MDD 3.78%, worst 9.29%, P90 7.81%. Median turnover 236.58%, trade count 116.5, days to valid portfolio (among reached) 1.

Coverage: measured valid 18/23 (78.26%); incomplete 1; formal compliance confirmed 0/23, UNKNOWN.

Single column: unchanged baseline fallback; no new strategy qualified.

## Chronological results

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

[Baseline](24d_x0352_baseline.md): cold-start/tail-trim diagnostics. [Recent regime](24d_recent_regime.md): separate 2024, 2025, 2026.

[Family diagnostics](24d_parameter_search.md#full-family-post-freeze-diagnostics): **30 fixed configurations × 200 monthly starts** (29 parameter sets + 1 sanity reference). Frozen search used 28; later diagnostics cannot change adoption.

Selection covers the predeclared neighbourhood and measured feasibility only, not global optimality. Read conditional returns alongside failure counts.



## Execution and data limits

D−1 decisions/sizing; same-day Open fills are an explicit research proxy for official transaction-average execution. Commission: 0.1425% each side; sell tax: 0.3%. No impact/queue model establishes capacity; high participation remains material. Dividends credit terminal NAV only.

Yahoo: **151/151 symbols**; retain **663 invalid** / **676 quality-flagged rows**. Calendar ends **2026-09-23**. Hash-check raw responses and nominal-share derivatives with `auto_adjust=False`, `actions=True`, `repair=True`. Reversing future split factors restores nominal units only if action history is complete/consistent. Ex-post vendor repair may reconstruct intraday data; strategy features are daily, without a 4H gate. Weekday calendar combines 0050 with dates having ≥20 valid positive-volume official stocks; calendar_v2.json restores 6 sessions missing qualifying ETF bars. Official calendar unverified. The unrepaired 0050 discontinuity prevents a clean return-benchmark claim.

Declared data-quality policy quarantines severe jumps and restarts indicator warm-up; it changes research eligibility, not vendor data. Missing historical names and failures remain visible.

Legacy **309.16% LONG_HORIZON_RETURN** uses another period/model; it is neither **24D_EPISODE_RETURN** nor selection evidence.

**Inherited confound:** x0352 parameters used **2025–2026 data**. New search excludes 2023–2024 holdout, but it remains **not a pristine prospective family test**.

Evidence: [final_selection.json](../outputs/24d/final_selection.json), [audit.json](../outputs/24d/audit.json), [episodes.csv](../outputs/24d/episodes.csv), [candidates.csv](../outputs/24d/candidates.csv). Arithmetic audit status: **EVERY_ATTEMPT_RECONSTRUCTED_BEFORE_SAVE**.

Reproduce in a fresh directory with three [README commands](../README.md#重現與驗證), reusing `data/yahoo_daily/v3_20260923`. Verify: `.venv/bin/python scripts/verify_24d.py` and `.venv/bin/python scripts/report_24d.py --verify`.
Calendar evidence: [calendar_v2.json](../data/yahoo_daily/v3_20260923/calendar_v2.json). Rules: [fixed rule coverage](../docs/v2_double_check_rules.md).
Organizer page: HTTP 403 during verification. The [sponsor announcement](https://www.esunfhc.com/zh-tw/news-center/news-center/news/detail?id=1E8E47CF311B43E2B558F06B9CF79A55&p=C184F013F5EA4654A68BF10BF86AA6F5) supports general mechanics only, not new detailed platform rules.
[Supplement audit](../outputs/24d_supplement/supplement_audit.json): additive diagnostics; frozen selection/count unchanged.
Canonical tables: [monthly_episodes.csv](../outputs/24d/monthly_episodes.csv) (200 rows), [rolling_episodes.csv](../outputs/24d/rolling_episodes.csv) (4060 rows), [oct_nov_episodes.csv](../outputs/24d/oct_nov_episodes.csv) (16 rows), [recent_regime.csv](../outputs/24d/recent_regime.csv) (40 rows); [canonical_aliases.json](../outputs/24d/canonical_aliases.json) records source hashes/null policy.
[Post-freeze diagnostic seal](../outputs/24d_diagnostics/diagnostics_audit.json): no adoption or retuning from these results.

Details: [baseline](24d_x0352_baseline.md), [search](24d_parameter_search.md), [validation](24d_validation.md), [holdout](24d_holdout.md), [recent regime](24d_recent_regime.md), [seasonal analogues](24d_oct_nov_analogs.md), [failures](24d_failure_analysis.md), [frozen candidate](24d_final_candidate.md).

## Evaluation-only sanity reference

Simple momentum shares dates, filters, planner and costs; excluded from selection.

| Set | Candidate | Measured valid / requested | Incomplete | Median | Mean | P25 | P10 | Worst |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_monthly | x0352_daily_baseline | 137/200 | 13 | 2.74% | 3.10% | -1.21% | -5.00% | -14.96% |
| all_monthly | simple_momentum_reference | 137/200 | 13 | 2.32% | 2.93% | -1.23% | -5.19% | -14.10% |
