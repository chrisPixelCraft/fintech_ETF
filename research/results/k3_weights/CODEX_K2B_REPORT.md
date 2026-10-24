# K2b production execution report

## Status

**BLOCKED: acceptance criterion 4 fails.** The scoped implementation, full suite, equivalence replays, and forced switch pass. The required offline rerun preserves an existing 20-name saved target, so it produces 20 names rather than 22. Stopped without changing saved-target retry, state, planner, validator, or safety gates.

## Changed files

| Path | Change |
|---|---|
| `production/tournament.py:208` | Read K2 core/filler counts and weights from production strategy JSON; require their combined count. |
| `production/strategy.json` | Set 12 fillers at `0.10/12`; update description. |
| `production/strategy_k2_20.json` | Byte-identical rollback copy of the starting configuration. |
| `tests/test_tournament.py` | Expect 22 names; check 10 core and 12 filler weights, 95% sum, and candidate counts from a settled 22-name book. |
| `docs/how_to_run.md:49` | Update K2 count and weight description only. |
| `docs/production_spec.md:16` | Update K2 count and weight description only. |
| `research/results/k3_weights/CODEX_K2B_REPORT.md` | This report. |

Rollback SHA256: `52a6dbe4fdf9533b79539703b10739896982c4ec50f23b48081069dfe5c86928`. Verified against the pre-edit strategy bytes stored in `/tmp/k2b_execution/strategy_before.json`.

K2 production selection has no held-tier selector to change. The weight test classifies 10 cores at ≥5% and 12 fillers below 5%; fillers remain approximately 0.8333%.

## Tests

