# H1 residual：Test STOP，Mom20 維持

## 8. 結果

Test 四項三項未過，不改網格／門檻、不再救（第 5 節）。

| 階段 | 配對平均 Δ | 中位數 Δ | 95% CI | 勝率 |
|---|---:|---:|---:|---:|
| DEV 2015–2021（調參） | +1.17% | +0.70% | +0.71% ~ +1.64% | 62% |
| Validation 2022–2024（sanity，不乾淨） | +1.78% | +2.85% | +0.97% ~ +2.56% | 67% |
| **Test 2025-01 到 2026-09（決定性）** | **−2.15%** | **−2.20%** | **−3.81% ~ −0.49%** | **30%** |

Mom20 test +8.79% 與 2025–2026 final test 一致。符合第 6 節風險：Mom60 −1.78%，扣 beta 仍未改善長動能落後。Validation 後挑訊號在未見資料反轉，2022–2024 +2.02% 非獨立證據。

Production 維持 Mom20；研究分支 `claude/h1-residual`，2026-09-25 凍結。規則先於新結果；DEV 調參、Validation sanity、Test 各一次，任一步 FAIL 即停。

## 0. 來由與偏誤

[Momentum-v2](momentum_v2_spec.md) Composite：2022–2024 STOP（+0.23% < +0.5%），判定不變；同期 H1（N 60／B 120）診斷 +2.02%、勝率 74%，因而立題。

H1 看過 2022–2024 才挑出，該段僅 sanity；2015–2021 已測 8 組，只供調參。2025-01 到 2026-09 為 H1 未測的唯一乾淨證據，但 Mom20 選擇曾參考該段 final test。

## 1. 策略

```text
β      = cov(lr, lm) / var(lm)        近 B 日，兩者皆有值的日子
resid  = Σ (lr − β × lm)              近 N 日
分數   = rank(resid)，取 Top25
```

`lr`：個股 action-neutral 日對數報酬；`lm`：0050。視窗至少 80% 有值，否則空值、排名 0.5。與 Mom20 同池：近 20 日每日可交易。

沿用 production：前 25 等權、投入 95%、續抱 35 名、10% 換手門檻、末 3 天不交易。實作／測試同 Momentum-v2：`momv2.signals`，`kind='h1'`。

## 2. DEV 選參：2015–2021

| 參數 | 網格 |
|---|---|
| N（residual 累積天數） | 20 / 25 / 40 / 60 / 90 / 120 |
| B（β 估計天數） | 60 / 120 / 250，且 N ≤ B |

16 組含原 Momentum-v2 8 組，沿用相同 run；因原最佳 N = 60 位於邊界擴網格。組合層不調。沿用 Momentum-v2 第 8 節：

1. 合格：配對中位數 Δ ≥ 0、周轉 ≤ 1.5 倍 Mom20、失格率不增加。
2. 預設 N 60／B 120；合格且平均 Δ 高 **0.2% 以上**才替換。
3. 多個符合取平均 Δ 最高，同分取網格先者。

先寫 `research/results/h1_residual/freeze.json` 並 commit，才跑 Validation。

## 3. 期間

| 期間 | 角色 | 次數 |
|---|---|---|
| 2014 | 暖身（β 250 日） | — |
| 2015–2021（DEV） | 調參 | 16 組各一次 |
| 2022–2024（Validation） | Sanity check（不乾淨） | 一次 |
| 2025-01 到 2026-09（Test） | 決定性 gate | 一次，sanity 通過才跑 |

資料自 2014-01-01；24 交易日窗，月初／月中與 Mom20 配對。

## 4. Gate

Validation 2022–2024 sanity：配對平均 Δ > 0。

Test 2025-01 到 2026-09：平均 Δ > 0、中位數 Δ ≥ 0、失格不多於 Mom20、多出成本 < 平均 Δ。報 95% bootstrap CI、勝率、贏／輸窗平均、最差 5 窗。

## 5. 決策

```text
DEV 16 組 → 選參 → freeze.json commit
      ↓
Validation sanity：平均 Δ > 0？
├─ 否 → STOP，Mom20 維持
└─ 是 → Test 一次
        ├─ FAIL → STOP，Mom20 維持，不再救
        └─ PASS → Prospective shadow → 全部 P0 重跑 → 才考慮升 production
```

FAIL 不改網格／門檻、不混 component。Shadow 每日並行 Mom20 模擬 D-Plan、不交易；升 production 須重跑全部 P0。

## 6. 事前風險

純 Mom60 在 2025–2026 final test 輸 Mom20 −1.78%，H1 60 日僅差扣 beta。H1 曾有 2024-01 −8.4% 窗，須看尾部。周轉 H1 約 1.9、Mom20 約 3.9，成本較低。

## 7. DEV 凍結結果（早於 Validation）

N = 120、B = 250：平均 Δ +1.170%、中位數 +0.70%、周轉 1.43。預設 60／120 +0.965%；超過 +1.165% 門檻僅 0.004 個百分點，實質平手，依規則選出。

N 再在最大邊界；DEV 越長越好、周轉越低，但不准再擴網格。[完整表](../results/h1_residual/summary.md)：research/results/h1_residual/summary.md。

## 9. 修訂紀錄

無。
