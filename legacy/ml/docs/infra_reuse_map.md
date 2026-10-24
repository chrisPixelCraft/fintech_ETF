# Competition Infrastructure Reuse Map

**Extract infrastructure; rewrite the strategy-coupled ledger.** Keep V3 features/scores and V5 selection/regime in legacy. Scope: data, calendar, execution, rule checks, planner, ledger, episodes, D-Plan. Task facts: [docs/task.md](../../../docs/task.md).

Inspected 2026-09-24 at `HEAD 0363e41`. Tests used the concurrently built `.venv`: Python 3.12.10 / pandas 3.0.6, versus pinned Python 3.10 / pandas 2.0.3 (`requirements.txt`).

Labels: **EXTRACT** = copy unchanged to `competition/`; **EXTRACT+CHANGE** = copy with listed edits; **REWRITE** = port semantics; **LEGACY** = leave code, reuse ideas at most.

## Summary

| # | Component | Source | Recommendation | Tests (this env) |
|---|---|---|---|---|
| 1 | Rules + universe config | `data/reference/competition_rules.json`, `universe_competition_20260731.csv` | EXTRACT | n/a |
| 2 | Yahoo daily loader | `src/yahoo_daily.py` | EXTRACT+CHANGE | `test_yahoo_daily` fails: `yfinance` missing |
| 3 | Yahoo acquisition | `scripts/download_yahoo_daily.py` | EXTRACT+CHANGE | shares test above |
| 4 | Official execution parser | `src/v4_execution.py` | EXTRACT | 11/11 OK |
| 5 | Official execution fetcher | `scripts/v4_build_execution_data.py` | EXTRACT+CHANGE | none direct |
| 6 | Official VWAP data asset | `data/tuning_2nd/official_universe/processed/daily.csv` | EXTRACT (read-only data) | n/a |
| 7 | Research calendar | `data/yahoo_daily/v3_20260923/calendar_v2.json` via `yahoo_daily.load_calendar` | EXTRACT, labelled proxy | covered by #2 |
| 8 | Weight → lot planner | `src/v5_planner.py` | EXTRACT+CHANGE (inline one dependency) | 13/13 OK |
| 9 | D-Plan weight serializer | `src/official_v2_review.py:20-43` | EXTRACT (two functions only) | via #8 |
| 10 | Settlement rule checks | `src/backtest.py:198`, `src/double_check_ledger.py:16-42` | EXTRACT (functions only) | via ledger tests |
| 11 | Pre-trade stress envelope | `src/compliance_planner.py:65-196` | EXTRACT+CHANGE (optional verifier) | 8/8 OK (whole module) |
| 12 | Ledger / NAV simulator | `src/v4_ledger.py` | REWRITE | 4/4 OK, but coupled to V3 |
| 13 | Metrics | `src/backtest_v2.py:186-209` | EXTRACT+CHANGE | via ledger |
| 14 | Episode registry | `scripts/v5_data.py:75-136`, `config/v5_study.json` | EXTRACT+CHANGE | none direct |
| 15 | Episode runner | `src/v5_episode.py`, `src/v4_baseline.py` | LEGACY | 4 OK, 2 skipped |
| 16 | D-Plan derive/validate | `daily_auto/validate.py` at `d613b75` (not in tree) | EXTRACT+CHANGE after recovery | not runnable locally |
| 17 | Portfolio math helpers | `src/v5_portfolio.py:145-197` | EXTRACT (two functions, optional) | indirect |
| 18 | Independent audit | `scripts/v5_verify.py:203` | LEGACY; port the idea into tests | n/a |

`tests/test_double_check.py`: 5/9 failures, `AssertionError: 2025-01-08: wrong next session` at legacy `scripts/audit_double_check.py:145`. Pandas 3 is the suspected cause, unconfirmed on the pinned stack.

## 1. Rules and universe config

**EXTRACT** into one frozen `CompetitionRules` object; modules currently redeclare `min_count`, `max_weight`, `commission` in config dicts.

