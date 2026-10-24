# Champion Strategies Reference

For a 24-trading-day, long-only, daily-decision competition, test horizon-aligned forecasts, relative ranks, confidence, allocation and regime exposure under the same execution simulator and validation protocol. These are transferable ideas, not proof that any architecture beats the existing momentum baseline.

## 1. M6 Investment Winner — Colin Catlin / AutoTS

Colin Catlin won **Investment Decision** using AutoTS. M6 covered 100 stocks and ETFs; portfolios updated every four weeks.

```text
Historical prices → recent/relevant history → aggregate to competition horizon
→ forecasting candidates → cross-validation → genetic search
→ model/ensemble selection → next-period Point / Upper / Lower forecasts
→ expected return + uncertainty → cross-sectional normalization → weights
```

- Match the forecast to the competition horizon: compare 1D, 5D, 10D, 20D and remaining-competition return, rather than assuming daily-path prediction is necessary.
- Separate signal strength from confidence. AutoTS supplied Lower, Point and Upper forecasts, conceptually \(L_i, P_i, U_i\). The official hinge-style forecast used:

\[
\hat P_i = \frac{U_i + L_i}{2}
\]

Convert that price into expected return. A possible adaptation is:

\[
Alpha_i = ExpectedReturn_i \times Confidence_i
\]

- Agreement raises conviction: `Lower > 0`, `Point > 0`, `Upper > 0` is stronger than `Lower < 0`, `Point > 0`, `Upper > 0`. Estimate confidence through ensemble dispersion, intervals, sign agreement, historical residual distributions or calibration.
- Select models adaptively through cross-validation over preprocessing, hyperparameters and ensembles. One fixed EMA, momentum horizon or ML model need not dominate every regime.
- Fit with recent history, rolling windows or recency weighting when useful; old regimes may differ. Long history still supports robustness testing.
- Keep extra features only with out-of-sample improvement: external macro variables were tested and discarded when CV showed no value.
- Humans design constraints and verify; models execute frozen logic. Discretionary overrides can destroy a validated edge.

## 2. M6 Investment #2 — ATA Method

ATA predicts **relative ranking probabilities**, rather than exact future returns:

1. Rank daily returns across all assets. For 100 assets, use buckets 1–20, 21–40, 41–60, 61–80 and 81–100.
2. Count recent rolling bucket frequencies. Example: Top 20 = 5 times; 21–40 = 8; 41–60 = 2; 61–80 = 1; Bottom 20 = 4. This measures persistent relative leadership, beyond price appreciation.
3. Forecast the future distribution:

\[
P(Rank_1), P(Rank_2), ..., P(Rank_5)
\]

4. Compare ATA variants; agreement strengthens conviction, as with AutoTS.
5. Select consistently top-ranked assets for a long-only portfolio. Original M6 short positions do not transfer.

## 3. M6 High-Ranking Method — Ai / Liu / Lin

Separate prediction from allocation:

```text
Features → prediction model → expected opportunity → portfolio optimizer → weights
```

Prediction estimates expected opportunity, relative return, rank or probability using robust feature selection/extraction, denoising autoencoders, neural models and return-ranking prediction. Allocation separately decides names, count, weights and worthwhile turnover through risk-constrained, Differential Evolution style optimization. A forecast score need not imply equal weights.

## 4. Single-Stage Portfolio Optimization

This later M6-data research is **not an M6 winning submission**. Test it independently; superiority is unproven.

```text
Standard: market data → return forecast → portfolio optimization
Alternative: market data → direct portfolio optimization → weights
```

Possible formulations: penalized regression, constrained direct weights, AutoML over portfolio objectives and direct utility maximization. The question is whether precise individual-stock forecasts are necessary for a strong short-horizon portfolio.

## 5. ETF Global Portfolio Challenge — Regime Lessons

Short contests allow rebalancing, and winners often benefited from a dominant regime/theme: volatility products, semiconductor bear products, energy, commodities or emerging markets. Complete algorithms are generally unpublished; portfolio behavior is the strongest available evidence.

Transfer regime detection → leading theme/sector → exposure tilt → updates as leadership changes. The instruments, leverage and volatility-product concentration may depend on the original rules; they need not transfer. Equal sector weights at all times are only one alternative.

## 6. Shared Patterns

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


## 7. Design Questions to Test

1. Target: next-day, 5D, 10D, 20D or remaining-competition return?
2. Exact return \(E[r_i]\) or relative rank \(P(i \in TopK)\)? Ranking may be easier and more robust; test it.
3. Expected return alone or return + uncertainty + confidence?
4. Fixed weighted scores or ensemble consensus, agreement and adaptive weights?
5. Full history, recent rolling history, recency weights or multiple training windows? Old history may not represent the current regime.
6. Top-N equal weights or prediction followed by a constrained optimizer?
7. Should market breadth, cross-sectional dispersion, volatility, index momentum, sector leadership or overnight/global signals change risk/cash targets, sector tilts, turnover budgets or ensemble weights?

## 8. Transfer Limits

- Adapt long/short methods to long-only construction.
- Transfer regime awareness rather than copying leveraged ETF concentration.
- First-place probability and expected terminal wealth can differ. For final NAV / total return, prioritize expected terminal NAV, risk, execution and constraints over adversarial leaderboard positioning.

## 9. Independent Strategy Families

| Family | Pipeline / purpose | Inspiration |
|---|---|---|
| A — Short-Horizon Momentum | 3D / 5D / 10D / 20D momentum, trend, volume, volatility, controlled rotation; interpretable baseline | Simple baseline |
| B — Adaptive Forecast Ensemble | Experts → walk-forward CV → adaptive ensemble → return + uncertainty → optimizer | AutoTS; confidence/agreement |
| C — Relative Rank Forecasting | Cross-sectional ranks → rolling frequencies → future rank distribution → Top-K probability → portfolio | ATA |
| D — Forecast + Portfolio Optimization | Return/rank prediction → risk-aware optimizer | Ai / Liu / Lin |
| E — Direct Portfolio Optimization | Features → direct portfolio objective → weights | Single-stage research |
| F — Regime-Aware Strategy | Market/sector regime → risk budget, alpha weights, sector exposure, turnover budget | ETF Global winner behavior |

## 10. Exploration Rule

Derive competing architectures from the competition objective, causal data availability, execution rules, constraints and out-of-sample evidence. Do not assume momentum is structurally correct. Compare all families under the same simulator and validation protocol; the method details and limits are above.

## 11. Source References

### M6 / AutoTS

- Colin Catlin, *Winning the M6 Financial Forecasting Decision Category*  
  https://syllepsis.live/2023/04/03/winning-the-m6-financial-forecasting-decision-category/

- M6 / AutoTS paper  
  https://www.sciencedirect.com/science/article/pii/S016920702500072X

### ATA

- *ATA method's performance in the M6 competition*  
  https://www.researchgate.net/publication/381912928_ATA_method_s_performance_in_the_M6_competition

### Prediction + Portfolio Optimization

- Ai / Liu / Lin M6 work  
  https://www.sciencedirect.com/science/article/abs/pii/S0169207024000359

### Direct Portfolio Optimization

- Single-stage portfolio optimization on M6 data  
  https://www.sciencedirect.com/science/article/pii/S0169207024000918

### ETF Global

- ETF Global competition archive / winner writeups  
  https://blog.etfg.com/
