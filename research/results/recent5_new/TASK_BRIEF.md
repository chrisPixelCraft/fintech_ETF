Goal:
Mechanically backtest the pre-registered recent-5-window comparison (PREREGISTRATION.md) and apply its frozen gates. Strategy logic is already written by Claude in strategies.py; Codex writes only the runner and outputs.

Frozen files (SHA256, verify at start and end; any mismatch = stop and report):
- PREREGISTRATION.md 1659fba5e3b2234ca37c5f59a037eedadd7e414f4db76331843d559b3b9f1ff6
- strategies.py 386900cbe56e6af8ee14ed35480805dbd81114c0ad9112e17233abfe8a631793
- data/revenue_202609.csv 06c6ca14e1159dbe8fc3d56a8dfd31b24247a178ac6526a3359f962069ab6c35
- ../recent3_core_satellite/PREREGISTRATION.md c51f0e6a8295a97598b374899e04bba9fb7654ef5a130bc4703a61254e4348b8

Scope / non-goals:
- In: 7 strategies × 5 windows = 35 episodes: S1 RevenueMomentum, S2 CrashGuard (from strategies.py), Mom20, C1, C2, C3, C4 (reuse CoreStrategy and ResidualStrategy from ../recent3_core_satellite/run.py via importlib, unchanged); 0050 reference; gates; AS; crash table.
- Out: do NOT edit strategies.py or any strategy logic, parameters, gates. No parameter search. No edits outside this directory. If strategies.py raises an error, stop and report the traceback (do not patch it).

Implementation approach:
- Template: ../recent3_core_satellite/run.py (same market load, rules, policy, ExecutionConfig, run_episode, verify_episode, AS method incl. its documented fixed-snapshot fallback, crash fields, result fields).
- Construct S1 as RevenueMomentum(m.calendar, rules, base['params']['portfolio']); S2 as CrashGuard(rules). Fresh strategy instance per episode. Put this directory on sys.path to import strategies.
- Windows: W5 = last 24 sessions ending 2026-10-22 (assert first session 2026-09-16); W4..W1 each the 24 sessions immediately before. Assert W3/W4/W5 dates equal recent3 W1/W2/W3.
- Consistency asserts: for Mom20, C1–C4 on W3/W4/W5, terminal_return must equal ../recent3_core_satellite/results.csv rows (W1→W3, W2→W4, W3→W5) within 1e-12. Mom20 W5 == -0.10124596758199833. Any failure: stop, report.
- Save strategy logs (S1 eligibility/release age, S2 daily state) per episode as JSON.
- Apply G0–G4 and tie-break mechanically in code; for non-passers list failing gates with numbers; output descriptive W1–W2-only Δ50/ΔM.

Acceptance criteria:
- 35/35 episodes complete; 35 verify_episode calls with problem counts listed.
- All consistency asserts pass; frozen hashes unchanged at end.
- results.csv / results.json (one row per strategy×window with: terminal_return, max_drawdown, costs, turnover, warning_days, disqualified, benchmark_0050_return, delta_vs_0050, delta_vs_mom20, min_as, crash_w5_compounded_return), gates.json, active_share.csv (7 × 120 = 840 rows; 0 rows = FAIL), episodes/<strategy>_<window>/{ledger,trades,holdings,issues}.csv + strategy_log.json.
- No NaN/Inf in results.

Constraints and risks:
- Main-directory mode (needs uncommitted production/ code and gitignored production_runs/ inputs). Baseline `git status --porcelain` = 34 lines; do not modify or revert any existing file.
- Command: PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/recent5_new/run.py

Deliverables checklist:
- research/results/recent5_new/run.py
- research/results/recent5_new/results.csv
- research/results/recent5_new/results.json
- research/results/recent5_new/gates.json
- research/results/recent5_new/active_share.csv
- research/results/recent5_new/episodes/ (35 dirs)
- research/results/recent5_new/CODEX_REPORT.md

Execution mode: main directory.
Allowed write paths: research/results/recent5_new/ except PREREGISTRATION.md, TASK_BRIEF.md, strategies.py, fetch_revenue_202609.py, data/.
Snapshot: none; frozen-file hashes above are checked instead.
Report: research/results/recent5_new/CODEX_REPORT.md with Status, Changed files, Tests run and results, Items expected/processed/failed, Acceptance criteria, Plan deviations / interpretations, Execution, Quota, Blockers, and a gate table.
Documentation preflight: no human-facing docs in this task; CODEX_REPORT.md is exempt.
Git: do not commit, push, branch, or merge.
