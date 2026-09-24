# Champion Strategies Reference

> Purpose: provide a compact research reference for designing the next ETF/short-horizon competition strategy.  
> This document summarizes successful approaches from M6 Financial Forecasting Competition and ETF Global Portfolio Challenge, with emphasis on ideas that may transfer to a 24-trading-day, long-only, daily-decision competition.

---

# 1. M6 Investment Winner — Colin Catlin / AutoTS

## Competition setting

M6 used 100 real assets, including stocks and ETFs, with portfolio decisions updated every four weeks.

Colin Catlin won the **Investment Decision** category using AutoTS.

## Core pipeline

```text
Historical prices
↓
Recent/relevant history
↓
Aggregate daily data to competition horizon
↓
Generate many forecasting candidates
↓
Cross-validation
↓
Genetic search
↓
Model / ensemble selection
↓
Forecast next competition period
↓
Point / Upper / Lower forecast
↓
Expected return + uncertainty
↓
Cross-sectional normalization
↓
Portfolio weights
```

## Key idea 1 — Align forecast horizon with competition horizon

The model does not need to predict every intermediate daily path if the competition ultimately evaluates performance over a fixed short horizon.

General principle:

```text
Competition objective
↓
Choose matching prediction horizon
```

For a ~24-trading-day competition, useful targets may include:

```text
1D
5D
10D
20D
remaining-competition return
```

rather than only next-day return.

---

## Key idea 2 — Predict a distribution, not only one number

AutoTS produced:

```text
Lower forecast
Point forecast
Upper forecast
```

Conceptually:

\[
L_i,\quad P_i,\quad U_i
\]

The official competition approach used a hinge-style forecast based on the interval:

\[
\hat P_i = \frac{U_i + L_i}{2}
\]

Then convert the forecasted price into expected return.

Key lesson:

> Signal strength and signal confidence are different quantities.

A strategy can use:

\[
Alpha_i = ExpectedReturn_i \times Confidence_i
\]

instead of using expected return alone.

---

## Key idea 3 — Agreement / confidence

A strong case:

```text
Lower > 0
Point > 0
Upper > 0
```

All forecasts agree on the direction.

A weaker case:

```text
Lower < 0
Point > 0
Upper > 0
```

The expected direction is positive, but uncertainty is high.

General principle:

```text
Model agreement ↑
→ conviction ↑

Model disagreement ↑
→ confidence ↓
```

This can be implemented through:

- ensemble dispersion
- prediction intervals
- sign agreement
- historical residual distributions
- calibrated confidence

---

## Key idea 4 — Adaptive model selection

There was no assumption that one forecasting model would always dominate.

The process was closer to:

```text
Model pool
+
preprocessing
+
hyperparameters
+
ensembles
↓
cross-validation
↓
automatic selection
```

Different market periods may favor different models.

Important implication:

> Do not assume one fixed EMA, momentum horizon, or ML model should remain optimal across every regime.

---

## Key idea 5 — Recent data may matter more than very old data

The approach emphasized relatively recent market history rather than assuming all historical data is equally useful.

Reason:

```text
old market regime
≠
current market regime
```

Long history can still help with robustness testing, but model fitting may benefit from recency weighting or rolling windows.

---

## Key idea 6 — More features are not automatically better

External macro variables were tested, but extra features were not kept when cross-validation did not show added value.

General principle:

> Add features only when they improve out-of-sample evidence.

Not:

```text
more data
=
better model
```

---

## Key idea 7 — Avoid discretionary overrides

Manual overrides can destroy a systematically validated edge.

Preferred structure:

```text
Human:
design system
define constraints
verify

Model:
execute frozen decision logic
```

---

# 2. M6 Investment #2 — ATA Method

This approach was structurally very different from the AutoTS winner.

Instead of predicting exact future returns, it focused heavily on **relative ranking probabilities**.

## Step 1 — Daily cross-sectional ranking

For every day, rank all assets by daily return.

Example with 100 assets:

```text
Rank 1–20
Rank 21–40
Rank 41–60
Rank 61–80
Rank 81–100
```

---

## Step 2 — Build recent rank-frequency features

For each asset, count how often it appears in each rank bucket over a recent rolling window.

Example:

```text
Top 20:       5 times
21–40:        8 times
41–60:        2 times
61–80:        1 time
Bottom 20:    4 times
```

This asks:

> How consistently has this asset been a relative market leader?

rather than only:

> How much has this asset risen?

---

## Step 3 — Forecast future rank distribution

Forecast the future probability of each rank bucket.

Conceptually:

\[
P(Rank_1), P(Rank_2), ..., P(Rank_5)
\]

This converts the problem into:

