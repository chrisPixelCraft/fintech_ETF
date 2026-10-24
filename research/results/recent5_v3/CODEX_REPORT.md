# CODEX_REPORT

- Status: COMPLETE; 候選通過，由使用者決定是否取代 C1.
- Changed files: run.py, results.csv, results.json, gates.json, active_share.csv, CODEX_REPORT.md; episodes/{strategy}_{window}/{ledger,trades,holdings,issues}.csv and strategy_log.json (25 directories). Allowed paths only.
- Tests run and results: 25/25 verify_episode calls; 0 problems; 10/10 C1/Mom20 consistency checks (1e-12), Mom20 W5 fixed return, template date assertions, 600 AS rows, finite results, start/end frozen hashes, input/provenance hashes and unchanged 35-line Git status PASS.
- Items expected/processed/failed: episodes 25/25/0; verification 25/25/0; AS 600/600/0.
- Acceptance criteria: all execution/output checks PASS; candidate gate failures are outcomes.
- Execution: `PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/recent5_v3/run.py`; main directory; elapsed 16.94s; no Git writes.
- Quota: unavailable; one fixed batch, no parameter search.
- Blockers: none.

| Candidate | G0 | G1 | G2 | G3 | G4 heuristic | Wins | Mean Δ50 |
|---|---|---|---|---|---|---|---|
| T1 | PASS | FAIL | PASS | PASS | PASS | 2/5 | 0.5864% |
| T2 | PASS | PASS | PASS | PASS | PASS | 4/5 | 0.4918% |
| T3 | PASS | FAIL | FAIL | PASS | PASS | 2/5 | -0.2913% |

Failing gates (returns/drawdowns in decimal units):

- T1: G1 {"mean_delta_vs_0050": 0.005863938670396895, "minimum_mean_delta_exclusive": 0, "passed": false, "required_wins": 4, "wins": 2}
- T2: none
- T3: G1 {"mean_delta_vs_0050": -0.002913385154727455, "minimum_mean_delta_exclusive": 0, "passed": false, "required_wins": 4, "wins": 2}; G2 {"c1_mean_delta_vs_0050": 0.009765464120576994, "mean_delta_vs_0050": -0.002913385154727455, "passed": false, "threshold": 0.004765464120576994, "tolerance": 0.005}

| Strategy | Mean Δ50 ex W1 | W1 Δ50 | W2 Δ50 | Mean turnover | Total costs (TWD) |
|---|---|---|---|---|---|
| Mom20 | -5.8965% | 2.6308% | 4.6862% | 3.9813 | 50961683.61 |
| C1 | -1.3286% | 10.1972% | 0.3703% | 0.9283 | 6674769.81 |
| T1 | 0.9635% | -0.9221% | 4.3120% | 1.5344 | 15943255.36 |
| T2 | 0.0260% | 2.3549% | 0.6387% | 0.9303 | 6700965.02 |
| T3 | -1.7575% | 5.5735% | -1.4011% | 1.1623 | 10231571.10 |

- T1 filter counts (build/rescreen chronological): W1: 24, 25; W2: 25, 37; W3: 39, 34; W4: 54; W5: 42, 38.
- Per-window turnover, fees and taxes: results.csv and gates.json descriptive fields.

| Strategy | W5 crash compounded return |
|---|---|
| Mom20 | -15.9145% |
| C1 | -12.7384% |
| T1 | -9.1009% |
| T2 | -12.2319% |
| T3 | -13.1545% |

- Crash dates (Mom20 W5 worst five): 2026-10-15, 2026-10-22, 2026-10-20, 2026-10-13, 2026-10-12.

- Verification problem counts (W1/W2/W3/W4/W5):
  - Mom20: 0/0/0/0/0
  - C1: 0/0/0/0/0
  - T1: 0/0/0/0/0
  - T2: 0/0/0/0/0
  - T3: 0/0/0/0/0

Plan deviations / interpretations:

- C1 is the reference for G0 warnings, G2 mean delta and G3 drawdown/crash; only T1/T2/T3 enter selection.
- G4 requires 24 W5 daily rows with minimum across all required ETFs >= 15%; AS is heuristic/UNVERIFIED.
- Same template AS fixed-snapshot fallback: when actual-date check has no by_etf, use production.active_share.active_share against the mandated 2026-09-24 top10 snapshot; retain actual-date status and invalid-data fields.
- Same template crash definition: Mom20 W5 five worst sessions, stable ordering; compound each strategy on those dates with initial-capital predecessor and final dividend credit.
- Tie-break is highest win count, then highest mean delta vs 0050; exact ties are listed without an added criterion.
- Strategy logs are the unchanged strategies own exposed log; T1 filter counts include build and all revenue rescreens.
- No deviations; strategy logic, parameters, gates and architecture unchanged.
- AS fallback: 505/600 rows. AS remains heuristic/UNVERIFIED.
- Selection order: T2.

- Post-run independent output audit: PASS (syntax; 25 unique result rows; 125 episode files; 600 unique AS rows; 10 reference/date matches; no NaN/Inf/missing result values; ledger costs/turnover; T1 logs; independently recomputed G0–G4; input SHA256; 35-line Git status).
- Audit note: an initial extra check incorrectly required empty issues.csv; corrected to the specified verify_episode=0 criterion. Mom20 W2/W3 each retain one FILL_OUTSIDE_PRICE_ENVELOPE issue from the unchanged simulator; verification remains 0 problems. No strategy or output changes were needed.
