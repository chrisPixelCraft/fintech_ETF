# Two-day leaderboard switch report

## Status

**PASS — the frozen switch now requires rank ≤6 on two consecutive trading dates.**

- Base HEAD: `f4b59ee`; execution stayed in the main working directory.
- Human-readable-docs: **Invoked**, before editing Markdown.
- No commit, push, branch creation, merge, or gate changes.
- All six acceptance criteria passed; the offline output location adjustment is recorded below.

## Changed files

| File | Change |
|---|---|
| `production/tournament.py` | Check the rank keyed by the preceding market trade date; read switch parameters. |
| `production/engine.py` | Pass `inp.prev_date` and switch parameters through both existing call sites. |
| `production/strategy.json` | Add `params.switch.consecutive_days: 2`. |
| `tests/test_tournament.py` | Cover rank history and boundaries; seed forced replay ranks from index 11. |
| `docs/{how_to_run,strategy,production_spec}.md` | Update switch wording only. |
| `research/results/conc_tournament/CODEX_SWITCH_2DAY_REPORT.md` | Save this report. |

The existing thresholds remain `min_day_index=12`, `max_rank=6`, and `min_sessions_remaining_exclusive=3`. Rank persistence, T2 locking, staged transitions, strategy selection, planner, validator, and all other gates remain unchanged. `production/run_daily.py` already supplies the previous date in `DayInput`, so it needed no edit.

## Tests

| Command | Result | Runtime |
|---|---|---|
| `.venv/bin/python -m unittest tests.test_tournament -q` | 20 passed | 20.174 s |
| `.venv/bin/python -m unittest discover -s tests -q` | 114 passed | 37.053 s |
| `git diff --check` | Passed | — |

The full suite ran after the final test edits. Its only diagnostic was an existing `ResourceWarning` for an unclosed registry CSV in `tests/test_run_experiment.py:71`; there were no failures.

| Required case | Evidence in `tests/test_tournament.py` | Result |
|---|---|---|
| Index 12, current 6, previous 6 | `test_frozen_switch_boundaries` | T2 |
| Index 12, current 6, previous 7 | `test_previous_rank_required` | `RANK_NOT_CONSECUTIVE` |
| Index 12, current 6, previous missing | `test_previous_rank_required` | `RANK_HISTORY_MISSING` |
| Current rank missing | `test_frozen_switch_boundaries` | `RANK_MISSING` |
| Index 11, both ranks ≤6 | `test_frozen_switch_boundaries` | K2 |
| Three sessions remaining | `test_frozen_switch_boundaries` | K2 |
| Later rank 20 after switching | `test_frozen_switch_boundaries` | T2 stays locked |
| Rank recorded at index 11 counts at index 12 | `test_day_11_rank_counts_across_market_calendar_gap` | Switches across weekend |
| Older/future ranks cannot replace previous rank | `test_previous_rank_required` | No switch |
| Configuration is read | `test_consecutive_days_read_from_config` | Configured one-day test switches |
| HOLD preserves ranks and target | `test_hold_dplan_writes_saved_target_state` | Saved state matches |
| Offline isolation and invalid-plan persistence | `test_offline_state_isolated_and_written_only_with_dplan` | Passed |

## Replays

Executed with `PYTHONHASHSEED=0 .venv/bin/python`, using `recent_market()` and `replay_window()` from `tests.test_tournament`. Each day traversed engine → D-Plan → validator → decoded orders → production settlement.

Inputs: `production_runs/preflight_20261026/{strategy_retest/corrected_nominal_daily.parquet,official_combined.csv,calendar_v2.json}`. Reference: `research/results/k3_weights/results.csv`, loaded with `float_precision='round_trip'`, selecting K2b rows by window ID. Exact float equality was asserted.

| Replay | Dates | Terminal return | Reference | Difference | NORMAL / HOLD |
|---|---|---|---|---|---|
| W4, rank None | 2026-08-13..09-15 | 0.028854071217284893 | 0.028854071217284893 | 0 | 24 / 0 |
| W5, rank None | 2026-09-16..10-22 | -0.014616507500586629 | -0.014616507500586629 | 0 | 21 / 3 |
| W5, rank 1 from index 11 | 2026-09-16..10-22 | -0.07836663120804654 | — | — | 24 / 0 |

All **72 D-Plans validated**, decoded orders matched engine orders, and settlement warnings were **0**. Unranked W5 HOLD dates: 2026-10-13, 2026-10-16, 2026-10-19.

Forced W5 switched only at **index 12, 2026-10-06**. The largest accepted staged steps were **8/17** then **9/9**. T2 names were fully held after settlement on **2026-10-07**, completing the transition in two sessions. The existing decision-time `t2_complete` flag became true on **2026-10-08**.

