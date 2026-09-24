# AI CUP 2026 — 24-Trading-Day Competition Strategy Research v2

## 0. Mission

The goal is to find ONE fixed strategy for the actual AI CUP competition horizon:

```text
2026-10-26 → 2026-11-27
≈ 24 Taiwan trading days
```

The optimization target is therefore NOT:

```text
2009–2026 total return
CAGR
long-term compounding
```

The target is:

> Among competition-compliant strategies, find a single fixed parameter set that produces strong and robust returns over many historical ~24-trading-day competition-like episodes.

Every simulated episode starts from:

```text
cash = NT$1,000,000,000
holdings = {}
```

and ends after approximately:

```text
24 trading days
```

---

# 1. Historical data period

Download Yahoo Finance daily data covering:

```text
2009-01-01
→
2026-09 latest COMPLETE Taiwan trading day
```

Do not include an unfinished current trading session.

Purpose by period:

```text
2009
→ indicator warm-up

2010–2019
→ development / parameter research

2020–2022
→ validation

2023–2024
→ first holdout / recent-regime verification

2025–2026-09
→ recent-market stress test
→ comparison with existing x0352 research
→ pre-competition robustness check
```

The final split may be adjusted slightly if individual stocks have insufficient history, but chronological ordering must be preserved.

Never random-shuffle time-series data.

---

# 2. Data source

Use:

```text
Yahoo Finance
yfinance
```

No API key required.

Fetch daily:

```text
Open
High
Low
Close
Adj Close
Volume
Dividends
Stock Splits
```

Recommended settings:

```python
auto_adjust=False
actions=True
repair=True
```

Save locally as Parquet.

Example:

```text
data/
└── yahoo_daily/
    ├── 2330.TW.parquet
    ├── 2454.TW.parquet
    ├── ...
    └── metadata.json
```

Metadata must record:

```text
symbol
source
retrieved_at
first_date
last_date
row_count
SHA256
```

Do not silently interpolate missing OHLCV.

---

# 3. Competition universe

Use the official AI CUP 2026 150-stock whitelist.

The purpose of the historical test is:

> Test the exact 2026 competition universe under many historical market regimes.

Because the 2026 universe was not historically knowable, label all historical results:

```text
COMPETITION_UNIVERSE_STRESS_TEST
```

Do NOT describe these results as point-in-time historically deployable performance.

Explicitly disclose survivorship / composition look-ahead bias.

This is acceptable for this research because the practical question is:

> Which strategy should we use on these exact 150 stocks during the 2026 competition?

---

# 4. Competition constraints

Every simulated portfolio must obey:

```text
Official whitelist only

20–30 holdings

2330.TW <= 25% NAV

all other stocks <= 10% NAV

cash >= 0

cash < 25% NAV

Taiwan cash equities only

1000-share lots

no short selling

no margin

no securities lending

no day trading
```

Trading costs:

```text
commission = 0.1425%
sell tax = 0.3%
```

Active Share target:

```text
>= 20%
```

If exact historical benchmark information is unavailable:

```text
ACTIVE_SHARE_NOT_VERIFIED
```

Do not falsely mark it PASS.

---

# 5. Timing / anti-lookahead rule

For trading date `D`, strategy decisions may only use information available through:

```text
D-1 close
```

Correct:

```text
D-1 close
↓
calculate indicators
↓
rank stocks
↓
construct D portfolio
↓
submit / execute D order
```

Forbidden:

```text
D close
D high
D low
D volume
↓
D decision
```

All indicators must be backward-looking.

Add automated anti-lookahead tests.

---

# 6. Execution model

The official competition executes trades using the day's transaction average price.

Yahoo daily OHLCV does not directly provide the exact official daily transaction average price.

Therefore the research backtest must use ONE explicit execution proxy consistently.

Priority:

```text
official daily average price
    ↓ if unavailable

valid daily VWAP
    ↓ if unavailable

documented daily proxy
```

Never silently equate Yahoo Close with the official competition execution price.

Record:

```text
execution_model
```

in every result.

---

# 7. Baseline

Start from the currently frozen:

```text
x0352
```

Do NOT redesign the strategy from zero.

Create:

```text
x0352_daily_baseline
```

Remove ONLY the dependency on 4H-data availability.

Do not use synthetic 4H signals.

Preserve the remaining x0352 logic.

Current frozen parameters:

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

---

# 8. Baseline score

Reproduce the approximate x0352 score:

```text
10D return      44%
30D return      11%
volume          18%
MACD             9%
medium trend     9%
long trend       9%
```

Conceptually:

```text
momentum
+
volume
+
MACD
+
EMA trend
+
long-term EMA trend
↓
ranking
```

Entry filters include:

