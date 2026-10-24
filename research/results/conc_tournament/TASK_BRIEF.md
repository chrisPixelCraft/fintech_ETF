Goal:
Mechanically backtest the batch-4 pre-registration (PREREGISTRATION.md): 4 strategies over 276 historical 24-session windows plus the recent 5 windows, compute tail metrics, apply the frozen gates. Strategy logic is written by Claude in strategies.py; Codex writes only the runner and outputs.

Frozen files (SHA256; verify at start and end; mismatch = stop and report):
- PREREGISTRATION.md f7d825b4fe47fcfc4dee6a5015eceb1938d24664ec23b81f689fc2bade51c1e9
- strategies.py c05cb770ff1eaec3ffec8f61d10cfc8e18dc693b7aecb5373402e0c6f6c31121
- ../recent5_v3/strategies.py becd34b594fc15d07d3de1660d427bee7d997db4bf17ab83eab90705972815fe
- ../recent5_v3/results.csv 37c35ed79f6f5a308335dfdfa6fdc01485f64fd57b4e06f9444374df549c57dd
- ../recent5_new/results.csv 43f565f833fdc5745993ccdfbe50cb36ac91cbf07b9c88933fcd68db58c789b8

Scope / non-goals:
- In: strategies K1 MomConc(rules), K2 TrustRevConc(m.calendar, rules, m.close.columns), T2 LargeTrustCached(m.calendar, rules, m.close.columns), Mom20 (build_strategy from production/strategy.json); windows = 276 historical + 5 recent = 281 per strategy, 1,124 episodes; 0050 reference per window.
- Out: do NOT edit strategies.py or any strategy logic/parameters/gates; no parameter search; no edits outside this directory except the single holdout-log append below. If strategies.py raises, stop and report the traceback.

Implementation approach:
- Template: ../recent5_v3/run.py (same market load, rules, policy, ExecutionConfig, run_episode, verify_episode, result fields). Put this directory on sys.path to import strategies. Fresh strategy instance per episode (panels are cached inside strategies.py).
- Historical windows: [e for s in ('dev','validation','holdout') for e in competition.episodes.build_episodes(m.calendar, s, offsets=('month_start','mid_month'), start='2015-01-01', data_end='2026-10-22')]; assert exactly 276. Recent windows: identical to ../recent5_v3 W1..W5 (assert dates).
- 0050 per window: same benchmark_0050_return computation as the template.
- Pilot inside the run: first execute 3 historical windows × 4 strategies, assert verify problems == 0 and finite returns, print them, then continue with the full set. Parallelism allowed (≤ 8 processes) if results are deterministic; record worker count.
- Holdout log: before running holdout windows, append one JSON line per strategy to research/holdout_access_log.jsonl with keys utc, run_id (<strategy>__holdout_conc_tournament), config (absolute path of strategies.py), config_hash (its SHA256), git_commit, git_dirty, argv — same shape as existing lines. Never modify existing lines.
- Consistency asserts: T2 and Mom20 terminal_return on the 5 recent windows must equal ../recent5_v3/results.csv (strategy names T2, Mom20) within 1e-12. Failure: stop, report. Descriptive only: Mom20 historical vs research/results/final_test/episode_returns.csv where window ids match (report count and max abs diff; differences are expected from the repaired data snapshot).
- Metrics per strategy, separately for the 276 historical set and the recent 5: mean/median Δ50, win rate, P(Δ50 ≥ +5pp), P(Δ50 ≤ −5pp), tail edge, P(Δ50 ≥ +10pp), P90 max drawdown, total and max-per-window warning days, disqualifications, mean turnover/costs. Per block (2015–2018, 2019–2021, 2022–2024, 2025–2026 by window start): tail edge and mean Δ50. K2: distribution of passed_filter at build.
- Gates G0–G4 and tie-break exactly as PREREGISTRATION.md, mechanically in code; list failing gates with numbers.

Acceptance criteria:
- 1,124/1,124 episodes complete; verify_episode problem counts recorded (expected 0; any nonzero listed with ids).
- Consistency asserts pass; frozen hashes unchanged at end; holdout log has exactly 4 new lines appended and its prior 77 lines unchanged.
- Outputs: results.csv (one row per strategy×window with window_id, set, start, end, terminal_return, max_drawdown, costs, turnover, warning_days, disqualified, benchmark_0050_return, delta_vs_0050, verify_problem_count), metrics.json, blocks.csv, gates.json, CODEX_REPORT.md. Per-episode ledgers only for the 20 recent episodes (episodes/<strategy>_<window>/); for historical episodes do not write per-episode CSVs.
- No NaN/Inf in results.

Constraints and risks:
- Main-directory mode (needs uncommitted production/ code and gitignored production_runs/ inputs). Baseline `git status --porcelain` = 36 lines; do not modify or revert any existing file.
- Command: PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/conc_tournament/run.py

Deliverables checklist:
- research/results/conc_tournament/run.py
- research/results/conc_tournament/results.csv
- research/results/conc_tournament/metrics.json
- research/results/conc_tournament/blocks.csv
- research/results/conc_tournament/gates.json
- research/results/conc_tournament/episodes/ (20 recent dirs)
- research/results/conc_tournament/CODEX_REPORT.md
- research/holdout_access_log.jsonl (+4 lines)

Execution mode: main directory.
Allowed write paths: research/results/conc_tournament/ except PREREGISTRATION.md, TASK_BRIEF.md, strategies.py; plus append-only research/holdout_access_log.jsonl.
Snapshot: none; frozen-file hashes above are checked instead.
Report: research/results/conc_tournament/CODEX_REPORT.md with Status, Changed files, Tests run and results, Items expected/processed/failed, Acceptance criteria, Plan deviations / interpretations, Execution (incl. runtime, workers), Quota, Blockers, a metrics table (historical and recent) and a gate table.
Documentation preflight: no human-facing docs in this task; CODEX_REPORT.md is exempt.
Git: do not commit, push, branch, or merge.
