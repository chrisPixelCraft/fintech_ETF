Goal:
Mechanically execute the KT protocol in PREREGISTRATION.md: DEV grid over alpha (0.25, 0.5, 0.75) → mechanical selection → validation of the selected alpha only → report-only periods for all alphas. Strategy logic is Claude's (strategies.py); Codex writes only the runner and outputs.

Frozen files (SHA256; verify at start and end; mismatch = stop and report):
- PREREGISTRATION.md 3a9b758cfc72d7f2383c2e1924a8b9866057818c38c6b06a2c00f2e939e264cd
- strategies.py 74c80550071d73d81fdf52be8e39a0e02441c58863fadcbb0aa06f1a070a1a4d
- ../conc_tournament/strategies.py c05cb770ff1eaec3ffec8f61d10cfc8e18dc693b7aecb5373402e0c6f6c31121
- ../k3_weights/results.csv d43c02400f18d73799d8000a49a8af20098a7e5705f1ce4f880bf7811895fce9

Windows (same ids as ../k3_weights/results.csv): DEV = split 'dev' (166), VALIDATION = split 'validation' (70), REPORT = split 'holdout' (40) + recent W1..W5. Assert counts and that ids/dates match.

Strategies: KT(alpha) = strategies.KTHybrid(m.calendar, rules, m.close.columns, alpha) for alpha in strategies.ALPHAS. Control K2b values come from ../k3_weights/results.csv (strategy K2b; no rerun). Fresh instance per episode.

Phases (in order; write each phase's outputs before the next):
1. DEV: run all 3 alphas on DEV (498 episodes). Metrics per alpha: P(Δ50 ≥ +5pp), P(≥ +10pp), P(≤ −5pp), tail edge, stability score = P(≥+5pp) − 2·P(≤−5pp), mean/median Δ50, win rate, total/max warning days, DQ, turnover, costs, P90 max drawdown. Apply eligibility and selection exactly as PREREGISTRATION.md; write dev_grid.csv and selection.json (with K2b DEV values for reference).
2. VALIDATION: run ONLY the selected alpha on the 70 validation windows; apply V1–V4 vs K2b control; write validation.csv and decision.json (ADOPT / REJECT with numbers).
3. REPORT: run all 3 alphas on holdout 40 + recent 5 (report-only, after decision.json exists); write report.csv including K2b/K2/T2/K3w_05 controls from ../k3_weights/results.csv and ../conc_tournament/results.csv; add per-window recent table (Δ50 for each alpha, K2b, T2) and the compounded return over Mom20's five worst W5 sessions (dates 2026-10-15, 2026-10-22, 2026-10-20, 2026-10-13, 2026-10-12) for each alpha and K2b.
- Holdout log: before phase 3, append one line per alpha to research/holdout_access_log.jsonl in the existing shape (append-only).
- Template for market load, rules, policy, ExecutionConfig, run_episode, verify_episode, metrics, parallelism (≤ 8 workers), pilot (3 windows × 1 alpha): ../k3_weights/run.py.
- Record warning dates/reasons per episode.

Acceptance criteria:
- Episode counts: phase 1 = 498; phase 2 = 70; phase 3 = 135. verify_episode problems recorded (expected 0).
- Frozen hashes unchanged at end; holdout log only appended (prior 99 lines unchanged); git status unchanged except new files here and the log append (baseline 49 lines).
- Outputs: run.py, dev_grid.csv, selection.json, validation.csv, decision.json, report.csv, recent_windows.csv, results.csv (all episodes), metrics.json, CODEX_REPORT.md (Status, Changed files, asserts, Items expected/processed/failed, DEV table, selection, validation decision table, report table, recent per-window table, crash-days table, Plan deviations, Execution, Quota, Blockers). No NaN/Inf.

Constraints:
- Main-directory mode. Command: PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/kt_hybrid/run.py
- Allowed write paths: research/results/kt_hybrid/ except PREREGISTRATION.md, TASK_BRIEF.md, strategies.py; plus append-only research/holdout_access_log.jsonl.
- Do not edit strategy logic, grid, selection or decision rules; ambiguities → literal reading, listed under Plan deviations. If strategies.py raises, stop and report.
- Documentation preflight: no human-facing docs; CODEX_REPORT.md exempt.
- Git: do not commit, push, branch, or merge.
