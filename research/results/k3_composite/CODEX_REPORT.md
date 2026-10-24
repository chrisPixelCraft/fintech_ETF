# CODEX_REPORT

- Status: COMPLETE — Maintain K2.
- Changed files: run.py, results.csv, metrics.json, blocks.csv, gates.json, k1_warning_diagnostic.csv, CODEX_REPORT.md; episodes/{K3.0,K3.1}_{W1..W5}/{ledger,trades,holdings,issues}.csv and strategy_log.json; research/holdout_access_log.jsonl (+2 lines). Allowed paths only.
- Tests/asserts: specified command; 6 pilot episodes; 562 verify_episode calls / 0 problems; 7 K1 diagnostic reruns match stored returns within 1e-12 and verify cleanly; 276 historical + 5 recent windows match all controls; finite/nonmissing serialized outputs; 10 recent ledger directories × 24 days; four frozen hashes, protected inputs, market provenance and 46-line Git status unchanged; original 94 log lines byte-identical, +2 lines.
- Independent post-run checks: recomputed all G0–G4 from serialized results; episode counts, warning records, diagnostic returns, numeric finiteness, frozen hashes and original log-prefix hash PASS.
- Items expected/processed/failed: new episodes 562/562/0; K1 diagnostic windows 7/7/0.
- Plan deviations: none in scope, architecture, strategies, parameters or gates. Closest means fewest failed gates (ties retained), descriptive only; preregistration gives no distance. Exact passing tail-edge ties remain tied. P90 uses pandas linear quantile; win means Δ50 > 0. 0050 uses the simulator benchmark path with zero trading costs/turnover and rule warnings.
- Execution: `PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/k3_composite/run.py`; main directory; 23.62s; 4 workers; no Git writes.
- Quota: unavailable; one frozen batch, no search.
- Blockers: none.

| Set | Strategy | Mean Δ50 | Median Δ50 | Win | ≥+5pp | ≥+10pp | ≥+15pp | ≤−5pp | Tail edge | P90 DD | Warn total/max | DQ | Turnover | Costs TWD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| historical | K3.0 | 1.763% | 0.783% | 55.072% | 25.000% | 9.420% | 2.536% | 9.420% | 15.580% | 10.760% | 5/2 | 0 | 3.2377 | 8157845.26 |
| historical | K3.1 | 1.063% | 0.648% | 53.986% | 21.739% | 6.884% | 2.536% | 14.493% | 7.246% | 10.701% | 2/1 | 0 | 3.4782 | 8858057.06 |
| historical | K2 | 1.509% | 1.295% | 61.594% | 22.101% | 5.797% | 1.812% | 10.145% | 11.957% | 11.033% | 0/0 | 0 | 1.7987 | 3956581.20 |
| historical | K1 | 1.219% | 0.038% | 50.362% | 25.725% | 11.594% | 6.159% | 21.377% | 4.348% | 13.481% | 8/2 | 0 | 5.2363 | 13915987.70 |
| historical | T2 | 0.602% | 0.194% | 54.348% | 8.333% | 1.087% | 0.362% | 3.261% | 5.072% | 10.156% | 0/0 | 0 | 0.9141 | 1307686.24 |
| historical | Mom20 | 0.940% | 0.160% | 51.449% | 19.565% | 7.609% | 1.449% | 11.232% | 8.333% | 11.181% | 0/0 | 0 | 3.6846 | 9361746.72 |
| historical | 0050 | 0.000% | 0.000% | 0.000% | 0.000% | 0.000% | 0.000% | 0.000% | 0.000% | 9.271% | 0/0 | 0 | 0.0000 | 0.00 |
| recent | K3.0 | -2.344% | -0.628% | 40.000% | 0.000% | 0.000% | 0.000% | 40.000% | -40.000% | 15.353% | 0/0 | 0 | 2.9945 | 7516636.66 |
| recent | K3.1 | 0.730% | -3.993% | 40.000% | 40.000% | 20.000% | 0.000% | 20.000% | 20.000% | 17.518% | 0/0 | 0 | 3.2898 | 8531717.61 |
| recent | K2 | -0.119% | 1.652% | 60.000% | 0.000% | 0.000% | 0.000% | 20.000% | -20.000% | 8.324% | 0/0 | 0 | 1.6713 | 3511338.21 |
| recent | K1 | -4.402% | -7.041% | 40.000% | 20.000% | 20.000% | 0.000% | 60.000% | -40.000% | 22.849% | 0/0 | 0 | 5.9767 | 16054238.23 |
| recent | T2 | 0.492% | 0.982% | 80.000% | 0.000% | 0.000% | 0.000% | 0.000% | 0.000% | 15.563% | 0/0 | 0 | 0.9303 | 1340193.00 |
| recent | Mom20 | -4.191% | -7.756% | 40.000% | 0.000% | 0.000% | 0.000% | 60.000% | -60.000% | 16.467% | 0/0 | 0 | 3.9813 | 10192336.72 |
| recent | 0050 | 0.000% | 0.000% | 0.000% | 0.000% | 0.000% | 0.000% | 0.000% | 0.000% | 10.262% | 0/0 | 0 | 0.0000 | 0.00 |

| Candidate | G0 | G1 | G2 | G3 | G4 |
|---|---|---|---|---|---|
| K3.0 | FAIL | FAIL | PASS | PASS | FAIL |
| K3.1 | FAIL | FAIL | FAIL | PASS | PASS |

Failing gates (probabilities/returns in decimal units):
- K3.0: G0 {"passed": false, "disqualifications": 0, "maximum_disqualifications": 0, "total_warning_days": 5, "maximum_total_warning_days": 0}; G1 {"passed": false, "p_gain_5pp": 0.25, "minimum_gain_5pp": 0.2510144927536232, "p_gain_10pp": 0.09420289855072464, "minimum_gain_10pp": 0.07797101449275362}; G4 {"passed": false, "recent_mean_delta_vs_0050": -0.023443075910342427, "minimum": -0.02}
- K3.1: G0 {"passed": false, "disqualifications": 0, "maximum_disqualifications": 0, "total_warning_days": 2, "maximum_total_warning_days": 0}; G1 {"passed": false, "p_gain_5pp": 0.21739130434782608, "minimum_gain_5pp": 0.2510144927536232, "p_gain_10pp": 0.06884057971014493, "minimum_gain_10pp": 0.07797101449275362}; G2 {"passed": false, "p_loss_5pp": 0.14492753623188406, "maximum_loss_5pp": 0.13144927536231885}
- Selected: none; closest if none pass: K3.0, K3.1.
- K3.1 build confirmed counts: historical n=276, min/median/mean/max=11/38/40.87/90; recent n=5, min/median/mean/max=55/68/68.6/84. Full histograms: metrics.json.
- Details: per-episode verification counts and build records in metrics.json; block metrics in blocks.csv; K1 warning dates/reasons in k1_warning_diagnostic.csv; K3 warning dates/reasons in results.csv.
- Limits: 2026 survivor universe, overlapping windows, previously accessed holdout, post-K1/K2 design, assumed revenue availability on day 11.
