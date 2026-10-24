# Frozen Mom20 regression

**HOLD for official v4.3 readiness.** Mom20 reproduces prior results; historical Active Share and real organizer DataPack integration remain unverified. Zero parameter searches or changes.

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

[baseline.json](baseline.json): episode summaries, input hashes, market provenance, warnings, exact splits. Daily parity: [baseline_days.csv](baseline_days.csv).

- `production/strategy.json`: Mom20, top 25, invest 95%, keep rank 35, turnover gate 10%, freeze last 3 sessions.
- `competition.backtest.run_episode` is compared with daily D-Plans produced and validated by `production.replay.replay_episode`: targets, orders, holdings, cash, NAV.
- No candidate comparison: evidence supports incumbent reproducibility, not global optimality.

## Constraints and warnings

| Item | Coverage |
|---|---|
| Completed trade fills | 4,163 official VWAP; 673 HLC3 proxies |
| Historical warning days | 1; no disqualification |
| Position count | 0–28 across all days; the zero-position day was warned |
| Cash fraction | 1.0605%–100%; the 100% day was warned |
| Active Share | Unverified: no ETF holdings supplied to replay |
| Official v4.3 DataPack | No real account/settlement fixture supplied |

2025-08-01, `holdout_2025_08`: seven `UNFILLED_NO_PRICE` orders, 18 `REJECTED_FILL` records. All daily transactions are rejected with one warning, leaving cash only. Both paths agree: **parity does not establish daily compliance.**

`production/replay.py:69` builds `DayInput` without `etfs` / `required_etfs`; `production/engine.py:140` rejects `INVALID`, but permits `ACTIVE_SHARE_UNVERIFIED` on the normal path. “All days normal” therefore cannot certify Active Share. Parent integration must enforce top-15/15% and incomplete-data behavior.

All 71 tests passed; one `ResourceWarning` for an unclosed registry CSV at `tests/test_run_experiment.py:71`.

## Reproduce

From repository root:

```bash
PYTHONHASHSEED=0 .venv/bin/python -m unittest discover -s tests -v
PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/rules_v43_validation/run_baseline.py
```

Writes stay in this results folder; registry, holdout log, prior replays and production config are unchanged. `baseline.json` records explicit holdout use: already-observed regression data, zero tuning trials.

Two setup attempts failed: missing `PYTHONPATH=.` stopped before any episode; default dev began January 2010 with insufficient input, yielding `EMERGENCY_REVIEW_REQUIRED`, then `dplan.build` raised `KeyError` for the missing mode. Completed dev uses the documented 2019+ floor; production code unchanged.

## Interpretation limits

- Monthly / mid-month 24-day windows overlap; 1,104 day observations are dependent.
- Applying the 2026 universe historically creates survivorship / universe-selection bias.
- Repeatedly examined 2025–2026 holdout cannot support new tuning or fresh out-of-sample claims.
- Auto execution mixes official prices / proxies; returns are not all official-price.
- Shared tree with concurrent rule integration; strategy / rules loaded once, source not immutable. The 71-test result predates integration; parent must run the final integrated suite.
- Untested: live fetch, submission, trading, final-schema certification, historical ETF membership.

**Decision:** provisionally retain frozen signal. Before submission, complete rules/DataPack integration and integrated compliance tests; returns / parity cannot certify readiness.
