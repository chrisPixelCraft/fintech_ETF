Goal:
Change the production base strategy from K2 (20 names) to K2b = K2 with a 2-name buffer (22 names): 10 core names at 8.5% + 12 filler names at 10%/12 (≈0.8333%) = 95% invested. Everything else stays: selection/ranking, saved-target retry, re-screen rule, breach repair, last-3 freeze, leaderboard switch to T2 with staged transition, data gates, Active Share gates.

Reference implementation (behaviour must match): research/results/k3_weights/strategies.py class K2Buffer (= conc_tournament TrustRevConc with FILL_N=12, FILL_W=0.10/12).

Scope / allowed changes:
- production/tournament.py: K2 build must take core/filler counts and weights from the strategy config instead of hard-coded literals (currently `production/tournament.py:210`); ordered[:core_n] core, ordered[core_n:core_n+filler_n] fillers; the "needs 20 fully valid names" check becomes "needs core_n+filler_n". The held-tier threshold used anywhere for K2 must still classify fillers correctly (fillers ≈0.83% < 5%).
- production/strategy.json: K2 params filler_n 12, filler_weight 0.08333333333333333 (= 0.10/12); keep everything else. Update the description string.
- Keep a rollback copy of the current 20-name config as production/strategy_k2_20.json (byte-identical to the current production/strategy.json before edits).
- tests/test_tournament.py: update expectations that assume 20 names; add a test that K2 builds 22 names with weights {8.5% ×10, 0.10/12 ×12} summing to 0.95, and that the staged switch from a 22-name K2 book to T2 keeps every candidate within 20–30 names.
- docs/how_to_run.md and docs/production_spec.md: change the K2 weight/count description only (invoke human-readable-docs first; report Invoked/Unavailable).
- Do NOT modify competition/, research/ (except writing CODEX_K2B_REPORT.md here), production/active_share.py, production/state.py, production/validate.py, official_docs/, data/hybrid/, data/reference/, docs/strategy.md, README.md, docs/*.docx.

Acceptance criteria:
1. Full suite passes: `.venv/bin/python -m unittest discover -s tests -q` (report count/time).
2. Equivalence: production replay (tests.test_tournament.replay_window, rank None) on W4 (2026-08-13..09-15) and W5 (2026-09-16..10-22): terminal_return equals research/results/k3_weights/results.csv rows strategy=K2b, window_id W4 / W5 (0.028854… and −0.014617…, use exact stored floats, round_trip) within 1e-9; report NORMAL/HOLD day counts; all D-Plans validate; settlement warnings 0. Reference file SHA256 must be d43c02400f18d73799d8000a49a8af20098a7e5705f1ce4f880bf7811895fce9.
3. Forced-switch replay W5 with rank 1 from day_index 12: report per-day k, orders, held-name count; T2 names fully reached (report session); all D-Plans valid; warnings 0.
4. Offline `./run_daily.sh 2026-10-26 --offline` re-run: status, mode, orders, held names after plan (must be 22), D-Plan validity (move prior output aside via existing superseded handling).
5. git status: only allowed paths changed; tracked diffs outside the allowed set still match the starting patch; report before/after lists.

Constraints:
- Main-directory mode; the repo has the user's uncommitted work (Claude holds a snapshot). Minimal targeted edits; never revert unrelated hunks.
- If a requirement conflicts with planner/validator behaviour, stop and report; do not change competition/ or safety gates.

Report: research/results/k3_weights/CODEX_K2B_REPORT.md (Status, Changed files, Tests, Equivalence, Forced switch, Offline run, Acceptance table, Plan deviations, Execution, Quota, Blockers).
Git: do not commit, push, branch, or merge.
