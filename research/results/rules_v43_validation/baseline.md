# Frozen Mom20 regression

**HOLD for official v4.3 readiness.** Frozen Mom20 reproduces its previous results, but this replay does not verify historical Active Share or real organizer DataPack integration. No strategy parameters were searched or changed.

## Evidence

| Check | Observed result |
|---|---|
| Existing unit tests | 71 passed; 15.470 seconds |
| Dev, 2019–2021 | 3 windows × 24 days; mean +11.8244% |
| Validation, 2022–2024 | 3 windows × 24 days; mean +4.0850% |
| Previously seen holdout | 40 windows × 24 days; mean +8.7940%, median +10.9855% |
| Production/canonical parity | All 46 windows, 1,104 day observations passed |
| NAV/cash differences | Maximum 0 TWD; tolerance 0.000001 TWD |
| Independent ledger verification | 46 episodes; zero problems |
| Runtime | 25.543 seconds, including data loading |

Machine evidence: [baseline.json](baseline.json), with per-episode summaries, input hashes, market provenance, warnings and exact split membership. Daily parity evidence: [baseline_days.csv](baseline_days.csv).

- The strategy is `production/strategy.json`: Mom20, top 25, target investment 95%, keep rank 35, turnover gate 10%, freeze last 3 sessions.
- Each episode runs canonical `competition.backtest.run_episode`, then `production.replay.replay_episode` produces and validates daily D-Plans and compares targets, orders, holdings, cash and NAV.
- No candidate comparison was performed. These observations support reproducibility of the incumbent, not a new claim of global optimality.

## Constraints and warnings

| Item | Coverage |
|---|---|
| Completed trade fills | 4,163 official VWAP; 673 HLC3 proxies |
| Historical warning days | 1; no disqualification |
| Position count | 0–28 across all days; the zero-position day was warned |
| Cash fraction | 1.0605%–100%; the 100% day was warned |
| Active Share | Unverified: no ETF holdings supplied to replay |
| Official v4.3 DataPack | No real account/settlement fixture supplied |

On 2025-08-01, `holdout_2025_08` has seven `UNFILLED_NO_PRICE` orders and 18 `REJECTED_FILL` records. The simulator rejects the day's transactions and records one warning; the portfolio remains cash-only that day. Production and canonical paths agree about this failure. **Parity therefore does not establish daily compliance.**

`production/replay.py:69` constructs `DayInput` without `etfs` or `required_etfs`. `production/engine.py:140` rejects `INVALID`; an `ACTIVE_SHARE_UNVERIFIED` result can remain on the normal path. Consequently, the replay's “all days normal” check must not be interpreted as an Active Share pass. Parent integration must separately enforce the new top-15/15% rule and incomplete-data behavior.

The unit suite emitted one `ResourceWarning` for an unclosed registry CSV in `tests/test_run_experiment.py:71`; all 71 tests passed.

## Reproduce

Run from the repository root:

```bash
PYTHONHASHSEED=0 .venv/bin/python -m unittest discover -s tests -v
PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/rules_v43_validation/run_baseline.py
```

The runner writes only this results folder and does not update the research registry, holdout access log, existing replay artifacts or production configuration. Its explicit holdout use is recorded in `baseline.json`: this is a replay regression on already-observed data, with zero tuning trials.

Two setup attempts preceded the completed run. A direct script invocation without `PYTHONPATH=.` failed before running an episode. An initial default dev selection started in January 2010, where insufficient input led to `EMERGENCY_REVIEW_REQUIRED`; existing `dplan.build` then raised `KeyError` because its mode lookup omitted that value. The completed dev selection uses the existing documented 2019+ evaluation floor. Production code was not changed by this subtask.

## Interpretation limits

- Monthly and mid-month 24-day windows overlap. The 1,104 day observations are not independent samples.
- The fixed 2026 universe is applied to earlier years, creating survivorship and universe-selection bias.
- The 2025–2026 holdout has already been examined repeatedly. It is unsuitable for new tuning or fresh out-of-sample claims.
- Auto execution mixes official prices with proxies; observed returns are not all official-price returns.
- This task ran in a shared working tree while other agents integrated rule changes. Strategy and rules were loaded once; source was not isolated in an immutable checkout. The 71-test result predates integration. Parent must run the final integrated suite before acceptance.
- No live data fetch, submission, trading, final-schema certification or historical ETF membership test was performed.

**Decision:** retain the frozen signal provisionally; do not infer v4.3 submission readiness from returns or parity. Complete rules/DataPack integration and run the integrated compliance suite before submission.
