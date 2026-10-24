Goal:
Mechanically execute the K3 weight-search protocol in PREREGISTRATION.md: DEV grid (16 weights) → mechanical selection → validation of the selected weight only → report-only periods. Strategy logic is Claude's (strategies.py); Codex writes only the runner and outputs and must not peek at validation/holdout for any weight other than the selected one and the two listed references.

Frozen files (SHA256; verify at start and end; mismatch = stop and report):
- PREREGISTRATION.md b22cde4c3008ff1f88825a1ffff9ee5d0ade2d9ce51759078ca20af1ddeeb286
- strategies.py 2f3b2adcdc615a7d0d109c64427f79a36294e6bdb8debe10e1d7c147fb9ae7d7
- ../k3_composite/strategies.py ff357195946691a22691dd604e2086d2d228c9d60faf2e30d5182182a0a0d85d
- ../conc_tournament/strategies.py c05cb770ff1eaec3ffec8f61d10cfc8e18dc693b7aecb5373402e0c6f6c31121
- ../conc_tournament/results.csv de312da76203b2b6e82bcc590bb593814d701a66af1a393caa11cea26444303a

Windows (same ids as ../conc_tournament/results.csv): DEV = historical windows with split 'dev' (start ≥ 2015-01-01; expect 166), VALIDATION = split 'validation' (expect 70), REPORT = split 'holdout' (40) + recent W1..W5. Assert counts.

Strategies:
- K3w(w) = strategies.K3Weighted(m.calendar, rules, m.close.columns, weights=w) for every w in strategies.GRID (16).
- K2b = strategies.K2Buffer(m.calendar, rules, m.close.columns) (diagnostic).
- K2 control values come from ../conc_tournament/results.csv (no rerun).

Phases (run in this order; write each phase's outputs before starting the next):
1. DEV: run all 16 K3w weights and K2b on DEV (17 × 166). Compute per weight: P(Δ50 ≥ +5pp), P(≥ +10pp), P(≤ −5pp), tail edge, mean/median Δ50, win rate, total/max warning days, DQ, turnover, costs. Eligibility and smoothed tail edge (mean over the point and its grid neighbours, neighbours = grid points whose every weight differs by ≤ 0.25 and at least one differs; the equal-weight point has no neighbours and uses its own value) exactly as PREREGISTRATION.md; select mechanically; record whether the selection lies on a boundary (any weight 0 or 1). Write dev_grid.csv and selection.json (selected weight, all smoothed values, tie-break trace). Paired bootstrap (10,000 draws, seed 0, by window) of tail-edge difference selected − K2 on DEV.
2. VALIDATION: run ONLY the selected K3w, the equal-weight K3w (1/3,1/3,1/3) and K2b on the 70 validation windows. Apply V1–V4 to the selected weight vs K2 control; write validation.csv and decision.json (ADOPT / REJECT with numbers). The equal-weight and K2b rows are report-only.
3. REPORT: run the selected K3w, equal-weight K3w and K2b on holdout 40 + recent 5; write report.csv with the same metrics plus K2/K1/Mom20/T2 controls from ../conc_tournament/results.csv.
- Holdout log: before phase 3, append one line per strategy run on holdout (selected K3w, equal-weight K3w, K2b) to research/holdout_access_log.jsonl in the existing shape (append-only).
- Template for market load, rules, policy, ExecutionConfig, run_episode, verify_episode, metrics, parallelism (≤ 8 workers) and a 3-window × 2-strategy pilot: ../k3_composite/run.py.
- Record warning dates/reasons per episode.

Acceptance criteria:
- Episode counts: phase 1 = 2,822; phase 2 = 210; phase 3 = 135 (if the selected weight equals the equal-weight point, run it once and note it; counts adjust accordingly). verify_episode problems recorded (expected 0).
- Frozen hashes unchanged at end; holdout log only appended (prior 96 lines unchanged); git status unchanged except new files here and the log append (baseline 47 lines).
- Outputs: run.py, dev_grid.csv, selection.json, validation.csv, decision.json, report.csv, results.csv (all episodes: strategy, weights, window_id, split, terminal_return, max_drawdown, delta_vs_0050, warning_days, warning_reasons, turnover, costs, verify_problem_count), metrics.json, CODEX_REPORT.md (Status, Changed files, asserts, Items expected/processed/failed, DEV grid table, selection, validation decision table, report table, Plan deviations, Execution incl. runtime/workers, Quota, Blockers). No NaN/Inf.

Constraints:
- Main-directory mode. Command: PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/k3_weights/run.py
- Allowed write paths: research/results/k3_weights/ except PREREGISTRATION.md, TASK_BRIEF.md, strategies.py; plus append-only research/holdout_access_log.jsonl.
- Do not edit strategy logic, grid, selection or decision rules. If something is ambiguous, take the literal reading and list it under Plan deviations.
- Documentation preflight: no human-facing docs; CODEX_REPORT.md exempt.
- Git: do not commit, push, branch, or merge.