| Trade date | day_index | Strategy mode | Engine mode | k / remaining sells | Orders |
|---|---|---|---|---|---|
| 2026-09-16 | 0 | K2 | NORMAL | — | 22 |
| 2026-09-17 | 1 | K2 | NORMAL | — | 0 |
| 2026-09-18 | 2 | K2 | NORMAL | — | 0 |
| 2026-09-21 | 3 | K2 | NORMAL | — | 0 |
| 2026-09-22 | 4 | K2 | NORMAL | — | 0 |
| 2026-09-23 | 5 | K2 | NORMAL | — | 0 |
| 2026-09-24 | 6 | K2 | NORMAL | — | 3 |
| 2026-09-29 | 7 | K2 | NORMAL | — | 0 |
| 2026-09-30 | 8 | K2 | NORMAL | — | 0 |
| 2026-10-01 | 9 | K2 | NORMAL | — | 0 |
| 2026-10-02 | 10 | K2 | NORMAL | — | 0 |
| 2026-10-05 | 11 | K2 | NORMAL | — | 0 |
| 2026-10-06 | 12 | T2 | NORMAL | 8/17 | 26 |
| 2026-10-07 | 13 | T2 | NORMAL | 9/9 | 19 |
| 2026-10-08 | 14 | T2 | NORMAL | — | 0 |
| 2026-10-12 | 15 | T2 | NORMAL | — | 0 |
| 2026-10-13 | 16 | T2 | NORMAL | — | 0 |
| 2026-10-14 | 17 | T2 | NORMAL | — | 0 |
| 2026-10-15 | 18 | T2 | NORMAL | — | 0 |
| 2026-10-16 | 19 | T2 | NORMAL | — | 0 |
| 2026-10-19 | 20 | T2 | NORMAL | — | 0 |
| 2026-10-20 | 21 | T2 | NORMAL | — | 0 |
| 2026-10-21 | 22 | T2 | NORMAL | — | 0 |
| 2026-10-22 | 23 | T2 | NORMAL | — | 0 |

Reproduce the replay calls:

```python
from tests.test_tournament import recent_market, replay_window
market = recent_market()
w4 = replay_window(market, '2026-08-13', '2026-09-15')
w5 = replay_window(market, '2026-09-16', '2026-10-22')
forced = replay_window(market, '2026-09-16', '2026-10-22', forced_switch=True)
```

## Offline run

Executed from the repository root with a temporary run directory:

```bash
./run_daily.sh 2026-10-27 --offline --rank 5 --runs-dir <temporary-directory>
```

| Check | Observed result |
|---|---|
| Status / shell exit | `SIMULATION_ONLY` / 2, as designed |
| Engine / strategy mode | `NORMAL` / `K2` |
| Contest position | `day_index=0`, `sessions_remaining=24` |
| Switch | False |
| D-Plan | `D-Plan_TEAM_11076_2026-10-27.json` |
| Orders / validation errors | 22 / 0 |
| Persisted rank | `state.ranks["2026-10-27"] = 5` |
| Persisted switch date | null |
| Snapshot previous date | 2026-10-22 |

The generated temporary `audit.json`, D-Plan, and `state/tournament.json` were read and checked before automatic temporary-directory cleanup. The contest position was independently checked with `run_daily.contest_position(recent_market(), Timestamp('2026-10-27'), settings)`.

The shell wrapper's later audit display still reads its fixed `production_runs/<T>` location even when `--runs-dir` is passed, so it displayed an older audit. The results above come from the new temporary run's actual files and the Python entrypoint's first output line. The wrapper was outside the allowed edit scope and was left unchanged.

## Acceptance table

| Criterion | Status | Evidence |
|---|---|---|
| 1. Required unit cases | PASS | Tests table above |
| 2. Full suite | PASS | 114 tests, 37.053 s |
| 3. Unranked W4/W5 exact equivalence | PASS | Both differences exactly 0 |
| 4. Forced W5 switch and staged completion | PASS | Index 12; completed in two sessions; no invalid plans or warnings |
| 5. Offline D-Plan and rank persistence | PASS | Index 0; valid SIMULATION_ONLY; rank 5 saved |
| 6. Only allowed paths changed | PASS | Seven modified tracked files plus this report; baseline untracked paths preserved |

Final status inspection preserves the pre-existing untracked entries:

- `research/results/benchmark_20261025/tmp/`
- `research/results/conc_tournament/CODEX_SWITCH_2DAY_PROMPT.txt`
- `research/results/conc_tournament/SWITCH_2DAY_BRIEF.md`

No new repository files outside the allowlist were created. Protected modules, official documents, and data were not edited.

## Plan deviations

- Added the existing `--runs-dir` option to the offline command to keep generated runtime artifacts out of disallowed repository paths. The date, offline mode, rank, production engine, validator, and gates were unchanged.
- No code, threshold, strategy, planner, validator, or gate deviations.
- Codebase-memory discovery tools were unavailable in the active tool catalog; used local source searches.

## Blockers

None.

