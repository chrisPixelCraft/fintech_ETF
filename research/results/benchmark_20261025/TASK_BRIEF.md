Goal:
Produce the data (no plots) for three figures Claude will draw: (A) latest 24 sessions 2026-09-16..10-22, all strategies; (B) recent 5 windows W1..W5 benchmark, all strategies; (C) 2025–2026 holdout 40-window mean return for the README methods plus the new production strategy K2 and T2. Fixed strategies only; no tuning, no selection.

Strategy sets (all frozen as already implemented):
- Legacy 13 (the README "recent24" set): mom20, mom25, mom10, mom60, residual20_0050, residual20_ew, residual120_h1, tech_composite, macd_combined, tsmc_core_equal, tsmc_formal, autots_h5, lgbm_D_lr0.01. Implementations/configs exactly as in production_runs/preflight_20261026/strategy_retest/{momentum/run.py, technical/run_retest.py, models/run_models.py} (AutoTS keeps its original model-selection procedure; LGBM its fixed 116 trees / horizon 10 / retrain every 5 sessions; labels matured to D−1).
- New (already computed; reuse, do not rerun unless needed for a missing window): K2 and T2 from research/results/conc_tournament/results.csv (historical holdout windows and recent W1..W5); C1, C3, S1, S2 from research/results/recent5_new/results.csv; T1, T3 from research/results/recent5_v3/results.csv.
- 0050 reference per window: same benchmark_0050_return computation as research/results/conc_tournament/run.py.

Windows:
- Recent W1..W5: identical to research/results/recent5_v3 (assert dates; W5 = 2026-09-16..10-22).
- Holdout 40: competition.episodes.build_episodes(m.calendar, 'holdout', offsets=('month_start','mid_month'), data_end='2026-10-22'); assert 40 and that their ids/dates equal the holdout windows in research/results/conc_tournament/results.csv.
- Market/data: the same repaired inputs as the template runners (corrected_nominal_daily.parquet, official_combined.csv, calendar_v2.json). Holdout access: append one line per legacy strategy to research/holdout_access_log.jsonl in the existing shape (append-only).

Implementation approach:
- Write research/results/benchmark_20261025/run.py that generalizes the three template runners to a list of windows (do not edit the templates). Parallelism allowed (≤ 8 processes); deterministic seeds as in the templates.
- Run legacy 13 on W1..W5 (65 episodes) and on holdout 40 (520 episodes). Budget: if AutoTS or LGBM on holdout 40 exceeds 60 minutes wall time, stop that part and report; fall back to research/results/final_test/episode_returns.csv for those two (mark source column) rather than skipping silently.
- Consistency asserts (stop and report on failure): legacy 13 on W5 equal research/results/recent24_strategies/comparison.csv net_return within 1e-9; mom20 on W1..W5 equals research/results/recent5_v3/results.csv Mom20 within 1e-12; mom20 on holdout 40 equals conc_tournament Mom20 historical rows for those windows within 1e-12.
- verify_episode on every new episode; record problem counts.

Outputs (research/results/benchmark_20261025/):
- recent5.csv: one row per strategy × window for ALL strategies (legacy 13 + new K2, T2, C1, C3, S1, S2, T1, T3) and a 0050 row per window: strategy, label (Chinese label as in recent24 comparison.csv where it exists; for new ones use: K2 投信＋營收集中（正式）, T2 大型股＋投信, C1 台積電＋大型股, C3 低波動大型股, S1 營收動能, S2 崩跌防護, T1 投信＋營收, T3 大型股＋營收), group (legacy/new/benchmark), window, start, end, terminal_return, max_drawdown, costs_pct_initial_nav, delta_vs_0050, source.
- latest24.csv: W5 subset of recent5.csv.
- holdout40.csv: per strategy × window for legacy 13 + K2 + T2 + 0050.
- holdout40_summary.csv: per strategy: n, mean_return, median_return, mean_delta_vs_0050, win_rate_vs_0050, mean_delta_vs_mom20, source.
- summary.json: counts, runtimes, asserts, holdout-log lines, sources.
- CODEX_REPORT.md (Status, Changed files, Tests/asserts, Items expected/processed/failed, Plan deviations, Execution incl. runtime/workers, Quota, Blockers).

Acceptance criteria:
- 585 new legacy episodes (65 + 520) complete, or the documented AutoTS/LGBM fallback; counts reported; verify problems listed.
- All consistency asserts pass; no NaN/Inf; every strategy has 5 recent rows; legacy 13 + K2 + T2 have 40 holdout rows.
- No edits outside research/results/benchmark_20261025/ except the append-only holdout log lines.

Constraints:
- Main-directory mode. Baseline `git status --porcelain` must be unchanged except new files in the output dir and the holdout log append.
- Command: PYTHONHASHSEED=0 OPENBLAS_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python research/results/benchmark_20261025/run.py
- Documentation preflight: no human-facing docs; CODEX_REPORT.md exempt.
- Git: do not commit, push, branch, or merge.
