# 24-Day Competition Strategy Tuning Protocol

## Goal

以現有 `x0352_daily_baseline` 為起點，調出一套適合 AI CUP 約 24 個交易日賽期的固定參數。

真正 objective：

```text
NT$1B cash
+ zero holdings
+ 24 trading days
→ maximize robust terminal NAV
```

不要以：

```text
2010–2026 cumulative return
CAGR
單一最好月份
```

作為主要 optimization target。

---

# 1. Use Tuning Skill

優先使用 repository / agent 已安裝的 tuning skill。

讓 tuning skill 負責：

* parameter search
* grid search
* brute-force search
* pruning
* local refinement
* experiment tracking
* parallel execution
* caching
* resume
* result comparison

但 tuning skill 不可以自行改：

* objective
* competition constraints
* train/validation/holdout split
* look-ahead rule
* execution assumptions

這些由本 spec 固定。

---

# 2. Baseline

從目前 frozen：

```text
x0352_daily_baseline
```

開始。

Baseline：

```text
target_count = 20

return_short = 10
return_long = 30

ema_fast = 10
ema_slow = 30

macd = 8 / 21 / 5

momentum_weight = 0.55
long_return_fraction = 0.20

max_replacements_per_day = 0
replacement_margin = 0.20

volatility_spike_ratio = 2.0
one_day_chase_return = 0.04

volume_low = 0.8
volume_high = 2.5

cash_guard_ratio = 0.12
```

先完整 reproduce baseline，再開始 tuning。

---

# 3. Data Split

資料：

```text
Yahoo daily
2009-01-01 → latest complete trading day in 2026-09
```

切分：

```text
Development
2010–2018

Validation
2019–2022

---------------- FREEZE ----------------

Holdout
2023–2024

Recent stress test
2025–2026/09
```

規則：

```text
Development
→ 可以大量調參

Validation
→ 可以選 candidate
→ 可以有限 local refinement

Holdout
→ 禁止調參

Recent stress
→ 禁止調參
→ 只看目前市場 regime 是否崩掉
```

---

# 4. Evaluation Unit

不要跑一條 16 年連續帳本當主要 objective。

每個 episode：

```text
start:
NT$1,000,000,000
zero holdings

run:
24 trading sessions

end:
terminal NAV
```

Primary episodes：

```text
每個月第一個交易日開始
→ 24 trading days
```

另外再做：

```text
rolling 24D windows
```

作 robustness stress test。

Rolling windows 不視為獨立 statistical samples。

---

# 5. Primary Metrics

每個 candidate 計算：

```text
median_24d_return
mean_24d_return

p25_24d_return
p10_24d_return
worst_24d_return

positive_episode_rate

median_mdd
worst_mdd

valid_episode_rate
compliance_pass_rate

median_turnover

median_days_to_20_holdings
```

---

# 6. Candidate Selection Policy

不要單純：

```text
argmax(mean return)
```

採 lexicographic selection：

```text
Step 1
淘汰 competition compliance / feasibility 太差者

↓

Step 2
median_24d_return 最大

↓

Step 3
p25_return 最大

↓

Step 4
p10_return 較佳

↓

Step 5
median MDD 較低

↓

Step 6
mean return 較高

↓

Step 7
turnover 較低
```

如果兩個 candidate 很接近：

優先選：

```text
simpler
+
more stable
+
closer to x0352
```

而不是更複雜的設定。

---

# 7. Tuning Strategy

採：

```text
Baseline
↓
Phase A: structural search
↓
Phase B: signal horizon
↓
Phase C: turnover / replacement
↓
Phase D: score weighting
↓
Phase E: risk / entry filters
↓
Phase F: joint brute-force refinement
↓
Validation
↓
Freeze
↓
Holdout
```

---

# 8. Phase A — Structural Search

先測最可能直接影響短賽期的結構。

```text
target_count:
20
22
25
28
30

max_replacements_per_day:
0
1
2
4
```

目的：

回答：

```text
20 檔集中
vs
25–30 檔分散

以及：

不主動換股
vs
短期主動換 winner
```

不要先動其他參數。

保留前約：

```text
top 20–30%
```

設定進下一階段。

---

# 9. Phase B — Signal Horizon Search

