# CODEX_REPORT

- Status: COMPLETE — 不建議集中版
- Changed files: run.py, results.csv, metrics.json, blocks.csv, gates.json, CODEX_REPORT.md; episodes/{K1,K2,T2,Mom20}_{W1..W5}/{ledger,trades,holdings,issues}.csv and strategy_log.json; research/holdout_access_log.jsonl (+4 lines). Allowed paths only.
- Tests run and results: specified command; 12 pilot episodes passed; 1,124 verification calls / 0 problems; 10 recent consistency checks within 1e-12; 276 historical + 5 recent date/count checks; finite/nonmissing results; 20 recent ledgers × 24 rows; frozen/provenance hashes, protected inputs and 36-line unrelated Git status unchanged; prior 77 log lines unchanged, +4 PASS.
- Items expected/processed/failed: episodes 1124/1124/0.
- Acceptance criteria: all execution/output checks PASS; gate failures are outcomes.
- Plan deviations / interpretations: no scope, architecture, strategy, parameter or gate changes. Closest failed candidate means fewest failed gates (ties retained), descriptive only; the preregistration supplies no distance metric. Exact passing tail-edge ties remain tied. P90 uses pandas linear quantile; win means Δ50 > 0; K2 distribution uses initial build only.
- Execution: `PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/conc_tournament/run.py`; main directory; 37.82s; 4 fork workers (pilot/recent serial); no Git writes.
- Quota: unavailable; one frozen batch, no parameter search.
- Blockers: none.

| Set | Strategy | Mean Δ50 | Median Δ50 | Win | ≥+5pp | ≤−5pp | Tail edge | ≥+10pp | P90 DD | Warn total/max | DQ | Mean turnover | Mean costs TWD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| historical | K1 | 1.219% | 0.038% | 50.362% | 25.725% | 21.377% | 4.348% | 11.594% | 13.481% | 8/2 | 0 | 5.2363 | 13915987.70 |
| historical | K2 | 1.509% | 1.295% | 61.594% | 22.101% | 10.145% | 11.957% | 5.797% | 11.033% | 0/0 | 0 | 1.7987 | 3956581.20 |
| historical | T2 | 0.602% | 0.194% | 54.348% | 8.333% | 3.261% | 5.072% | 1.087% | 10.156% | 0/0 | 0 | 0.9141 | 1307686.24 |
| historical | Mom20 | 0.940% | 0.160% | 51.449% | 19.565% | 11.232% | 8.333% | 7.609% | 11.181% | 0/0 | 0 | 3.6846 | 9361746.72 |
| recent | K1 | -4.402% | -7.041% | 40.000% | 20.000% | 60.000% | -40.000% | 20.000% | 22.849% | 0/0 | 0 | 5.9767 | 16054238.23 |
| recent | K2 | -0.119% | 1.652% | 60.000% | 0.000% | 20.000% | -20.000% | 0.000% | 8.324% | 0/0 | 0 | 1.6713 | 3511338.21 |
| recent | T2 | 0.492% | 0.982% | 80.000% | 0.000% | 0.000% | 0.000% | 0.000% | 15.563% | 0/0 | 0 | 0.9303 | 1340193.00 |
| recent | Mom20 | -4.191% | -7.756% | 40.000% | 0.000% | 60.000% | -60.000% | 0.000% | 16.467% | 0/0 | 0 | 3.9813 | 10192336.72 |

| Candidate | G0 | G1 | G2 | G3 | G4 |
|---|---|---|---|---|---|
| K1 | FAIL | PASS | PASS | PASS | FAIL |
| K2 | PASS | PASS | FAIL | PASS | PASS |

Failing gates (decimal return/probability units):
- K1: G0 {"passed": false, "disqualifications": 0, "maximum_disqualifications": 0, "max_warning_days": 2, "maximum_warning_days": 1, "total_warning_days": 8, "maximum_total_warning_days": 0}; G4 {"passed": false, "recent_mean_delta_vs_0050": -0.04401647129550188, "minimum": -0.02}
- K2: G2 {"passed": false, "p_gain_5pp": 0.2210144927536232, "threshold": 0.2456521739130435, "controls": {"T2": 0.08333333333333333, "Mom20": 0.1956521739130435}, "margin": 0.05}
- Selected: none; closest if none pass: K2.
- Mom20 historical reference: {"matched_count": 40, "max_abs_diff": 0.020793249363954602, "note": "Descriptive only; repaired data snapshot differences expected; legacy CSV rounded."}
- K2 initial-build filter distribution: metrics.json (counts/histograms by set).
- Verification counts for all 1,124 IDs, block metrics and provenance: metrics.json / blocks.csv.
- Limitations: 2026 survivor universe, overlapping windows, revenue available on day 11, holdout already accessed previously; large-win probability is only a top-six proxy.
- Execution recovery: first command completed 1,124 episodes (38.12s, zero verification problems), then failed an overly strict Git-status assertion because it included the authorized log append. Corrected runner-only assertion to exclude that one authorized path and reran the same command with unchanged strategies/gates. Reused and validated the four existing run-log entries; no extra log lines. Unique deliverable episodes: 1,124; total executed across two attempts: 2,248.
- Independent post-run validation: reread CSV/JSON, recalculated G0–G4 directly from episode returns (PASS); replayed all 12 serial pilot episodes with four workers (all numeric fields exactly equal, max diff 0; verify problems 0); rechecked frozen hashes and 77+4 append-only log (PASS). Additional validation runtime: 1.99s. Total executed including both batch attempts and validation replays: 2,260; unique result rows remain 1,124.
