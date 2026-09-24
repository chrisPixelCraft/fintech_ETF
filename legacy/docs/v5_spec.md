# V5 Study Spec — Champion-Inspired Families

This spec is **predeclared before any V5 result exists**. Changing a gate, split, or grid after results are seen requires a new version and a written reason.

Sources:

- Strategy ideas: `docs/champion.md`
- Hard competition rules: `docs/v4_master_spec.md` §3–5, `official_docs/D-Plan_撰寫指南.md`
- Execution: the Stage-1 simulator (`src/v4_execution.py`, `src/v4_ledger.py`, `src/v4_baseline.py`)

The competition runs 2026-10-26 → 2026-11-27 (24 sessions). The prospectus is due 10/21.

## 1. What V4 taught us (binding)

V4 ended `NO_V4_WINNER` (commit `72163500`, `reports/v4_final.md` in history). V5 must not repeat these defects:

| V4 defect | V5 rule |
|---|---|
| 18 / 8 / 4 episodes; ~1%/episode edges undetectable | Monthly anchors: 108 / 48 / 24 |
| July stock dividends leave odd lots and freeze the book | Planner handles corporate-action odd lots (§5) |
| Paired gate required V3 to be complete | Pair only episodes where both runs are complete |
| Zero-tolerance P10 of paired deltas | Tail gate on each candidate's own quantiles, with tolerance |
| Hardcoded `sector_robustness=False` | Sector analysis is a diagnostic, not a veto |
| One-at-a-time grid; 11 duplicate candidates | Small full-factorial grids; deduplicate by behaviour |
| Absolute `raw_path` in manifests | Every manifest path is repo-relative |
| Regime, confidence, and optimizer added nothing | Portfolio-layer ablations run only on top of family winners |
| Rotation hurt; zero rotation was best | Buffer-rule rotation, with "never rotate" in the grid |

## 2. Episodes

Each episode: NT$1B initial cash, zero holdings, 24 sessions, official-average execution, official-close sizing.

- The anchor is the first session on or after the 1st of each month.
- **development**: 2010-01 … 2018-12 (108)
- **validation**: 2019-01 … 2022-12 (48)
- **holdout_retrospective**: 2023-01 … 2024-12 (24)
  - This holdout is not pristine: V3 was tuned on it, and V4 viewed its Jan/Jul windows
  - It can reject a candidate; it cannot certify one
- **diagnostic_seasonal**: the session nearest Oct 26, 2010–2025 (16)
- **diagnostic_recent**: monthly anchors 2025-01 … latest complete 24-session window

Consecutive monthly episodes overlap by about 3 sessions. Uncertainty uses a block bootstrap by calendar quarter.

## 3. Shared interfaces

All code lives in the `v5_*` namespace (`src/`, `scripts/`, `config/`, `configs/`, `tests/`, `reports/`, `outputs/v5/`).

### 3.1 Features — `src/v5_features.py`

- `build_features(daily)` returns the long panel (`date`, `symbol`, …, `feature_ready`)
  - Restored from V4, where it passed future-corruption tests
- `history_at(panel, t)` returns the last cross-section dated strictly before `t`
- Family-specific features are computed inside each family module from this panel.

### 3.2 Signals — one module per family

```python
def score_table(panel, decision_dates, config) -> pd.DataFrame:
    """Long frame: decision_date, symbol, score (higher = better),
    optional expected_return, lower, upper, confidence, p_topk.
    Row for decision date t may use only panel rows with date < t
    (training labels must have matured by t-1)."""
```

- Scores are precomputed once for every study decision date and shared by all episodes. Walk-forward models refit at most every 5 sessions.
- Causality contract: `score_table(panel.loc[panel.date < T], dates<=T)` must equal `score_table(panel, …)` restricted to dates ≤ T, **bit-identical**. Every family's tests must check this with non-uniform future corruption (random per-symbol factors, shuffles, deletions).
- Determinism: fixed seeds; the same inputs give the same hash.

### 3.3 Construction — `src/v5_portfolio.py`

```python
def construct(scores_t, rows_prev, portfolio_state, config) -> pd.Series  # target_weight by symbol
```

Constraints before planning: long only, 20–30 names, normal ≤ 10%, 2330 ≤ 25%, cash target in [0, 25%).

### 3.4 Planner — `src/v5_planner.py`

```python
def plan(target_weight, prev_close, prev_nav, holdings, cash, config) -> Plan
# Plan: orders {symbol: signed shares, multiple of 1000}, target_shares, status, reason, audit
```

