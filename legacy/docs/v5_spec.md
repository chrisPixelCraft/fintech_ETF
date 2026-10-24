# V5 Study Spec — Champion-Inspired Families

**Predeclared before V5 results.** Post-result gate/split/grid changes require a new version and written reason. Contest: 2026-10-26 → 2026-11-27 (24 sessions); prospectus due 10/21.

Sources: ideas `docs/champion.md`; rules `docs/v4_master_spec.md` §3–5 and `official_docs/D-Plan_撰寫指南.md`; Stage-1 execution `src/v4_execution.py`, `src/v4_ledger.py`, `src/v4_baseline.py`.

## 1. Binding V4 lessons

V4: `NO_V4_WINNER` (commit `72163500`, historical `reports/v4_final.md`).

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

NT$1B cash, zero holdings, 24 sessions; official-average fills, official-close sizing. Monthly anchor: first session on/after the 1st.

- **development**: 2010-01 … 2018-12 (108).
- **validation**: 2019-01 … 2022-12 (48).
- **holdout_retrospective**: 2023-01 … 2024-12 (24). V3 tuning/V4 Jan/Jul exposure makes it non-pristine: may reject, cannot certify.
- **diagnostic_seasonal**: nearest session to Oct 26, 2010–2025 (16).
- **diagnostic_recent**: monthly, 2025-01 … latest complete 24-session window.

Monthly episodes overlap about 3 sessions; use calendar-quarter block bootstrap.

## 3. Shared interfaces

Namespace `v5_*`: `src/`, `scripts/`, `config/`, `configs/`, `tests/`, `reports/`, `outputs/v5/`.

### 3.1 Features — `src/v5_features.py`

`build_features(daily)` returns long panel (`date`, `symbol`, …, `feature_ready`), restored from V4’s future-corruption-tested implementation. `history_at(panel, t)` returns the last cross-section strictly before `t`. Compute family features inside family modules.

### 3.2 Signals — one module per family

```python
def score_table(panel, decision_dates, config) -> pd.DataFrame:
    """Long frame: decision_date, symbol, score (higher = better),
    optional expected_return, lower, upper, confidence, p_topk.
    Row for decision date t may use only panel rows with date < t
    (training labels must have matured by t-1)."""
```

Precompute scores once per decision date, shared across episodes; refit walk-forward models at most every 5 sessions. Require **bit-identical** `score_table(panel.loc[panel.date < T], dates<=T)` and `score_table(panel, …)` restricted to dates ≤ T. Test every family with random per-symbol future factors, shuffles and deletions. Fix seeds; identical inputs must yield identical hashes.

### 3.3 Construction — `src/v5_portfolio.py`

```python
def construct(scores_t, rows_prev, portfolio_state, config) -> pd.Series  # target_weight by symbol
```

Before planning: long only, 20–30 names, normal ≤ 10%, 2330 ≤ 25%, cash [0, 25%).

### 3.4 Planner — `src/v5_planner.py`

```python
def plan(target_weight, prev_close, prev_nav, holdings, cash, config) -> Plan
# Plan: orders {symbol: signed shares, multiple of 1000}, target_shares, status, reason, audit
```

`target_shares = floor(w × prev_nav / prev_close / 1000) × 1000`; repair count/caps/cash, never negative cash.

### 3.5 Runner — `src/v5_episode.py`

Wrap sealed Stage-1 ledger as V4 did (`git show 72163500:src/v4_stage2_episode.py`): strategy → weights → planner → orders → official execution → ledger. Preserve failures; disqualified returns are forensic only.

## 4. Families

Shared grid default: `topn_equal`, `N ∈ {22, 26}`, buffer rotation. Each family: **≤12 distinct configurations**.

| Family | Source idea | Core mechanism |
|---|---|---|
| A `momentum` | baseline | • rank blend of R5/R10/R20/R60<br>• trend filter `price_ema20 > 0` |
| B `ensemble` | M6 AutoTS | • expert pool, horizon-aligned targets<br>• walk-forward expert weighting<br>• L/P/U from residual quantiles |
| C `rank` | M6 ATA | • daily rank buckets (quintiles)<br>• rank-frequency windows<br>• P(top quintile over h) |
| E `direct` | single-stage | • linear score fitted to trailing portfolio utility<br>• L2 penalty, walk-forward refit |

D (prediction + optimizer) and F (regime) are portfolio-layer treatments on family winners (§6).

### A — momentum

Blend {short (R5,R10,R20), long (R20,R60)} × trend filter {off,on} × N {22, 26} × rotation {never,buffer 2N}: 16 combinations. Predeclared exclusion of 4 `short + filter off + buffer` leaves 12.

### B — ensemble

Experts: R5/R20/R60 momentum, −R3 reversal, `price_ema20` trend, ridge on standardized features. Horizon h ∈ {10, 20}; `remaining` is an ablation.

Weight by trailing rank-IC on `W` matured-label decision dates, W ∈ {60, 250}; bound [0, 0.5], then renormalize. `lower`/`upper` = point forecast + trailing residual q10/q90; hinge `P = (U + L) / 2`. Confidence: sign-agreement shares across {L,P,U} and experts. Scores: `P` or `P × confidence`.

