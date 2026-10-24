# AI CUP 2026 — 24-Trading-Day Competition Strategy Research v2

Find **one frozen, competition-compliant strategy** with robust terminal NAV across historical ~24-trading-day episodes starting from NT$1B cash and zero holdings. The actual contest is 2026-10-26 → 2026-11-27, approximately 24 Taiwan trading days. Do not select primarily on 2009–2026 cumulative return, CAGR, long-term compounding, mean return alone or the best single episode.

## 0–4. Mission, Data and Constraints

0. Reset every episode to cash = NT$1,000,000,000 and holdings = {}; end after approximately 24 trading days. No positions carry between episodes.
1. Download 2009-01-01 → latest COMPLETE Taiwan trading day in 2026-09; exclude unfinished sessions. Initial period roles: 2009 warm-up; 2010–2019 development; 2020–2022 validation; 2023–2024 first holdout/recent-regime check; 2025–2026-09 recent stress, x0352 comparison and pre-competition robustness. Insufficient stock history may require small split adjustments; retain chronology, never shuffle. The primary split proposal is in section 19.
2. Use Yahoo Finance / yfinance, no API key. Fetch daily Open, High, Low, Close, Adj Close, Volume, Dividends and Stock Splits; auto_adjust=False, actions=True, repair=True. Save Parquet locally, e.g. data/yahoo_daily/2330.TW.parquet, 2454.TW.parquet and metadata.json. Metadata: symbol, source, retrieved_at, first_date, last_date, row_count, SHA256. Do not silently interpolate OHLCV.
3. Use the official AI CUP 2026 150-stock whitelist. Label all historical results COMPETITION_UNIVERSE_STRESS_TEST: this exact 2026 universe was not historically knowable. Disclose survivorship/composition look-ahead; do not claim point-in-time deployable performance. The practical question concerns these exact stocks in 2026.
4. Every portfolio must obey whitelist-only Taiwan cash equities; 20–30 holdings; 2330.TW <= 25% NAV, others <= 10%; cash >= 0 and < 25% NAV; 1000-share lots; no short, margin, securities lending or day trading. Commission = 0.1425%, sell tax = 0.3%. Active Share target >= 20%; without exact historical benchmark data mark ACTIVE_SHARE_NOT_VERIFIED, never PASS.

## 5–9. Causal Baseline and Execution

5. For trading date `D`, use only D-1 close → indicators → ranking → D portfolio → D orders/execution. D close/high/low/volume cannot inform D decisions. Indicators must look backward; add automated anti-lookahead tests.
6. Official fills use daily transaction average price, unavailable directly from Yahoo OHLCV. Use one explicit, consistent proxy with priority: official daily average → valid daily VWAP → documented daily proxy. Never silently equate Yahoo Close with official execution; record execution_model in every result.
7. Start from frozen x0352; create x0352_daily_baseline by removing ONLY the 4H-data availability dependency. No synthetic 4H signals or redesign; preserve remaining logic and parameters:

```text
target_count = 20

return_short = 10
return_long = 30

ema_fast = 10
ema_slow = 30

long_ema_fast = 100
long_ema_slow = 200

macd_fast = 8
macd_slow = 21
macd_signal = 5

momentum_weight = 0.55
long_return_fraction = 0.20

volatility_spike_ratio = 2.0

one_day_chase_return = 0.04

volume_low = 0.8
volume_high = 2.5

max_replacements_per_day = 0

replacement_margin = 0.2

cash_guard_ratio = 0.12
```

8. Reproduce the approximate score:

```text
10D return      44%
30D return      11%
volume          18%
MACD             9%
medium trend     9%
long trend       9%
```

Momentum + volume + MACD + medium/long EMA trends determine ranking. Entry requires warm-up, price > EMA slow, volatility ratio <= threshold, 1-day return <= chase threshold, allowed volume ratio and positive volume. Exit logic stays aligned with existing implementation.

9. Retain warmup_sessions = 200; raw data starts 2009-01-01 and evaluation normally no earlier than 2010 where history suffices. Never fabricate indicators.

## 10–14. Episode Design