- `data/reference/competition_rules.json`: limits, fees, execution formulas, warnings, scoring, page citations and `unresolved` list.
- `data/reference/universe_competition_20260731.csv`: 150 rows matching the official PDF code by code; columns `ticker, name, market, yahoo_symbol, reference_date, attachment_created_at, source_page, source_url`.
- Replace `source_document.path` (`~/Downloads`) with `official_docs/`.
- Do not edit the CSV: `yahoo_daily.load_metadata` checks its SHA256.

## 2–3. Yahoo daily loader and acquisition

`src/yahoo_daily.py`: 234 lines, no other `src` imports or strategy assumptions.

| Function | Signature | Guarantee |
|---|---|---|
| `official_symbols` | `() -> list[str]` | Exactly 150 unique symbols; 0050 excluded |
| `price_validity` | `(frame) -> Series[bool]` | Finite positive OHLC; consistent bounds |
| `nominal_daily` | `(raw, symbol) -> DataFrame` | Reverses future splits only; never uses `Adj Close` |
| `validate_frame` | `(raw, symbol, calendar) -> dict` | Records gaps, actions and flags per symbol |
| `resolve_cache` | `(cache_dir=None) -> Path` | Today's snapshot, else the latest complete one |
| `load_metadata` | `(cache_dir=None) -> dict` | Fails on a universe or artifact hash mismatch |
| `load_daily` | `(cache_dir=None, include_benchmark=False) -> DataFrame` | Hash-checked panel; `attrs['calendar']` attached |
| `broad_observation_calendar` | `(daily, benchmark_dates, minimum_stocks=20)` | Weekday sessions observed across the stock universe |
| `calendar_amendment` | `(cache_dir=None, write=False) -> dict` | Deterministic calendar; verified against sources |
| `load_calendar` | `(cache_dir=None) -> list[str]` | Returns `calendar_v2` sessions |

`scripts/download_yahoo_daily.py`: `acquire(cache_dir=DEFAULT_CACHE, end=None, attempts=3)` creates immutable, resumable snapshots; a new `end` needs a new directory.

`tests/test_yahoo_daily.py`: 9 tests; module-level `yfinance` import fails solely because the new `.venv` lacks it. Dependencies: `yfinance==0.2.66`, `pyarrow` (`requirements-yahoo.txt`).

**EXTRACT+CHANGE**:

- Replace depth-sensitive `ROOT = parents[1]` and date-dependent `DEFAULT_CACHE` with explicit arguments.
- Add as-of access, e.g. `history_until(daily, date)`; no decision-time filter exists.
- Expose `action_neutral_return` for AutoTS.
- Add incremental T−1 production updates; current design snapshots the full history daily.
- Map `5371.TWO` to 3718: Yahoo has only 15 rows for 3718.

## 4–6. Official execution prices

**EXTRACT** `src/v4_execution.py` unchanged: 160 lines, no `src` imports or strategy assumptions. Constants: `OFFICIAL_DAILY_AVERAGE`, `DAILY_OPEN_RESEARCH_PROXY`. Tests: `tests/test_v4_execution.py`, 11/11 OK.

| Function | Signature | Guarantee |
|---|---|---|
| `normalize_official_rows` | `(rows: Iterable[dict]) -> DataFrame` | Average = `trading_value / volume`; missing or nonpositive inputs fail closed with flags; duplicates raise |
| `official_url` | `(market, date) -> str` | TWSE `MI_INDEX` / TPEx `dailyQuotes` exact-unit endpoints |
| `parse_official_day` | `(payload, market, date, *, source_url="", raw_sha256="", symbols=None) -> DataFrame` | Rejects stale dates and ambiguous tables; no unit guessing |
| `read_execution_table` | `(path) -> DataFrame` | Recomputes averages; never trusts a cached price |
| `resolve_execution_prices` | `(table, date, symbols, mode, proxy_open=None) -> Series` | `official_average` or `open_proxy`; missing stays NaN, no fallback |
| `content_sha256` | `(bytes) -> str` | Raw response hash |

**EXTRACT+CHANGE** fetcher `scripts/v4_build_execution_data.py`:

