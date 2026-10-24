# V4 Stage 1 — Execution comparison

**Frozen V3 only: no V4 strategy implementation, tuning or winner.** Four complete pairs show mean official-minus-Open return **-0.304 percentage points**; all tracks retain **BLOCK_SUBMISSION**.

Primary tracks change Yahoo Open fills to exchange trading value/share volume, retaining V3 signals, prior-close sizing/valuation, fees, corporate actions and simulated settlement. This isolates fills without certifying live D-Plan. `official_average_official_close` additionally uses exchange closes for sizing/marking after frozen indicators; it tests the official pipeline while bundling two price-basis changes.

## Evidence and coverage

Each track attempts 7 predeclared windows from NT$1B cash, no holdings. Dates preceded return inspection. This bounded sample is not exhaustive 2010–2026 validation; recent/seasonal windows are diagnostics, and the latest two overlap.

Raw-source verification authenticated 23,250 expected observations; 20,959 have usable official value/volume. Missing historical rosters and invalid value/volume remain explicit; request success does not establish coverage.

| Episode | Split | First session | Last session |
|---|---|---|---|
| development_2010 | development | 2010-01-04 | 2010-02-04 |
| development_2018 | development | 2018-01-02 | 2018-02-02 |
| validation_2020 | validation | 2020-03-02 | 2020-04-06 |
| holdout_2023 | historical_holdout | 2023-01-03 | 2023-02-15 |
| seasonal_2025 | seasonal_diagnostic | 2025-10-20 | 2025-11-21 |
| recent_latest | recent_diagnostic | 2026-08-21 | 2026-09-23 |
| rolling_latest_minus5 | rolling_diagnostic | 2026-08-14 | 2026-09-16 |

## Results, including failures

Complete returns include measured-compliance failures. Incomplete terminal 24-day returns stay null; partial returns are forensic only and excluded from summaries. All attempts remain in denominators.

| Execution | Complete / attempted | Measured PASS / attempted | Official fills available / attempted |
|---|---:|---:|---:|
| open_proxy | 5 / 7 | 3 / 7 | 0 / 7 |
| official_average | 5 / 7 | 1 / 7 | 4 / 7 |
| official_average_official_close | 5 / 7 | 3 / 7 | 5 / 7 |

| Episode | Open return | Average return | Return difference (pp) | Open MDD | Average MDD | MDD difference (pp) |
|---|---:|---:|---:|---:|---:|---:|
| development_2010 | -8.949% | -14.597% | -5.648 | 11.071% | 15.940% | 4.869 |
| development_2018 | 1.658% | 1.218% | N/A | 2.782% | 3.299% | N/A |
| validation_2020 | N/A | N/A | N/A | -0.000% | -0.000% | N/A |
| holdout_2023 | N/A | N/A | N/A | -0.000% | -0.000% | N/A |
| seasonal_2025 | 3.876% | 4.029% | 0.153 | 9.111% | 8.860% | -0.250 |
| recent_latest | 0.594% | 2.975% | 2.380 | 9.630% | 9.199% | -0.431 |
| rolling_latest_minus5 | -8.679% | -6.779% | 1.900 | 10.694% | 10.300% | -0.394 |

Incomplete MDD covers only the partial path. Paired differences require complete official fills; completed paths with missing prices remain descriptive failures, not canonical pairs.

| Execution | Mean | Median | P25 | P10 | Worst | Positive / complete |
|---|---:|---:|---:|---:|---:|---:|
| open_proxy | -2.300% | 0.594% | -8.679% | -8.841% | -8.949% | 3 / 5 |
| official_average | -2.631% | 1.218% | -6.779% | -11.470% | -14.597% | 3 / 5 |
| official_average_official_close | -1.419% | 1.377% | -6.779% | -7.948% | -8.727% | 3 / 5 |

Official-price track: official prior-close sizing and daily marks.

| Episode | Official-price return | Canonical data status | Measured compliance |
|---|---:|---|---|
| development_2010 | -8.727% | AVAILABLE | PASS_MEASURED |
| development_2018 | 1.377% | AVAILABLE | PASS_MEASURED |
| validation_2020 | N/A | BLOCK_CANONICAL_V4 | FAIL_MEASURED |
| holdout_2023 | N/A | BLOCK_CANONICAL_V4 | FAIL_MEASURED |
| seasonal_2025 | 4.061% | AVAILABLE | PASS_MEASURED |
| recent_latest | 2.975% | AVAILABLE | FAIL_MEASURED |
| rolling_latest_minus5 | -6.779% | AVAILABLE | FAIL_MEASURED |

