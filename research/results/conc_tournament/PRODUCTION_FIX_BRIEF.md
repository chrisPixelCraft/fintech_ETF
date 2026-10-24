Goal (bounded fix round 1 for PRODUCTION_BRIEF.md):
Resolve the two blockers in CODEX_PRODUCTION_REPORT.md. Everything else in PRODUCTION_BRIEF.md (scope, protected paths, gates, parameters) still applies.

Fix 1 — K2 stateful, exactly like the reference:
- Persist the K2 plan in the tournament state: {"k2_target": {symbol: weight}, "k2_month": "<newest usable revenue month at the build/rescreen decision>"} in addition to mode/switched_on/ranks.
- Decision logic must mirror research/results/conc_tournament/strategies.py TrustRevConc.decide line by line: build when no saved target (book empty) → save target; last 3 sessions → hold; if newest usable revenue month at T-1 != saved k2_month → set k2_month, compute target, and if one-way turnover vs current weights ≥ 10% save it as k2_target and trade it; else if breach (held weight > cap − 0.5pp or cash band) → trade the SAVED k2_target; else hold.
- The saved target is updated even when the planner later returns HOLD_FALLBACK (as the reference does). Therefore state must be written for every completed run that produces a D-Plan, including HOLD D-Plans. Offline runs write state only in their run folder.
- Remove the stateless held-tier repair for K2.

Fix 2 — staged switch to T2 (frozen design; do not relax the planner envelope):
- On the switch day build the T2 target A once (2330 23.5% + 24 names × 71.5%/24) and save it as "t2_target" with "t2_complete": false.
- Each decision while mode = T2 and t2_complete is false (and sessions_remaining > 3):
	- sell_list = held names not in A, ordered by current weight descending (ties by symbol).
	- buy_list = names in A not held, ordered by A weight descending, then by T2 trust ratio descending (2330 first).
	- For k = len(sell_list) down to 1, build candidate C_k: the first k names of sell_list → 0; other held names not in A keep their current weight; held names in A → their A weight; then add buy_list names in order at their A weight while the candidate total stays ≤ 0.95 + 1e-9. Skip C_k if its name count is outside 20–30. When k = len(sell_list) the candidate must equal A.
	- Use the first C_k (largest k) whose planner.plan status is OK or REPAIRED and whose planned-book Active Share check is not INVALID (same checks as the normal path). Record k and the step in the audit (reason SWITCH_STEP:k/len).
	- If no candidate is accepted → hold with reason SWITCH_STEP_INFEASIBLE (submittable HOLD D-Plan).
	- When the held name set equals A's names, set t2_complete = true; afterwards T2 behaves as specified before (hold; breach → re-target A; last-3 freeze).
- K2 behaviour before the switch is unchanged (Fix 1).

Acceptance criteria:
1. Full suite passes: `.venv/bin/python -m unittest discover -s tests -q` (report count/time). Update tests/test_tournament.py: K2 saved-target retry after a HOLD; state written on HOLD D-Plans; staged switch picks the largest accepted k, respects 20–30 names, never trades in the last 3 sessions, marks t2_complete when names match.
2. Equivalence: production replay W4 and W5 (rank None) terminal_return equals research/results/conc_tournament/results.csv K2 rows within 1e-9 for BOTH windows; report NORMAL/HOLD day counts (HOLD days must coincide with the reference ledger's HOLD days).
3. Forced-switch replay W5 with rank 1 from day_index 12: report per-day k, orders, mode; T2 names fully reached within the contest window (report the session it completed, expected ≤ 3 sessions); all D-Plans validate; settlement warnings 0; terminal return reported.
4. Offline `./run_daily.sh 2026-10-26 --offline` re-run: status, mode, orders, D-Plan validity (move the previous run output aside per existing superseded handling).
5. git status: only allowed paths changed; tracked diffs outside the allowed set still match the starting patch.

Execution mode / allowed paths / git: same as PRODUCTION_BRIEF.md. Report: append a "Fix round 1" section to research/results/conc_tournament/CODEX_PRODUCTION_REPORT.md (keep the original sections). If a requirement here conflicts with the planner or validator, stop and report — do not change competition/ or safety gates.