- `build_execution_data(dates, universe_path, output, workers=2, timeout=25) -> dict`: resumable gzip raw cache and manifest; missing rows are `MISSING_OFFICIAL_ROW`.
- Configure output (`outputs/v4` absent); emit official close for daily T−1 sizing.

**EXTRACT**, read-only asset `data/tuning_2nd/official_universe/processed/daily.csv`: `execution_vwap, official_turnover, execution_volume, vwap_source, price_source`. Coverage: 2024 20%; 2025/2026 99.3%. `vwap_source` mixes TWSE, TPEx, FinMind; validated FinMind rows are not exchange tape. Adapt to `v4_execution.COLUMNS`.

## 7. Calendar

**EXTRACT** historical loader: `calendar_v2.json`, 4,330 sessions, 2009-01-05 → 2026-09-23. Policy: 0050 observations ∪ days with ≥20 valid stocks; explicitly a research proxy.

Contest calendar 2026-10-26 → 11-27 is absent: 25 weekdays vs stated 24 sessions. Add versioned `contest_calendar.json` from the official TWSE 2026 holiday table.

## 8–9. Planner and D-Plan weight serializer

**EXTRACT+CHANGE** `src/v5_planner.py` (360 lines): D−1 target weights → whole-lot orders satisfying hard rules.

| Function | Signature | Guarantee |
|---|---|---|
| `plan` | `(target_weight, prev_close, prev_nav, holdings, cash, config=None) -> Plan` | Official floor formula; caps; 20–30 count; cash ≥0 and <25% under a ±10% price envelope; lot multiples |
| `settings` | `(config=None) -> dict` | Rejects `cash_max > .25` and invalid envelopes |
| `cap_of` | `(symbol, c) -> float` | 25% for 2330, 10% otherwise |
| `is_whole_lot` | `(quantity, lot=1000) -> bool` | Tolerance 1e-6 |
| `Plan` | dataclass `(orders, target_shares, status, reason, audit)` | `status` is one of `OK`, `REPAIRED`, `HOLD_FALLBACK`, `INFEASIBLE` |

Repair order: formula → no-trade band → caps → count → worst-case funding → cash ceiling top-up. Every order round-trips `representable_weight` for D-Plan C2. `tests/test_v5_planner.py`: 13/13 OK; formula, caps, count, cash, costs, odd lots, split float residue.

Dependency problem: `src.official_deep_tuning.representable_weight` imports `backtest_v2, tuning_2nd, tuning_a_deep` and `official_v2_review`; the latter imports `compliance_planner, tuning_2nd` at load time, pulling legacy strategies.

- Inline pure `Decimal` functions `whole_lots` and `representable_weight` from `src/official_v2_review.py:20-43`.
- Inline float-residue wrapper from `src/official_deep_tuning.py:24-35`; load `DEFAULTS` from `CompetitionRules`.
- Document tunable policies: `rebalance_band=.005`, `cash_ratio_margin=.005`, `odd_lot_mode='hold'` (U7 assumption).
- Retain `buy_price_buffer=1.10` / `sell_price_buffer=.90` for ±10% limits.

Serializer `representable_weight(target, price, nav, cap) -> float` selects the target-lot bin midpoint capped at `cap`. Odd targets raise `DPLAN_ODD_TARGET_UNREPRESENTABLE`; failed floor recovery of `target` raises `DPLAN_WEIGHT_ROUNDTRIP_FAILED`.

## 10–11. Rule checks and the stress envelope

| Function | Location | Signature | Guarantee |
|---|---|---|---|
| `rule_check` | `src/backtest.py:198` | `(holdings, cash, prices, config, whitelist, benchmark_top10=None) -> dict` | Settlement checks for count, negative cash, cash ≥25%, whitelist and caps; Active Share only as `UNVERIFIED_FORMULA` |
| `warning_reasons` | `src/double_check_ledger.py:16` | `(checks, active, overdue) -> list[str]` | One warning per day; passive caps get grace |
| `cap_state` | `src/double_check_ledger.py:23` | `(holdings, prices, nav, previous_ages, bought, config, baseline_holdings=None, baseline_cash=None) -> (ages, active, passive)` | Uses a no-trade counterfactual to separate active from passive breaches |
| `stress_violations` | `src/compliance_planner.py:123` | `(orders, holdings, cash, ranked, config, *, cash_ceiling=None, allow_mixed=False) -> list[str]` | Closed-form worst corners for cash, cash ratio and per-name concentration |