```text
sufficient warm-up

price > EMA slow

volatility ratio <= threshold

1-day return <= chase threshold

volume ratio within allowed range

positive trading volume
```

Exit logic should remain aligned with the existing implementation.

---

# 9. Warm-up

Retain:

```text
warmup_sessions = 200
```

This is why raw data starts from:

```text
2009-01-01
```

Evaluation should normally begin no earlier than:

```text
2010
```

for securities with sufficient history.

A stock without enough history must not receive fabricated indicators.

---

# 10. Core experiment: 24-trading-day episodes

The primary evaluation unit is:

```text
24 trading days
```

NOT:

```text
calendar year
```

and NOT:

```text
2009–2026 cumulative return
```

Every episode starts independently:

```text
cash = 1B TWD
holdings = empty
```

No position may carry between episodes.

---

# 11. Monthly-start episodes

Primary interpretable dataset:

For every month from:

```text
2010-01
→
2026-08
```

where a complete 24-session forward window exists:

```text
first Taiwan trading day of month
↓
24 trading sessions
```

Example:

```text
2013-05 first trading day
→ next 24 sessions
```

This should produce roughly 190–200 historical competition-like episodes depending on data coverage.

For every episode record:

```text
start_date
end_date
starting_cash
terminal_NAV
return
max_drawdown
turnover
trade_count
holding_count violations
weight violations
cash violations
other compliance failures
status
```

---

# 12. Rolling 24-day stress test

After monthly-start evaluation works correctly, evaluate rolling start dates:

```text
every valid trading day
→ next 24 trading sessions
```

Cover approximately:

```text
2010 → 2026-08
```

This may generate thousands of windows.

Purpose:

```text
start-date robustness
regime robustness
tail-risk inspection
```

Important:

> Rolling windows overlap heavily.

Therefore do NOT treat each rolling window as an independent statistical observation.

Rolling windows are a stress test, not the primary statistical sample.

---

# 13. Competition-season analogue

Separately test historical periods resembling the actual competition dates.

For each year:

```text
2010
...
2025
```

start around:

```text
first trading day on or after Oct 26
```

and evaluate the next:

```text
24 trading sessions
```

Label:

```text
OCT_NOV_ANALOG
```

Do NOT optimize solely on these ~16 observations.

Use them as a secondary seasonal robustness test.

---

# 14. Recent regime evaluation

Because the real competition occurs in late 2026, separately report:

```text
2024
2025
2026 YTD through September
```

with special attention to 24D episodes.

Do not allow recent data to silently dominate the entire parameter search.

Report separately:

```text
long-history result
recent-regime result
```

---

# 15. Parameter search objective

Do NOT optimize:

```text
2009–2026 cumulative return
```

Do NOT optimize:

```text
best single 24D episode
```

Do NOT optimize:

```text
mean return only
```

Search for strategies that perform well across MANY independent competition-like periods.

---

# 16. Search space

Start near x0352.

Possible search dimensions:

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

Use coarse search first.

Example philosophy:

```text
x0352
↓
limited neighborhood search
↓
identify useful axes
↓
local refinement
```

Avoid huge brute-force combinations.

Track the total number of strategies evaluated.

---

# 17. Candidate metrics

For every fixed parameter set calculate across monthly-start 24D episodes:

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

Also calculate:

```text
long_horizon_return
```

but ONLY as a diagnostic.

Do not use it as the primary winner criterion.

---

# 18. Primary strategy selection

First filter candidates by competition validity.

A high-return strategy that frequently fails portfolio construction must not win.

Primary hierarchy:

```text
1. compliance / feasibility

2. median 24D return

3. p25 24D return

4. lower-tail robustness

5. median / worst MDD

6. mean 24D return

7. turnover
```

Recommended lexicographic ranking:

```text
valid candidate
↓
median_return DESC
↓
p25_return DESC
↓
p10_return DESC
↓
median_MDD ASC
↓
mean_return DESC
↓
turnover ASC
```

Do not collapse everything into an arbitrary single score unless necessary.

---

# 19. Development / validation / test split

Use chronological splits.

Primary proposal:

```text
Development:
2010-01 → 2018-12

Validation:
2019-01 → 2022-12

Holdout:
2023-01 → 2024-12

Recent stress test:
2025-01 → 2026-09
```

Important distinction:

## Development

May be used for:

```text
parameter search
strategy iteration
feature decisions
```

## Validation

May be used for:

```text
candidate ranking
limited refinement
```

## Holdout

Must NOT influence parameter selection.

Use only after strategy is frozen.

## Recent stress test

Use after candidate freeze to answer:

> Does the strategy still behave reasonably in the latest market regime?

Do NOT retune repeatedly on 2025–2026 and continue calling it unseen data.

---

# 20. Walk-forward analysis

