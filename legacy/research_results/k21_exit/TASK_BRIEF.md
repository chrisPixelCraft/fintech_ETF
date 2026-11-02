Goal: mechanically backtest K21Exit and K2Stop vs K2b exactly per PREREGISTRATION.md; do not interpret beyond the decision rule.
Scope / non-goals: new runner and outputs only. Do NOT edit strategies.py, PREREGISTRATION.md, competition/, production/, tests/, docs, README. No parameter changes, no extra variants.
Implementation approach:
- Write research/results/k21_exit/run.py modelled on research/results/conc_tournament/run.py (load_market args identical; 4 fork workers; PYTHONHASHSEED=0). Check SHA256 of strategies.py and PREREGISTRATION.md against FROZEN.sha256 at start.
- BASE policy/execution: production/strategy_k2_20.json via engine.policy_of and ExecutionConfig. Load K2Buffer as strategies.base.K2Buffer.
- Windows: build_episodes for dev, validation, holdout with offsets month_start, mid_month, start 2015-01-01, data_end 2026-10-22 (assert 276: 166/70/40), plus W1-W5 as in conc_tournament/run.py. Run K2b, K2.1, K2.S on all 281 = 843 runs.
- Pilot first: 3 windows x 3 strategies, assert each K2b terminal equals k3_weights/results.csv K2b within 1e-9 and verify problems 0.
- Before holdout windows, append one entry per strategy to research/holdout_access_log.jsonl in the same format as existing entries, append-only.
- Outputs in research/results/k21_exit: results.csv (columns as conc_tournament/results.csv plus exits, exit_retries, plan_status counts), paths.csv (strategy, window, day, nav incl. receivable, bench cumulative), exits.csv (strategy, window, date, sold, bought, whipsaw_5d), metrics.json, decision.json.
- Metrics: for each of sets all-236, dev, validation, holdout, recent, weak-start: count, mean/median delta vs 0050, win rate, P+5, P-5, tail edge, mean paired delta vs K2b, CI per preregistration, turnover, costs, warnings, DQ. Evaluate G1-G6 for K2.1 and K2.S using exact definitions.
- whipsaw_5d: for each exit pair sold[i]/bought[i], True if sold action-neutral return over the next 5 sessions > bought (unpaired names skipped; windows ending earlier use available sessions, flagged).
- Causality: on 3 windows (2019, 2023, W5) truncate or corrupt market and flows after decision day following tests/test_causality.py approach; K2.1 and K2.S decisions must be unchanged. Put this in research/results/k21_exit/check_causality.py and report items checked; 0 checked = fail.
Acceptance criteria:
- 843/843 complete; no NaN/Inf; K2b all 281 equal reference within 1e-9; verify problems 0 for K2b, reported for others; causality passes; G1-G6 in decision.json; snapshot untouched.
- Run: PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/chrishsieh/Documents/dev/fintech_ETF/.venv/bin/python research/results/k21_exit/run.py
Constraints and risks: data/ and production_runs/ are read-only symlinks to Claude's snapshot; never modify or recreate them. Failure handling: at most 2 different recovery approaches, then Blocked report.
Deliverables checklist: run.py; check_causality.py; results.csv; paths.csv; exits.csv; metrics.json; decision.json; CODEX_REPORT.md; holdout log append.
Execution mode: worktree + read-only data link; worktree is the working directory.
Allowed write paths: research/results/k21_exit/ (not strategies.py or PREREGISTRATION.md), research/holdout_access_log.jsonl (append only).
Snapshot: created by Claude; read-only; Codex must not create, overwrite, or recreate it.
Report: research/results/k21_exit/CODEX_REPORT.md (Status, Changed files, Tests, Items, Acceptance, Deviations, Execution, Quota, Blockers).
Documentation preflight: no human-facing Markdown in scope; CODEX_REPORT.md is a machine report.
Git: do not commit, push, branch, or merge; leave all changes uncommitted.
