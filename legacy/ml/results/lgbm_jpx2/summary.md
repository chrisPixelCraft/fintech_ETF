# JPX #2 LightGBM baseline

- **資料**：dev 窗口（2019-01-01 起，month_start + mid_month），三個策略跑同一組窗口，逐窗口配對比較；holdout 沒有使用
- **比較對象**：momentum_20d（主要）、AutoTS baseline_autots（次要）；portfolio、planner、成本都相同

## Result

LightGBM vs Momentum vs AutoTS（70 個配對窗口）：**STOP_LGBM_BASELINE**

## Evidence

| 策略 | 平均 | 中位數 | 配對平均 Δ | 配對中位數 Δ | 勝過動能 | P25 | P10 | 最差 | 平均 MDD | 周轉 | 成本 | 警告 | 失格 | 秒/窗口 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| momentum_20d | +3.41% | +3.87% | — | — | — | -1.23% | -6.11% | -22.46% | 5.48% | 3.41 | 0.87% | 0 | 0 | 0 |
| autots | +2.99% | +4.13% | -0.41% | -0.05% | 49% | -0.83% | -5.91% | -24.48% | 5.38% | 3.54 | 0.91% | 0 | 0 | 26 |
| lgbm_jpx2 | +3.15% | +3.15% | -0.25% | -0.45% | 43% | +0.08% | -6.24% | -28.42% | 6.24% | 6.01 | 1.64% | 0 | 0 | 30 |

- LightGBM − 動能 配對平均 Δ 的 95% bootstrap 區間：-1.08% ~ +0.64%
- LightGBM − AutoTS：平均 Δ +0.16%，中位數 Δ -0.24%，勝率 44%，95% 區間 -0.81% ~ +1.22%
- 模型：350 次 refit，best iteration 中位數 171.0（P10 3.0，最大 3000），validation Pearson 平均 0.094（pooled，不是判斷依據）
- Smoke（6 窗口）：PASS

## Decision

`STOP_LGBM_BASELINE`

規則（spec §18）：配對中位數 Δ > 0、配對平均 Δ > 0、失格率不高於動能，三項都成立才進下一階段。