這是最重要的一輪。

### Return

```text
return_short:
3
5
7
10
15
20

return_long:
15
20
30
40
50
60
```

只保留：

```text
short < long
```

### EMA

```text
5 / 15
5 / 20
8 / 21
10 / 30
15 / 40
20 / 50
```

### MACD

```text
5 / 13 / 4
6 / 13 / 4
8 / 21 / 5
12 / 26 / 9
16 / 35 / 9
```

這一輪可以直接使用 tuning skill 做：

```text
coarse grid
+
pruning
```

目的：

> 找出最適合 24-day horizon 的 signal speed。

---

# 10. Phase C — Replacement / Turnover

搜尋：

```text
max_replacements_per_day:
0
1
2
4

replacement_margin:
0
0.025
0.05
0.10
0.20
0.30
```

只有：

```text
max_replacements_per_day > 0
```

時 `replacement_margin` 才有意義。

特別比較：

```text
0 replacement

vs

1 replacement/day
```

因為短期比賽可能需要快速 rotate winners。

但如果額外換股沒有提升：

```text
median return
或
P25
```

就不要為了 turnover 而 turnover。

---

# 11. Phase D — Ranking Weights

搜尋：

```text
momentum_weight:
0.40
0.50
0.55
0.65
0.75
0.85
0.95

long_return_fraction:
0
0.10
0.20
0.35
0.50
0.70
```

權重公式沿用：

```text
short momentum
= m × (1-q)

long momentum
= m × q

volume
= (1-m) × 0.4

MACD
= (1-m) × 0.2

trend
= (1-m) × 0.2

long trend
= (1-m) × 0.2
```

不要改 score family。

先調 mixture。

---

# 12. Phase E — Risk / Entry Filters

搜尋：

### Chase

```text
one_day_chase_return:
0.03
0.04
0.055
0.07
0.085
0.10
```

### Volatility

```text
volatility_spike_ratio:
1.5
2.0
2.5
3.0
4.0
```

### Volume

```text
volume_low:
0.2
0.5
0.8
1.0

volume_high:
2
2.5
3
5
8
```

### Cash guard

```text
cash_guard_ratio:
0.08
0.10
0.12
0.15
0.18
```

目的不是單純降低風險。

同時觀察：

```text
return
+
candidate availability
+
days to 20 holdings
+
compliance
```

尤其不要因 filter 太嚴導致：

```text
FAIL_TOO_FEW_ELIGIBLE
```

---

# 13. Phase F — Brute-Force Refinement

前五階段完成後，取約：

```text
Top 5–10 parameter regions
```

然後才做局部 brute-force。

例如最佳附近是：

```text
return = 5 / 20
EMA = 8 / 21
MACD = 8 / 21 / 5
replacement = 1
momentum_weight = 0.65
```

那就只搜索附近：

```text
return_short:
3 / 5 / 7

return_long:
15 / 20 / 25

EMA:
5/20
8/21
10/25

replacement:
0 / 1 / 2

momentum_weight:
0.55 / 0.65 / 0.75
```

這裡可以允許 tuning skill：

```text
Cartesian grid brute-force
```

但只限縮小後的 search region。

---

# 14. Optional Global Search

如果運算資源足夠，可以另外跑一次：

```text
random search
Sobol
Bayesian optimization
TPE
```

由 tuning skill 自行選擇它最擅長的方法。

目的不是取代 staged search。

而是找：

> staged search 是否漏掉跨參數 interaction。

Global search 的 search space仍需使用本 spec 的 bounds。

禁止擴張成無限制 search。

---

# 15. Comparison of Search Methods

最終至少比較：

```text
x0352 baseline

staged tuning winner

local brute-force winner

global tuning-skill winner
```

全部使用相同 validation episodes。

不要因為不同 optimizer 看了不同資料而直接比較。

---

# 16. Validation Selection

Development 搜尋完成後：

取：

```text
Top ~10–20 candidates
```

在 2019–2022 validation 上重跑。

不要直接把 Development 第一名當最終策略。

優先選：

```text
development 好
+
validation 也好
+
parameter behavior 穩定
```

如果 development winner 在 validation 明顯崩掉：

```text
reject
```

---

# 17. Parameter Stability

對最終候選做 neighborhood test。

