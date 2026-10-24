Goal:
Run the pre-registered recent-3-window comparison in PREREGISTRATION.md (SHA256 c51f0e6a8295a97598b374899e04bba9fb7654ef5a130bc4703a61254e4348b8, frozen 2026-10-24 22:25 CST). Produce per-window results for C1–C4 vs Mom20, apply the frozen decision rule mechanically, and compute heuristic Active Share.

Scope / non-goals:
- In: windows W1/W2/W3, strategies Mom20, C1, C2, C3, C4 exactly as specified; 0050 reference; AS; crash-exposure table.
- Out: no parameter search, no extra candidates, no threshold changes, no edits to production/, competition/, research/*.py, legacy/, data/, production_runs/. Do not touch PREREGISTRATION.md.
- If a spec is ambiguous, choose the most literal reading, record it under "Plan deviations / interpretations"; do not change gates.

Implementation approach:
- Template: production_runs/preflight_20261026/strategy_retest/momentum/run.py. Reuse the SAME market load (corrected_nominal_daily.parquet, official_combined.csv, calendar_v2.json), load_rules(), engine.policy_of(base), backtest.run_episode, backtest.verify_episode, ExecutionConfig from production/strategy.json.
- Windows: W3 = last 24 calendar sessions ending 2026-10-22 (assert first = 2026-09-16); W2 = 24 sessions immediately before W3; W1 = 24 before W2. Episode decision date = session before window start, as in template.
- Strategies implement decide(view, state) returning target weights through competition/portfolio.py where possible (caps, 20–30 names, cash rules). Only D-1 data (AsOfView). Large-cap rank = mean(close×volume) over last 60 sessions, names with 60 full valid sessions; ticker for TSMC is 2330.
	- C1/C3: build targets on first session; afterwards return current weights (no trade) unless a rule breach would occur (any weight above cap minus 0.5pp, or cash outside rules) → re-target to original weights. Freeze last 3 sessions.
	- C2: core names fixed at window start (2330 23.5% + large-cap ranks 1–9 excl. 2330 at 3.5%). Satellite 40% = top 15 Mom20 scores (20-session action-neutral log return, same as research/baselines.py) among non-core; keep existing satellite names while non-core rank ≤ 21; skip rebalance if whole-portfolio turnover < 10%; freeze last 3.
	- C4: ResidualStrategy with SignalConfig(kind='h1', resid_window=120, beta_window=250, benchmark='0050') exactly as template.
	- Mom20: build_strategy from production/strategy.json as template.
- Consistency asserts: Mom20 W3 terminal_return == -0.10124596758199833 (1e-12); C4 W3 matches research/results/recent24_strategies/comparison.json Residual 120 value (1e-9). If either fails, stop and report — do not proceed.
- Per strategy×window record: terminal_return, max_drawdown, costs, turnover, warning_days, disqualified flag, verify_episode problem count, 0050 close-to-close return.
- Crash exposure: 5 worst daily NAV returns of Mom20 in W3; for each strategy, compounded return over those same 5 dates.
- Active Share: load data/momv2/etf_top10_20260924.csv with production.active_share.load_etf_holdings; for every session of every window and strategy, weights = end-of-day holdings value / nav_tradable(nav); call production.active_share.check(weights, etfs, required=all ETFs in file, trade_date) and take min(by_etf.values()) and its ETF. Report min AS, days < 15%, per strategy for W3 (gate) and W1/W2 (descriptive). Note status will be UNVERIFIED (top10 file); that is expected.
- Decision: apply G0–G3 and tie-break from PREREGISTRATION.md mechanically in code; write per-gate pass/fail with numbers.

Acceptance criteria:
- 15 episodes (5 strategies × 3 windows) complete; each verify_episode returns 0 problems or the problems are listed (counts in report must equal 15).
- Both consistency asserts pass.
- results.json and results.csv contain every field above; gates.json contains G0–G3 per candidate and the final verdict.
- AS rows: (strategies × sessions) count reported and equals 5 × 72; 0 rows = FAIL.
- No NaN/Inf in results.

Constraints and risks:
- Main-directory mode: uncommitted v4.3 code in production/ and gitignored production_runs/ data are required. Never modify or revert any existing file. Baseline `git status --porcelain` has 33 lines.
- Run with: PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/recent3_core_satellite/run.py
- Do not run long sweeps; the whole job is 15 short episodes.

Deliverables checklist:
- research/results/recent3_core_satellite/run.py
- research/results/recent3_core_satellite/results.json
- research/results/recent3_core_satellite/results.csv
- research/results/recent3_core_satellite/gates.json
- research/results/recent3_core_satellite/active_share.csv
- research/results/recent3_core_satellite/episodes/<strategy>_<window>/{ledger,trades,holdings,issues}.csv
- research/results/recent3_core_satellite/CODEX_REPORT.md

Execution mode: main directory (reason: needs uncommitted production/*.py and gitignored production_runs/ inputs).
Allowed write paths: research/results/recent3_core_satellite/ (except PREREGISTRATION.md and TASK_BRIEF.md). Python __pycache__ side effects are tolerated.
Snapshot: none; inputs are read-only by instruction. Claude re-hashes PREREGISTRATION.md after the run.
Report: research/results/recent3_core_satellite/CODEX_REPORT.md using the standard fields (Status, Changed files, Tests run and results, Items expected/processed/failed, Acceptance criteria, Plan deviations, Execution, Quota, Blockers).
Documentation preflight: no human-facing docs in this task; CODEX_REPORT.md is exempt.
Git: do not commit, push, branch, or merge; leave all changes uncommitted for Claude review.
