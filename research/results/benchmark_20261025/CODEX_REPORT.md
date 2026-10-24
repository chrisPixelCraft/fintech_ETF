# CODEX_REPORT

- Status: COMPLETE; all acceptance criteria passed.
- Changed files: output directory only: run.py, recent5.csv (110 rows), latest24.csv (22), holdout40.csv (640), holdout40_summary.csv (16), summary.json, validation.json, baseline.json, market_provenance.json, progress.json, execution.log, CODEX_REPORT.md, episodes/*/*/summary.json (585), and tmp/ runtime caches. Outside this directory: exactly 13 append-only lines in research/holdout_access_log.jsonl.
- Tests/asserts: 585 verify_episode calls, zero problems; 58 numeric consistency checks passed (13 W5 at 1e-9; Mom20 recent 5 + holdout 40 at 1e-12). Window dates/IDs, finite CSV values, unique strategy/window pairs, row counts, recomputed summary statistics, input hashes and unchanged baseline Git status all passed. See validation.json.
- Items expected/processed/failed: legacy episodes 585/585/0 (recent 65, holdout 520); reused new-strategy episodes 120; benchmark rows 45. Every recent strategy has 5 rows; every holdout strategy has 40. No fallback used.
- Plan deviations: current production/strategy.json now contains K2/T2. Loaded the exact frozen legacy base from strategy_retest/momentum/mom20/config.json and checked params/execution/planner against production/strategy_mom20.json. Initial attempt stopped on missing params.portfolio before completing any episode; corrected run passed all consistency checks. No strategy, parameter, scope or architecture changes.
- Execution: `PYTHONHASHSEED=0 OPENBLAS_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python research/results/benchmark_20261025/run.py`; exit 0; main-directory mode; 410.31s (6.84 min); up to 8 workers. AutoTS holdout 158.32s; LightGBM holdout 113.37s. Both below 60 minutes.
- Quota: usage information unavailable.
- Blockers: none. No commit, push, branch or merge performed.
