SELECTED STRATEGY: `x0352_daily_baseline` (diagnostic fallback; **NO_ELIGIBLE_CANDIDATE**, not adopted)

SELECTION DATA: Development 2010-01-04–2019-11-01 starts; last episode ends 2019-12-04; validation 2020-01-02–2022-11-01 starts; last episode ends 2022-12-02.

HOLDOUT DATA: 2023-01-03–2024-11-01 starts; last episode ends 2024-12-04; selection chronology and boundary purges are recorded in final_selection.json.

24D MEDIAN RETURN: 5.94% (measured-valid 18/23 holdout)

24D P25 RETURN: 0.31% (measured-valid 18/23 holdout)

24D WORST RETURN: -3.93% (measured-valid 18/23 holdout)

POSITIVE EPISODE RATE: 77.78% (measured-valid 18/23 holdout)

COMPLIANCE PASS RATE: 0/23 formally confirmed; UNKNOWN. Measured rule pass: 18/23 (78.26%).

MEDIAN MDD: 3.78% (measured-valid 18/23 holdout)

**Interpretation:** `COMPETITION_UNIVERSE_STRESS_TEST`. The 2026 official 150-stock whitelist is applied retrospectively; survivorship and composition look-ahead prevent a point-in-time deployability claim. Each episode starts from TWD 1 billion and zero holdings for 24 observed trading sessions.

**Metrics:** Return, drawdown, win/loss and turnover statistics below use only complete episodes passing the measured research checks. These include competition constraints plus stricter data and execution checks; a measured failure is not necessarily an official-rule violation. Coverage and pass rates retain every requested episode. Incomplete 24D returns stay null; partial forensic returns are not substitutes. ¹ All-complete median is a diagnostic that includes completed rule failures, so it is not compliant performance. Turnover is gross traded notional / initial cash.

**Formal status:** `BLOCK_READY` / `BLOCK_SUBMISSION`; `ACTIVE_SHARE_NOT_VERIFIED`. Zero episodes have confirmed formal compliance. Measured PASS and arithmetic audit PASS do not verify official Active Share, platform settlement, company actions, current announcements or operational submission.


## Holdout comparison

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

Baseline versus selected strategy: **no new strategy qualified**. The single column is the unchanged baseline diagnostic fallback.


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

Cold-start timing and tail-trim diagnostics are in the [baseline report](24d_x0352_baseline.md); the [recent-regime report](24d_recent_regime.md) separates 2024, 2025 and 2026.

