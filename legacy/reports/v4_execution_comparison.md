# V4 Stage 1 — Execution comparison

**Scope: frozen V3 signals and parameters; no V4 strategy implementation, tuning, or winner selection.**

The paired comparison changes fills from Yahoo Open to exchange trading value divided by share volume. The two primary tracks retain the same V3 signal history, prior-close sizing/valuation basis, fees, corporate-action policy, and simulated settlement rules. This isolates the fill assumption; it does not certify a live D-Plan. The third track, `official_average_official_close`, also uses exchange closes for D-Plan sizing and marking after constructing the frozen indicators. It tests the official price pipeline but bundles two price-basis changes.

## Evidence and coverage

The predeclared study attempts 7 windows per track, each starting with NT$1B and no holdings. The dates were fixed before inspecting returns. This is a bounded diagnostic sample, not exhaustive 2010–2026 validation. Recent and seasonal windows are diagnostics only. The two latest windows overlap and are not independent samples.

Raw-source verification authenticated 23,250 expected observations, of which 20,959 have usable official value/volume. Missing historical roster observations and invalid value/volume remain explicit; request success alone does not imply full coverage.

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

A complete return is reported even when measured compliance fails. Incomplete episodes have no terminal 24-day return; their partial returns remain forensic artifacts and are excluded from return summaries. Every attempt remains in the denominator.

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

MDD for incomplete episodes is a partial-path diagnostic, not a full 24-day MDD. Paired differences require complete official fills; completed but missing-price paths remain descriptive failures, not canonical comparisons.

| Execution | Mean | Median | P25 | P10 | Worst | Positive / complete |
|---|---:|---:|---:|---:|---:|---:|
| open_proxy | -2.300% | 0.594% | -8.679% | -8.841% | -8.949% | 3 / 5 |
| official_average | -2.631% | 1.218% | -6.779% | -11.470% | -14.597% | 3 / 5 |
| official_average_official_close | -1.419% | 1.377% | -6.779% | -7.948% | -8.727% | 3 / 5 |

The full official-price diagnostic also uses official prior closes and daily marks:

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

Totals above describe observed days across all attempts, including incomplete runs; overlapping dates can be counted twice.

## Paired sensitivity and ranking

4 paired windows have complete returns and official fills. Mean official-minus-Open return is **-0.304 percentage points**, with a range of **-5.648 to 2.380 points**. This sample measures sensitivity; it cannot establish how much of every earlier V3 conclusion came from Open execution.

Strategy ranking changes are **not applicable**: only one frozen V3 configuration was tested. The following ranking is an episode-return diagnostic on the same complete pairs, not a strategy selection.

| Episode | Open return rank | Official return rank |
|---|---:|---:|
| development_2010 | 4 | 4 |
| recent_latest | 2 | 2 |
| rolling_latest_minus5 | 3 | 3 |
| seasonal_2025 | 1 | 1 |

Per-trade quote comparisons are retained in `outputs/v4/stage1/trade_price_comparison.csv`, including signed quantity, Open, official average, absolute difference, and relative difference. 592 distinct episode/date/symbol quote pairs are available; mean official/Open difference is 0.443%. This is an unweighted quote statistic, not portfolio attribution.

The mean official-average versus **official Open** quote difference is -0.126%; the mean official-Open versus **Yahoo Open** difference is 0.568%. Consequently, the requested Open-proxy comparison includes vendor/exchange price-basis differences as well as intraday execution timing. It must not be interpreted as pure intraday timing alpha. The official-close track separately measures the full official price basis.

## Verification and interpretation

The Open track is compared table-by-table with the original V3 callable for equity, trades, orders, holdings, compliance, and snapshots. Independent accounting reconstruction checks prices, fees, taxes, fixed prior-close orders, corporate actions, cash, holdings, NAV, and period metrics. Source/input hashes and deterministic output hashes bind the run; the verifier rejects altered artifacts.

Evidence: `outputs/v4/stage1/open_reproduction.json`, `audits.json`, `manifest.json`, `results.csv`, and the per-episode `ledgers/` tables. The execution cache retains source URLs, raw payloads, quality flags, units, and provenance. Missing official value/volume never falls back to Open or Close.

The fixed 2026 competition universe is a retrospective stress universe, not historically known constituents. The frozen V3 parameters and historical holdout were already examined in earlier work; this is not a newly unseen test set. Yahoo corporate-action history and the observed-session calendar remain research inputs. Whole-day rollback and period-end dividend credit reproduce V3 research settlement assumptions. Neither Active Share, organizer settlement parity, corporate-action contracts, nor market impact at NT$1B is certified. All tracks retain **BLOCK_SUBMISSION**.

Source rules: `official_docs/D-Plan_撰寫指南.md`, section ⑥; `docs/v4_master_spec.md`, sections 3–5; `docs/v4_execution_validation_spec.md`, sections 2–4, 9–10. Observed execution differences are evidence about this fixed baseline and these windows only. There is no V4 winner and no strategy parameter was tuned.

## Reproduce

```bash
python scripts/v4_build_execution_data.py --dates outputs/v4/execution_requested_dates.csv
python scripts/v4_run_baseline.py --output outputs/v4/stage1_replay
python scripts/v4_verify.py --output outputs/v4/stage1_replay
python scripts/v4_report.py --output outputs/v4/stage1_replay
python -m unittest discover -s tests -p 'test_v4_*.py' -q
```
