# CODEX_REPORT

- Status: COMPLETE; validation ADOPT (no strategy change).
- Changed files: run.py, dev_grid.csv, selection.json, validation.csv, decision.json, report.csv, results.csv, metrics.json, CODEX_REPORT.md; holdout_access_log.jsonl appended only.
- Items expected/processed/failed: 3167/3167/0; DEV 2822, validation 210, report 135.
- Asserts: 6 pilot episodes; every episode complete and verified (0 problems); window counts 166/70/40+5 and IDs/dates match controls; finite/nonmissing outputs; 5 frozen hashes, market provenance, pre-existing dirty files and 47-line Git status unchanged; prior 96 log lines byte-identical.
- Selection: K3w_05, weights (momentum, trust, revenue)=(0.25, 0.0, 0.75); boundary=True; equal-weight duplicate=False.
- DEV paired bootstrap: {"draws": 10000, "seed": 0, "unit": "paired window", "difference_selected_minus_k2": 0.0963855421686747, "percentile_95_ci": [0.012048192771084338, 0.18072289156626506], "interval": "95% percentile; linear quantiles"}
- Plan deviations / literal conventions: no scope, architecture or rule changes. Equal-weight point is isolated; smoothing includes all neighbouring grid points regardless of eligibility. Exact rational smoothing/ties; win means Δ50 > 0. Bootstrap uses NumPy default_rng(0), 95% percentile interval (level unspecified). Turnover/costs are episode means. CSV weight IDs map to weights in every output. Control rows are stored values, never rerun; their warning dates were not re-created. No external research.
- Execution: `PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/k3_weights/run.py`; main directory; 71.98s; 8 fork workers, ordered imap; serial 3-window × 2-strategy DEV pilot reused in counts. Phase files persisted before the next phase.
- Quota: unavailable; one grid batch; no adaptive weight search.
- Final serialization check: dev_grid.csv contains exactly 16 weight rows; K2b diagnostics remain in metrics/results/report. Removed its inapplicable grid cells and aligned runner serialization; all four CSVs pass default-reader no-missing and finite checks. No simulations rerun.
- Blockers: none.

| DEV weight | Eligible | ≥+5pp | ≥+10pp | ≤−5pp | Tail | Smoothed | Mean Δ50 | Warn total/max | DQ |
|---|---|---|---|---|---|---|---|---|---|
| [0.0, 0.0, 1.0] | True | 29.52% | 10.84% | 10.84% | 18.67% | 18.88% | 2.40% | 0/0 | 0 |
| [0.0, 0.25, 0.75] | True | 26.51% | 9.04% | 7.83% | 18.67% | 18.31% | 1.97% | 1/1 | 0 |
| [0.0, 0.5, 0.5] | True | 20.48% | 3.61% | 7.23% | 13.25% | 16.63% | 1.22% | 1/1 | 0 |
| [0.0, 0.75, 0.25] | True | 18.07% | 3.01% | 6.63% | 11.45% | 10.12% | 0.95% | 1/1 | 0 |
| [0.0, 1.0, 0.0] | True | 12.65% | 1.20% | 7.83% | 4.82% | 6.43% | 0.30% | 2/1 | 0 |
| [0.25, 0.0, 0.75] | True | 28.31% | 11.45% | 9.04% | 19.28% | 20.12% | 2.51% | 1/1 | 0 |
| [0.25, 0.25, 0.5] | True | 30.72% | 11.45% | 9.04% | 21.69% | 19.02% | 2.32% | 1/1 | 0 |
| [0.25, 0.5, 0.25] | True | 23.49% | 4.22% | 5.42% | 18.07% | 12.99% | 1.31% | 2/1 | 0 |
| [0.25, 0.75, 0.0] | True | 12.65% | 0.00% | 9.64% | 3.01% | 8.19% | 0.30% | 2/1 | 0 |
| [0.5, 0.0, 0.5] | True | 30.12% | 10.24% | 7.83% | 22.29% | 19.52% | 2.40% | 1/1 | 0 |
| [0.5, 0.25, 0.25] | True | 27.71% | 9.04% | 7.83% | 19.88% | 15.58% | 1.90% | 1/1 | 0 |
| [0.5, 0.5, 0.0] | True | 14.46% | 1.81% | 10.84% | 3.61% | 10.72% | 0.16% | 2/1 | 0 |
| [0.75, 0.0, 0.25] | True | 26.51% | 10.84% | 12.05% | 14.46% | 13.86% | 2.05% | 1/1 | 0 |
| [0.75, 0.25, 0.0] | True | 22.29% | 4.22% | 13.25% | 9.04% | 10.12% | 0.60% | 2/1 | 0 |
| [1.0, 0.0, 0.0] | True | 21.08% | 8.43% | 17.47% | 3.61% | 9.04% | 0.94% | 2/1 | 0 |
| [0.3333333333333333, 0.3333333333333333, 0.3333333333333333] | True | 25.30% | 7.23% | 6.63% | 18.67% | 18.67% | 1.87% | 1/1 | 0 |