例如最終：

```text
momentum_weight = 0.65
```

再測：

```text
0.60
0.65
0.70
```

如果：

```text
0.65 非常好
但 0.60 / 0.70 都崩掉
```

標記：

```text
PARAMETER_FRAGILE
```

優先選 performance plateau，而不是尖峰 optimum。

---

# 18. Freeze

Validation 後選 ONE strategy：

```text
competition_24d_candidate
```

保存：

```text
configs/competition_24d_final.json
```

並記錄：

```text
parameter values
selection date
training period
validation period
number of candidates tried
objective
code commit
SHA256
```

從這一刻：

```text
FREEZE
```

---

# 19. Holdout

只在 freeze 之後執行：

```text
2023–2024
```

輸出：

```text
median
mean
P25
P10
worst
positive rate
MDD
compliance
```

如果結果不好：

可以判斷策略不好。

但是：

> 不可以偷偷重新調參後仍稱這是 holdout。

若決定重新設計：

```text
2023–2024
→ development data
```

並明確記錄。

---

# 20. Recent Stress Test

最後跑：

```text
2025-01
→
2026-09 latest complete trading day
```

這是正式比賽前最接近的 regime。

比較：

```text
x0352
vs
competition_24d_final
```

特別看：

```text
median 24D
P25
worst
Oct-Nov analogue
recent 6 months
days to deploy capital
```

不要再用這段資料自動 tuning。

---

# 21. October–November Analogue

每年額外測：

```text
first trading day >= Oct 26
→ next 24 sessions
```

涵蓋：

```text
2010–2025
```

這是 secondary metric。

不要單獨 optimize 它。

---

# 22. Early Pruning

為節省計算：

candidate 在 Development 如果明顯：

```text
compliance rate 太低
或
median 明顯低於 baseline
或
P25 明顯惡化
```

允許 tuning skill early stop。

例如：

```text
valid_episode_rate < 90%
→ prune

median return
< baseline median - tolerance
→ prune
```

具體 tolerance 可由 tuning skill 根據 sample size 設定。

---

# 23. Stop Condition

停止 tuning 的條件：

出現任一即可：

```text
連續兩輪 refinement
median 提升 < 0.2 percentage point

且
P25 沒有明顯改善
```

或：

```text
附近參數形成穩定 plateau
```

或：

```text
validation 不再改善
```

不要因為可以繼續搜就無限搜。

---

# 24. What NOT to Optimize

禁止把以下當 winner criterion：

```text
highest 16-year cumulative return

highest single-month return

highest single 24D return

2025–2026 alone

October–November alone

Development mean return alone
```

---

# 25. Final Decision

最後 winner 必須在：

```text
24D median
+
lower tail
+
compliance
+
drawdown
+
parameter stability
+
validation generalization
```

之間取得好的 trade-off。

不是尋找：

```text
最大可能收益
```

而是：

> 在未知的 2026/10/26–11/27 市場 regime 下，最有機會穩定產生高 terminal NAV 的固定策略。

---

# 26. Required Final Table

| Strategy            | Median 24D | P25 | P10 | Worst | Positive % | Median MDD | Valid % |
| ------------------- | ---------: | --: | --: | ----: | ---------: | ---------: | ------: |
| x0352               |            |     |     |       |            |            |         |
| Staged winner       |            |     |     |       |            |            |         |
| Brute-force winner  |            |     |     |       |            |            |         |
| Global skill winner |            |     |     |       |            |            |         |

再分別列：

```text
Development
Validation
Holdout
2025–2026/09 stress
Oct-Nov analogues
```

---

# 27. Final Agent Workflow

```text
reproduce x0352
↓
build 24D evaluation harness
↓
Phase A structural
↓
Phase B signal horizons
↓
Phase C replacements
↓
Phase D score weights
↓
Phase E filters
↓
local brute force
↓
optional global tuning-skill search
↓
Development shortlist
↓
Validation
↓
parameter stability test
↓
FREEZE
↓
2023–2024 Holdout
↓
2025–2026/09 stress test
↓
Oct-Nov analogues
↓
final report
```

Use the tuning skill aggressively for execution and search efficiency.

Do not allow the tuning skill to violate the experimental boundaries defined above.