Command: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -q`.

- **101 tests passed in 36.449 seconds**; exit code 0.
- The existing staged-switch test now explicitly starts from 22 settled names and checks every generated candidate is within 20–30 names.
- One `ResourceWarning` reports an unclosed file in `tests/test_run_experiment.py:71`; it does not fail the suite.
- Log: `/tmp/k2b_execution/tests.log`.

## Equivalence

Used `tests.test_tournament.replay_window`, with no rank supplied, on `recent_market()` using the local preflight snapshot. Expected floats were loaded with `pandas.read_csv(..., float_precision='round_trip')` from rows `strategy=K2b` and `window_id=W4/W5`.

Reference: `research/results/k3_weights/results.csv`.

SHA256: `d43c02400f18d73799d8000a49a8af20098a7e5705f1ce4f880bf7811895fce9` — matches the required hash.

| Window | Stored return | Production return | Difference | NORMAL | HOLD | D-Plan errors | Settlement warning days |
|---|---|---|---|---|---|---|---|
| W4 | 0.028854071217284893 | 0.028854071217284893 | 0.0 | 24 | 0 | 0 | 0 |
| W5 | -0.014616507500586629 | -0.014616507500586629 | 0.0 | 21 | 3 | 0 | 0 |

W4: 2026-08-13–2026-09-15. W5: 2026-09-16–2026-10-22. Both differences satisfy `abs(diff) <= 1e-9`. All 48 D-Plans validate; decoded plan orders equal engine orders before settlement.

## Forced switch

W5 used `forced_switch=True`: rank is `None` before day_index 12 and 1 thereafter. The replay validated all 24 D-Plans and settled their decoded orders with **0 warning days**.

T2 names first fully reached **2026-10-07**, day_index **13**, session **14**. The first switch session was 2026-10-06, day_index 12; accepted k=8, 26 orders, 27 held names. The next accepted k=9 reached all 25 T2 names. Completion state is observed on the following decision.

`k` is the accepted staged-switch step; `—` means no switch step. Orders count submitted order lines; held names are counted after settlement.

| Date | day_index | k | Orders | Held names |
|---|---|---|---|---|
| 2026-09-16 | 0 | — | 22 | 22 |
| 2026-09-17 | 1 | — | 0 | 22 |
| 2026-09-18 | 2 | — | 0 | 22 |
| 2026-09-21 | 3 | — | 0 | 22 |
| 2026-09-22 | 4 | — | 0 | 22 |
| 2026-09-23 | 5 | — | 0 | 22 |
| 2026-09-24 | 6 | — | 3 | 22 |
| 2026-09-29 | 7 | — | 0 | 22 |
| 2026-09-30 | 8 | — | 0 | 22 |
| 2026-10-01 | 9 | — | 0 | 22 |
| 2026-10-02 | 10 | — | 0 | 22 |
| 2026-10-05 | 11 | — | 0 | 22 |
| 2026-10-06 | 12 | 8 | 26 | 27 |
| 2026-10-07 | 13 | 9 | 19 | 25 |
| 2026-10-08 | 14 | — | 0 | 25 |
| 2026-10-12 | 15 | — | 0 | 25 |
| 2026-10-13 | 16 | — | 0 | 25 |
| 2026-10-14 | 17 | — | 0 | 25 |
| 2026-10-15 | 18 | — | 0 | 25 |
| 2026-10-16 | 19 | — | 0 | 25 |
| 2026-10-19 | 20 | — | 0 | 25 |
| 2026-10-20 | 21 | — | 0 | 25 |
| 2026-10-21 | 22 | — | 0 | 25 |
| 2026-10-22 | 23 | — | 0 | 25 |

Replay evidence: `/tmp/k2b_execution/replays.json`; execution log: `/tmp/k2b_execution/replay.log`.

## Offline run

Command: `PYTHONDONTWRITEBYTECODE=1 ./run_daily.sh 2026-10-26 --offline`.

| Check | Result |
|---|---|
| Status | `SIMULATION_ONLY` |
| Mode | `NORMAL` |
| Action | `repair` |
| Orders | 20 |
| Held names after plan | **20 — FAIL; required 22** |
| Plan status | `REPAIRED` |
| D-Plan validation | PASS; 0 errors |
| Active Share | `ACTIVE_SHARE_UNVERIFIED` |
| Shell exit code | 2; expected for simulation-only output |

The existing superseded handler archived the prior D-Plan at `production_runs/2026-10-26/superseded/20261025T011208128632/D-Plan_TEAM_11076_2026-10-26.json`. It does not archive or clear the saved strategy target.

Generated D-Plan: `production_runs/2026-10-26/D-Plan_TEAM_11076_2026-10-26.json`. Audit: `production_runs/2026-10-26/audit.json`. Independent validation used the persisted empty input book, T−1 close, rules, NAV, cash, and filename; decoded orders leave 20 positive positions.

**Cause:** `production/run_daily.py:211` loads the existing daily tournament state. That state already contains a 20-name `k2_target` for 2026-09. `production/tournament.py:223` builds only if no saved target exists; the same revenue month does not trigger a rescreen. The empty input book breaches the cash limit, so `production/tournament.py:238` retries the unchanged saved target. This behavior is required by the brief.

## Acceptance table

| Criterion | Result | Evidence |
|---|---|---|
| 1. Full suite | PASS | 101 tests; 36.449s. |
| 2. Equivalence | PASS | Exact W4/W5 returns; 48 valid D-Plans; 0 settlement warnings; reference SHA256 matches. |
| 3. Forced switch | PASS | T2 reached session 14; 24 valid D-Plans; 0 settlement warnings. |
| 4. Offline rerun | **FAIL** | Valid simulation plan, but 20 held names instead of 22. |
| 5. Allowed changes | PASS | No outside-allowed tracked patch changed; existing outside-allowed untracked file hashes unchanged. |

## Plan deviations

- The brief lists `filler_weight 0.08333333333333333` while also requiring `0.10/12`, approximately 0.8333% per filler, and 95% invested. Used the mathematically consistent `0.008333333333333333`, matching `K2Buffer` and both exact-return replays.
- Stopped at the offline saved-target conflict as instructed. No migration, state reset, or alternate output-directory run was introduced.
- Runtime artifacts under ignored `production_runs/2026-10-26/` were written by the expressly required offline command and existing superseded handler. Temporary evidence was written under `/tmp/k2b_execution/`.

## Execution

- Main-directory execution; repository root was the working directory.
- `human-readable-docs`: **Invoked**, before human-facing Markdown edits and the offline audit generation. Source: `/Users/chrishsieh/.agents/skills/human-readable-docs/SKILL.md`.
- `git-workflow`: Invoked for read-only Git inspection; explicit user restrictions override its branch/commit defaults.
- Codebase-memory tools were unavailable; used local searches and file reads.
- No subagents, external research, network refresh, commits, pushes, branches, or merges.
- Starting patch evidence: `/tmp/k2b_execution/before.json`. All tracked patches outside the allowed set were compared byte-for-byte to the starting patch. Mismatches: **0**.

Before/after lists below use `git status --short` granularity. Untracked directories are folded as Git normally displays them. The starting snapshot additionally captured all 1,267 individual status entries and hashes of their existing files. The only newly visible short-status entry is the rollback configuration; the report is inside the already-untracked `research/results/k3_weights/` directory.

Before:

```text
 M README.md
 M data/reference/competition_rules.json
 M docs/how_to_run.md
 M docs/production_spec.md
 M docs/strategy.md
 M docs/task.md
 M official_docs/D-Plan.schema.json
 M official_docs/D-Plan_撰寫指南.md
 M production/active_share.py
 M production/dplan.py
 M production/engine.py
 M production/run_daily.py
 M production/state.py
 M production/strategy.json
 M production/validate.py
 M research/holdout_access_log.jsonl
 M research/run_experiment.py
 M run_daily.sh
 M tests/test_causality.py
 M tests/test_production.py
 M tests/test_production_data.py
