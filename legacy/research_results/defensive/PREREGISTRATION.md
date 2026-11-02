# Defensive D: preregistration (exploratory; frozen before the full run)

Context: live 10/27-10/30, rank 406/437; user asks whether a low-loss version is worth adopting. Goal here is smaller losses, not a higher mean.
Disclosure: motivated by 4 live days where low-risk names and high cash held up. Before freezing only cash feasibility, warnings and verify on 4 windows were inspected (no returns); invested target set to 80% because 76% is repaired by the planner to the same cash level.

## Hypothesis and alternatives
- H: 22 lowest-volatility names at 80% target invested reduce bad-window losses materially at a limited cost in mean return vs K2b.
- Prediction: worst-decile return and drawdown improve, mean return falls by no more than 1pp.
- Strongest alternative: lower exposure simply gives up return; bad windows are market-wide and improve only as much as beta falls.

## Candidate
- D: research/results/defensive/strategies.py LowVol. Baseline K2b from research/results/k3_weights/results.csv. Also report 0050.

## Gates, D vs K2b, primary set 236 windows 2015-2024; all must pass
Definitions: CVaR10 = mean terminal return of a strategy's own worst 10% of windows (24 of 236).
- G1 CVaR10 difference (D - K2b) >= +2.0pp
- G2 95% CI lower bound of the G1 difference > 0: calendar-quarter blocks of window start, 10000 draws, seed 0
- G3 mean max drawdown difference (K2b - D) >= +1.0pp
- G4 mean terminal return difference (D - K2b) >= -1.0pp
- G5 validation 2022-2024 CVaR10 difference > 0 (worst 10% = 7 of 70)
- G6 0 disqualifications, 0 warning days, 0 verify problems
- G7 veto, 2025-2026 holdout (40 windows, already used, logged): CVaR10 difference (worst 4 of 40) >= 0

## Decision
- All pass: PASS, Claude may propose it as an option; user decides. It knowingly lowers upside.
- Any fail: STOP; no parameter search in this study.

## Diagnostics only (never change the decision)
- Weak-start windows (K2b day-4 gap vs 0050 in -3.5..-1.5pp): day 5-24 D - K2b, raw and after an assumed 0.6pp switch cost.
- Win rate vs 0050, P(>= +5pp vs 0050), P(<= -5pp vs 0050), cash ratio min/max, turnover, costs, recent W1-W5.
