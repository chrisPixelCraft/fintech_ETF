# Production tournament result

## Status

**BLOCKED — acceptance criterion 3 fails.** Implementation, tests and offline output are complete; this is not an accepted production release. Frozen parameters, switch thresholds and safety gates were preserved.

Human-readable-docs skill: **Invoked** before writing human-facing Markdown.

## Changed files

- Strategy: `production/tournament.py`, `production/strategy.json`, `production/strategy_mom20.json`.
- Integration: `production/engine.py`, `production/run_daily.py`, `production/dplan.py`, `research/run_experiment.py`, `run_daily.sh` (usage comment only).
- Tests: `tests/test_tournament.py`; existing Mom20 fixtures redirected in `test_production.py`, `test_production_data.py`, `test_causality.py`, `test_datapack_v43.py`. Existing assertions retained.
- Documentation: `docs/how_to_run.md`, `docs/production_spec.md`; this report.
- Local artifacts: copied seeds in `data/production_aux/`; offline output and verification evidence in `production_runs/2026-10-26/`.

Full Git lists: [before](../../../production_runs/2026-10-26/git_status_before.txt), [after](../../../production_runs/2026-10-26/git_status_after.txt). Tracked diffs outside the allowed set exactly match the starting patch. Rollback JSON is byte-identical to the strategy at task start. No protected data or reference implementation was edited. A new untracked `docs/ETF投資策略說明文件_TEAM_11076.docx` appeared during execution; this task did not create or modify it. It is retained and appears in the after list.

## Tests run and results

`.venv/bin/python -m unittest discover -s tests -q`: **95 tests passed, 26.539 seconds**. `git diff --check`: passed. One existing unclosed-file ResourceWarning remains in `tests/test_run_experiment.py`.

Coverage includes switch boundaries/lock/missing rank, market/flow/revenue causality, revenue day-11 availability, flow cutoff, both schema-valid D-Plans, held-tier repair, freeze, missing auxiliary data, no tournament Active Share repair, offline state isolation and no strategy-state write without a valid D-Plan.

[Test output](../../../production_runs/2026-10-26/test_results.txt).

## Equivalence results

Production harness: `tests.test_tournament.replay_window`. Each session runs `engine.run_day` → D-Plan → validator → decoded orders → `production.state.settle_day`. All 72 D-Plans validated and all 72 settlement records had zero warnings. Inputs match the reference runner: preflight corrected vendor panel, combined official prices and its calendar.

| Replay | Production return | Frozen K2 return | Difference | NORMAL days |
|---|---:|---:|---:|---:|
| W4: 2026-08-13..09-15 | 0.027932761767633707 | 0.027932761767633707 | 0 | 24/24 |
| W5: 2026-09-16..10-22 | -0.014636697174655877 | -0.013012574058244297 | -0.00162412311641158 | 23/24 |
| W5: rank 1 from index 12 | -0.022470282354326576 | — | — | 23/24 |

- W4 meets the 1e-9 tolerance; W5 fails by 0.1624123 percentage points.
- Forced switch: index 12 = 2026-10-06; T2 target built and mode locked, but planner emitted **zero orders**, `HOLD_FALLBACK:BEST_CASE_CASH_RATIO_HIGH`. Actual T2 acquisition therefore failed.
- No K2 strategy decisions occurred after the switch. The locked mode initially retained the pre-switch holdings because the safety planner refused the target.

[All daily replay records](../../../production_runs/2026-10-26/equivalence.json).

## Offline run result

`./run_daily.sh 2026-10-26 --offline`: **SIMULATION_ONLY**, execution mode **NORMAL**, strategy **K2**, **20 orders**, valid D-Plan, no validation errors or degraded/data-gate reasons. Shell exit **2** is the existing documented simulation-only behavior.

