# LightGBM-v2 每日排名：2025-2026/09

- 窗口：40 個 24 日窗口，與 Mom20 逐窗口配對；規則見 docs/lgbm_v2_daily_spec.md
- 2025–2026 純測試：只跑一次，只報告
- **注意**：run after the 2015-2024 STOP at the user's request (spec section 7); does not change the STOP

**STOP**（本期間通過條件）

| 策略 | 平均 | 中位數 | 配對平均 Δ | 配對中位數 Δ | 95% CI | 勝率 | P10 | 最差 | 周轉 | 成本 | 失格 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Mom20 | +8.79% | +10.99% | — | — | — | — | -8.67% | -16.28% | 3.38 | 0.86% | 0 |
| LightGBM-v2 每日 | +7.10% | +7.52% | -1.69% | -2.57% | -3.66% ~ +0.32% | 32% | -4.84% | -18.31% | 5.68 | 1.55% | 0 |

## 通過條件

- ✗ mean_delta > +0.5%
- ✗ median_delta >= 0
- ✓ no extra disqualification
- ✗ extra cost < mean_delta