10. Primary unit = 24 trading days, not calendar years or cumulative 2009–2026 return; each starts at cash = 1B TWD, holdings = empty.
11. Primary monthly-start dataset: first Taiwan session of each month, 2010-01 → 2026-08, only where the next 24 sessions are complete; e.g. first session of 2013-05 → next 24 sessions. Expect roughly 190–200 episodes depending on coverage. Record start_date, end_date, starting_cash, terminal_NAV, return, max_drawdown, turnover, trade_count, holding_count violations, weight violations, cash violations, other compliance failures and status.
12. After monthly evaluation works, stress every valid daily start → next 24 sessions over approximately 2010 → 2026-08, potentially thousands of windows. Inspect start-date/regime robustness and tails. Heavy overlap prevents treating windows as independent observations; this is stress evidence, not the primary statistical sample.
13. For each year 2010 ... 2025, start on the first session on/after Oct 26 and run 24 sessions; label OCT_NOV_ANALOG. These ~16 observations are secondary seasonal robustness evidence, never the sole tuning target.
14. Separately report 2024, 2025 and 2026 YTD through September, emphasizing 24D episodes. Keep long-history and recent-regime results distinct; do not let recent data silently dominate search.

## 15–20. Search and Selection

15. Optimize across many independent competition-like periods, rather than cumulative return, one exceptional episode or mean alone.
16. Begin with coarse, bounded x0352 neighborhood search → useful axes → local refinement. Avoid huge brute-force combinations and track total strategies evaluated. Possible dimensions:

```text
target_count

return_short
return_long

ema_fast
ema_slow

macd_fast
macd_slow
macd_signal

momentum_weight
long_return_fraction

volume_low
volume_high

volatility_spike_ratio

one_day_chase_return

max_replacements_per_day

replacement_margin

cash_guard_ratio
```

17. For each fixed set, compute monthly-episode metrics:

```text
median_return

mean_return

p25_return

p10_return

worst_return

best_return

positive_episode_rate

negative_episode_rate

median_MDD

p90_MDD

worst_MDD

compliance_pass_rate

valid_episode_count

median_turnover

median_trade_count
```

Also compute long_horizon_return as diagnostic only, never the primary winner criterion.

18. Filter by compliance/feasibility before return: frequent construction failures disqualify a high-return candidate. Priority: 1. compliance; 2. median 24D return; 3. P25; 4. lower tails; 5. median/worst MDD; 6. mean; 7. turnover. Recommended lexicographic order: valid candidate → median_return DESC → p25_return DESC → p10_return DESC → median_MDD ASC → mean_return DESC → turnover ASC. Avoid arbitrary single-score aggregation unless necessary.
19. Primary chronological proposal: Development 2010-01 → 2018-12 for search, iteration and feature decisions; Validation 2019-01 → 2022-12 for ranking/limited refinement; Holdout 2023-01 → 2024-12 only after freeze, never for selection; Recent stress 2025-01 → 2026-09 only after freeze to assess current-regime behavior. Repeated retuning on recent data invalidates unseen-data claims.
20. Add expanding walk-forward analysis, e.g. train through 2014 → evaluate 2015; 2015 → 2016; 2016 → 2017; ...; 2024 → 2025. Diagnose regime dependence, parameter instability and overfitting; overlapping windows remain non-independent.

## 21–24. Comparisons and Failures

21. Compare x0352_daily_baseline with selected_24d_strategy; optional sanity references: simple 10D / 20D momentum and equal-weight eligible basket. All use identical universe, dates, execution model, fees, initial cash and constraints.
22. Cold-start at 1B cash, zero holdings. Measure invested % and holding count on Day 1, Day 3, Day 5, days until >=20 valid holdings, and MEDIAN_DAYS_TO_VALID_PORTFOLIO for each candidate. Multiweek deployment can make a long-term winner unsuitable here.
23. Test max_replacements_per_day = 0, 1, 2; more only with evidence. Compare 24D benefit against fees, noise and drawdown; neither zero nor high turnover is assumed optimal.
24. Retain every failed episode in the audit dataset. Report valid / total episodes, and disclose any survivor-only metrics. Possible statuses:

```text
PASS

FAIL_TOO_FEW_ELIGIBLE

FAIL_HOLDING_COUNT

FAIL_CASH

FAIL_WEIGHT_CAP

FAIL_ROUND_LOT

FAIL_MISSING_DATA

FAIL_EXECUTION_DATA

ACTIVE_SHARE_NOT_VERIFIED

FAIL_OTHER
```