- `target_shares = floor(w × prev_nav / prev_close / 1000) × 1000` (official formula)
- Repairs holding count, caps, and cash; never produces negative cash.

### 3.5 Episode runner — `src/v5_episode.py`

- Wraps the sealed Stage-1 ledger the way the V4 adapter did (`git show 72163500:src/v4_stage2_episode.py`).
- Pipeline: strategy → weights → planner → orders → official execution → ledger.
- Every failure is preserved. A disqualified episode keeps only a forensic return.

## 4. Families

Every family uses the same construction default for its selection grid: `topn_equal`, `N ∈ {22, 26}`, and a buffer rotation rule. Each family's grid has **at most 12 distinct configurations**.

| Family | Source idea | Core mechanism |
|---|---|---|
| A `momentum` | baseline | • rank blend of R5/R10/R20/R60<br>• trend filter `price_ema20 > 0` |
| B `ensemble` | M6 AutoTS | • expert pool, horizon-aligned targets<br>• walk-forward expert weighting<br>• L/P/U from residual quantiles |
| C `rank` | M6 ATA | • daily rank buckets (quintiles)<br>• rank-frequency windows<br>• P(top quintile over h) |
| E `direct` | single-stage | • linear score fitted to trailing portfolio utility<br>• L2 penalty, walk-forward refit |

Family D (prediction + optimizer) and Family F (regime) are **portfolio-layer treatments**, tested in §6 on top of each family winner.

### A — momentum

- Grid: blend ∈ {short (R5,R10,R20), long (R20,R60)} × trend filter ∈ {off, on} × N ∈ {22, 26} × rotation ∈ {never, buffer 2N}
  - That is 16 combinations; drop the 4 with `short + filter off + buffer` to reach 12, as declared here

### B — ensemble

- Experts: momentum R5 / R20 / R60, reversal −R3, trend `price_ema20`, and a ridge model on standardized features.
- Targets use horizon h ∈ {10, 20}. `remaining` is an ablation.
- Walk-forward weighting: trailing rank-IC over the last `W` decision dates whose labels have matured.
  - W ∈ {60, 250}
  - Weights are bounded to [0, 0.5], then renormalized
- Interval: `lower`/`upper` are the point forecast plus residual quantiles q10/q90 over the trailing window.
- Point forecast `P = (U + L) / 2` (hinge).
- Confidence is the share of {L, P, U} and of experts that agree in sign.
- Score variants: `P` vs `P × confidence`.
- Grid: h (2) × W (2) × score (2) × N (1 = 22) + 4 rotation variants on the best-declared default = 12.

### C — rank

- Daily cross-sectional return rank into quintiles (on the D-1 and earlier universe).
- Features: the share of days in each quintile over windows {10, 20, 60}.
- Model: logistic regression (numpy IRLS, L2) predicting P(top quintile of h-day forward return), with h ∈ {10, 20}.
  - Trained walk-forward on a trailing window of matured labels, T ∈ {250, 750}
- Consensus: the average probability across the three window-variants.
  - `agreement` is the count of variants placing the name in their own top N
- Grid: h (2) × T (2) × score ∈ {mean p, mean p × agreement/3} (2) + 4 rotation / N variants = 12.

### E — direct

- Standardized feature vector: R5, R20, R60, `price_ema20`, `volume_ratio`, `vol20`, `momentum_quality`, `drawdown20`.
- Score is `x·w`. `w` maximizes trailing mean top-N soft-portfolio return − λ‖w‖² over T sessions, refit every 20 sessions.
  - Deterministic gradient ascent, fixed initialization
- Grid: T ∈ {250, 750} × λ ∈ {0.1, 1.0} × h ∈ {10, 20} + 4 rotation / N variants = 12.

## 5. Corporate-action odd lots

A stock dividend can leave a holding that is not a multiple of 1000. V4 declared this `INFEASIBLE` and froze the book.

- The planner must treat an odd remainder as a legacy position.
  - It may sell the board-lot part and keep the odd remainder
  - Or it holds the remainder, which still counts toward NAV and caps
- The rule adopted must cite `official_docs/` or `data/reference/competition_rules.json`.
- If the documents are silent, record the assumption as `ASSUMPTION_ODD_LOT` in every result.
  - Classify these days separately from strategy-caused failures.
- The simulator's accounting is not modified.

## 6. Protocol

