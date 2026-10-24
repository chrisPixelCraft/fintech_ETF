# LightGBM relative-alpha baseline

**STOP_ALPHA_LGBM**：dev gate 失敗，Validation、Holdout 未執行。停止 alpha LightGBM，不調參、加特徵、換 horizon 或改 portfolio。

Dev 自 2019-01-01 起，month_start + mid_month；四策略同窗配對，只改 target，模型、特徵、組合、執行不變。Parity **PASS**：6 窗口的報酬、交易、持股、委託、模型 log 均與 JPX2 相同。

## Main table（70 個 dev 窗口）

| 策略 | 平均 | 中位數 | 配對 Δ vs 動能 | 勝率 | P10 | 最差 | MDD | 周轉 | 成本 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Momentum | +3.41% | +3.87% | — | — | -6.11% | -22.46% | 5.48% | 3.41 | 0.87% |
| Raw LGBM | +3.15% | +3.15% | -0.25% | 43% | -6.24% | -28.42% | 6.24% | 6.01 | 1.64% |
| Alpha LGBM | +3.12% | +3.13% | -0.29% | 44% | -4.65% | -25.86% | 5.87% | 5.79 | 1.58% |
| AutoTS | +2.99% | +4.13% | -0.41% | 49% | -5.91% | -24.48% | 5.38% | 3.54 | 0.91% |

## 配對比較：70 窗口

| 配對 | 平均 Δ | 中位數 Δ | 勝率 | 95% CI |
|---|---:|---:|---:|---|
| Alpha − Momentum | -0.29% | -0.38% | 44% | -1.09% ~ +0.55% |
| Alpha − Raw LGBM | -0.04% | -0.05% | 47% | -0.53% ~ +0.45% |
| Alpha − AutoTS | +0.12% | -0.80% | 36% | -0.75% ~ +1.10% |
| Raw LGBM − Momentum | -0.25% | -0.45% | 43% | -1.08% ~ +0.64% |

## Diagnosis

1. Target effect：alpha − raw 的平均 -0.04%、中位數 -0.05%，CI 含 0；Top25 日重疊 raw 77% → alpha 77%。
2. Turnover effect：動能 / raw / alpha 周轉 3.41 / 6.01 / 5.79，成本 0.87% / 1.64% / 1.58%。未做無成本反事實，不能推論無成本會贏。
3. Next action：依上述 dev 結果停止，未開 Validation 或 Holdout。
