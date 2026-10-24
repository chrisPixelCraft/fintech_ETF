Goal:
Mechanically backtest the batch-3 pre-registration (PREREGISTRATION.md) and apply its frozen gates. Strategy logic is written by Claude in strategies.py; Codex writes only the runner and outputs.

Frozen files (SHA256; verify at start and end; mismatch = stop and report):
- PREREGISTRATION.md d4da1785d3873d8824dd26757298d5164943d1efbe7a7f7acd7219a7d1bb5065
- strategies.py becd34b594fc15d07d3de1660d427bee7d997db4bf17ab83eab90705972815fe
- data/flows_20260924_20261022.csv 14cd7ae3609489794d8f00ec7ee29bed35e084d81c7bea20b0f5e00694bb741f
- ../recent5_new/strategies.py 386900cbe56e6af8ee14ed35480805dbd81114c0ad9112e17233abfe8a631793
- ../recent5_new/results.csv 43f565f833fdc5745993ccdfbe50cb36ac91cbf07b9c88933fcd68db58c789b8

Scope / non-goals:
- In: 5 strategies × 5 windows = 25 episodes: T1 TrustRev, T2 LargeTrust, T3 LargeRev (strategies.py), C1 (CoreStrategy('C1') from ../recent3_core_satellite/run.py via importlib, unchanged), Mom20; 0050 reference; gates; AS; crash table.
- Out: do NOT edit strategies.py or any strategy logic/parameters/gates; no parameter search; no edits outside this directory. If strategies.py raises, stop and report the traceback.

Implementation approach:
- Template: ../recent5_new/run.py (same market load, rules, policy, ExecutionConfig, windows W1..W5 with the same date asserts, run_episode, verify_episode, AS method incl. fixed-snapshot fallback, crash fields = Mom20 W5 worst five sessions, result fields).
- Construct T1/T2/T3 as Class(m.calendar, rules, m.close.columns). Fresh instance per episode. Put this directory on sys.path to import strategies.
- Consistency asserts: C1 and Mom20 terminal_return for all 5 windows must equal ../recent5_new/results.csv within 1e-12. Failure: stop, report.
- Save strategy logs (build/rescreen/repair actions, T1 filter counts) per episode as JSON.
- Gates G0–G4 and tie-break exactly as PREREGISTRATION.md, mechanically in code; C1 is the comparison reference for G0 (warnings), G2, G3. List failing gates with numbers. Descriptive: mean Δ50 excluding W1; W1–W2 only Δ50; turnover; costs.

Acceptance criteria:
- 25/25 episodes complete; 25 verify_episode calls with problem counts listed.
- Consistency asserts pass; frozen hashes unchanged at end.
- results.csv / results.json (one row per strategy×window: terminal_return, max_drawdown, costs, turnover, warning_days, disqualified, benchmark_0050_return, delta_vs_0050, delta_vs_c1, min_as, crash_w5_compounded_return), gates.json, active_share.csv (5 × 120 = 600 rows; 0 rows = FAIL), episodes/<strategy>_<window>/{ledger,trades,holdings,issues}.csv + strategy_log.json.
- No NaN/Inf in results.

Constraints and risks:
- Main-directory mode (needs uncommitted production/ code and gitignored production_runs/ inputs). Baseline `git status --porcelain` = 35 lines; do not modify or revert any existing file.
- Command: PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/recent5_v3/run.py

Deliverables checklist:
- research/results/recent5_v3/run.py
- research/results/recent5_v3/results.csv
- research/results/recent5_v3/results.json
- research/results/recent5_v3/gates.json
- research/results/recent5_v3/active_share.csv
- research/results/recent5_v3/episodes/ (25 dirs)
- research/results/recent5_v3/CODEX_REPORT.md

Execution mode: main directory.
Allowed write paths: research/results/recent5_v3/ except PREREGISTRATION.md, TASK_BRIEF.md, strategies.py, fetch_flows_tail.py, data/.
Snapshot: none; frozen-file hashes above are checked instead.
Report: research/results/recent5_v3/CODEX_REPORT.md with Status, Changed files, Tests run and results, Items expected/processed/failed, Acceptance criteria, Plan deviations / interpretations, Execution, Quota, Blockers, and a gate table.
Documentation preflight: no human-facing docs in this task; CODEX_REPORT.md is exempt.
Git: do not commit, push, branch, or merge.