> Which assets are most likely to remain among the strongest relative performers?

---

## Step 4 — Multi-model consensus

Different ATA variants were compared.

Assets favored by multiple variants received stronger conviction.

This shares an important principle with AutoTS:

```text
single-model prediction
<
multi-model agreement
```

---

## Step 5 — Portfolio construction

The transferable part is:

```text
forecast relative-rank distribution
↓
find consistently top-ranked assets
↓
long-only selection
```

The original M6 setup allowed short positions, but that part does not transfer to a long-only competition.

---

# 3. M6 High-Ranking Method — Ai / Liu / Lin

This method is useful mainly because it separates two problems that are often incorrectly merged.

## Problem A — Prediction

Estimate:

```text
expected opportunity
relative return
rank
probability
```

## Problem B — Portfolio construction

Decide:

```text
which names to hold
how many names
how much weight per name
how much turnover is worth paying for
```

Pipeline:

```text
Features
↓
Prediction model
↓
Expected opportunity
↓
Portfolio optimizer
↓
Final weights
```

## Prediction side

Their work used ideas such as:

- robust feature selection
- denoising autoencoder
- neural models
- return-ranking prediction

## Portfolio side

Portfolio weights were optimized separately using a Differential Evolution style search.

Core lesson:

> Stock selection and position sizing are separate optimization problems.

A forecast score should not automatically imply equal-weight allocation.

---

# 4. Single-Stage Portfolio Optimization

This is not an M6 winning submission, but a later research direction built on M6 data.

It questions the standard pipeline:

```text
Market data
↓
Return forecast
↓
Portfolio optimization
```

and instead tests:

```text
Market data
↓
Direct portfolio optimization
↓
Weights
```

Possible formulations include:

- penalized regression
- constrained direct weight optimization
- AutoML over portfolio objectives
- direct utility maximization

Important research question:

> Is accurate individual-stock return forecasting actually necessary for a good short-horizon portfolio?

This should be treated as an independent experimental branch rather than assumed superior.

---

# 5. ETF Global Portfolio Challenge — Short-Horizon Regime Lessons

ETF Global competitions are useful because the horizon is short and participants can rebalance during the contest.

Unlike M6, complete winning algorithms are generally not published, so the strongest evidence comes from winner portfolio behavior.

## Common winner pattern

Short-horizon winners often benefited from correctly identifying a dominant market regime or theme.

Examples included concentration in:

- volatility products
- semiconductor bear products
- energy
- commodities
- emerging markets

The exact instruments often do **not** transfer to other competitions.

The transferable lesson is:

```text
detect dominant regime
↓
identify leading theme / sector
↓
tilt exposure
↓
update when regime changes
```

rather than:

```text
all sectors
always equally weighted
```

---

# 6. Shared Patterns Across Successful Methods

| Pattern | AutoTS Winner | ATA #2 | Ai / Liu / Lin | ETF Global |
|---|---:|---:|---:|---:|
| Competition-horizon alignment | ✅ | ✅ | ✅ | ✅ |
| Cross-sectional / relative information | Partial | ✅ | ✅ | ✅ |
| Multi-model / consensus | ✅ | ✅ | Partial | Unknown |
| Explicit uncertainty / confidence | ✅ | Probability-based | Partial | Unknown |
| Adaptive model behavior | ✅ | Model variants | Partial | Regime-driven |
| Prediction separated from allocation | Simple | Simple | ✅ | Often discretionary |
| Regime / theme awareness | Implicit | Implicit | Partial | ✅ |
| Risk-aware allocation | Partial | Diversification | ✅ | Mixed |

---

# 7. Most Important Design Questions for the Next Strategy

Before designing the next version, answer these experimentally.

## 1. What should the prediction target be?

Candidates:

```text
next-day return
5D return
10D return
20D return
remaining-competition return
```

Do not assume next-day prediction is the correct objective.

---

## 2. Exact return or relative rank?

Compare:

\[
E[r_i]
\]

against:

\[
P(i \in TopK)
\]

Relative ranking may be easier and more robust than precise return prediction.

---

## 3. Should the model output confidence?

Compare:

```text
expected return only
```

vs.

```text
expected return
+
uncertainty
+
confidence
```

---

## 4. Should multiple signals require agreement?

Compare:

```text
fixed weighted score
```

vs.

```text
ensemble
consensus
agreement
adaptive weighting
```

---

## 5. How much historical data should be used?

Compare:

```text
full history
rolling recent history
recency-weighted history
multiple training windows
```

Do not assume the oldest data still reflects the current regime.

---

## 6. Should stock selection and weight allocation be separated?

Compare:

```text
Top-N + equal weight
```

against:

```text
prediction
↓
constrained portfolio optimizer
```

