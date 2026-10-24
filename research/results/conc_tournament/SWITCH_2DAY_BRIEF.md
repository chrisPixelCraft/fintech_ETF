Goal:
Change the frozen leaderboard switch from "rank <= 6 on one day" to "rank <= 6 on two consecutive trading days". Everything else in the switch (min day_index 12, sessions_remaining > 3, lock after switch, staged T2 transition) and all other production behaviour stays the same.

New rule (frozen by the user):
- The switch to T2 happens on trade date T only if all hold:
	- day_index(T) >= 12 and sessions_remaining(T) > 3 and mode is K2;
	- rank supplied for T (leaderboard published at 19:00 on T-1) is <= 6;
	- the rank recorded for the immediately preceding trade date (the session before T on the market calendar, i.e. inp.prev_date) is also <= 6. That earlier rank may come from a run before day_index 12 (users pass --rank every day).
- Rank for T missing → no switch, reason RANK_MISSING (as now).
- Rank for T <= 6 but the previous trade date has no recorded rank → no switch, reason RANK_HISTORY_MISSING.
- Rank for T <= 6 but previous rank > 6 → no switch, reason RANK_NOT_CONSECUTIVE.
- Ranks are still recorded in state['ranks'] keyed by trade date and persisted under the existing rules (with every produced D-Plan, including HOLD; offline runs only in their run folder).
- Add "consecutive_days": 2 to production/strategy.json params.switch and read it (do not hard-code 2). Keep min_day_index 12, max_rank 6, min_sessions_remaining_exclusive 3.

Scope / allowed changes:
- production/tournament.py (switch_state: needs the previous trade date; keep its other behaviour), production/run_daily.py and production/engine.py only as needed to pass the previous trade date through, production/strategy.json (add consecutive_days), tests/test_tournament.py (update/extend switch tests and the forced-switch replay helper so the forced replay supplies rank 1 from day_index 11 onward and therefore switches at day_index 12), docs/how_to_run.md, docs/strategy.md, docs/production_spec.md (switch rule wording only; invoke human-readable-docs first and report Invoked/Unavailable), research/results/conc_tournament/CODEX_SWITCH_2DAY_REPORT.md.
- Do not modify competition/, production/active_share.py, production/state.py, production/validate.py, strategy selection logic, the live Active Share gate, official_docs/, data/.

Acceptance criteria:
1. Unit tests cover: day 12 with rank 6 and previous rank 6 → switch; day 12 rank 6 previous rank 7 → no switch (RANK_NOT_CONSECUTIVE); day 12 rank 6 previous missing → no switch (RANK_HISTORY_MISSING); rank missing → RANK_MISSING; day 11 with both ranks <= 6 → no switch; sessions_remaining 3 → no switch; lock after switch with later rank 20; previous rank recorded on day 11 counts for day 12.
2. Full suite passes: `.venv/bin/python -m unittest discover -s tests -q` (report count/time).
3. Production replays unchanged where no rank is given: W4 and W5 with rank None still equal research/results/k3_weights/results.csv K2b rows exactly (0.028854071217284893, -0.014616507500586629).
4. Forced-switch replay W5 (rank 1 from day_index 11): switch occurs at day_index 12, staged transition completes, all D-Plans valid, settlement warnings 0; report per-day mode/k/orders.
5. Offline `./run_daily.sh 2026-10-27 --offline --rank 5` still produces a valid SIMULATION_ONLY D-Plan (day_index 0, no switch) and records the rank.
6. git status: only allowed paths changed.

Execution mode: main directory (repo clean at HEAD f4b59ee except untracked tmp caches).
Report: research/results/conc_tournament/CODEX_SWITCH_2DAY_REPORT.md (Status, Changed files, Tests, Replays, Offline run, Acceptance table, Plan deviations, Blockers).
Git: do not commit, push, branch, or merge.