| Validation gate | Passed | Numbers (decimal units) |
|---|---|---|
| V1 | True | {"selected": 0.14285714285714285, "minimum": 0.02857142857142857} |
| V2 | True | {"selected": 0.08571428571428572, "maximum": 0.15857142857142856} |
| V3 | True | {"selected": 0.01259332470954827, "exclusive_minimum": 0} |
| V4 | True | {"disqualifications": 0, "warning_days": 0, "maximum_each": 0} |

| Period | Strategy | N | Mean Δ50 | Median Δ50 | Win | ≥+5pp | ≥+10pp | ≤−5pp | Tail | Warn total/max | DQ | Turnover | Costs TWD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| dev | K2b | 166 | 1.16% | 1.16% | 62.05% | 19.28% | 3.01% | 10.24% | 9.04% | 0/0 | 0 | 1.6950 | 3620768.39 |
| validation | K3w_05 | 70 | 1.26% | 0.74% | 55.71% | 22.86% | 5.71% | 8.57% | 14.29% | 0/0 | 0 | 2.3713 | 5559380.06 |
| validation | K3w_15 | 70 | 0.35% | -0.98% | 42.86% | 15.71% | 5.71% | 11.43% | 4.29% | 0/0 | 0 | 3.3496 | 8411708.12 |
| validation | K2b | 70 | 0.61% | 0.57% | 55.71% | 17.14% | 2.86% | 12.86% | 4.29% | 0/0 | 0 | 1.8715 | 4131201.65 |
| validation | K2 | 70 | 0.60% | 0.48% | 54.29% | 15.71% | 2.86% | 12.86% | 2.86% | 0/0 | 0 | 1.8713 | 4132561.75 |
| holdout | K3w_05 | 40 | 3.66% | 3.53% | 77.50% | 40.00% | 22.50% | 12.50% | 27.50% | 0/0 | 0 | 2.2883 | 5429391.48 |
| holdout | K3w_15 | 40 | 3.67% | 2.43% | 57.50% | 37.50% | 20.00% | 12.50% | 25.00% | 0/0 | 0 | 3.1688 | 8076960.02 |
| holdout | K2b | 40 | 4.50% | 3.13% | 72.50% | 45.00% | 22.50% | 5.00% | 40.00% | 0/0 | 0 | 2.1005 | 5035157.80 |
| holdout | K2 | 40 | 4.42% | 3.20% | 70.00% | 42.50% | 22.50% | 5.00% | 37.50% | 0/0 | 0 | 2.1024 | 5037414.76 |
| holdout | K1 | 40 | 3.96% | 3.36% | 67.50% | 45.00% | 27.50% | 20.00% | 25.00% | 0/0 | 0 | 5.1919 | 13998785.51 |
| holdout | Mom20 | 40 | 4.26% | 4.25% | 70.00% | 45.00% | 25.00% | 7.50% | 37.50% | 0/0 | 0 | 3.3765 | 8609872.30 |
| holdout | T2 | 40 | 1.48% | 1.03% | 55.00% | 25.00% | 0.00% | 7.50% | 17.50% | 0/0 | 0 | 0.9243 | 1327736.94 |
| recent | K3w_05 | 5 | -2.04% | 0.52% | 60.00% | 20.00% | 0.00% | 40.00% | -20.00% | 0/0 | 0 | 2.3484 | 5521282.69 |
| recent | K3w_15 | 5 | -2.44% | -0.68% | 40.00% | 0.00% | 0.00% | 40.00% | -40.00% | 0/0 | 0 | 2.9978 | 7514803.90 |
| recent | K2b | 5 | -0.11% | 1.89% | 60.00% | 0.00% | 0.00% | 20.00% | -20.00% | 0/0 | 0 | 1.6723 | 3510077.42 |
| recent | K2 | 5 | -0.12% | 1.65% | 60.00% | 0.00% | 0.00% | 20.00% | -20.00% | 0/0 | 0 | 1.6713 | 3511338.21 |
| recent | K1 | 5 | -4.40% | -7.04% | 40.00% | 20.00% | 20.00% | 60.00% | -40.00% | 0/0 | 0 | 5.9767 | 16054238.23 |
| recent | Mom20 | 5 | -4.19% | -7.76% | 40.00% | 0.00% | 0.00% | 60.00% | -60.00% | 0/0 | 0 | 3.9813 | 10192336.72 |
| recent | T2 | 5 | 0.49% | 0.98% | 80.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0/0 | 0 | 0.9303 | 1340193.00 |

- Full DEV metrics and heatmap coordinates: dev_grid.csv. Episode warning dates/reasons: results.csv; verification details: metrics.json. Selection tie trace: selection.json.
- Limits: 2026 survivor universe, overlapping windows, 16-weight selection bias, 70-window validation, assumed revenue availability on day 11; holdout/recent are report-only.
