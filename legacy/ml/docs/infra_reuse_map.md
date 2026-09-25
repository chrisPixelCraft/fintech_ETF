# Competition Infrastructure Reuse Map

This map covers competition **infrastructure** only: data, calendar, execution, rule checks, planner, ledger, episodes, and D-Plan. Strategy code (V3 features and scores, V5 selection and regime) stays in legacy. Task facts are in [docs/task.md](../../../docs/task.md).

- Inspected on 2026-09-24 at `HEAD 0363e41`
- Tests were run with the concurrently built `.venv`
  - Python 3.12.10, pandas 3.0.6
  - The pinned stack is Python 3.10 with pandas 2.0.3 (`requirements.txt`)
- Recommendation labels
  - **EXTRACT**: copy to `competition/` as-is
  - **EXTRACT+CHANGE**: copy with the listed edits
  - **REWRITE**: port the semantics, not the code
  - **LEGACY**: leave it; reuse ideas at most

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

`tests/test_double_check.py` shows 5/9 failures here with `AssertionError: 2025-01-08: wrong next session` from `scripts/audit_double_check.py:145`. That audit script is legacy. The failure probably comes from the pandas 3 environment, but this was not confirmed on the pinned stack.

## 1. Rules and universe config

- `data/reference/competition_rules.json`
  - Machine-readable limits, fees, execution formulas, warnings and scoring
  - Page citations and an explicit `unresolved` list
- `data/reference/universe_competition_20260731.csv`
  - 150 rows; matches the official PDF code by code
  - Columns: `ticker, name, market, yahoo_symbol, reference_date, attachment_created_at, source_page, source_url`
- Caveats
  - `source_document.path` points to `~/Downloads`; use `official_docs/` instead
  - `yahoo_daily.load_metadata` checks the universe file's SHA256, so do not edit the CSV
- Recommendation: **EXTRACT**
  - Load both through one frozen `CompetitionRules` object
  - Today, every module re-declares `min_count`, `max_weight`, `commission` and so on in its own config dict

## 2–3. Yahoo daily loader and acquisition

**Path:** `src/yahoo_daily.py` (234 lines). It imports no other `src` module.

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

- Script `scripts/download_yahoo_daily.py`
  - `acquire(cache_dir=DEFAULT_CACHE, end=None, attempts=3)`
  - Immutable, resumable snapshot per directory
  - A new `end` requires a new directory
- Tests: `tests/test_yahoo_daily.py`, 9 tests
  - They import `yfinance` at module level
  - They fail in the new `.venv` only because `yfinance` is not installed
- Strategy assumptions: none
- Edits needed (**EXTRACT+CHANGE**)
  - `ROOT = parents[1]` is depth-sensitive; pass paths explicitly
  - `DEFAULT_CACHE` depends on today's date; make it an argument
  - Add an as-of accessor (for example `history_until(daily, date)`)
    - There is currently no decision-time filter
  - Expose `action_neutral_return` as the return series for AutoTS
  - Daily production needs an incremental T−1 update
    - The current design is a full snapshot per day
  - Map `5371.TWO` to 3718
    - Yahoo has only 15 rows under 3718
- Dependencies: `yfinance==0.2.66`, `pyarrow` (`requirements-yahoo.txt`)

## 4–6. Official execution prices

**Path:** `src/v4_execution.py` (160 lines). No `src` imports.

| Function | Signature | Guarantee |
|---|---|---|
| `normalize_official_rows` | `(rows: Iterable[dict]) -> DataFrame` | Average = `trading_value / volume`; missing or nonpositive inputs fail closed with flags; duplicates raise |
| `official_url` | `(market, date) -> str` | TWSE `MI_INDEX` / TPEx `dailyQuotes` exact-unit endpoints |
| `parse_official_day` | `(payload, market, date, *, source_url="", raw_sha256="", symbols=None) -> DataFrame` | Rejects stale dates and ambiguous tables; no unit guessing |
| `read_execution_table` | `(path) -> DataFrame` | Recomputes averages; never trusts a cached price |
| `resolve_execution_prices` | `(table, date, symbols, mode, proxy_open=None) -> Series` | `official_average` or `open_proxy`; missing stays NaN, no fallback |
| `content_sha256` | `(bytes) -> str` | Raw response hash |

