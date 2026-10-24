Goal:
Replace the production strategy (frozen Mom20) with the tournament strategy: K2 TrustRevConc as the base, plus a frozen leaderboard switch to T2 LargeTrust. Keep every existing safety gate. Produce tests and an offline run for trade date 2026-10-26.

Frozen strategy specification (Claude-owned; do not change parameters or rules):
- Reference implementations (behaviour must match): research/results/conc_tournament/strategies.py (TrustRevConc = K2, LargeTrustCached = T2) and research/results/recent5_v3/strategies.py (trust_panel, revenue_high_panel, large_order, _flow_ratio, LargeTrust). Port the logic into production code; production must NOT import from research/results/.
- K2: 10 core names at 8.5% + 10 fillers at 1% (95% invested). Filter: 10-session trust net buy > 0, 3-month average revenue at its 12-month high (12 consecutive months required), latest YoY > 20%; rank by 10-session trust net buy / 20-session mean volume; core = filtered ranks 1–10, fillers = 11–20, then large-cap order (60-session mean close×volume, fully valid names). Revenue month m usable from day 11 of month m+1. Flows of session t usable from the decision whose T-1 = t.
- K2 trading (stateless form, equivalent to the reference): build when the book is empty; re-screen on the first decision where the newest usable revenue month at T-1 differs from that at the session before T-1, trading only if one-way turnover ≥ 10%; otherwise hold; if any held weight > cap − 0.5pp or cash breaks the band, re-target held names to their tier weights (≥5% weight = core 8.5%, else filler 1%); last 3 sessions of the contest: no trades.
- T2: 2330 at 23.5% + top 24 of the 40 largest (ex-2330) by 20-session trust net buy / 20-session mean volume, 71.5%/24 each. Built on the switch day, then held; same breach repair (2330 23.5%, others 71.5%/24); last-3 freeze.
- Leaderboard switch (frozen):
	- Input: `--rank N` = our rank within our group on the leaderboard published at 19:00 on T-1 (optional CLI arg; recorded in audit).
	- For trade date T with day_index ≥ 12 (13th contest session onward) and sessions_remaining > 3: if rank ≤ 6 and mode is K2 → switch to T2 on T (build T2 and trade).
	- Once switched, mode stays T2 for the rest of the contest (lock), whatever later ranks are.
	- Rank missing on a day where the switch could apply → stay in the current mode, add reason RANK_MISSING (still submittable).
	- Persist mode in <runs_dir>/state/tournament.json: {"mode": "K2"|"T2", "switched_on": date|null, "ranks": {date: rank}}; written only when the run's D-Plan is produced (live) or in the offline run folder for --offline (never alter live state from an offline run).

