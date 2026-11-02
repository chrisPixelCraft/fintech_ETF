# CODEX_REPORT

- Status: COMPLETE_WITH_REPORT_GAP; validation REJECT; no production change.
- Changed files: run.py, dev_grid.csv, selection.json, validation.csv, decision.json, report.csv, recent_windows.csv, results.csv, metrics.json, CODEX_REPORT.md; research/holdout_access_log.jsonl appended 3 lines.
- Items expected/processed/failed: 703/703/0 episodes (DEV 498; validation 70; report 135). K2b crash-days statistic: unavailable.
- Asserts: 3-window × 1-alpha pilot reused; all episodes complete, 703 verify calls, 0 problems; window counts and control IDs/dates match; outputs finite/nonmissing; 4 frozen hashes, prior dirty files, market provenance and 49-line Git status unchanged; prior 99 log lines byte-identical.
- Selection: alpha=0.25; exact rational stability score; tied scores choose larger alpha.
- Plan deviations / literal conventions: no strategy, architecture, grid or rule changes. V1–V3 use actual same-window K2b values (parenthetical preregistration values are rounded). Win means Δ50 > 0; tail edge = P(≥+5pp) − P(≤−5pp); turnover/costs are episode means; P90 uses linear quantiles. Crash sessions use daily NAV returns, compounded only over the five specified dates; no terminal dividend credit allocated to individual sessions. K2b crash return is unavailable: supplied results.csv contains no daily path, and no K2b daily ledger exists in prior results. No rerun or K2 substitution.
- Execution: `PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/kt_hybrid/run.py`; repository root; 16.67s; 8 fork workers with ordered imap. DEV outputs persisted before validation; decision persisted before holdout logging and report.
- Quota: unavailable; one frozen grid batch, no adaptive search.
- Blockers: K2b five-session crash return requires an existing K2b daily ledger or separate authorization to rerun; all other requested computations completed.

| DEV strategy | α | Eligible | ≥+5pp | ≥+10pp | ≤−5pp | Tail | Stability | Mean Δ50 | Warn total/max | DQ |
|---|---|---|---|---|---|---|---|---|---|---|
| KT_0.25 | 0.25 | True | 9.64% | 1.20% | 1.81% | 7.83% | 6.02% | 0.77% | 0/0 | 0 |
| KT_0.5 | 0.5 | True | 10.24% | 1.20% | 5.42% | 4.82% | -0.60% | 0.93% | 0/0 | 0 |
| KT_0.75 | 0.75 | True | 16.87% | 2.41% | 6.63% | 10.24% | 3.61% | 1.03% | 0/0 | 0 |

- K2b DEV reference: ≥+5pp 19.28%, ≤−5pp 10.24%, stability -1.20%, mean Δ50 1.16%.

| Validation gate | Passed | Numbers (decimal returns/probabilities) |
|---|---|---|
| V1 | True | {"selected": 0.02857142857142857, "maximum": 0.12857142857142856} |
| V2 | False | {"selected": 0.07142857142857142, "minimum": 0.14142857142857143} |
| V3 | False | {"selected": 0.0018679294890984251, "minimum": 0.003066960670156331} |
| V4 | True | {"disqualifications": 0, "warning_days": 0, "maximum_each": 0} |

