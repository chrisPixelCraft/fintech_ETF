# CODEX_REPORT

- Status: COMPLETE; frozen decision STOP.
- Changed files: research/results/defensive/{run.py,check_causality.py,results.csv,paths.csv,metrics.json,decision.json,CODEX_REPORT.md}; research/holdout_access_log.jsonl append only (+2 lines in the initial attempt; +0 in recovery; one entry per strategy before holdout).
- Tests: 6/6 pilot; 281/281 K2b reference matches <=1e-9 (max 0.0); 562 verification calls; problems by strategy {"K2b": 0, "D": 0}; no CSV NaN/Inf; strict JSON; frozen/protected/snapshot hashes and prior holdout log bytes unchanged.
- Items: expected/executed=562/562; complete=562; incomplete=0; paths=13488; causality decisions checked=21, cases=3, extra backtests=6.
- Acceptance: PASS; 562/562 executed, all 281 K2b match, finite outputs, verify counts reported, causality passed, G1-G7 present, snapshot untouched. Gate failures are study outcomes.
- Deviations: none to scope, strategy, parameters or gates. G2 uses paired quarter resampling, pooled windows, separately recomputed own worst ceil(10%) tails per draw, percentile 95% CI, 10000 draws, default_rng(0). G6 uses the preregistered primary 236; all-window diagnostics also reported. Weak-start membership uses all 281 K2b day-4 paths, with a 236-only subset; returns use NAV including receivable from day-4 close to actual last observed close, no padding; assumed switch cost subtracts .006. No external research or extra variants.
- Execution: `PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/chrishsieh/Documents/dev/fintech_ETF/.venv/bin/python research/results/defensive/run.py`; worktree root; 4 fork workers, ordered imap, serial pilot retained; elapsed 18.69s. No Git commands, commit, push, branch or merge; HEAD metadata read directly for the access log.
- Quota: unavailable; one frozen study; recovery approach 1: causality probes now halt before the first future decision, after comparing seven decisions and portfolio states with the original sessions_remaining. The initial 562 study runs completed before poisoned future validity triggered the frozen 22-name assertion outside the comparison horizon. Reran the identical 562-run study; 1124 study executions total, 562 unique final rows; prior two holdout log entries validated and reused. No strategy/gate changes; no further recovery used.
- Blockers: none.

| Gate | Result | Value / details |
|---|---|---|
| G1 | PASS | {"minimum": 0.02, "passed": true, "value": 0.06352706276999272} |
| G2 | PASS | {"ci95": {"blocks": 40, "draws": 10000, "lower": 0.038401147699186934, "method": "paired calendar-quarter blocks of start; replacement; pooled windows; own worst ceil(10% of resampled count); percentile 95%", "seed": 0, "upper": 0.08461603119788805}, "lower_exclusive": 0, "passed": true} |
| G3 | PASS | {"minimum": 0.01, "passed": true, "value": 0.03642258315598315} |
| G4 | FAIL | {"minimum": -0.01, "passed": false, "value": -0.017656260270286633} |
| G5 | PASS | {"minimum_exclusive": 0, "passed": true, "value": 0.06265305175875391} |
| G6 | PASS | {"disqualifications": 0, "maximum_each": 0, "passed": true, "verify_problems": 0, "warning_days": 0} |
| G7 | PASS | {"minimum": 0, "passed": true, "value": 0.06653051280701597} |

- Causality items, per-window verification counts, weak-start records and all diagnostics: metrics.json. Decision application: STOP; no automatic production change.