## 25–28. Reports and Overfitting Control

25. Main comparison template:

| Metric              | x0352_daily | Selected 24D |
| ------------------- | ----------: | -----------: |
| Median 24D return   |             |              |
| Mean 24D return     |             |              |
| P25 return          |             |              |
| P10 return          |             |              |
| Worst return        |             |              |
| Positive windows    |             |              |
| Median MDD          |             |              |
| Worst MDD           |             |              |
| Compliance rate     |             |              |
| Days to 20 holdings |             |              |
| Turnover            |             |              |


Report 2010–2018 development, 2019–2022 validation, 2023–2024 holdout and 2025–2026/09 recent regime separately.

26. Produce reports/24d_oct_nov_analogs.md comparing Year, x0352, Selected and MDD for 2010 ... 2025. Do not select using this table alone.
27. Show median, quartiles, P10, worst and best 24D returns. Check dependence on extreme bull episodes with return excluding top 5% episodes and median without top/bottom extreme windows.
28. Record number_of_candidate_parameter_sets, number_of_parameter_search_rounds, dates used for development/validation, and dates untouched before final test. Never repeatedly inspect/tweak holdout. If holdout causes redesign, it becomes development data and is no longer unseen evaluation.

## 29–33. Freeze, Outputs and Execution

29. Produce exactly one configs/competition_24d_final.json with strategy version, indicator windows, score weights, filters, portfolio parameters, execution assumptions, data cutoff, selection timestamp and SHA256. Live operation: update data → same frozen strategy → D-Plan → constraint validation → submit; never re-optimize every morning.
30. Required reports and machine-readable artifacts:

```text
reports/
├── 24d_strategy_summary.md
├── 24d_x0352_baseline.md
├── 24d_parameter_search.md
├── 24d_validation.md
├── 24d_holdout.md
├── 24d_recent_regime.md
├── 24d_oct_nov_analogs.md
├── 24d_failure_analysis.md
└── 24d_final_candidate.md
```

```text
outputs/24d/
├── monthly_episodes.csv
├── rolling_episodes.csv
├── oct_nov_episodes.csv
├── candidates.csv
├── development.csv
├── validation.csv
├── holdout.csv
├── recent_regime.csv
├── final_selection.json
└── audit.json
```

31. Execute autonomously in this order:

```text
1. Inspect current repo and frozen x0352
2. Confirm official 150-stock whitelist
3. Download Yahoo daily data:
   2009-01-01 → latest complete trading day in 2026-09
4. Validate and cache data
5. Remove only x0352's 4H coverage dependency
6. Build x0352_daily_baseline
7. Verify causal D-1 signal timing
8. Implement cold-start 24D episode runner
9. Run monthly-start episodes 2010–2026
10. Split development / validation / holdout / recent regime
11. Run constrained parameter search on development only
12. Rank candidates on validation
13. Freeze candidate
14. Run 2023–2024 holdout
15. Run 2025–2026/09 recent-regime stress test
16. Run rolling-window stress test
17. Run historical Oct-26 → 24-session analogues
18. Compare candidate against x0352
19. Analyze failure modes and lower-tail risk
20. Freeze final JSON
21. Independently audit all outputs
22. Produce final report
```

32. Done requires answers on baseline transfer from long-term to 24D, one robust fixed strategy, median/P25/P10/worst returns, loss frequency, speed of deploying NT$1B, maintaining 20–30 holdings, limited replacements, 2023–2024 generalization, 2025–2026/09 competitiveness, late-Oct/late-Nov performance, dependence on extreme bull episodes and direct AI CUP 2026 usability.
33. Final response only:

```text
A. Data coverage

B. x0352 24D baseline performance

C. Selected fixed strategy and parameters

D. Development metrics

E. Validation metrics

F. 2023–2024 untouched holdout metrics

G. 2025–2026/09 recent-regime metrics

H. Historical Oct–Nov analogue results

I. Worst-case / lower-tail behavior

J. Competition compliance / failure rate

K. Remaining blockers

L. Exact reproduction commands
```
