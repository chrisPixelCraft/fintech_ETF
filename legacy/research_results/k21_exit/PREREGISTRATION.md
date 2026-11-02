# K2.1 exit rule: preregistration (exploratory rescue after a live weak start; frozen before the full run)

Context: live 10/27-10/30, NAV -4.22%, 0050 -1.82%, rank 406/437. Name K2.1 here means the exit rule below, not the abandoned sector-cap K2.1 of 2026-10-28.
Disclosure: rule design was motivated by 4 live days (6505 kept while trust sold). Two validation windows (2022_01, 2022_01m) were smoke-tested for crashes before freezing; no parameter was changed after seeing them.

## Hypothesis and alternatives
- H: selling core names when the 10-session trust signal turns non-positive (or on a 10% stop) improves return vs K2b.
- Prediction: mean paired delta >= +0.50pp, tail edge not worse.
- Strongest alternative: extra turnover and whipsaw cost more than the exits save.

## Candidates (strategies.py)
- Baseline K2b: production 22-name K2 (k3_weights K2Buffer); reference research/results/k3_weights/results.csv.
- K2.1 (primary): K21Exit. Core name with 10-session trust ratio <= 0 is replaced.
- K2.S (secondary): K2Stop. Core name down >= 10% (action-neutral) since entering the core is replaced.
- Shared: core only; replacement from names passing the K2 filter, then large-cap order with positive ratio; replaced names blocked until the next accepted re-screen; last-3 freeze; 22 names and weights unchanged; retry after planner HOLD.

## Evidence set and gates (K2.1 vs K2b, paired by window)
Primary set: 236 windows 2015-2024 (dev + validation). All gates must pass.
- G1 mean delta >= +0.50pp
- G2 95% CI lower bound > 0: bootstrap by calendar-quarter blocks of window start, 10000 draws, seed 0
- G3 tail edge (P(vs 0050 >= +5pp) - P(vs 0050 <= -5pp)) >= K2b tail edge - 1.0pp
- G4 validation 2022-2024 mean delta > 0
- G5 0 disqualifications, warning days <= K2b total, 0 verify problems
- G6 veto, 2025-2026 holdout (40 windows, already used, logged): mean delta >= -0.50pp

## Decision
- All pass: PASS, Claude may propose production change; user decides.
- Any fail: STOP, production stays K2b; no parameter tweaking or threshold search in this study.
- K2.S: same gates reported, secondary; it cannot be adopted unless K2.1 fails, and then only as a separate user decision noting multiplicity.

## Diagnostics only (never change the decision)
- Recent W1-W5 and weak-start subset (K2b day-4 gap vs 0050 in -3.5..-1.5pp): rest-of-window K2.1 and K2.S minus K2b.
- Exits per window, turnover, costs, whipsaw rate (replaced name outperforms its replacement over the following 5 sessions).