Also perform expanding walk-forward evaluation.

Example:

```text
train through 2014 → evaluate 2015
train through 2015 → evaluate 2016
train through 2016 → evaluate 2017
...
train through 2024 → evaluate 2025
```

Purpose:

```text
detect regime dependence
detect parameter instability
detect overfitting
```

Do not claim statistical independence between overlapping windows.

---

# 21. Baselines

At minimum compare:

```text
x0352_daily_baseline

selected_24d_strategy
```

Optional sanity references:

```text
simple 10D momentum
simple 20D momentum
equal-weight eligible basket
```

All strategies must use identical:

```text
universe
dates
execution model
fees
initial cash
portfolio constraints
```

---

# 22. Cold-start behavior

This is especially important.

The real competition begins with:

```text
1B cash
zero holdings
```

Therefore measure:

```text
Day 1 invested %
Day 3 invested %
Day 5 invested %

Day 1 holding count
Day 3 holding count
Day 5 holding count

days until >=20 valid holdings
```

A strategy that requires several weeks before constructing a valid portfolio may have excellent long-term returns but be unsuitable for this competition.

Report:

```text
MEDIAN_DAYS_TO_VALID_PORTFOLIO
```

for every candidate.

---

# 23. Short-horizon turnover

Unlike long-term x0352, a 24-day competition may benefit from some limited replacement.

Explicitly test:

```text
max_replacements_per_day:
0
1
2
```

possibly more only if evidence supports it.

Measure whether increased replacement:

```text
improves 24D return

versus

increases fees / noise / drawdown
```

Do not assume either zero-turnover or high-turnover is optimal.

---

# 24. Failure handling

Every episode must remain in the audit dataset.

Possible status:

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

Do not remove failed windows and calculate metrics only on survivors without disclosure.

Always report:

```text
valid / total episodes
```

---

# 25. Report comparisons

Main comparison:

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

Also separately report:

```text
2010–2018 development
2019–2022 validation
2023–2024 holdout
2025–2026/09 recent regime
```

---

# 26. October–November table

Produce:

```text
reports/24d_oct_nov_analogs.md
```

Example:

| Year | x0352 | Selected | MDD |
| ---- | ----: | -------: | --: |
| 2010 |       |          |     |
| ...  |       |          |     |
| 2025 |       |          |     |

Do not use this table alone to select the strategy.

---

# 27. Distribution analysis

Do not report only averages.

Generate distribution outputs for 24D returns:

```text
median
quartiles
P10
worst
best
```

Identify whether performance depends on:

```text
a few extreme bull-market episodes
```

Report:

```text
return excluding top 5% episodes
```

and:

```text
median without top/bottom extreme windows
```

to test robustness.

---

# 28. Overfitting control

Record:

```text
number_of_candidate_parameter_sets
number_of_parameter_search_rounds
dates used for development
dates used for validation
dates untouched before final test
```

Never repeatedly inspect the holdout and tweak the strategy.

If a holdout result causes a redesign:

```text
that holdout becomes development data
```

and must no longer be described as unseen evaluation.

---

# 29. Final strategy

At the end produce exactly one frozen configuration:

```text
configs/competition_24d_final.json
```

Containing:

```text
strategy version
indicator windows
score weights
filters
portfolio parameters
execution assumptions
data cutoff
selection timestamp
```

Also produce:

```text
SHA256
```

Once frozen, live competition operation should be:

```text
update market data
↓
run same strategy
↓
generate D-Plan
↓
validate competition constraints
↓
submit
```

NOT:

```text
re-optimize strategy every morning
```

---

# 30. Required output files

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

Machine-readable:

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

---

# 31. Agent execution order

Execute autonomously:

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

---

# 32. Definition of Done

The experiment must answer:

```text
1. Does x0352's long-term performance translate to a 24-day competition?

2. What fixed strategy maximizes robust 24-day performance?

3. What is its median 24D return?

4. What are P25 and P10 returns?

5. What is its worst historical 24D episode?

6. How often does it lose money?

7. How quickly can it deploy the initial NT$1B?

8. How often does it successfully maintain 20–30 holdings?

9. Does limited daily replacement improve short-horizon performance?

10. Does the strategy generalize to 2023–2024 holdout?

11. Does it remain competitive in 2025–2026/09?

12. How did it perform historically in late-Oct → late-Nov windows?

13. Is its apparent advantage caused by a small number of extreme bull-market episodes?

14. Can the same frozen strategy be directly used for AI CUP 2026?
```

---

# 33. Final Agent response

Return only:

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

Do not select a winner based primarily on 2009–2026 cumulative return.

The primary objective is:

> robust terminal NAV after approximately 24 trading days from NT$1B cash and zero holdings.