- Constants `OFFICIAL_DAILY_AVERAGE`, `DAILY_OPEN_RESEARCH_PROXY`
- Tests: `tests/test_v4_execution.py`, 11/11 OK
- Strategy assumptions: none
- Recommendation: **EXTRACT** as-is
- Fetcher `scripts/v4_build_execution_data.py`
  - `build_execution_data(dates, universe_path, output, workers=2, timeout=25) -> dict`
  - Resumable gzip raw cache and manifest
  - Missing rows are explicit `MISSING_OFFICIAL_ROW`
  - **EXTRACT+CHANGE**
    - Make the output path configurable (`outputs/v4` is absent)
    - Also emit the official close for sizing
    - Needed daily for T−1 closes
- Data asset `data/tuning_2nd/official_universe/processed/daily.csv`
  - Columns include `execution_vwap, official_turnover, execution_volume, vwap_source, price_source`
  - VWAP coverage: 2024 is 20%; 2025 and 2026 are 99.3%
  - `vwap_source` mixes TWSE, TPEx and FinMind
    - FinMind rows are validated against official data, but they are not an exchange tape
  - Use it read-only as the historical execution table
    - Convert it to the `v4_execution.COLUMNS` schema with an adapter

## 7. Calendar

- `calendar_v2.json`
  - 4,330 sessions, 2009-01-05 → 2026-09-23
  - Policy: 0050 observations ∪ days with ≥20 valid stocks
  - Self-labelled as a research proxy
- The 2026-10-26 → 11-27 contest calendar does not exist in the repo
  - There are 25 weekdays against the 24 sessions the rules state
  - It must come from the official TWSE 2026 holiday table
- Recommendation
  - **EXTRACT** the loader for historical sessions
  - Add a separate, versioned `contest_calendar.json` built from the official holiday source

## 8–9. Planner and D-Plan weight serializer

**Path:** `src/v5_planner.py` (360 lines). It turns D−1 target weights into whole-lot orders that pass the hard rules.

| Function | Signature | Guarantee |
|---|---|---|
| `plan` | `(target_weight, prev_close, prev_nav, holdings, cash, config=None) -> Plan` | Official floor formula; caps; 20–30 count; cash ≥0 and <25% under a ±10% price envelope; lot multiples |
| `settings` | `(config=None) -> dict` | Rejects `cash_max > .25` and invalid envelopes |
| `cap_of` | `(symbol, c) -> float` | 25% for 2330, 10% otherwise |
| `is_whole_lot` | `(quantity, lot=1000) -> bool` | Tolerance 1e-6 |
| `Plan` | dataclass `(orders, target_shares, status, reason, audit)` | `status` is one of `OK`, `REPAIRED`, `HOLD_FALLBACK`, `INFEASIBLE` |

- Repair order
  - formula → no-trade band → caps → count → worst-case funding → cash ceiling top-up
- Every emitted order must round-trip `representable_weight`
  - This guarantees the D-Plan C2 formula reproduces the order
- Tests: `tests/test_v5_planner.py`, 13/13 OK
  - Covers formula, caps, count, cash, costs, odd lots and split float residue
- Dependency problem
  - It imports `src.official_deep_tuning.representable_weight`
  - That module imports `backtest_v2, tuning_2nd, tuning_a_deep` and `official_v2_review`
  - `official_v2_review` imports `compliance_planner, tuning_2nd` at module load
  - This pulls the whole legacy strategy tree
- Policy defaults, not rules (keep them tunable and documented)
  - `rebalance_band=.005`
  - `cash_ratio_margin=.005`
  - `odd_lot_mode='hold'`, the U7 assumption
  - `buy_price_buffer=1.10` and `sell_price_buffer=.90` match the ±10% daily limit; keep them
- Recommendation: **EXTRACT+CHANGE**
  - Inline `whole_lots` and `representable_weight` from `src/official_v2_review.py:20-43`
    - Both are pure `Decimal` code
  - Inline the float-residue wrapper from `src/official_deep_tuning.py:24-35`
  - Read `DEFAULTS` from `CompetitionRules`
- Serializer contract (`representable_weight(target, price, nav, cap) -> float`)
  - Picks the midpoint of the same target-lot bin, capped at `cap`
  - Raises `DPLAN_ODD_TARGET_UNREPRESENTABLE` on odd targets
  - Raises `DPLAN_WEIGHT_ROUNDTRIP_FAILED` if the floor does not recover `target`

## 10–11. Rule checks and the stress envelope