1. **S0 — Infrastructure verification**
   - Unit tests pass
   - V3 baseline parity through the V5 runner on 3 episodes
   - Causality tests for every family
2. **S1 — Development**
   - Run every family grid on all 108 development episodes
   - Choose one winner per family by the §7 ranking
   - Record the one-axis neighbourhood of each winner
3. **S2 — Portfolio-layer ablations (development only)**, on each family winner:
   - construction ∈ {`topn_equal`, `score_bounded` (∝ score rank, caps respected), `risk_aware` (mean − λ·diag-variance, λ fixed at 1)}
   - regime overlay ∈ {off, on}
     - Breadth plus market volatility from the D-1 panel
     - `risk_off` raises cash to 10% and cuts the rotation budget to 0
   - A treatment is kept only if its development median Δ ≥ 0 and its P25 Δ ≥ −0.5pp against the plain winner
4. **Validation**
   - Run the 4 frozen family candidates and `A0_V3` on 48 episodes
5. **Freeze** — write `outputs/v5/study/freeze.json`, hash-chained in `events.jsonl`.
   - The preferred candidate is the best validation median that passes §7
6. **Holdout_retrospective and diagnostics** — run only after the freeze.

## 7. Ranking and gates

Ranking order: feasibility → median → P25 → P10 → worst / MDD → mean → turnover / cost → simplicity (A < E < C < B).

A candidate is `STABLE_CANDIDATE` only if **all** of the following hold:

- **Feasibility (validation + holdout)**
  - complete ≥ 95% of attempted
  - measured PASS ≥ 90%
  - `ASSUMPTION_ODD_LOT` days reported separately
- **Validation vs `A0_V3`**, paired on both-complete episodes:
  - median Δ ≥ 0
  - own P25 ≥ V3 P25 − 1.0pp
  - own P10 ≥ V3 P10 − 1.5pp
- **Complexity is earned**
  - A family other than A must have a paired median Δ ≥ 0 against the A winner on validation
  - Otherwise the A winner is preferred
- **Holdout does not reverse**
  - paired median Δ vs V3 ≥ −0.5pp
- **Stability**
  - At least 2 one-axis neighbours, each feasible
  - Each neighbour's development median must be within 1.0pp of the winner

Outcomes:

- If a candidate passes, write `configs/v5_final.json` with `submission_status = BLOCK_SUBMISSION` until the live D-Plan path is certified.
- Otherwise the result is `NO_V5_WINNER`.

The preferred candidate cannot be swapped after holdout. Recent and seasonal windows are never used to select a candidate.

## 8. Known limitations (declare in every report)

- **Survivorship bias:** the 2026-07-31 universe is applied back to 2010. Absolute returns are inflated; paired comparisons are less affected. Momentum tilts are likely the most exposed.
- **Unrecorded capital reductions:** 387 adjacent-session moves exceed the price limit (V4 audit). These distort returns for every family.
- **Market impact at NT$1B:** not modelled beyond official-average fills.

## 9. Outputs

- `outputs/v5/data/`: execution cache covering every episode session, with repo-relative manifest paths
- `outputs/v5/scores/<family>/<config_id>.parquet` and a hash manifest
- `outputs/v5/study/`: `events.jsonl`, `candidates.json`, per-episode results, `freeze.json`, `manifest.json`, and `verification.json`
- `reports/v5_final.md`: answers the 7 design questions in `docs/champion.md` §7, with evidence

## 10. Amendments (declared before any study result)

Both amendments were made on 2026-09-24, after integration checks and before any episode return was computed.

1. **The feature panel is restricted to the study session calendar** (`calendar_v2`, the same calendar the ledger uses).
   - Six off-calendar dates (for example the Saturday make-up day 2016-01-30) had vendor rows but zero `feature_ready` names.
   - `history_at` would take such a date as D-1 and leave every family with no scores.
2. **`development_2010_01` and `development_2010_02` are excluded for every candidate.**
   - Ensemble (h20) scores start 2010-01-13; Direct scores start 2010-02-26.
   - Keeping them would fail those families for warm-up reasons only.
   - The exclusion list lives in `config/v5_grids.json` (`excluded_episodes`), which is hash-bound into the study inputs.
   - Development therefore has 106 episodes.

Pre-result design repair to Family B: sign agreement for `confidence` is computed on cross-sectional excess forecasts (value minus the date's median). With raw forecasts, the market-level intercept made most confidence values 0.889, so the confidence ablation would have measured nothing.
