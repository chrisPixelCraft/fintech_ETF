Goal:
Mechanically backtest the K3 pre-registration (PREREGISTRATION.md): K3.0 and K3.1 over the same 276 historical + 5 recent windows as research/results/conc_tournament, compute tail metrics, apply the frozen gates against K2. Strategy logic is written by Claude in strategies.py; Codex writes only the runner and outputs.

Frozen files (SHA256; verify at start and end; mismatch = stop and report):
- PREREGISTRATION.md f22f894156ff740754bffbcf712b08dd5aabf8d6cfdad6bfb1429b3b600a06ab
- strategies.py ff357195946691a22691dd604e2086d2d228c9d60faf2e30d5182182a0a0d85d
- ../conc_tournament/strategies.py c05cb770ff1eaec3ffec8f61d10cfc8e18dc693b7aecb5373402e0c6f6c31121
- ../conc_tournament/results.csv de312da76203b2b6e82bcc590bb593814d701a66af1a393caa11cea26444303a

Scope / non-goals:
- In: K30Average(m.calendar, rules, m.close.columns) and K31ConfirmedMomentum(...) on 281 windows each (562 episodes). Controls K2, K1, T2, Mom20 and 0050 are reused from ../conc_tournament/results.csv (same window ids); do not rerun them except the diagnostic below.
- Diagnostic: rerun K1 (../conc_tournament/strategies.py MomConc) only on its 7 windows with warning_days > 0 in ../conc_tournament/results.csv; record each warning date and warning_reasons from the ledger; assert the rerun terminal_return equals the stored value within 1e-12.
- Out: do NOT edit strategies.py or any strategy logic/parameters/gates; no parameter search; no edits outside this directory except the append-only holdout log. If strategies.py raises, stop and report the traceback.

Implementation approach:
- Template: ../conc_tournament/run.py (same market load, rules, policy, ExecutionConfig, window construction and asserts, run_episode, verify_episode, metrics, blocks, parallelism ≤ 8 workers, pilot of 3 windows × 2 strategies first).
- Holdout log: append one line per new strategy (K3.0, K3.1) to research/holdout_access_log.jsonl in the existing shape; never modify existing lines.
- Record per episode warning_days and warning reasons (from the ledger) for K3.0/K3.1; record K3.1 'confirmed' counts at build (distribution).
- Metrics per strategy (K3.0, K3.1 plus the reused controls), separately for historical 276 and recent 5: mean/median Δ50, win rate, P(Δ50 ≥ +5pp), P(≥ +10pp), P(≥ +15pp), P(≤ −5pp), tail edge, P90 max drawdown, total/max warning days, disqualifications, mean turnover and costs; per block (2015–2018, 2019–2021, 2022–2024, 2025–2026): tail edge and mean Δ50.
- Gates G0–G4 and the selection rule exactly as PREREGISTRATION.md (K2 control values computed from the reused results), mechanically in code; list failing gates with numbers.

Acceptance criteria:
- 562/562 new episodes complete; verify_episode problem counts recorded (expected 0); 7 K1 diagnostic reruns match stored returns.
- Frozen hashes unchanged at end; holdout log +2 lines, prior 94 lines unchanged; git status unchanged except new files here and the log append (baseline 46 lines).
- Outputs: results.csv (one row per strategy × window for K3.0/K3.1 with the same columns as ../conc_tournament/results.csv plus warning_reasons), metrics.json, blocks.csv, gates.json, k1_warning_diagnostic.csv, per-episode ledgers only for the 10 recent episodes, CODEX_REPORT.md (Status, Changed files, Tests/asserts, Items expected/processed/failed, a metrics table including controls, a gate table, Plan deviations, Execution incl. runtime/workers, Quota, Blockers).
- No NaN/Inf.

Constraints:
- Main-directory mode. Command: PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python research/results/k3_composite/run.py
- Allowed write paths: research/results/k3_composite/ except PREREGISTRATION.md, TASK_BRIEF.md, strategies.py; plus append-only research/holdout_access_log.jsonl.
- Documentation preflight: no human-facing docs; CODEX_REPORT.md exempt.
- Git: do not commit, push, branch, or merge.