| Function | Location | Signature | Guarantee |
|---|---|---|---|
| `rule_check` | `src/backtest.py:198` | `(holdings, cash, prices, config, whitelist, benchmark_top10=None) -> dict` | Settlement checks for count, negative cash, cash ≥25%, whitelist and caps; Active Share only as `UNVERIFIED_FORMULA` |
| `warning_reasons` | `src/double_check_ledger.py:16` | `(checks, active, overdue) -> list[str]` | One warning per day; passive caps get grace |
| `cap_state` | `src/double_check_ledger.py:23` | `(holdings, prices, nav, previous_ages, bought, config, baseline_holdings=None, baseline_cash=None) -> (ages, active, passive)` | Uses a no-trade counterfactual to separate active from passive breaches |
| `stress_violations` | `src/compliance_planner.py:123` | `(orders, holdings, cash, ranked, config, *, cash_ceiling=None, allow_mixed=False) -> list[str]` | Closed-form worst corners for cash, cash ratio and per-name concentration |

- `rule_check`, `warning_reasons` and `cap_state` are pure functions
  - Their modules import strategy code at load time
  - Copy the functions; do not import the modules
- `stress_violations` needs edits
  - It reads prices from a `ranked` frame's `close`; change it to a price mapping
  - It rejects mixed buy/sell by default, which is a V3 policy
    - The rules allow same-day funding (`funding_for`)
  - `_guard` ceiling `.25 − cash_guard_headroom(.02)` is a policy value
- The rest of `compliance_planner.py` is **LEGACY**
  - `make_plan_v2` and `_buy_plan` depend on `score/entry_ok/exit/target_count`
  - `cash_guard_ratio=.16` is a strategy parameter

## 12–13. Ledger and metrics

**Path:** `src/v4_ledger.py`, `run_ledger(daily, universe, config, requested_calendar, planner, execution_prices, execution_volumes, execution_mode)`.

Semantics that are correct and must be kept:

- Orders are fixed at D−1; only fills read execution prices
- Pending orders execute sells first (`src/v4_ledger.py:86`)
- A missing price leaves the order unfilled and logs a warning
  - It never fabricates a price
- Fees and tax use official rates on the fill notional
- Corporate actions apply before fills
  - Splits scale shares
  - Cash dividends accrue to `receivable`
- A settlement violation rolls back that day's trades
  - It keeps corporate entitlements
  - It adds one warning; three warnings stop the run
- Active and passive caps use a 5-session grace
- The terminal dividend credit is added only to final NAV

Why it cannot be extracted as-is:

- It imports `strategy_24d._score`, a V3 strategy scorer
  - `v5_episode` swaps it through `FunctionType` global rebinding
- It requires V3 panel columns
  - `signal_available`, `source_symbol`, `ready`
- The planner callback has the V3 signature
  - `(ranked, holdings, cash, nav, c, buy_phase, selected)`
  - It includes the V3 `SELL_THEN_WAIT_SETTLEMENT` two-phase concept
- It requires `research_shadow=True`
  - It hard-codes `BLOCK_SUBMISSION` status strings
- It pulls `_settings`, `_cap`, `_metrics` and `_safe_meta` from `backtest_v2`
- Its tests go through `v4_baseline.run_episode` with a `strategy_24d` fixture
  - `tests/test_v4_ledger.py:5-8`

Recommendation: **REWRITE** as `competition/ledger.py` with a strategy-free step API. For example:

- `Book(holdings, cash, nav, receivable, cap_ages, warnings)`
- `settle(book, orders, exec_prices, bars_t, rules) -> (Book, DayRecord)`
- Port the four `test_v4_ledger` cases
  - Build them on synthetic bars instead of the V3 fixture
- Port the penalty cases from `tests/test_double_check.py`
  - VWAP-shock rollback
  - Terminal dividend not tradable
  - Sale fees creating an active cap
- Metrics: copy `backtest_v2._metrics` (`src/backtest_v2.py:186`)
  - Replace fields that assume V3 snapshots
  - `infeasible_signal_days` reads `plan_reason`

## 14–15. Episodes

- Registry: `scripts/v5_data.py`
  - `load_calendar(path)` at L75
  - `_window(dates, start, length)` at L82
  - `build_registry(study, dates)` at L91
  - `requested_dates(episodes)` at L135
  - Pure and deterministic
  - Monthly anchors and a seasonal Oct-26 anchor
- Splits: `config/v5_study.json`
  - Development 2010–2018 (108 episodes)
  - Validation 2019–2022 (48)
  - Retrospective holdout 2023–2024 (24), labelled "not pristine"
  - Seasonal Oct-26 anchors 2010–2025 (16)
  - Recent months from 2025
- Recommendation: **EXTRACT+CHANGE**
  - Move the functions into `competition/episodes.py`
  - Tag each episode by execution mode
    - Official VWAP exists only for 2025+
    - Earlier episodes are proxy
  - Define new AutoTS splits and freeze a holdout before tuning