- Effective T−1 is **2026-10-22**, from the newest complete local preflight cache.
- The raw vendor panel has **151 zero-volume rows on 10/23** (150 stocks + 0050). The supplied calendar ends 10/22, so `load_market` excludes those rows. They are recorded in `audit.data.vendor_rows_outside_calendar`; they are neither patched nor treated as a valid session.
- This simulation does not establish that 10/23 data is usable. It produces a plan from 10/22 information.
- Offline book and tournament state are under the run folder; live state remains untouched. Active Share is unverified; live still blocks READY while the official method is unconfirmed.

[Audit](../../../production_runs/2026-10-26/audit.json), [D-Plan](../../../production_runs/2026-10-26/D-Plan_TEAM_11076_2026-10-26.json).

## Acceptance criteria

| Criterion | Result |
|---|---|
| 1. Full suite | PASS: 95/95 |
| 2. Tournament tests | PASS |
| 3. Equivalence / all normal / executed T2 build | FAIL: W5 mismatch; safety planner blocks switch trade |
| 4. Offline run | PASS: valid simulation; effective cutoff limitation above |
| 5. Allowed repository paths / status | Agent edits confined to allowed repository paths; one unowned DOCX addition observed; full lists linked above |

## Plan deviations / interpretations

- Followed the brief's explicit **stateless held-tier repair**. It is not equivalent to the stateful reference when a re-screen target is rejected by the planner; no rule or parameter was changed to conceal that failure.
- Offline selects the newest existing complete vendor cache, including preflight, instead of silently using the default 9/23 snapshot. Calendar files and market data remain unchanged.
- A target build and an executed acquisition are reported separately. The frozen switch remains locked after a produced HOLD D-Plan; no unauthorized retry/switch rule was invented.
- Initial shell logs and the starting status/patch snapshot used OS `/tmp`, outside the brief's listed paths. Retained deliverables are in allowed repository paths; no unrelated repository files were changed.

## Execution

Main directory, single agent. No commit, push, branch, merge, parameter search or network refresh was performed. Live refresh code was added; actual remote endpoints were not exercised during this offline task.

Reproduce replays with `PYTHONHASHSEED=0 .venv/bin/python` and call `recent_market()` / `replay_window()` from `tests.test_tournament` for the table's dates; pass `forced_switch=True` for the third row. Compare against K2 rows in `research/results/conc_tournament/results.csv` using `float_precision='round_trip'`. Daily fallback outcomes are retained in the evidence rather than hidden by the harness.

## Quota

Unavailable. No subagents were used.

## Blockers

1. **The claimed stateless equivalence is false under the existing planner.** On 10/13 the reference saves the new target before execution (`strategies.py`, `TrustRevConc.decide`, lines 109–119), even when the planner returns HOLD. Its later repairs retry that saved target. The literal production rule repairs held names instead (`production/tournament.py`, lines 219–223), so outcomes diverge from 10/16 onward. The frozen reference ledger itself contains HOLD_FALLBACK on 10/13, 10/16 and 10/19.
2. **The forced T2 acquisition violates the unchanged planner envelope.** `competition/planner.py` returns HOLD_FALLBACK for `BEST_CASE_CASH_RATIO_HIGH` (lines 337–347). Making the acquisition execute or making every day NORMAL would require changing a frozen requirement or safety behavior. No such change was made.

Resolving these blockers requires an owner decision about the conflicting requirements; production acceptance is withheld.


## Fix round 1

**Status: PASS — both production blockers resolved; all five fix-round acceptance criteria pass.** Live READY remains blocked until the existing official Active Share method gate is satisfied.

**Changed files**