---

## 7. Should market regime explicitly affect the portfolio?

Possible regime inputs:

```text
market breadth
cross-sectional dispersion
volatility
index momentum
sector leadership
overnight/global signals
```

Possible outputs:

```text
risk budget
cash target
sector tilt
turnover budget
ensemble weights
```

---

# 8. What Should NOT Be Copied Directly

## Do not copy short-selling logic

Some M6 approaches use long/short portfolios.

A long-only competition needs a different construction.

---

## Do not copy leveraged ETF concentration

ETF Global winners sometimes benefited from leveraged or volatility products.

That edge may be specific to their rules and universe.

Transfer the **regime-detection idea**, not necessarily the instrument.

---

## Do not optimize leaderboard-gaming behavior

Some competition strategies may optimize the probability of finishing first rather than expected terminal wealth.

For a competition whose primary objective is final NAV / total return, prioritize:

```text
expected terminal NAV
+
risk
+
execution
+
constraints
```

not adversarial leaderboard positioning.

---

# 9. Recommended Solution Space for Agent Exploration

The next strategy should not assume the existing momentum baseline is structurally correct.

At minimum, explore these independent families:

## Family A — Short-Horizon Momentum

```text
3D / 5D / 10D / 20D momentum
trend
volume
volatility
controlled rotation
```

Purpose:

> simple, interpretable baseline.

---

## Family B — Adaptive Forecast Ensemble

```text
multiple forecasting experts
↓
walk-forward validation
↓
adaptive ensemble
↓
expected return + uncertainty
↓
portfolio optimizer
```

Inspired primarily by:

- M6 AutoTS winner
- agreement/confidence forecasting

---

## Family C — Relative Rank Forecasting

```text
cross-sectional rank
↓
rolling rank-frequency features
↓
future rank distribution
↓
Top-K probability
↓
portfolio construction
```

Inspired by:

- M6 ATA method

---

## Family D — Forecast + Portfolio Optimization

```text
return/rank prediction
↓
risk-aware portfolio optimizer
```

Inspired by:

- Ai / Liu / Lin

---

## Family E — Direct Portfolio Optimization

```text
features
↓
direct portfolio objective
↓
weights
```

Inspired by:

- single-stage portfolio research

---

## Family F — Regime-Aware Strategy

```text
market / sector regime
↓
change risk budget
change alpha weights
change sector exposure
change turnover budget
```

Inspired by:

- ETF Global short-horizon winner behavior

---

# 10. Compact Reference for an Agent

```text
Study these strategy families before designing the next strategy:

1. M6 Investment Winner — Colin Catlin / AutoTS
   - horizon-aligned forecasting
   - automated model search
   - walk-forward CV
   - genetic optimization
   - ensembles
   - probabilistic upper / point / lower forecasts
   - confidence / agreement
   - dynamically changing best models

2. M6 Investment #2 — ATA
   - daily cross-sectional return ranks
   - recent rank-frequency distributions
   - forecast future rank probabilities
   - select assets consistently predicted to be top-ranked

3. M6 Ai / Liu / Lin
   - separate prediction from portfolio allocation
   - robust feature extraction
   - risk-constrained portfolio optimization
   - Differential Evolution style optimization

4. Single-stage M6 research
   - test direct data → portfolio optimization
   - do not assume precise individual-return prediction is required

5. ETF Global winners
   - short contests are regime-sensitive
   - sector/theme leadership can dominate
   - allow portfolio exposure to tilt when leadership is strong

Do not assume the existing momentum baseline is structurally correct.

Derive the next strategy from:
competition objective
+ causal data availability
+ execution rules
+ portfolio constraints
+ out-of-sample evidence.

Test competing architectures under the same execution simulator and validation protocol.
```

---

# 11. Source References

## M6 / AutoTS

- Colin Catlin, *Winning the M6 Financial Forecasting Decision Category*  
  https://syllepsis.live/2023/04/03/winning-the-m6-financial-forecasting-decision-category/

- M6 / AutoTS paper  
  https://www.sciencedirect.com/science/article/pii/S016920702500072X

## ATA

- *ATA method's performance in the M6 competition*  
  https://www.researchgate.net/publication/381912928_ATA_method_s_performance_in_the_M6_competition

## Prediction + Portfolio Optimization

- Ai / Liu / Lin M6 work  
  https://www.sciencedirect.com/science/article/abs/pii/S0169207024000359

## Direct Portfolio Optimization

- Single-stage portfolio optimization on M6 data  
  https://www.sciencedirect.com/science/article/pii/S0169207024000918

## ETF Global

- ETF Global competition archive / winner writeups  
  https://blog.etfg.com/
