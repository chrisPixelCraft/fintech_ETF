# Hybrid 評估：2015–2024（只跑一次）

- 窗口：236 個 24 日窗口（月初＋月中），資料自 2014-01-01 起
- 規則寫死於 [docs/hybrid_spec.md](../../../docs/hybrid_spec.md)

**STOP**

| 策略 | 平均 | 中位數 | 配對平均 Δ | 配對中位數 Δ | 95% CI | 勝率 | P10 | 最差 | 周轉 | 成本 | 失格 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Mom20 | +2.01% | +1.19% | — | — | — | — | -6.49% | -23.67% | 3.74 | 0.95% | 0 |
| hybrid_v2A | +2.27% | +1.55% | +0.26% | +0.00% | -0.03% ~ +0.57% | 33% | -6.19% | -29.32% | 5.09 | 1.35% | 0 |
| hybrid_v2B | +2.25% | +1.55% | +0.25% | +0.00% | -0.06% ~ +0.56% | 32% | -5.78% | -31.37% | 5.07 | 1.34% | 0 |

## 通過條件

- hybrid_v2A：✗ mean_delta > +0.5%，✓ median_delta >= 0，✓ no extra disqualification，✗ extra cost < mean_delta
- hybrid_v2B：✗ mean_delta > +0.5%，✓ median_delta >= 0，✓ no extra disqualification，✗ extra cost < mean_delta

## Panic 診斷（只報告，不用於任何選擇）

- hybrid_v2A：panic 日 1667 天，含 panic 日的窗口 149 個；這些窗口的 Δ 平均 +0.42%、中位數 +0.07%
- hybrid_v2B：panic 日 1667 天，含 panic 日的窗口 149 個；這些窗口的 Δ 平均 +0.39%、中位數 +0.01%

- 平常日 Hybrid 就是 Mom20，差異全部來自 panic 日