- Runner: `src/v5_episode.py` and `src/v4_baseline.py` are **LEGACY**
  - `_bind()` rebinds function globals to inject planners
  - It needs V3 config keys (`deep_feature_params`, `warmup_sessions`)
- Ideas worth keeping
  - Strategy contract: `generate_weights(decision_date, panel, portfolio_state, competition_state) -> DataFrame[target_weight]`
  - A disqualified episode keeps only a forensic return (`normalize_episode_result`)

## 16. D-Plan generation and validation

**Fact:** no D-Plan builder or validator exists in the working tree. This clone contains one commit, so `d613b75` is not local. The files exist on GitHub under that commit.

| File at `d613b75` | Size | Content |
|---|---|---|
| `daily_auto/validate.py` | 32.7 KB | `derive_orders`, `project_orders`, `active_share_value`, `validate_plan` |
| `daily_auto/compliance_state.py` | 7.2 KB | Passive-cap age history: `resolve_passive_cap_days` |
| `daily_auto/operations.py` | 39.3 KB | Daily operations; not inspected |
| `tests/test_daily_auto.py` | 21.2 KB | Semantic D-Plan tests |
| `tests/test_daily_compliance_state.py` | 2.4 KB | Cap-age tests |

Key signatures, read from the raw GitHub file:

- `derive_orders(plan, state) -> dict(orders, derivations)`
  - Exact `Decimal` floor formula against actual held shares
  - Enforces action consistency (BUY/ADD/TRIM/SELL_ALL)
  - Rejects zero-delta decisions, overselling and odd-lot deltas
- `project_orders(state, orders) -> dict`
  - T−1 close estimate of cash, NAV, weights, net flow, fees and tax
  - Input for the C12 posture check
- `validate_plan(plan, state, *, checked_at=None, filename=None, schema_path=SCHEMA, evidence_root=ROOT) -> dict`
  - `jsonschema` `Draft202012Validator` with `FormatChecker`
  - Sequential IDs, the reference chain and holdings coverage
  - C2, C12 (2% hold tolerance), C14 funding gap, filename, Taipei time
- Recommendation: **EXTRACT+CHANGE** after recovery
  - Recover with `git fetch origin d613b75` or the raw GitHub URLs
  - Remove the hard-coded `submission_status='BLOCK_SUBMISSION'` in `finish()`
  - Replace `evidence_root` and `official_reference.json` coupling with `CompetitionRules`
  - Keep `Active Share` as `UNKNOWN` until U4 is resolved
- `jsonschema==4.20.0` is already pinned in `requirements.txt`

## 17–18. Optional helpers

- `src/v5_portfolio.py`
  - `cap_fill(preference, budget, caps) -> Series` at L145
    - Capped proportional water-filling
  - `risk_aware_weights(mu, variance, budget, caps, floor, lam) -> ndarray` at L171
    - Separable mean-variance with box constraints (KKT bisection)
  - Both are neutral math; **EXTRACT** only if the AutoTS mapping needs them
  - `construct`, `select_names`, `regime_table` and `regime_at` are strategy; **LEGACY**
- `scripts/v5_verify.py`
  - `reconstruct(location, record, tolerance=1.0)` rebuilds cash, fees, lots, caps and NAV from ledger CSVs
  - It is an independent verifier pattern
  - Port the idea into ledger tests rather than the script

## Do not carry over

- V3 feature panel and scoring
  - `strategy_24d.build_features`, `_score`, `backtest.compute_features`, `score_candidates`
- V3 entry/exit flags, `target_count` and the two-phase sell-then-buy flow
- The 4H/1H intraday requirement (`hourly_canonical.csv`)
- Tuned parameters
  - x0352, `configs/competition_24d_final.json`, `cash_guard_ratio=.16`
- V5 construction policy
  - `cap_headroom=.01`, `n=22`, rotation buffer, regime overlay
  - These may be re-derived for AutoTS, but not inherited
- Source-patching adapters
  - `official_v2_review.isolated_planner`, `run_guard`
  - `v5_episode._bind`

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

Dependency direction: `rules` ← everything else. `planner` and `ledger` must never import forecasting or strategy code.

## Environment notes

- The new `.venv` differs from the pinned stack
  - It has pandas 3.0.6 and numpy 2.5.3
  - `requirements.txt` pins pandas 2.0.3 and numpy 1.24.4
  - Re-run the extracted module tests on the stack you choose
- `yfinance` is absent from the new `.venv`
- No `pytest`; the tests use `unittest` (`python -m unittest tests.<name>`)
