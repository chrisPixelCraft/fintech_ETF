Goal:
Let live runs reach READY_TO_SUBMIT using the automatically fetched MoneyDJ top-10 holdings when the heuristic Active Share has a wide margin, while keeping every other gate.

New live Active Share policy (frozen by the user):
- Live runs already auto-fetch MoneyDJ top-10 when --etf-holdings is not given (production/run_daily.py around lines 233–238). Keep that.
- Today live is blocked whenever the AS status is UNVERIFIED/INVALID or official_method_confirmed is false (production/run_daily.py around lines 255–264). Change this so live is ALSO allowed when all of the following hold for the final planned-book Active Share report (the one recorded in audit['active_share'], i.e. after lot rounding / fallback re-check):
	1. every required ETF (production/etf_holdings.required(), currently 30) is present: report.missing empty, report.invalid_data empty, report.stale empty (MAX_AGE_DAYS unchanged = 7);
	2. each required ETF has at least `allow_top_n` = 10 holdings (incomplete-because-only-top-10 is acceptable; fewer than 10 is not);
	3. report.minimum is finite and ≥ `heuristic_min` = 0.30.
- When the heuristic path allows submission, append reasons: `ACTIVE_SHARE_HEURISTIC_PASS:top10:min=<4dp>:closest=<etf>` and keep `ACTIVE_SHARE_METHOD_UNCONFIRMED` as an informational reason; set audit field `active_share_gate` = "HEURISTIC_TOP10_PASS". When it does not, keep the current blocking behaviour and reasons and set `active_share_gate` = "BLOCKED" with the failing condition(s) listed. If the official method is ever confirmed (official_method_confirmed true) the existing PASS path is unchanged ("OFFICIAL_PASS").
- If the MoneyDJ fetch fails for any required ETF → condition 1 fails → blocked (existing failures recorded).
- Put the two numbers in production/settings.json as {"live_active_share": {"heuristic_min": 0.30, "allow_top_n": 10, "source": "MoneyDJ top-10 auto-fetch"}} and read them from there.
- Offline runs stay SIMULATION_ONLY exactly as now. A user-supplied complete top-15 CSV via --etf-holdings must still work (it also passes conditions 1–3 if fresh and ≥ 0.30).
- Do NOT change production/active_share.py thresholds/semantics (OFFICIAL_MIN 0.15, INTERNAL_MIN 0.20, TOP 15, MAX_AGE_DAYS 7), engine repair logic, planner, validator, DataPack checks, strategy logic.

Tests (add to tests/test_production.py or a new tests/test_live_as_gate.py; use fixtures/monkeypatch, no network):
- live + complete top-10 for all required ETFs + min ≥ 0.30 → READY_TO_SUBMIT, D-Plan written, reasons contain HEURISTIC_PASS, active_share_gate = HEURISTIC_TOP10_PASS.
- min = 0.25 → blocked (EMERGENCY_REVIEW_REQUIRED), no D-Plan, gate BLOCKED with reason.
- one required ETF missing (fetch failure) → blocked.
- stale data (as_of 8 days before T) → blocked.
- an ETF with only 9 holdings → blocked.
- offline run unchanged (SIMULATION_ONLY).
- Existing tests keep passing; report full-suite count/time: `.venv/bin/python -m unittest discover -s tests -q`.

Docs (invoke human-readable-docs first; report Invoked/Unavailable): update docs/how_to_run.md, docs/rules_v43.md and docs/production_spec.md where they say live is always blocked until the official AS method / complete top-15 exist — describe the new heuristic rule (MoneyDJ top-10 auto-fetch, min ≥ 30%, otherwise blocked) concisely.

Allowed write paths: production/run_daily.py, production/settings.json, tests/ (new or edited test files only for this gate), docs/how_to_run.md, docs/rules_v43.md, docs/production_spec.md, research/results/conc_tournament/CODEX_LIVE_AS_REPORT.md.
Execution mode: main directory (repo is clean at HEAD 9f1cc4c except untracked tmp caches). Report: research/results/conc_tournament/CODEX_LIVE_AS_REPORT.md (Status, Changed files, Tests, Acceptance table, Plan deviations, Blockers).
Git: do not commit, push, branch, or merge.