Copy pure `rule_check`, `warning_reasons`, `cap_state`, not their strategy-importing modules.

**EXTRACT+CHANGE** `stress_violations`: replace `ranked` frame’s `close` with a price mapping. Mixed buy/sell rejection is V3 policy; rules allow same-day `funding_for`. `_guard` ceiling `.25 − cash_guard_headroom(.02)` is also policy.

Remaining `compliance_planner.py`: **LEGACY**. `make_plan_v2` / `_buy_plan` depend on `score/entry_ok/exit/target_count`; `cash_guard_ratio=.16` is a strategy parameter.

## 12–13. Ledger and metrics

**REWRITE** `src/v4_ledger.py` as strategy-free `competition/ledger.py`. Existing API: `run_ledger(daily, universe, config, requested_calendar, planner, execution_prices, execution_volumes, execution_mode)`.

Preserve semantics:

- Fix orders at D−1; only fills read execution prices. Execute sells first (`src/v4_ledger.py:86`).
- Missing prices leave orders unfilled with warnings; never fabricate prices. Apply official fees/tax to fill notional.
- Apply corporate actions before fills: splits scale shares; cash dividends accrue to `receivable`.
- Settlement violations roll back trades, retain corporate entitlements and add one warning; three warnings stop the run.
- Active/passive caps use 5-session grace; terminal dividend credit enters final NAV only.

Coupling prevents extraction:

- `strategy_24d._score` is swapped by `v5_episode` using `FunctionType` global rebinding.
- Requires V3 columns `signal_available`, `source_symbol`, `ready` and planner `(ranked, holdings, cash, nav, c, buy_phase, selected)` with `SELL_THEN_WAIT_SETTLEMENT` two-phase logic.
- Requires `research_shadow=True`, hardcodes `BLOCK_SUBMISSION`, imports `_settings`, `_cap`, `_metrics`, `_safe_meta` from `backtest_v2`.
- Tests use `v4_baseline.run_episode` and `strategy_24d` fixtures (`tests/test_v4_ledger.py:5-8`).

Proposed API: `Book(holdings, cash, nav, receivable, cap_ages, warnings)`; `settle(book, orders, exec_prices, bars_t, rules) -> (Book, DayRecord)`.

Port four `test_v4_ledger` cases to synthetic bars. Port `tests/test_double_check.py` penalties: VWAP-shock rollback, terminal dividend not tradable, sale-fee-induced active cap.

**EXTRACT+CHANGE** metrics `backtest_v2._metrics` (`src/backtest_v2.py:186`): replace V3 snapshot fields; `infeasible_signal_days` reads `plan_reason`.

## 14–15. Episodes

**EXTRACT+CHANGE** registry `scripts/v5_data.py`: pure, deterministic `load_calendar(path)` L75, `_window(dates, start, length)` L82, `build_registry(study, dates)` L91, `requested_dates(episodes)` L135. Monthly and seasonal Oct-26 anchors.

`config/v5_study.json`: development 2010–2018 (108), validation 2019–2022 (48), retrospective holdout 2023–2024 (24; not pristine), seasonal Oct-26 2010–2025 (16), recent months from 2025.

Move functions to `competition/episodes.py`; tag official VWAP (2025+) vs earlier proxy episodes. Define AutoTS splits and freeze holdout before tuning.

Runners `src/v5_episode.py`, `src/v4_baseline.py`: **LEGACY**. `_bind()` rebinds globals; requires V3 `deep_feature_params`, `warmup_sessions`. Keep ideas: `generate_weights(decision_date, panel, portfolio_state, competition_state) -> DataFrame[target_weight]`; disqualified episodes retain forensic returns only (`normalize_episode_result`).

## 16. D-Plan generation and validation