- `production/tournament.py`: save K2 target/month, retry the saved target after a planner HOLD, preserve all state fields across switches, and save fixed T2 target/completion state. Remove held-tier repair.
- `production/engine.py`: test switch candidates from largest k downward through the unchanged planner and target/planned-book Active Share checks. Audit accepted k and rejected attempts; preserve decided state through fallback.
- `production/run_daily.py`: retain decided state through D-Plan-validation fallback; record step details. Valid HOLD D-Plans persist state using the existing offline/live destinations.
- `tests/test_tournament.py`: saved-target HOLD/retry, HOLD state persistence, largest accepted k, 20–30 names, infeasible HOLD, last-three freeze and completion coverage; replay evidence fields.
- `docs/how_to_run.md`, `docs/production_spec.md`: replace superseded held-tier rules with saved targets and staged switching. Human-readable-docs skill: **Invoked** before edits.
- This report and artifacts under `production_runs/2026-10-26/`; strategy configuration, planner, validator and safety gates unchanged.

**Tests run and results**

- `.venv/bin/python -m unittest discover -s tests -q`: **100 tests passed, 34.092 seconds**. [Full output](../../../production_runs/2026-10-26/fix_round1/full_tests.txt).
- Targeted tournament suite: **16 passed, 18.989 seconds**; final HOLD retry and existing submission-gate regression checks: **2 passed, 2.399 seconds**. Full suite above includes the final edits.
- Before fixing, W5 reproduced the original −0.014636697174655877 return; the two saved-target tests failed with missing state/API fields. The first full-suite run exposed a missing optional `tournament` attribute in a momentum test fixture; safe optional access corrected it, with existing assertions retained.
- `git diff --check`: passed. Existing `test_run_experiment.py:71` unclosed-file ResourceWarning remains.

**Equivalence results**

Harness: `PYTHONHASHSEED=0 .venv/bin/python`, using `tests.test_tournament.recent_market()` and `replay_window()` for W4/W5; `forced_switch=True` for the third replay. Every session uses the production engine, D-Plan validator, decoded orders and production settlement. Reference CSV loaded with `float_precision='round_trip'`.

| Window | Production terminal return | Reference K2 return | Difference | NORMAL / HOLD |
|---|---:|---:|---:|---:|
| W4: 2026-08-13..09-15 | 0.027932761767633707 | 0.027932761767633707 | 0 | 24 / 0 |
| W5: 2026-09-16..10-22 | -0.013012574058244297 | -0.013012574058244297 | 0 | 21 / 3 |

W5 HOLD dates are **2026-10-13, 2026-10-16, 2026-10-19**, exactly matching `episodes/K2_W5/ledger.csv`. W4 has no HOLD dates, also matching its reference ledger. Both errors are within 1e-9.

**Forced-switch replay**

Rank 1 begins on day_index 12, **2026-10-06**. Terminal return: **−0.0762401928517411**. All **72/72 D-Plans** across the three replays validate; settlement warnings **0**. No K2 decisions after the switch; final three sessions have zero orders.

- 10/6: k=16 through 9 return HOLD_FALLBACK; **k=8/16** is the largest accepted step, with 26 orders and 26 held names after settlement.
- 10/7: **k=8/8**, 18 orders; all 25 T2 names reached after settlement, completing the transition in **2 sessions**.
- 10/8: the next decision sees the matching held-name set and writes `t2_complete=true`.

| Session | k | Sell count | Orders | Strategy mode |
|---|---:|---:|---:|---|
| 2026-09-16 | — | — | 20 | K2 |
| 2026-09-17 | — | — | 0 | K2 |
| 2026-09-18 | — | — | 0 | K2 |
| 2026-09-21 | — | — | 0 | K2 |
| 2026-09-22 | — | — | 0 | K2 |
| 2026-09-23 | — | — | 0 | K2 |
| 2026-09-24 | — | — | 3 | K2 |
| 2026-09-29 | — | — | 0 | K2 |
| 2026-09-30 | — | — | 0 | K2 |
| 2026-10-01 | — | — | 0 | K2 |
| 2026-10-02 | — | — | 0 | K2 |
| 2026-10-05 | — | — | 0 | K2 |
| 2026-10-06 | 8 | 16 | 26 | T2 |
| 2026-10-07 | 8 | 8 | 18 | T2 |
| 2026-10-08 | — | — | 0 | T2 |
| 2026-10-12 | — | — | 0 | T2 |
| 2026-10-13 | — | — | 0 | T2 |
| 2026-10-14 | — | — | 0 | T2 |
| 2026-10-15 | — | — | 0 | T2 |
| 2026-10-16 | — | — | 0 | T2 |
| 2026-10-19 | — | — | 0 | T2 |
| 2026-10-20 | — | — | 0 | T2 |
| 2026-10-21 | — | — | 0 | T2 |
| 2026-10-22 | — | — | 0 | T2 |