| Execution | Cash violation days | Weight violation days | Holding-count days | Odd-lot days | Missing fill days | Unfilled days | No-valid-plan days |
|---|---:|---:|---:|---:|---:|---:|---:|
| open_proxy | 6 | 0 | 6 | 34 | 0 | 0 | 6 |
| official_average | 6 | 0 | 6 | 34 | 1 | 1 | 6 |
| official_average_official_close | 6 | 0 | 6 | 34 | 0 | 0 | 6 |

| Execution | Mean turnover (initial NAV units) | Total transaction cost (NT$) | Mean day-1 invested | Mean day-3 invested | Mean day-5 invested |
|---|---:|---:|---:|---:|---:|
| open_proxy | 1.7496 | 29,359,768.89 | 65.059% (n=7) | 64.040% (n=7) | 88.708% (n=5) |
| official_average | 1.7603 | 29,532,043.16 | 65.461% (n=7) | 64.558% (n=7) | 89.240% (n=5) |
| official_average_official_close | 1.7774 | 29,924,120.83 | 64.498% (n=7) | 63.589% (n=7) | 88.529% (n=5) |

Totals cover observed days across all attempts, including incomplete runs; overlap can double-count dates.

## Paired sensitivity and ranking

4 complete official-fill pairs: mean official-minus-Open **-0.304 percentage points**, range **-5.648 to 2.380 points**. This measures sensitivity here, not execution attribution for every earlier V3 conclusion.

Strategy ranking is **not applicable**: only one frozen configuration. Below are episode-return ranks on the same complete pairs, not strategy selection.

| Episode | Open return rank | Official return rank |
|---|---:|---:|
| development_2010 | 4 | 4 |
| recent_latest | 2 | 2 |
| rolling_latest_minus5 | 3 | 3 |
| seasonal_2025 | 1 | 1 |

`outputs/v4/stage1/trade_price_comparison.csv` retains signed quantity, Open, official average, absolute/relative differences. Its 592 distinct episode/date/symbol pairs average 0.443% official/Open difference: unweighted quotes, not portfolio attribution.

Official-average versus **official Open** averages -0.126%; official-Open versus **Yahoo Open**, 0.568%. The requested proxy comparison includes vendor/exchange price-basis and intraday timing differences, not pure timing alpha. The official-close track tests the full official price basis.

## Verification and interpretation

Open is checked table-by-table against the V3 callable: equity, trades, orders, holdings, compliance, snapshots. Independent reconstruction checks prices, fees/taxes, fixed prior-close orders, actions, cash, holdings, NAV and period metrics. Source/input and deterministic output hashes bind the run; altered artifacts fail verification.

Evidence: `outputs/v4/stage1/open_reproduction.json`, `audits.json`, `manifest.json`, `results.csv`, per-episode `ledgers/`. Cache retains source URLs, raw payloads, quality flags, units and provenance. Missing official value/volume never falls back to Open/Close.

The 2026 universe is retrospective, not historical constituents; V3 parameters/holdout were previously examined, so this is not unseen evidence. Yahoo action history and observed-session calendar remain research inputs. Whole-day rollback and period-end dividend credit reproduce V3 settlement assumptions. Active Share, organizer settlement parity, action contracts and NT$1B market impact remain uncertified: **BLOCK_SUBMISSION**.

Rules: `official_docs/D-Plan_撰寫指南.md` ⑥; `docs/v4_master_spec.md` 3–5; `docs/v4_execution_validation_spec.md` 2–4, 9–10. Differences apply only to this baseline/windows; no V4 winner or parameter tuning.

## Reproduce

```bash
python scripts/v4_build_execution_data.py --dates outputs/v4/execution_requested_dates.csv
python scripts/v4_run_baseline.py --output outputs/v4/stage1_replay
python scripts/v4_verify.py --output outputs/v4/stage1_replay
python scripts/v4_report.py --output outputs/v4/stage1_replay
python -m unittest discover -s tests -p 'test_v4_*.py' -q
```
