# H1 spec：單獨用 market-residual momentum 取代 Mom20

一次回答：只用扣除 0050 beta 後的 residual 強勢排名（取代 Mom20），是否比 Mom20 好？

- Production 完全不動，仍是 Mom20；只在分支 `claude/h1-residual` 研究
- 每條規則在跑任何新結果前寫死於本文件
- DEV 調參一次、Validation sanity 一次、Test 一次；任何一步 FAIL 就停止，不再救
- 凍結日期：2026-09-25

## 0. 來由與揭露

- [Momentum-v2](momentum_v2_spec.md) 的 Composite 在 2022–2024 validation STOP（+0.23% < +0.5%），該判定不變
- 同一次 validation 中，診斷用的 H1（N 60／B 120）為 +2.02%、勝率 74%；因此另開本研究
- **H1 是看過 2022–2024 結果後才被挑出來的**，所以 2022–2024 對 H1 已不是獨立證據，只做 sanity check
- 2015–2021 已在 Momentum-v2 DEV 用過 8 組 H1 設定；本研究仍只把它當調參資料
- 唯一乾淨的證據是 2025-01 到 2026-09：H1 從未在該段評估；但當初選 Mom20 有參考這段（final test）

## 1. 策略

```text
β      = cov(lr, lm) / var(lm)        近 B 日，兩者皆有值的日子
resid  = Σ (lr − β × lm)              近 N 日
分數   = rank(resid)，取 Top25
```

- `lr`：個股日對數報酬（action-neutral）；`lm`：0050 日對數報酬
- 視窗內至少 80% 的日子有值，否則空值；空值排名給 0.5
- 可排名股票與 Mom20 相同：近 20 日每天都可交易
- 組合層與 production 相同：前 25 檔等權、投入 95%、續抱 35 名、換手門檻 10%、最後 3 天不交易
- 程式：`momv2.signals`，`kind='h1'`（與 Momentum-v2 相同的實作與測試）

## 2. 調參（只在 DEV 2015–2021）

| 參數 | 網格 |
|---|---|
| N（residual 累積天數） | 20 / 25 / 40 / 60 / 90 / 120 |
| B（β 估計天數） | 60 / 120 / 250，且 N ≤ B |

- 共 16 組；Momentum-v2 已跑的 8 組設定完全相同，直接沿用同一批 run
- 擴大網格的原因：上次最佳 N = 60 落在網格邊界
- 選參規則（與 Momentum-v2 第 8 節相同）：
  1. 合格：配對中位數 Δ ≥ 0、周轉 ≤ 1.5 倍 Mom20、失格率不高於 Mom20
  2. 預設值 N 60／B 120；合格且平均 Δ 比預設值高 **0.2% 以上** 才取代
  3. 多個符合取平均 Δ 最高；完全相同取網格順序在前者
- 選出的參數寫入 `research/results/h1_residual/freeze.json` 並 commit，之後才跑 Validation
- 組合層參數不調

## 3. 期間

| 期間 | 角色 | 次數 |
|---|---|---|
| 2014 | 暖身（β 250 日） | — |
| 2015–2021（DEV） | 調參 | 16 組各一次 |
| 2022–2024（Validation） | Sanity check（不乾淨） | 一次 |
| 2025-01 到 2026-09（Test） | 決定性 gate | 一次，sanity 通過才跑 |

- 市場資料從 2014-01-01 起；24 交易日窗口，每月月初、月中起點，逐窗口和 Mom20 配對

## 4. 通過條件

**Sanity check：Validation 2022–2024**

| 條件 | 門檻 |
|---|---|
| 配對平均 Δ | > 0 |

**決定性 gate：Test 2025-01 到 2026-09**

| 條件 | 門檻 |
|---|---|
| 配對平均 Δ | > 0 |
| 配對中位數 Δ | ≥ 0 |
| 失格次數 | 不多於 Mom20 |
| 多出來的成本 | < 平均 Δ |

- 同時報告 95% bootstrap CI、勝率、贏／輸窗口的平均、最差 5 個窗口

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

- FAIL 後不改網格、不換門檻、不混其他 component
- Shadow：每天和 Mom20 並行產生模擬 D-Plan，不實際交易
- 升 production 必須重新通過全部 P0

## 6. 事先寫下的風險

- 純 Mom60（不扣 beta）在 2025–2026 final test 輸 Mom20 −1.78%；H1 的 60 日與它接近，差別只在扣除 beta
- H1 在 2024-01 有 −8.4% 的窗口；Test 要看尾部
- H1 周轉約 1.9，Mom20 約 3.9；成本較低

## 7. DEV 選參結果（凍結於 Validation 之前）

- 選出 **N = 120、B = 250**：DEV 配對平均 Δ +1.170%，中位數 +0.70%，周轉 1.43
- 預設 60／120 為 +0.965%；門檻 +1.165%，只高出 0.004 個百分點，實質上是平手，但依規則機械選出
- 又落在網格邊界（N 最大值）；DEV 上的趨勢是窗口越長越好、周轉越低；規則不允許再擴網格
- 完整表格：[research/results/h1_residual/summary.md](../research/results/h1_residual/summary.md)

## 8. 修訂紀錄

| 日期 | 修改 | 原因 | 當時是否看過結果 |
|---|---|---|---|
| — | — | — | — |
