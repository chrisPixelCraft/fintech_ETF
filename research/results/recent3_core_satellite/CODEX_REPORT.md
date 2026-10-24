# CODEX_REPORT

- Status: COMPLETE; 可替代候選（由使用者決定是否換）; selected C3.
- Changed files: run.py, results.json, results.csv, gates.json, active_share.csv, CODEX_REPORT.md; 15 episodes × {ledger,trades,holdings,issues}.csv. All under research/results/recent3_core_satellite/.
- Tests run and results: exact command below; both W3 consistency asserts PASS; 15/15 verify_episode calls, 0 problems; 360/360 AS rows; no NaN/Inf in results; protected input hashes and 33-line git status unchanged.
- Items expected/processed/failed: episodes 15/15/0; verification calls 15/15/0; AS rows 360/360/0.
- Acceptance criteria: all execution/output checks PASS. Candidate gate failures are experimental results, not execution failures.
- Execution: `PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/recent3_core_satellite/run.py`; elapsed 3.45s; main directory; no git writes. Two command invocations: initial implementation validation, then corrected C3 top50 interpretation and added crash fields to results.csv; final artifacts contain exactly 15 episodes. No parameter search.
- Persisted-output checks: PASS; 15 result rows, 60 episode CSVs, 360 unique AS rows; CSV has no missing result values; all final-three-session trade freezes hold; AS/result roundtrip and preregistration SHA256 pass.
- Quota: not available; no sweeps or parameter search.
- Blockers: none.

## Frozen decision

| Candidate | G0 | G1 | G2 | G3 heuristic | Mean Δ | Mean MDD |
|---|---|---|---|---|---|---|
| C1 | PASS | PASS | PASS | PASS | 7.5292% | 12.6479% |
| C2 | PASS | PASS | FAIL | PASS | 2.7659% | 13.6692% |
| C3 | PASS | PASS | PASS | PASS | 7.8634% | 10.7845% |
| C4 | PASS | PASS | PASS | PASS | 7.7933% | 12.4988% |

## Plan deviations / interpretations

- C1/C2 exclude 2330 before taking 24/9 large-cap names. C3 takes the overall top50 first, excludes the separately allocated 2330, then selects 24 by sample std of 60 action-neutral daily simple returns; ties break by symbol.
- C1/C3 cap-minus-0.5pp is a D-1 repair trigger, not a lower target cap. Last-three-session freeze takes precedence over repair.
- C2 keeps actually held non-core names ranked <=21, fills to 15 by score, and uses portfolio one-way turnover (0.5 times sum absolute stock-weight changes).
- G0 warning comparison is required separately in every window. Tie-break considers passing candidates within strictly 0.5pp of the lowest mean drawdown, then highest mean delta.
- check(actual trade_date) excludes ETFs dated in the future, leaving by_etf empty before 2026-09-24. For those rows only, compute every ETF via the same production.active_share.active_share formula using the mandated fixed snapshot. Preserve actual-date API status and invalid-data list; all AS remains heuristic/UNVERIFIED. No threshold or snapshot is changed.
- nav_tradable is canonical ledger.nav (cash plus holdings, excluding receivables); crash daily returns include initial-capital predecessor and terminal dividend credit, consistent with episode terminal return.

- AS actual-date formula fallback: 265/360 rows; all 360 statuses UNVERIFIED.
- No scope, architecture, candidates, parameters, or gates changed. Results are descriptive; no production strategy changed.

## Verification problems

{"C1_W1": [], "C1_W2": [], "C1_W3": [], "C2_W1": [], "C2_W2": [], "C2_W3": [], "C3_W1": [], "C3_W2": [], "C3_W3": [], "C4_W1": [], "C4_W2": [], "C4_W3": [], "Mom20_W1": [], "Mom20_W2": [], "Mom20_W3": []}
