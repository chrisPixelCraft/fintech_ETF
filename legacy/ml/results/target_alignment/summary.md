# LightGBM target alignment study

- **資料**：dev 2019-01-01 起，month_start + mid_month，五個策略同一組窗口逐窗口配對；三個 LightGBM 只差 target
- **Parity**：PASS（6 窗口的報酬、交易、持股、委託、訓練資料 audit、模型與預測 log 與 JPX2 完全相同）
- **Execution price**：2019–2021 dev 完全沒有官方 VWAP，execution_alpha 用的是 HLC3 proxy；這裡驗證的是「與 simulator 成交語意對齊」，不是官方 VWAP target

## Decision

**STOP_TARGET_FORMULATION**；三個 target 中對動能最好的是 Raw LGBM

## Main Table（70 個 dev 窗口）

| 策略 | 平均 | 中位數 | Δ vs 動能 | 勝率 | P10 | 最差 | MDD | 周轉 | 成本 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Momentum | +3.41% | +3.87% | — | — | -6.11% | -22.46% | 5.48% | 3.41 | 0.87% |
| Raw LGBM | +3.15% | +3.15% | -0.25% | 43% | -6.24% | -28.42% | 6.24% | 6.01 | 1.64% |
| Alpha LGBM | +3.12% | +3.13% | -0.29% | 44% | -4.65% | -25.86% | 5.87% | 5.79 | 1.58% |
| Execution Alpha LGBM | +2.87% | +2.76% | -0.53% | 46% | -5.19% | -30.47% | 6.04% | 5.71 | 1.55% |
| AutoTS | +2.99% | +4.13% | -0.41% | 49% | -5.91% | -24.48% | 5.38% | 3.54 | 0.91% |

## Target Comparison

- Raw → Alpha（Q1）：平均 Δ -0.04%，中位數 Δ -0.05%，勝率 47%，95% CI -0.53% ~ +0.45%（70 窗口）
- Alpha → Execution Alpha（Q2）：平均 Δ -0.24%，中位數 Δ -0.14%，勝率 47%，95% CI -0.66% ~ +0.15%（70 窗口）
- Execution Alpha − Momentum（Q3）：平均 Δ -0.53%，中位數 Δ -0.57%，勝率 46%，95% CI -1.42% ~ +0.36%（70 窗口）
- Execution Alpha − Raw：平均 Δ -0.28%，中位數 Δ -0.16%，勝率 47%，95% CI -0.83% ~ +0.29%（70 窗口）

| Target | Validation Pearson | Top25 日重疊 | 進場/日 | 周轉 | 成本 | 官方 VWAP label 比例 |
|---|---:|---:|---:|---:|---:|---:|
| Raw LGBM | 0.094 | 77% | 4.10 | 6.01 | 1.64% | — |
| Alpha LGBM | 0.063 | 77% | 3.96 | 5.79 | 1.58% | — |
| Execution Alpha LGBM | 0.061 | 78% | 3.91 | 5.71 | 1.55% | 0% |

成本差距不代表「沒成本就會贏」：沒有跑無成本反事實。

## Next Action

- 三種 target 都沒有明顯改善：問題不只在 target
- 下一個方向：JPX5 behavioral features 或直接 ranking objective
- 不調參、不改 portfolio、不換 horizon

- Validation：未執行（dev gate 未通過）；Holdout：未執行