| Period | Strategy | N | Mean Δ50 | Median Δ50 | Win | ≥+5pp | ≥+10pp | ≤−5pp | Tail | Warn total/max | DQ | Turnover | Costs TWD | P90 MDD |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| validation | KT_0.25 | 70 | 0.19% | 0.07% | 50.00% | 7.14% | 1.43% | 2.86% | 4.29% | 0/0 | 0 | 1.1868 | 2090920.92 | 11.11% |
| validation | K2b | 70 | 0.61% | 0.57% | 55.71% | 17.14% | 2.86% | 12.86% | 4.29% | 0/0 | 0 | 1.8715 | 4131201.65 | 12.12% |
| holdout | KT_0.25 | 40 | 2.22% | 1.68% | 62.50% | 27.50% | 7.50% | 7.50% | 20.00% | 0/0 | 0 | 1.2721 | 2379255.39 | 16.27% |
| holdout | KT_0.5 | 40 | 3.16% | 2.12% | 67.50% | 32.50% | 10.00% | 5.00% | 27.50% | 0/0 | 0 | 1.6041 | 3398890.62 | 14.04% |
| holdout | KT_0.75 | 40 | 4.07% | 2.88% | 72.50% | 40.00% | 17.50% | 5.00% | 35.00% | 0/0 | 0 | 1.9537 | 4517073.34 | 11.14% |
| holdout | K2b | 40 | 4.50% | 3.13% | 72.50% | 45.00% | 22.50% | 5.00% | 40.00% | 0/0 | 0 | 2.1005 | 5035157.80 | 10.81% |
| holdout | K2 | 40 | 4.42% | 3.20% | 70.00% | 42.50% | 22.50% | 5.00% | 37.50% | 0/0 | 0 | 2.1024 | 5037414.76 | 11.11% |
| holdout | T2 | 40 | 1.48% | 1.03% | 55.00% | 25.00% | 0.00% | 7.50% | 17.50% | 0/0 | 0 | 0.9243 | 1327736.94 | 18.65% |
| holdout | K3w_05 | 40 | 3.66% | 3.53% | 77.50% | 40.00% | 22.50% | 12.50% | 27.50% | 0/0 | 0 | 2.2883 | 5429391.48 | 16.35% |
| recent | KT_0.25 | 5 | -0.77% | -0.29% | 40.00% | 0.00% | 0.00% | 20.00% | -20.00% | 0/0 | 0 | 1.1667 | 2068828.67 | 13.23% |
| recent | KT_0.5 | 5 | -0.91% | -0.81% | 40.00% | 0.00% | 0.00% | 20.00% | -20.00% | 0/0 | 0 | 1.4191 | 2819105.29 | 11.06% |
| recent | KT_0.75 | 5 | -1.07% | 1.01% | 60.00% | 0.00% | 0.00% | 20.00% | -20.00% | 0/0 | 0 | 1.7212 | 3712090.04 | 9.13% |
| recent | K2b | 5 | -0.11% | 1.89% | 60.00% | 0.00% | 0.00% | 20.00% | -20.00% | 0/0 | 0 | 1.6723 | 3510077.42 | 8.20% |
| recent | K2 | 5 | -0.12% | 1.65% | 60.00% | 0.00% | 0.00% | 20.00% | -20.00% | 0/0 | 0 | 1.6713 | 3511338.21 | 8.32% |
| recent | T2 | 5 | 0.49% | 0.98% | 80.00% | 0.00% | 0.00% | 0.00% | 0.00% | 0/0 | 0 | 0.9303 | 1340193.00 | 15.56% |
| recent | K3w_05 | 5 | -2.04% | 0.52% | 60.00% | 20.00% | 0.00% | 40.00% | -20.00% | 0/0 | 0 | 2.3484 | 5521282.69 | 16.31% |

| Recent Δ50 | Dates | KT_0.25 | KT_0.5 | KT_0.75 | K2b | T2 |
|---|---|---|---|---|---|---|
| W1 | 2026-04-30–2026-06-03 | -5.02% | -5.18% | -6.50% | -6.28% | 2.35% |
| W2 | 2026-06-04–2026-07-08 | 1.95% | 1.64% | 1.64% | 1.99% | 0.64% |
| W3 | 2026-07-09–2026-08-12 | 1.38% | 1.90% | 2.59% | 3.89% | 0.98% |
| W4 | 2026-08-13–2026-09-15 | -1.87% | -0.81% | 1.01% | 1.89% | -3.36% |
| W5 | 2026-09-16–2026-10-22 | -0.29% | -2.11% | -4.08% | -2.07% | 1.84% |

| W5 crash dates | Strategy | Compounded return |
|---|---|---|
| 2026-10-15, 2026-10-22, 2026-10-20, 2026-10-13, 2026-10-12 | KT_0.25 | -11.09% |
| 2026-10-15, 2026-10-22, 2026-10-20, 2026-10-13, 2026-10-12 | KT_0.5 | -10.41% |
| 2026-10-15, 2026-10-22, 2026-10-20, 2026-10-13, 2026-10-12 | KT_0.75 | -9.56% |
| 2026-10-15, 2026-10-22, 2026-10-20, 2026-10-13, 2026-10-12 | K2b | UNAVAILABLE (missing daily control data; rerun prohibited) |

- Full precision metrics: CSV/JSON outputs. Per-episode warning dates/reasons: results.csv. Crash daily returns and verification details: metrics.json. Controls are stored values, never rerun.
