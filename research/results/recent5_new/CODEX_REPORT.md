# CODEX_REPORT

- Status: COMPLETE; 可替代候選（由使用者決定是否換）; selected C1.
- Changed files: run.py, results.csv, results.json, gates.json, active_share.csv, CODEX_REPORT.md; 35 episode directories with ledger/trades/holdings/issues.csv and strategy_log.json. Only allowed paths written.
- Tests run and results: 35/35 verify_episode calls; 0 problems; 15/15 prior-result consistency checks and Mom20 W5 fixed-value check PASS; dates PASS; 840/840 AS rows; finite results PASS; start/end frozen hashes PASS; input/provenance hashes and 34-line Git status unchanged.
- Items expected/processed/failed: episodes 35/35/0; verification 35/35/0; AS 840/840/0.
- Independent post-run checks: 35 episode directories and all 175 episode files present; 240 S1/S2 daily log entries; CSV has no missing/nonfinite values; G0-G4 independently recomputed from saved CSV: PASS.
- Acceptance criteria: all execution/output checks PASS. Candidate gate failures are experimental outcomes.
- Execution: `PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/recent5_new/run.py`; main directory; elapsed 9.48s; no Git writes.
- Quota: unavailable; one fixed run, no search or sweeps.
- Blockers: none.

| Candidate | G0 | G1 | G2 | G3 | G4 heuristic | Mean Δ50 | Mean ΔM | Mean MDD |
|---|---|---|---|---|---|---|---|---|
| S1 | PASS | FAIL | PASS | PASS | PASS | -2.6185% | 1.5725% | 12.1973% |
| S2 | PASS | FAIL | PASS | PASS | PASS | -0.8960% | 3.2950% | 10.2188% |
| C1 | PASS | PASS | PASS | PASS | PASS | 0.9765% | 5.1676% | 10.2954% |
| C2 | PASS | FAIL | PASS | PASS | PASS | -2.7287% | 1.4624% | 11.5783% |
| C3 | PASS | FAIL | PASS | PASS | PASS | -0.0276% | 4.1635% | 9.1174% |
| C4 | PASS | FAIL | FAIL | PASS | PASS | -3.9287% | 0.2623% | 12.0753% |

## Failing gates (numeric evidence)

- S1: G1 {"mean_delta_vs_0050": -0.02618536301785255, "minimum_mean_delta_exclusive": 0, "passed": false, "required_wins": 3, "wins": 1}
- S2: G1 {"mean_delta_vs_0050": -0.008960493468049502, "minimum_mean_delta_exclusive": 0, "passed": false, "required_wins": 3, "wins": 2}
- C1: none
- C2: G1 {"mean_delta_vs_0050": -0.027286739939372008, "minimum_mean_delta_exclusive": 0, "passed": false, "required_wins": 3, "wins": 2}
- C3: G1 {"mean_delta_vs_0050": -0.0002756198644085606, "minimum_mean_delta_exclusive": 0, "passed": false, "required_wins": 3, "wins": 3}
- C4: G1 {"mean_delta_vs_0050": -0.03928724882666812, "minimum_mean_delta_exclusive": 0, "passed": false, "required_wins": 3, "wins": 1}; G2 {"mean_delta_vs_mom20": 0.002623239245666231, "minimum_mean_delta": 0.01, "passed": false, "required_wins": 3, "wins": 3}

- Closest: not applicable; a candidate passed all gates.

## New W1-W2 only (descriptive)

| Strategy | Mean Δ50 | Mean ΔM |
|---|---|---|
| Mom20 | 3.6585% | 0.0000% |
| C1 | 5.2838% | 1.6253% |
| C2 | 3.1656% | -0.4929% |
| C3 | 2.2721% | -1.3864% |
| C4 | -7.3756% | -11.0341% |
| S1 | -2.6277% | -6.2862% |
| S2 | 5.8824% | 2.2239% |

## Crash: Mom20 W5 worst five sessions

- Dates: 2026-10-15, 2026-10-22, 2026-10-20, 2026-10-13, 2026-10-12

| Strategy | Compounded return |
|---|---|
| Mom20 | -15.9145% |
| C1 | -12.7384% |
| C2 | -14.7642% |
| C3 | -11.2650% |
| C4 | -7.1059% |
| S1 | -13.0590% |
| S2 | -12.5829% |

## Plan deviations / interpretations

- Closest non-passers means fewest failed gates; list all ties. This is descriptive and introduces no selection gate.
- S1 logs signal eligibility and release age on each decision; S2 logs daily state and slot count; other strategy logs are empty unless exposed by the frozen strategy.
- C1/C2 exclude 2330 before taking 24/9 large-cap names. C3 takes the overall top50 first, excludes the separately allocated 2330, then selects 24 by sample std of 60 action-neutral daily simple returns; ties break by symbol.
- C1/C3 cap-minus-0.5pp is a D-1 repair trigger, not a lower target cap. Last-three-session freeze takes precedence over repair.
- C2 keeps actually held non-core names ranked <=21, fills to 15 by score, and uses portfolio one-way turnover (0.5 times sum absolute stock-weight changes).
- G0 compares warnings separately in all five windows. Tie-break: passing candidates within strictly 0.5pp of lowest mean drawdown, then highest mean delta vs 0050.
- check(actual trade_date) excludes ETFs dated in the future, leaving by_etf empty before 2026-09-24. For those rows only, compute every ETF via the same production.active_share.active_share formula using the mandated fixed snapshot. Preserve actual-date API status and invalid-data list; all AS remains heuristic/UNVERIFIED. No threshold or snapshot is changed.
- nav_tradable is canonical ledger.nav (cash plus holdings, excluding receivables); crash daily returns include initial-capital predecessor and terminal dividend credit, consistent with episode terminal return.

- AS fixed-snapshot fallback: 707/840 rows; all AS heuristic/UNVERIFIED.
- No strategy logic, parameters, scope, architecture, or gates changed. No production change.

## Verification problem counts (each call)

- C1_W1: 0
- C2_W1: 0
- C3_W1: 0
- C4_W1: 0
- Mom20_W1: 0
- S1_W1: 0
- S2_W1: 0
- C1_W2: 0
- C2_W2: 0
- C3_W2: 0
- C4_W2: 0
- Mom20_W2: 0
- S1_W2: 0
- S2_W2: 0
- C1_W3: 0
- C2_W3: 0
- C3_W3: 0
- C4_W3: 0
- Mom20_W3: 0
- S1_W3: 0
- S2_W3: 0
- C1_W4: 0
- C2_W4: 0
- C3_W4: 0
- C4_W4: 0
- Mom20_W4: 0
- S1_W4: 0
- S2_W4: 0
- C1_W5: 0
- C2_W5: 0
- C3_W5: 0
- C4_W5: 0
- Mom20_W5: 0
- S1_W5: 0
- S2_W5: 0
