# CODEX_REPORT

- Status: COMPLETE_WITH_ACCEPTANCE_FAILURE; primary STOP; secondary STOP.
- Changed files: research/results/k21_exit/{run.py,check_causality.py,results.csv,paths.csv,exits.csv,metrics.json,decision.json,CODEX_REPORT.md}; research/holdout_access_log.jsonl appended by 3 lines.
- Tests: 9/9 pilot, 0 verify problems; 843/843 study verification calls; all 281 K2b returns match reference within 1e-9; max difference 0.0; verification counts {"K2b": 0, "K2.1": 0, "K2.S": 0}; finite/nonmissing CSVs and strict JSON; frozen, protected files, full linked snapshot and prior holdout log bytes unchanged.
- Items: study expected/processed/incomplete=843/843/1; complete=842; paths=20231; exit pairs=4582; causality cases=6, decisions checked=42, extra backtests=12.
- Acceptance: FAIL: frozen candidates were disqualified before completing all 24 sessions; 843/843 complete cannot be satisfied without changing prohibited strategy/engine rules. Other checks passed; G1-G6 are computed from unmodified engine outcomes.
- Deviations: no scope, strategy, parameter or gate changes. Literal conventions: 95% percentile CI, calendar-quarter blocks resampled with replacement and pooled window means, NumPy default_rng(0), 10000 draws per set/strategy; win means delta > 0. G5 uses the primary 236-window evidence set. Weak-start membership uses all 281 K2b day-4 paths; its return metrics measure day-4 close to window end. Exit rows represent logged intent, with planner HOLD retained; unpaired names skipped. Whipsaw uses sessions strictly after the exit date, caps at window end and flags truncation; missing action-neutral returns filled with zero. For disqualified windows whipsaw is capped at the final observed ledger day; weak-start rest returns use engine terminal returns, whose endpoint can be early. NAV paths include dividend receivable daily.
- Execution: `PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/chrishsieh/Documents/dev/fintech_ETF/.venv/bin/python research/results/k21_exit/run.py`; worktree root; 4 fork workers; ordered imap; serial pilot reused; elapsed 39.47s. No Git commands, commits, pushes, branches or merges. Read-only Git HEAD used for required holdout metadata; dirty=true because runner outputs are uncommitted.
- Quota: unavailable; no parameter search.
- Blockers: Acceptance requires 843 complete episodes, but frozen candidate disqualifications terminate episodes in the unchanged engine.
- Recovery: approach 1 only. Initial runner stopped after 49 collected results at K2.1 dev_2015_09 (23 sessions, 3 OVERDUE_CAP:3081.TWO warnings, verify 0); one isolated diagnostic confirmed the engine disqualification. Runner now records actual incomplete outcomes and paths, never pads them. The full frozen study was rerun; the 9-run pilot was repeated. Initial in-flight worker executions are not counted as final study rows. Prior failure report retained in metrics.json.

| Candidate | G1 | G2 | G3 | G4 | G5 | G6 | Decision |
|---|---|---|---|---|---|---|---|
| K2.1 | FAIL | FAIL | FAIL | FAIL | FAIL | PASS | STOP |
| K2.S | FAIL | FAIL | FAIL | FAIL | PASS | PASS | STOP |

- Gate values and thresholds: decision.json; metrics, paired CIs, verification detail and snapshot hashes: metrics.json.
- Causality: 2019, 2023, W5 × K2.1/K2.S; future prices/volume/action-neutral returns/validity/benchmark, trust flows and revenue panels corrupted after day 6 close; all decisions through day 7 and preceding NAVs unchanged; later ledgers changed. No zero-check pass.
- Decision application: any failed gate means STOP; production stays K2b. K2.S is secondary and requires K2.1 failure plus a separate user decision with multiplicity disclosure; no automatic adoption.