**EXTRACT+CHANGE after recovery.** No builder/validator in working tree; clone has one commit, no local `d613b75`. Files exist on GitHub at that commit:

| File at `d613b75` | Size | Content |
|---|---|---|
| `daily_auto/validate.py` | 32.7 KB | `derive_orders`, `project_orders`, `active_share_value`, `validate_plan` |
| `daily_auto/compliance_state.py` | 7.2 KB | Passive-cap age history: `resolve_passive_cap_days` |
| `daily_auto/operations.py` | 39.3 KB | Daily operations; not inspected |
| `tests/test_daily_auto.py` | 21.2 KB | Semantic D-Plan tests |
| `tests/test_daily_compliance_state.py` | 2.4 KB | Cap-age tests |

Signatures read from raw GitHub:

- `derive_orders(plan, state) -> dict(orders, derivations)`: exact `Decimal` floor against actual holdings; BUY/ADD/TRIM/SELL_ALL consistency; rejects zero-delta decisions, overselling, odd-lot deltas.
- `project_orders(state, orders) -> dict`: T−1 close estimates of cash, NAV, weights, net flow, fees/tax for C12 posture.
- `validate_plan(plan, state, *, checked_at=None, filename=None, schema_path=SCHEMA, evidence_root=ROOT) -> dict`: `jsonschema` `Draft202012Validator` / `FormatChecker`; sequential IDs, reference chain, holdings coverage, C2, C12 (2% hold tolerance), C14 funding gap, filename, Taipei time.

Recover via `git fetch origin d613b75` or raw GitHub URLs. Remove `submission_status='BLOCK_SUBMISSION'` in `finish()`; replace `evidence_root` / `official_reference.json` coupling with `CompetitionRules`. Keep `Active Share` `UNKNOWN` until U4 resolves. `jsonschema==4.20.0` already pinned in `requirements.txt`.

## 17–18. Optional helpers

From `src/v5_portfolio.py`, **EXTRACT** neutral math only if AutoTS mapping needs it:

- `cap_fill(preference, budget, caps) -> Series` L145: capped proportional water-filling.
- `risk_aware_weights(mu, variance, budget, caps, floor, lam) -> ndarray` L171: separable mean-variance with box constraints, KKT bisection.

`construct`, `select_names`, `regime_table`, `regime_at`: strategy, **LEGACY**.

`scripts/v5_verify.py`: `reconstruct(location, record, tolerance=1.0)` independently rebuilds cash, fees, lots, caps, NAV from CSVs. Port this verification pattern into ledger tests, not the script.

## Do not carry over

- V3 scoring: `strategy_24d.build_features`, `_score`, `backtest.compute_features`, `score_candidates`; entry/exit flags, `target_count`, two-phase sell-then-buy.
- 4H/1H requirement (`hourly_canonical.csv`).
- Tuned x0352, `configs/competition_24d_final.json`, `cash_guard_ratio=.16`.
- V5 `cap_headroom=.01`, `n=22`, rotation buffer, regime overlay; re-derive for AutoTS if needed.
- Source-patching adapters `official_v2_review.isolated_planner`, `run_guard`, `v5_episode._bind`.

## Proposed `competition/` layout

| Module | Built from |
|---|---|
| `competition/rules.py` | #1; frozen `CompetitionRules` |
| `competition/data.py` | #2, #3, #6; plus an as-of accessor |
| `competition/calendar.py` | #7; plus the official contest calendar |
| `competition/execution.py` | #4, #5 |
| `competition/planner.py` | #8, #9 |
| `competition/checks.py` | #10, #11 |
| `competition/ledger.py` | #12, #13 (rewrite) |
| `competition/episodes.py` | #14 |
| `competition/dplan.py` | #16 (recovered) plus a D-Plan builder |

Dependency: `rules` ← everything else. `planner` / `ledger` must never import forecasting or strategy code.

## Environment notes

New `.venv`: pandas 3.0.6 / numpy 2.5.3; `requirements.txt`: pandas 2.0.3 / numpy 1.24.4. Re-run extracted tests on the chosen stack. No `yfinance` or `pytest`; tests use `unittest` (`python -m unittest tests.<name>`).
