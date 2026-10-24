# Live Active Share Gate Report

## Status

Completed. Live runs can reach `READY_TO_SUBMIT` through the frozen heuristic policy when every other gate passes.

- Human-readable-docs: **Invoked** before Markdown edits; [installed skill](/Users/chrishsieh/.agents/skills/human-readable-docs/SKILL.md).
- Gate evidence: `production/run_daily.py:166` (`live_active_share_gate`) and `:193` (`record_active_share_gate`).
- Final-book recheck: `production/run_daily.py:315`; audit records the report used after validation fallback.
- Settings: `production/settings.json:11`; `heuristic_min=0.30`, `allow_top_n=10`, source `MoneyDJ top-10 auto-fetch`.

## Changed files

| File | Change |
|---|---|
| `production/run_daily.py` | Live heuristic gate, final fallback recheck, audit outcomes and failing conditions |
| `production/settings.json` | Frozen live AS policy values |
| `tests/test_live_as_gate.py` | Ten network-free gate regression tests |
| `docs/how_to_run.md` | Auto-fetch workflow and live eligibility |
| `docs/rules_v43.md` | Heuristic rule and audit outcomes |
| `docs/production_spec.md` | Current policy; corrected unconditional-block statements |
| `research/results/conc_tournament/CODEX_LIVE_AS_REPORT.md` | Execution and acceptance evidence |

`production/active_share.py`, strategy, engine repair logic, planner, validator, and DataPack checks were not edited. Official/internal thresholds remain 15%/20%, TOP remains 15, and freshness remains 7 days. No commit, push, branch, or merge was performed.

## Tests

| Command | Result | Time |
|---|---|---|
| `.venv/bin/python -B -m unittest tests.test_live_as_gate tests.test_datapack_v43 -q` | 19 passed | 1.770s |
| `.venv/bin/python -m unittest discover -s tests -q` | 111 passed | 40.943s |

The full-suite command ran with `PYTHONDONTWRITEBYTECODE=1` to avoid bytecode writes. The suite emitted a non-failing `ResourceWarning` for an unclosed read handle at `tests/test_run_experiment.py:71`; that unrelated test was not changed.

New tests use local market data, mocked network/data acquisition, and temporary output directories. The success cases exercise the real engine, planner, AS checker, D-Plan builder, validator, and writer. The 25% case controls AS values; the fallback regression controls the final fallback report and validation responses. These tests establish gate behavior without asserting live MoneyDJ availability.

## Acceptance table

Evidence below refers to `tests/test_live_as_gate.py` test methods.

| Requirement | Result | Evidence |
|---|---|---|
| All required ETFs, fresh top-10, minimum ≥30% | PASS: READY; D-Plan written; heuristic reason and gate | `test_live_top_ten_writes_valid_dplan` |
| Minimum =25% | PASS: BLOCKED; no D-Plan; minimum failure recorded | `test_minimum_point_two_five_blocks` |
| Required ETF missing after fetch failure | PASS: BLOCKED; fetch failure retained | `test_missing_fetch_blocks` |
| Data 8 days before T | PASS: BLOCKED; stale ETFs listed | `test_eight_day_old_data_blocks` |
| ETF holdings below 10 | PASS: BLOCKED; short holdings listed | `test_nine_holdings_blocks` |
| Offline behavior | PASS: SIMULATION_ONLY | `test_offline_remains_simulation` |
| Supplied complete top-15 CSV | PASS: READY; fetch skipped | `test_supplied_complete_top_fifteen_works` |
| Fallback minimum falls to25% | PASS: final audit BLOCKED; no D-Plan or stale heuristic-pass reason | `test_final_fallback_report_blocks` |
| Validator rejects otherwise eligible plan | PASS: submission stays blocked | `test_validator_rejection_still_blocks` |
| Exactly30%, non-finite minimum, invalid data, official path | PASS: boundary accepted; invalid cases blocked; official PASS/CAUTION retained | `test_boundary_invalid_and_official_policy` |

Heuristic passes retain `ACTIVE_SHARE_METHOD_UNCONFIRMED` as informational. Failures preserve `LIVE_ACTIVE_SHARE_BLOCKED` and add explicit `active_share_gate_failures`. Official confirmed acceptance retains the prior status predicate and records `OFFICIAL_PASS`.

## Plan deviations

None. Code discovery MCP was unavailable in the installed tool inventory; local searches and source reads were used. No external research or live submission was needed.

## Blockers

None.
