# V4 Final Evidence — Stage 2

## Decision: `NO_V4_WINNER`

No `configs/v4_final.json` is frozen. Submission status stays **BLOCK_SUBMISSION**.

- No finalist beats the V3 baseline reliably.
  - Every paired P25 and P10 delta vs V3 is negative on validation + holdout.
  - Every sign test has p ≥ 0.75; every bootstrap CI spans zero.
- The three finalists are near-copies of each other.
  - Their episode returns correlate 0.93–0.97.
  - Pairwise median gaps are within ±0.5%, with about 15/15 win splits.
- The validation ranking reverses on the holdout.
  - Validation medians: D13 > A11 > M00.
  - Holdout medians: M00 > A11 > D13.
- The predeclared gates fail for reasons unrelated to alpha, so the protocol itself needs repair (see [Protocol defects](#protocol-defects-found-by-review)).
- The independent audit could not run on this checkout, so the evidence is **CANDIDATE**, not TRUSTED.

The decision would be the same under any reasonable post-hoc gate. The gates are not re-tuned after seeing holdout results, because that would itself be selection leakage.

## Evidence base and trust state

| Item | State | Evidence |
|---|---|---|
| Study run | CANDIDATE | • `outputs/v4/stage2_verified/`<br>• 108 candidates, 3 families |
| Stage ordering | Verified | • `events.jsonl` hash chain recomputed<br>• freeze precedes holdout |
| Input provenance | Verified | • all 69 hashes in `inputs.json` match HEAD |
| Independent audit | **Not run** | • manifest stores Linux absolute paths<br>• see `stage2_review/audit/verify.stderr` |
| Unit tests | 265/266 pass | • 1 macOS `/private/var` path artifact<br>• `stage2_review/audit/tests.log` |
| Causality | Verified | • 9/9 future-corruption runs bit-identical<br>• positive control detects t-1 change |

Splits (24-session episodes, NT$1B start, zero holdings):

- Development 2010–2018: 18 half-year anchors
- Validation 2019–2022: 8 anchors
- Historical holdout 2023–2024: 4 anchors
- Seasonal (late Oct→Nov 2010–2025), recent 2025+, and rolling windows are diagnostic only
  - Recent and rolling windows overlap; they are not independent samples

Review evidence lives in `outputs/v4/stage2_review/`. Table files named below are in that folder.

## Core results

Frozen finalists: `M00` (Momentum), `A11` (Adaptive), `D13` (Direct). Preferred family before holdout: Direct.

### Validation (n = 8)

| Candidate | Complete | Measured PASS | Median | Mean | P25 | P10 | Worst | Max MDD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A0_V3 | 7/8 | 6/8 | 1.76% | 2.43% | -1.35% | -2.93% | -4.40% | 7.1% |
| M00 | 8/8 | 8/8 | -1.64% | 0.85% | -2.40% | -3.84% | -7.02% | 10.6% |
| A11 | 8/8 | 7/8 | 1.57% | 1.88% | -2.85% | -4.07% | -6.34% | 8.6% |
| D13 | 8/8 | 8/8 | 2.35% | 1.93% | -0.28% | -4.61% | -9.48% | 12.9% |

### Historical holdout (n = 4)

| Candidate | Complete | Measured PASS | Median | Mean | P25 | Worst | Max MDD |
|---|---:|---:|---:|---:|---:|---:|---:|
| A0_V3 | 3/4 | 2/4 | -0.18% | -0.49% | -5.25% | -10.31% | 15.8% |
| M00 | 4/4 | 4/4 | 4.40% | 2.76% | 0.93% | -9.07% | 15.0% |
| A11 | 4/4 | 4/4 | 3.58% | 1.41% | -1.01% | -12.45% | 17.5% |
| D13 | 4/4 | 3/4 | 3.00% | 0.71% | -4.23% | -13.60% | 16.6% |

Returns count only complete episodes. Incomplete V3 episodes stay in the denominators. Source: `t1_core_splits.csv`.

### Paired vs V3, validation + holdout (12 episodes)

V3 has 2 incomplete episodes here: day-3 no-valid-plan disqualifications, held 100% cash. Results are shown under two treatments.

| Finalist | Treatment | Median Δ | P25 Δ | P10 Δ | Wins / losses | Sign p |
|---|---|---:|---:|---:|---:|---:|
| M00 | V3 DQ excluded | -1.08% | -3.18% | -3.73% | 4/6 | 0.75 |
| M00 | V3 DQ = 0% | -0.92% | -2.80% | -3.59% | 5/7 | 0.77 |
| A11 | V3 DQ excluded | -1.38% | -2.23% | -2.74% | 4/6 | 0.75 |
| A11 | V3 DQ = 0% | -0.39% | -2.17% | -2.34% | 6/6 | 1.00 |
| D13 | V3 DQ excluded | -0.67% | -3.55% | -5.58% | 5/5 | 1.00 |
| D13 | V3 DQ = 0% | +0.77% | -3.37% | -4.93% | 7/5 | 0.77 |

Treating V3 disqualifications as -100% inflates finalist means to about +17%. That measures V3 feasibility failures, not V4 skill. Source: `t2_paired.csv`.

## Answers to the 14 required questions

### 1. How much does Open → official-average execution change?

Stage 1 answered this for the frozen V3 signal (`reports/v4_execution_comparison.md`).

- On 4 complete paired windows, the mean change is **-0.30 pp**
  - Range: -5.65 to +2.38 pp
- Official average vs Yahoo Open per quote: +0.44% mean difference
  - 0.57 pp of that is a vendor/exchange price-basis difference
- Measured PASS: Open 3/7 vs official average 1/7

The sample measures sensitivity. It cannot re-grade every earlier V3 conclusion.

### 2. How does the Momentum baseline perform?

- M00 (pure 5/20/60, 22 names, 1 replacement, margin 0.05) was the development leader.
  - Development median 1.56%
- On validation it has the worst median of the four, at -1.64%.
- It has the best holdout median, at 4.40%, on only 4 episodes.
- It pays the most turnover among finalists.
  - 2.07× initial NAV on validation
  - A11 and D13 are about 1.0×

### 3. Does Adaptive really improve?

**No.** The evidence does not support it.

- A11 − M00 over all 30 core pairs: median −0.01%, 15/15 wins
  - Val + holdout: −0.26%, 5/7 wins
- Forecast score vs plain momentum score, both with confidence (`A6 − A3`): −0.25% median, 13/17 wins
- A11 ranks only 3rd of 36 in its own family on development

### 4. Does the Direct optimizer improve?

**Not reliably.**

- D13 − M00 over 30 core pairs: +0.19% median, 15/15 wins
  - Val + holdout: −0.11%, 5/7 wins
- D13 has the worst lower tail of the four finalists.
  - Development worst -16.95%, MDD 17.8%
- Its nearest neighbour D00 beats it on development by +0.65%.
  - D00 has a −1.17% median on validation
  - D13's validation lead does not carry over to its closest variant
- "Direct" is not fitted in this study: `fit_direct` is never called.
  - D13 is one of 36 predeclared coefficient vectors, `src/v4_search.py:62`
  - The ranking uses development utility = median − 1.0 × MDD, `scripts/v4_stage2_run.py:150`
  - This only partly tests the spec §13 question

### 5. Does confidence help?

**Not established.**

- A11 vs `adaptive_no_confidence`: +0.28% median over 30 pairs, 19/11 wins, sign p = 0.20
- Momentum + confidence (`A3 − A2`): +0.56% median, 16/14 wins, p = 0.86
- The effect is negative at the 1-day and 20-day forecast horizons.
- Dispersion and residual confidence are combined, so their separate ablation required by spec §8 is missing.

### 6. Does the regime module help?

**No. It is inert.**

- `A4 − A3`: 27 of 30 pairs are identical.
- `B2 − B1`: 23 of 30 pairs are identical.

### 7. Where is the optimal daily-rotation region?

**Zero rotation, or margin ≥ 0.10 (which behaves identically).**

| Momentum (full, h5), development | Median | Turnover | Cost (NT$) |
|---|---:|---:|---:|
| 0 replacements | 1.27% | 0.91 | 23.3M |
| 1 replacement | 0.87% | 2.00 | 80.0M |
| 2 replacements | 0.84% | 2.21 | 91.5M |
| 3 replacements | 1.05% | 2.21 | 92.2M |

- 1 vs 0 replacements (`A2 − A1`): −0.52% median over 30 pairs
  - −1.30% on validation + holdout
- In the Adaptive family, budgets 1–3 are identical to 0 at margin 0.05.
- About 3.5× lower cost at the same or better return

Source: `t4_rotation_dev.csv`.

### 8. Does the portfolio optimizer beat equal weight?

**No.**

| Optimizer vs equal | Median Δ | Other effect |
|---|---:|---|
| Continuous (`A5 − A4`) | +0.03% | 9/30 episodes incomplete |
| DE seed 0 / 1 / 2 vs A11 | −1.03% / −0.76% / −0.07% | 4–5 DQs per seed |
| Score-proportional | −0.2% to −0.3% | none |

The DE seeds agree with one another (23 of about 25 pairs identical), including on the same failures.

### 9. Which components are useless?

- Regime: inert
- Remaining-horizon target: 0.00% median
- Risk penalty under the equal optimizer: no effect
- Rotation above 0: hurts
- Continuous and DE optimizers: hurt feasibility
- Expert forecasting vs raw momentum: no gain

The only actionable component finding is **low or no rotation**. It cuts cost by more than half with no return loss.

### 10. Which results hold only in the recent regime?

- D13 beats V3 in all recent and rolling windows.
  - Recent median 4.17% vs V3 3.67%
- D13 trails V3 on holdout when V3 disqualifications are excluded.
- Recent and rolling windows overlap and are diagnostic only.
  - They were never used for selection

**Inference:** D13's apparent edge sits mainly in 2025+ windows. It is not a stable property.

### 11. How is the lower tail?

Every finalist has a worse lower tail than V3 on the complete episodes.

| Validation | P10 | Worst |
|---|---:|---:|
| A0_V3 | -2.93% | -4.40% |
| M00 | -3.84% | -7.02% |
| A11 | -4.07% | -6.34% |
| D13 | -4.61% | -9.48% |

D13's development worst is -16.95%, and its holdout worst is -13.60%.

### 12. What is the largest source of compliance failures?

**Odd lots created by July stock dividends.**

- A stock dividend leaves odd-lot legacy shares.
  - The planner returns `INFEASIBLE_V4: Odd-lot legacy holdings…`
  - The book then freezes for the rest of the episode
- D13's 13/18 development PASS comes from 5 July windows.
- A11's 7/8 validation PASS comes from one odd-lot day in 2019_07.
- `FAIL_ROUND_LOT` is the top failure token in Momentum and Adaptive. It is the second-largest in Direct, just behind `FAIL_WEIGHT_CAP`.

Pass/fail therefore mostly reflects whether the book held a stock-dividend payer. Master spec §9 says corporate-action issues must not wipe out the alpha statistics. Source: `t6_failure_tokens_by_family.csv`.

V3's own failures are different: day-3 no-valid-plan disqualifications (3 core episodes).

### 13. Is there a stable V4 candidate?

**No.**

- The paired lower tail vs V3 is negative for every finalist and every neighbour.
- Holdout reverses the validation ranking.
- The bootstrap probability that D13 ranks first on validation is only 0.68.
- Neighbourhoods are nearly empty.
  - M00 has 1 one-axis neighbour, D13 has 1, and A11 has 2
  - `score_family` is missing from the stability axes

### 14. Can a D-Plan be formally produced?

**No.** Two separate reasons:

- No research winner exists.
- Submission blockers remain independent of alpha:
  - odd-lot corporate-action handling freezes the book
  - the fixed 2026 universe is not historically valid
  - Active Share and settlement parity are not certified

## Protocol defects found by review

These defects do not change the decision. They must be fixed before any Stage 3 predeclaration.

| Severity | Defect | Evidence |
|---|---|---|
| Fatal (gate design) | • Paired gate needs V3 complete<br>• V3 is 7/8 and 3/4<br>• every finalist fails before comparison | `scripts/v4_stage2_report.py:62-77` |
| Major | • `sector_robustness=False` is hardcoded<br>• winner impossible by construction | `scripts/v4_stage2_report.py:294` |
| Major | • zero-tolerance P10 Δ on n = 8/4<br>• almost no power | P10 ≈ minimum at this n |
| Major | • one-at-a-time search anchored on `full`<br>• `pure` won | `src/v4_search.py:26-35` |
| Major | • 11 of 108 candidates are duplicates<br>• effective budgets are 33 / 28 / 36 | margin ≥ 0.10 ≡ 0 replacements |
| Major | • fixed 2026 universe back to 2010<br>• 37/150 names missing in 2010 | `src/v4_ledger.py:48` `ex_post_fixed_universe` |
| Major | • V3 was tuned on 2010–2026<br>• holdout previously viewed | earlier `config/24d_*study.json` |
| Minor | • 387 unexplained moves beyond the daily price limit<br>• e.g. 2412 capital reduction | `src/strategy_24d.py:97` |
| Minor | • V4 causality tests are weak<br>• uniform ×3 future scaling | `tests/test_v4_forecast.py:21` |

Survivorship bias inflates **absolute** returns for all families, including V3. It likely favours high-volatility, long-momentum tilts, so D13 (+0.32 on `vol20`, +0.34 on `R60`) is probably the most exposed. This is an inference; without delisted data it cannot be quantified.

## What was learned

- The V4 architecture adds no measurable value over simple momentum at this sample size.
  - Neither forecasting, confidence, regime, nor the optimizer
- Turnover control is the only robust lever.
- Feasibility, not alpha, is the binding constraint.
  - Odd-lot corporate actions dominate failures
- With 12 out-of-sample episodes, only edges above about 1% per episode are detectable.

## Recommended next step

The highest-value action is not another search. Fix the measurement layer first:

1. Handle corporate-action odd lots.
   - Classify them apart from strategy-caused odd lots
2. Store execution-manifest paths relative to the repo, then run the independent audit.
3. Repair the gates.
   - Pair only complete episodes
   - Report incomplete counts separately
   - Tail gate on each candidate's own P25/P10 with a predeclared tolerance
4. Add more episode anchors per year, including Oct/Nov starts.
   - This gains statistical power without reusing the holdout
5. Obtain a point-in-time universe, or declare the survivorship caveat in every claim.

Then run a new predeclared study: M00-like pure momentum with zero rotation vs V3. The 2023–2024 holdout is spent, so the confirmation must be prospective (live 2026 competition data) or use fresh anchors.

## Reproduction

```bash
# Review analysis (read-only over the sealed study output)
python3 outputs/v4/stage2_review/analysis.py

# Independent audit; currently fails on non-original machines (absolute raw_path)
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python scripts/v4_stage2_verify.py --output outputs/v4/stage2_verified --workers 8
```

`scripts/v4_stage2_report.py` was not run. It requires `verification.json` with status `PASS_INDEPENDENT_STAGE2_AUDIT`, which does not exist yet. The per-family, ablation, and validation reports it generates (`reports/v4_{momentum,adaptive,direct,ablation,validation}.md`) are therefore still pending.
