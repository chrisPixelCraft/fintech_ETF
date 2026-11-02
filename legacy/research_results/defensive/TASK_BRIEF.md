Goal: mechanically backtest LowVol (D) vs K2b exactly per PREREGISTRATION.md; do not interpret beyond the decision rule.
Scope / non-goals: new runner and outputs only. Do NOT edit strategies.py, PREREGISTRATION.md, competition/, production/, tests/, docs, README. No parameter changes or extra variants.
Implementation approach:
- Write research/results/defensive/run.py modelled on research/results/k21_exit/run.py (same load_market args, 4 fork workers, PYTHONHASHSEED=0). At start check SHA256 of strategies.py and PREREGISTRATION.md against FROZEN.sha256.
- BASE policy/execution: production/strategy_k2_20.json. K2b uses K2Buffer loaded as in research/results/k21_exit/strategies.py (`base.K2Buffer`); LowVol from strategies.py.
- Windows: dev, validation, holdout with offsets month_start, mid_month, start 2015-01-01, data_end 2026-10-22 (assert 276: 166/70/40), plus W1-W5 as in conc_tournament/run.py. Run K2b and D on all 281 = 562 runs. A disqualified or incomplete episode is recorded as it happened, never padded.
- Pilot first: 3 windows x 2 strategies; K2b terminal must equal research/results/k3_weights/results.csv K2b within 1e-9.
- Before holdout windows append one entry per strategy to research/holdout_access_log.jsonl in the existing format, append-only.
- Outputs in research/results/defensive: results.csv (columns of k21_exit/results.csv without exit columns, plus cash_ratio_min, cash_ratio_max), paths.csv (strategy, window, day, date, nav incl. receivable, bench_cumulative), metrics.json, decision.json. Evaluate G1-G7 with the exact definitions; report all diagnostics; for weak-start, membership from K2b day-4 path across all 281 windows and also report the 236-only subset.
- Causality: on 3 windows (2019, 2023, W5) corrupt market data and revenue panel after a decision day; D decisions through that day must be unchanged. Script research/results/defensive/check_causality.py; report items checked; 0 checked = fail.
Acceptance criteria: 562/562 executed; no NaN/Inf; K2b all 281 equal reference within 1e-9; verify counts reported; causality passes; decision.json has G1-G7; snapshot untouched.
Run: PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/chrishsieh/Documents/dev/fintech_ETF/.venv/bin/python research/results/defensive/run.py
Constraints and risks: data/ and production_runs/ are read-only symlinks to Claude's snapshot; never modify or recreate them. Failure handling: at most 2 different recovery approaches, then a Blocked report.
Deliverables checklist: run.py; check_causality.py; results.csv; paths.csv; metrics.json; decision.json; CODEX_REPORT.md; holdout log append.
Execution mode: worktree + read-only data link; worktree is the working directory.
Allowed write paths: research/results/defensive/ (not strategies.py or PREREGISTRATION.md), research/holdout_access_log.jsonl (append only).
Snapshot: created by Claude; read-only; Codex must not create, overwrite, or recreate it.
Report: research/results/defensive/CODEX_REPORT.md (Status, Changed files, Tests, Items, Acceptance, Deviations, Execution, Quota, Blockers).
Documentation preflight: no human-facing Markdown in scope; CODEX_REPORT.md is a machine report.
Git: do not commit, push, branch, or merge; leave all changes uncommitted.