Scope / allowed changes:
- New: production/tournament.py (strategy logic + aux data loading/refresh), production/strategy_mom20.json (copy of the current production/strategy.json for rollback and existing momentum tests), tests/test_tournament.py.
- Modify: production/strategy.json (new frozen config: "strategy": "tournament", params for K2/T2/switch, plus a "portfolio" block with invested 0.95 and cap_scale used by fallbacks; keep execution and planner blocks), production/engine.py (dispatch momentum vs tournament; generalize momentum_of/fallback portfolio config; tournament has no Active Share repair: INVALID after check → raise → existing fallback), production/run_daily.py (--rank, tournament state, aux data refresh/degraded handling, audit fields mode/rank/switch), production/dplan.py (strategy-aware texts: K2/T2 logic sentence, per-stock trust ratio / filter status instead of momentum rank; keep schema-valid), research/run_experiment.py (register "tournament" so production.replay can run it, with rank None → K2 only), run_daily.sh (pass-through only; update usage comment), tests that read production/strategy.json and assume momentum (point them to strategy_mom20.json; keep their assertions), docs/how_to_run.md and docs/production_spec.md (technical: new flags, aux data, state file, switch rule).
- Aux data: cache dir data/production_aux/ (gitignored). Seed it once from research/results/recent5_v3/data/flows_20260924_20261022.csv and research/results/recent5_new/data/revenue_202609.csv (copy; do not modify sources). Base history: data/hybrid/flows.csv and data/hybrid/revenue.csv (read-only). Live refresh: fetch missing flow sessions up to T-1 (TWSE T86 + TPEx, using legacy/ml/hybrid/sources.py URL/parse functions) and published revenue months, appending to data/production_aux/. If T-1 flows or the expected newest revenue month are missing after refresh → degraded reason → existing fallback path. --offline: no network, cache only.
- Do NOT modify: competition/, research/baselines.py, research/results/** (except writing CODEX_PRODUCTION_REPORT.md here), production/active_share.py, production/state.py, production/validate.py, official_docs/, data/hybrid/, data/reference/. Do not weaken the live Active Share gate (official method unconfirmed still blocks READY in live mode).

Acceptance criteria:
1. Full test suite passes: `.venv/bin/python -m unittest discover -s tests -q` (report count/time); existing tests keep their assertions.
2. tests/test_tournament.py covers: switch rule (day_index 11 no switch; 12 with rank 6 switches; rank 7 no switch; rank missing → RANK_MISSING and no switch; locked after switch with later rank 20; no switch when sessions_remaining ≤ 3); K2/T2 causality (corrupt market/flows/revenue after T-1 → identical decision); revenue month usable only from day 11; flows ≤ T-1 only; K2 and T2 D-Plans validate with production/validate.py.
3. Backtest equivalence: run the production decision path day by day (production.replay or an equivalent harness) for the recent windows W4 (2026-08-13..09-15) and W5 (2026-09-16..10-22) with rank None; terminal_return must equal research/results/conc_tournament/results.csv K2 rows for W4/W5 within 1e-9, and every day must take the normal path. Also a forced-switch replay on W5 with rank 1 from day_index 12: report terminal return and verify the T2 build happened on day_index 12 and no K2 trades after.
4. Offline run: `./run_daily.sh 2026-10-26 --offline` completes; report status, mode, orders count, D-Plan validity, and any data-gate reasons (10/23 vendor rows have volume 0 — report what the pipeline does; do not patch data).
5. git status: only the allowed paths changed; record before/after lists.

Constraints and risks:
- Main-directory mode: production/, tests/, docs/ already contain the user's uncommitted v4.3 work. Never revert, reformat or rewrite unrelated hunks; make minimal targeted edits. Claude holds a snapshot (tar + patch) outside the repo.
- Keep code style of the surrounding modules (comment density, naming).
- If a spec point is ambiguous, pick the literal reading and list it under Plan deviations / interpretations; do not change parameters or the switch rule.

Deliverables checklist:
- production/tournament.py
- production/strategy.json (tournament), production/strategy_mom20.json
- production/engine.py, production/run_daily.py, production/dplan.py, research/run_experiment.py, run_daily.sh (comment)
- tests/test_tournament.py (+ minimal edits to existing tests)
- docs/how_to_run.md, docs/production_spec.md (technical sections)
- data/production_aux/ seeded
- production_runs/2026-10-26/ offline output
- research/results/conc_tournament/CODEX_PRODUCTION_REPORT.md

Execution mode: main directory.
Allowed write paths: as listed above only.
Report: research/results/conc_tournament/CODEX_PRODUCTION_REPORT.md with Status, Changed files, Tests run and results, Equivalence results, Offline run result, Acceptance criteria, Plan deviations / interpretations, Execution, Quota, Blockers.
Documentation preflight: before editing docs/how_to_run.md and docs/production_spec.md, invoke the installed human-readable-docs skill ($human-readable-docs) and report "Human-readable-docs skill: Invoked / Unavailable". Instruction for those docs: 幫我整理的更好閱讀 簡明、精準、一目了然的方式呈現給我
Git: do not commit, push, branch, or merge.