The separate [family diagnostics](24d_parameter_search.md#full-family-post-freeze-diagnostics) cover **30 fixed configurations × 200 monthly starts**: 29 parameter sets plus 1 sanity reference. Only 28 configurations entered the frozen search; later diagnostics cannot change adoption.

Selection is restricted to the predeclared candidate neighborhood and measured feasibility; it does not establish the globally strongest strategy. Conditional returns must be read with the failed-episode counts.

Recorded selection decision: **NO_ELIGIBLE_CANDIDATE**. Candidate above may be a diagnostic fallback when no candidate clears the recorded threshold; ready status: **BLOCK_READY**.

## Execution and data limits

Decisions and lot sizing use D−1 information; fills use the day’s Open as an explicit research proxy, not the official transaction-average price. Both sides include 0.1425% commission; sells also pay 0.3% tax. No market-impact or queue model establishes fill capacity; high volume participation remains a material limitation. Cash dividends are terminal NAV credits rather than reinvestable cash.

Yahoo snapshot: **151/151 symbols**, **663 invalid rows** and **676 quality-flagged rows** retained. Calendar ends **2026-09-23**. `auto_adjust=False`, `actions=True`, `repair=True`; raw library responses and separate nominal-share derivatives are hash-checked. Reversing vendor future split factors restores nominal units only if Yahoo’s action history is complete and consistent. Vendor repair is ex-post and can internally reconstruct from intraday data; the strategy uses daily features only, with no 4H gate. The observed weekday calendar combines the 0050 reference with dates supported by at least 20 valid positive-volume official stocks; calendar_v2.json records 6 sessions restored where the ETF had no qualifying bar. An official exchange calendar remains unverified. The 0050 series is not a verified return benchmark; its unrepaired discontinuity prevents treating it as a clean investment baseline.

Observed severe price jumps are quarantined by the engine’s declared data-quality policy, with indicator warm-up restarted after the affected observation. This is a recorded research eligibility rule, not a repair or deletion of the vendor source. Missing historical names and failed episodes remain visible.

The frozen legacy **309.16% LONG_HORIZON_RETURN** came from a different period and model; it is not a **24D_EPISODE_RETURN** and supplies no evidence for the selection here.

**Inherited selection confound:** Original x0352 parameters were selected using **2025–2026 data**. The 2023–2024 holdout is excluded from this new search, but is **not a pristine prospective test of the strategy family**.

Evidence: [final_selection.json](../outputs/24d/final_selection.json), [audit.json](../outputs/24d/audit.json), [episodes.csv](../outputs/24d/episodes.csv), [candidates.csv](../outputs/24d/candidates.csv). Arithmetic audit status: **EVERY_ATTEMPT_RECONSTRUCTED_BEFORE_SAVE**.

Reproduce in a fresh output directory using the three commands in [README](../README.md#重現與驗證); they explicitly reuse `data/yahoo_daily/v3_20260923`. Check the delivered evidence with `.venv/bin/python scripts/verify_24d.py` and `.venv/bin/python scripts/report_24d.py --verify`.
Calendar evidence: [calendar_v2.json](../data/yahoo_daily/v3_20260923/calendar_v2.json). Rules: [fixed rule coverage](../docs/v2_double_check_rules.md).
The organizer event page returned HTTP 403 during verification; the [sponsor announcement](https://www.esunfhc.com/zh-tw/news-center/news-center/news/detail?id=1E8E47CF311B43E2B558F06B9CF79A55&p=C184F013F5EA4654A68BF10BF86AA6F5) supports general mechanics only, not a verification of new detailed platform rules.
Additive diagnostics: [supplement audit](../outputs/24d_supplement/supplement_audit.json). The frozen primary selection and its candidate count are unchanged.
Canonical episode tables: [monthly_episodes.csv](../outputs/24d/monthly_episodes.csv) (200 rows), [rolling_episodes.csv](../outputs/24d/rolling_episodes.csv) (4060 rows), [oct_nov_episodes.csv](../outputs/24d/oct_nov_episodes.csv) (16 rows), [recent_regime.csv](../outputs/24d/recent_regime.csv) (40 rows); [canonical_aliases.json](../outputs/24d/canonical_aliases.json) records source hashes and null-value policy.
Full-family post-freeze evidence: [diagnostic seal](../outputs/24d_diagnostics/diagnostics_audit.json). No adoption or retuning is permitted from these results.

Details: [baseline](24d_x0352_baseline.md), [search](24d_parameter_search.md), [validation](24d_validation.md), [holdout](24d_holdout.md), [recent regime](24d_recent_regime.md), [seasonal analogues](24d_oct_nov_analogs.md), [failures](24d_failure_analysis.md), [frozen candidate](24d_final_candidate.md).

## Evaluation-only sanity reference

The simple-momentum reference uses the same dates, filters, planner and costs; it did not enter parameter selection.

| Set | Candidate | Measured valid / requested | Incomplete | Median | Mean | P25 | P10 | Worst |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| all_monthly | x0352_daily_baseline | 137/200 | 13 | 2.74% | 3.10% | -1.21% | -5.00% | -14.96% |
| all_monthly | simple_momentum_reference | 137/200 | 13 | 2.32% | 2.93% | -1.23% | -5.19% | -14.10% |