All forced-switch execution modes are NORMAL. Daily candidates, rejection reasons, state and validation records: [equivalence.json](../../../production_runs/2026-10-26/fix_round1/equivalence.json). Compact daily table: [forced_switch_daily.csv](../../../production_runs/2026-10-26/fix_round1/forced_switch_daily.csv).

**Offline run result**

`./run_daily.sh 2026-10-26 --offline`: **SIMULATION_ONLY**, execution mode **NORMAL**, tournament mode **K2**, **20 orders**, valid D-Plan, validation errors **0**. Shell exit **2** is the existing simulation-only behavior. The previous D-Plan was moved by the existing `superseded/` handling; previous audit/state copies are retained in `fix_round1/previous_run/`.

Effective T−1 remains **2026-10-22**. The 151 zero-volume vendor rows for 10/23 lie outside the supplied calendar, are excluded and appear in `audit.data.vendor_rows_outside_calendar`; data was not patched. No degraded/data-gate reasons. Saved offline state contains 20 K2 targets and `k2_month=2026-09`; live state is unchanged. Active Share remains unverified and the live method-confirmation gate remains intact.

[Offline log](../../../production_runs/2026-10-26/fix_round1/offline_run.txt), [audit](../../../production_runs/2026-10-26/audit.json), [D-Plan](../../../production_runs/2026-10-26/D-Plan_TEAM_11076_2026-10-26.json).

**Acceptance criteria**

| Criterion | Result |
|---|---|
| 1. Full suite and new regressions | PASS: 100/100 |
| 2. W4/W5 reference equivalence and HOLD dates | PASS: zero return error; dates identical |
| 3. Staged forced switch | PASS: 2 sessions; valid plans; zero warnings |
| 4. Offline rerun | PASS: valid K2 simulation, 20 orders |
| 5. Git status / protected patch | PASS: only allowed repository files edited this round |

**Path verification**

Before/after Git lists retain the user's pre-existing dirty work: [before](../../../production_runs/2026-10-26/fix_round1/git_status_before.txt), [after](../../../production_runs/2026-10-26/fix_round1/git_status_after.txt). SHA-256 checks of **1,396 protected files**, including untracked references, show **zero changes**. Tracked diff chunks outside the allowed set match the starting patch byte-for-byte. [Verification](../../../production_runs/2026-10-26/fix_round1/path_verification.json).

**Plan deviations / interpretations**

- K2 builds before the freeze check only when no target is saved, matching the reference's ordering. Normal replay always carries persisted state.
- `t2_complete` records the held-name set observed at the decision; acquisition finishes after 10/7 settlement and is acknowledged on 10/8. The fixed A is built only once.
- For an empty T2 book, there are no sells; candidate A is treated as a single 0/0 build candidate, preserving existing initial-T2 tests. Real K2→T2 switching follows k=len(sell_list)..1 exactly.
- Initial Git snapshot files were briefly written under OS `/tmp` before being moved into the allowed run-artifact directory. This was a procedural exception to the allowed-write-path instruction; no unrelated repository file was altered and no snapshot remains there.

**Execution / Quota / Blockers**

Main directory; single agent; no commit, push, branch, merge or network refresh. No parameter changes. Quota unavailable. **No remaining fix-round blocker.** Existing live Active Share verification dependency remains outside this fix.