?? docs/ETF投資策略說明文件_TEAM_11076.docx
?? docs/rules_v43.md
?? "official_docs/2026 Agent 基金經理人競賽後台操作說明.pdf"
?? official_docs/D-Plan.schema_v4.3.json
?? official_docs/D-Plan_TEAM_042_2026-10-28.json
?? official_docs/D-Plan_TEAM_043_2026-10-28.json
?? official_docs/D-Plan_撰寫指南_v4.3.md
?? official_docs/DataPack_規格書_v4.3.md
?? official_docs/玉山挑戰賽_ETF投資策略說明文件_範例文件_v1-1.pdf
?? "official_docs/玉山挑戰賽_Google Token Credit領取方式_v1-1.pdf"
?? official_docs/玉山挑戰賽_主動型ETF列表_v1-1.pdf
?? official_docs/玉山挑戰賽_投資組合及交易標的_150檔清單_v1-1.pdf
?? production/strategy_mom20.json
?? production/tournament.py
?? research/results/benchmark_20261025/
?? research/results/conc_tournament/
?? research/results/k3_composite/
?? research/results/k3_weights/
?? research/results/recent24_strategies/
?? research/results/recent3_core_satellite/
?? research/results/recent5_new/
?? research/results/recent5_v3/
?? research/results/rules_v43_validation/
?? tests/test_active_share_v43.py
?? tests/test_datapack_v43.py
?? tests/test_tournament.py
```

After:

```text
 M README.md
 M data/reference/competition_rules.json
 M docs/how_to_run.md
 M docs/production_spec.md
 M docs/strategy.md
 M docs/task.md
 M official_docs/D-Plan.schema.json
 M official_docs/D-Plan_撰寫指南.md
 M production/active_share.py
 M production/dplan.py
 M production/engine.py
 M production/run_daily.py
 M production/state.py
 M production/strategy.json
 M production/validate.py
 M research/holdout_access_log.jsonl
 M research/run_experiment.py
 M run_daily.sh
 M tests/test_causality.py
 M tests/test_production.py
 M tests/test_production_data.py
?? docs/ETF投資策略說明文件_TEAM_11076.docx
?? docs/rules_v43.md
?? "official_docs/2026 Agent 基金經理人競賽後台操作說明.pdf"
?? official_docs/D-Plan.schema_v4.3.json
?? official_docs/D-Plan_TEAM_042_2026-10-28.json
?? official_docs/D-Plan_TEAM_043_2026-10-28.json
?? official_docs/D-Plan_撰寫指南_v4.3.md
?? official_docs/DataPack_規格書_v4.3.md
?? official_docs/玉山挑戰賽_ETF投資策略說明文件_範例文件_v1-1.pdf
?? "official_docs/玉山挑戰賽_Google Token Credit領取方式_v1-1.pdf"
?? official_docs/玉山挑戰賽_主動型ETF列表_v1-1.pdf
?? official_docs/玉山挑戰賽_投資組合及交易標的_150檔清單_v1-1.pdf
?? production/strategy_k2_20.json
?? production/strategy_mom20.json
?? production/tournament.py
?? research/results/benchmark_20261025/
?? research/results/conc_tournament/
?? research/results/k3_composite/
?? research/results/k3_weights/
?? research/results/recent24_strategies/
?? research/results/recent3_core_satellite/
?? research/results/recent5_new/
?? research/results/recent5_v3/
?? research/results/rules_v43_validation/
?? tests/test_active_share_v43.py
?? tests/test_datapack_v43.py
?? tests/test_tournament.py
```

## Quota

Session quota and token-usage telemetry were unavailable. No quota estimate is claimed.

## Blockers

The existing offline saved-target state conflicts with the required 22-name rerun result. Satisfying both would require an explicitly authorized state migration or reset, or a change to saved-target retry semantics. Those actions are outside this brief, so the overall acceptance remains blocked.