Grid: h (2) × W (2) × score (2) × N (1 = 22), plus 4 rotation variants on best-declared default = 12.

### C — rank

Daily return quintiles on D-1/earlier universe; features are quintile day shares over {10, 20, 60}. Logistic regression (numpy IRLS, L2) predicts P(top quintile of h-day forward return), h ∈ {10, 20}; walk-forward on trailing matured labels T ∈ {250, 750}.

Consensus: three window-variants’ mean probability; `agreement` counts variants placing a name in their top N. Grid: h (2) × T (2) × score {mean p,mean p × agreement/3} (2), plus 4 rotation/N variants = 12.

### E — direct

Standardized R5,R20,R60,`price_ema20`,`volume_ratio`,`vol20`,`momentum_quality`,`drawdown20`; score `x·w`. Fit `w` to trailing mean top-N soft-portfolio return − λ‖w‖² over T sessions; deterministic gradient ascent, fixed initialization, refit every 20 sessions.

Grid: T {250, 750} × λ {0.1, 1.0} × h {10, 20}, plus 4 rotation/N variants = 12.

## 5. Corporate-action odd lots

Stock dividends can leave shares not divisible by 1000; V4 froze as `INFEASIBLE`. Treat odd remainders as legacy: sell board lots while retaining remainder, or hold it; retain NAV/cap accounting.

Cite the rule from `official_docs/` or `data/reference/competition_rules.json`. If silent, mark every result `ASSUMPTION_ODD_LOT`; separate these days from strategy failures. Do not change simulator accounting.

## 6. Protocol

1. **S0 — Infrastructure:** unit/causality tests; V3 parity through V5 runner on 3 episodes.
2. **S1 — Development:** all family grids on 108 episodes; one winner per family by §7; record one-axis neighbours.
3. **S2 — Development-only portfolio ablations**, per family winner:
	- Construction: `topn_equal`, `score_bounded` (score-rank proportional, capped), `risk_aware` (mean − λ·diag-variance, λ = 1).
	- Regime off/on: D-1 breadth/market volatility; `risk_off` raises cash to 10%, rotation budget to 0.
	- Keep only if median Δ ≥ 0 and P25 Δ ≥ −0.5pp vs plain winner.
4. **Validation:** 4 frozen candidates plus `A0_V3`, 48 episodes.
5. **Freeze:** `outputs/v5/study/freeze.json`, hash-chained in `events.jsonl`; prefer highest validation median passing §7.
6. **Holdout_retrospective and diagnostics:** after freeze only.

## 7. Ranking and gates

Rank: feasibility → median → P25 → P10 → worst/MDD → mean → turnover/cost → simplicity (A < E < C < B).

`STABLE_CANDIDATE` requires all:

- Validation + holdout: complete ≥ 95%, measured PASS ≥ 90%; report `ASSUMPTION_ODD_LOT` days separately.
- Validation vs `A0_V3`, paired both-complete: median Δ ≥ 0; own P25 ≥ V3 P25 − 1.0pp; own P10 ≥ V3 P10 − 1.5pp.
- Complexity: non-A validation paired median Δ vs A winner ≥ 0; otherwise prefer A.
- Holdout paired median Δ vs V3 ≥ −0.5pp.
- ≥2 feasible one-axis neighbours, each development median within 1.0pp of winner.

If passed, write `configs/v5_final.json`; keep `submission_status = BLOCK_SUBMISSION` until live D-Plan certification. Otherwise `NO_V5_WINNER`. No post-holdout candidate swaps; recent/seasonal windows never select candidates.

## 8. Limitations — declare in every report

- **Survivorship:** 2026-07-31 universe back to 2010 inflates absolute returns; paired comparisons less affected, momentum likely most exposed.
- **Unrecorded capital reductions:** V4 audit’s 387 adjacent-session moves exceed price limits, distorting every family.
- **NT$1B market impact:** unmodelled beyond official-average fills.

## 9. Outputs

- `outputs/v5/data/`: all episode sessions’ execution cache; repo-relative manifest paths.
- `outputs/v5/scores/<family>/<config_id>.parquet` plus hash manifest.
- `outputs/v5/study/`: `events.jsonl`, `candidates.json`, per-episode results, `freeze.json`, `manifest.json`, `verification.json`.
- `reports/v5_final.md`: evidence-backed answers to 7 questions in `docs/champion.md` §7.

## 10. Pre-result amendments

Declared 2026-09-24 after integration checks, before episode returns:

1. Restrict feature panel to study session calendar (`calendar_v2`, same as ledger). Six off-calendar dates, e.g. Saturday make-up 2016-01-30, had vendor rows but zero `feature_ready` names; `history_at` would pick them as D-1, leaving all families without scores.
2. Exclude `development_2010_01` / `development_2010_02` for all candidates. Ensemble h20 starts 2010-01-13; Direct starts 2010-02-26, so keeping these causes warm-up-only failures. Hash-bound `config/v5_grids.json` (`excluded_episodes`) records exclusions; development now 106 episodes.

Family B pre-result repair: `confidence` sign agreement uses cross-sectional excess forecasts (value − date median). Raw forecasts’ market intercept made most confidence 0.889, rendering the ablation uninformative.
