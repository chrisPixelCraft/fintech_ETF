# Momentum-v2 spec：三個新資訊來源 ＋ 一個固定 Composite

檢驗固定 Composite（Mom20＋market-residual＋低換手＋月營收）能否勝過 Mom20；僅 Composite 可升級。

Production 維持 Mom20；研究限 `claude/momentum-v2`。規則在看報酬前寫死，凍結於 2026-09-25。DEV 調參、Validation、Test 各一次；任一 FAIL 即停。

## 0. 範圍與既有結論

與 `claude/lgbm-v2-daily` 獨立平行；`hybrid/` 只 import 不改碼，該研究不影響本規則。
已結案不重跑：Mom lookback 15/20/25/30D、skip 1/3/5D、multi-horizon、risk-adjusted、near-high、volume-trend、up-ratio、relative-return target、LightGBM 學習率。Hybrid／LightGBM／crash detector 延後，Composite 有新資訊才重啟。

## 1. 結構

```text
每個決策日 D：D−1 收盤以前的資料 → 排名分數 → 共用組合層、Active Share、planner、D-Plan
```

與 Mom20 共用 `_eligible(view, 20)`：近 20 日每天可交易。組合沿用 `production/strategy.json`：Top 25 等權、投入 95%、續抱 35 名、換手門檻 10%、末 3 天不交易；相同窗口及排名集合逐窗口配對。

## 2. 共用記號

`lr` = 個股 `log(1 + action_neutral_return)`，標記日空值；`lm` = 0050 日對數報酬。`rank(x)` 為當日可排名股票的百分位（`pct=True`），越大越好、缺值 0.5。視窗有效日須 ≥80%，否則空值。`Mom20` = 近 20 日 `Σ lr`，同 production。

## 3. Hypotheses（僅診斷）

### H1：Market-residual momentum

```text
β      = cov(lr, lm) / var(lm)          近 B 日，兩者皆有值的日子
resid  = Σ (lr − β × lm)                近 N 日
H1 分數 = rank(resid)
```

僅扣 0050 beta，非多因子 residual；診斷用 `rank(resid)` 取 Top25。

### H2：低換手的持續型贏家

```text
換手率  = 近 N 日平均成交量（可交易日） ÷ 發行股數(D−1)
低換手  = rank(−換手率)
H2 分數 = 平均( rank(Mom20), 低換手 )
```

跨股票比較換手率，固定低換手較好；不同於已結案的個股 volume-trend。

### H3：月營收動能

沿用 `hybrid.features.revenue_features`（與 Hybrid spec 第 4、5 節相同）：

月份 m 營收於次月 11 日（含）收盤後可用，D 僅用 D−1 前已公布月份。`yoy` = 當月／報表去年同月 − 1，分母 ≤0 則空值；`Δyoy` = 相連兩月 `yoy` 差；`record` = 當月 ≥ 前 12 個月最高時為 1，否則 0。

```text
營收分數 = 依參數 S（第 5 節）取 rank 平均
H3 分數  = 平均( rank(Mom20), rank(營收分數) )
```

MoM 不用，季節性過強。

## 4. 資料與可用時間

| 資料 | 來源 | 可用時間 |
|---|---|---|
| 價格、成交量 | 既有 market panel（`competition.data`） | 當日收盤後 |
| 0050 報酬 | 同上 | 當日收盤後 |
| 月營收（2013-01 起） | 公開資訊觀測站 `t21sc03`（`data/hybrid/revenue.csv`） | 次月 11 日（含）收盤後 |
| 發行股數（2014-01 起） | TWSE `MI_QFIIS`、TPEx `qfii` 的「發行股數」 | 報表日收盤後 |

**發行股數**
`momv2/sources.py` 每月首個交易日抓快照，保存原始回應 sha256。t 取最新快照日 ≤t，乘上快照後至 t（含）的 `split` 股本倍數（配股、減資、分割）。配股掛牌常晚於除權，少數日股數可差幾個百分點，僅微調排名；缺快照則換手率空值、排名 0.5。

**H2 保留條件（看任何報酬前判定）**
2015-01 到 2026-09 有價格的（股票, 月份）須 ≥95% 有發行股數；否則取消 H2、不用 proxy，改第 6 節三項 Composite。

**月營收的已知限制（揭露）**
無公司實際公布時間；次月 11 日規則會延後早公布資料，不偷看。月報可能更正：m 當月營收對 m+12 去年同月，約 3.7% 差距 >0.1%、0.75% 差距 >5%；上月營收比對完全一致。跨年基準偶有調整；YoY 分母取同張報表，符合當時公布內容。

## 5. 參數

### DEV 獨立選參

| 策略 | 參數 | 網格 | 預設值 |
|---|---|---|---|
| H1 | residual 累積天數 N | 20 / 25 / 40 / 60 | 20 |
| H1 | β 估計天數 B | 60 / 120 | 60 |
| H2 | 換手率平均天數 N | 20 / 25 / 60 | 20 |
| H3 | 營收分數組成 S | `yoy`／`yoy+Δyoy`／`yoy+Δyoy+record` | `yoy+Δyoy` |

H1 8 組、H2 3 組、H3 3 組，共 14 組＋Mom20；不聯合搜尋。

固定：Composite 等權、Mom20（Mom25 已結案）、production 組合、0050、缺值排名 0.5、有效日 ≥80%、低換手較好。PIT、成本、窗口、配對及 gate 不可改。

## 6. Composite：唯一可以升級的策略

```text
Score = 平均( rank(Mom20), rank(resid), rank(−換手率), rank(營收分數) )
```

使用 DEV 所選參數；取消 H2 時：`Score = 平均( rank(Mom20), rank(resid), rank(營收分數) )`

## 7. 期間

| 期間 | 角色 | 次數 |
|---|---|---|
| 2014 | 暖身（β、換手率、營收 YoY 的歷史） | — |
| 2015–2021（DEV） | 只用來調參 | 14 組各跑一次 |
| 2022–2024（Validation） | 第一道 gate | Composite 一次 |
| 2025-01 到 2026-09（Test） | 第二道 gate，決定性證據 | 一次，Validation PASS 才跑 |

市場資料自 2014-01-01（`data.start`）；24 交易日窗口取月初／月中，跨切分依既有規則排除。各階段均公布 H1、H2、H3、Composite。150 檔為 2026 名單，早期存活偏誤較大；同池配對影響較小。

2022–2024 已用於 Mom20 P1、Hybrid，僅 retrospective evidence，非乾淨 OOS。2015–2024 已被 Hybrid 使用；LightGBM-v2 僅 panic 日用過 `resid_mom60`、`rev_*`。Mom20 選擇參考過 2025–2026 final test；Composite、H1、H2、H3 尚未在該段評估。

## 8. DEV 選參規則

DEV 各 component 與 Mom20 配對：

1. 合格須全通過。
	- 中位數 Δ ≥0。
	- 周轉 ≤1.5 倍 Mom20。
	- 失格率不增加。
2. 合格才可替換。
	- 平均 Δ 超預設 **0.2% 以上**。
3. 取平均 Δ 最高者。
	- 同值取網格前者。
	- 即短窗口、簡單者。
4. 無符合者用預設。
	- 即使預設不合格。
	- Composite 仍須 gate。

參數寫入 `research/results/momentum_v2/freeze.json` 並 commit，才跑 Validation；看完不可回調。

## 9. Composite Gate

逐窗口 `Δ = R(Composite) − R(Mom20)`：

| 條件 | Validation 2022–2024 | Test 2025-01 到 2026-09 |
|---|---|---|
| 平均 Δ | > +0.5% | > 0 |
| 中位數 Δ | ≥ 0 | ≥ 0 |
| 失格次數 | ≤ Mom20 | ≤ Mom20 |
| 額外成本 | < 平均 Δ | < 平均 Δ |

Test 約 38 窗口，僅要求優勢不消失／反轉。DEV Composite 與 2015–2018／2019–2021 分段只報告，非 gate。

## 10. 決策

```text
DEV 2015–2021：14 組 → 第 8 節選參 → freeze.json commit
      ↓
Validation 2022–2024：Composite 一次
├─ FAIL → STOP，Mom20 維持（Test 不跑）
└─ PASS → Test 2025–2026/09 一次
          ├─ FAIL → STOP，Mom20 維持
          └─ PASS → Prospective shadow → 全部 P0 重跑 → 才考慮升 production
```

任一 FAIL 即不調參、不重跑、不換權重；H1、H2、H3 再好亦不能單獨升級。Shadow 每日與 Mom20 並行模擬 D-Plan、不交易，記 signal、排名、持股、換手、成本、後續報酬、Active Share repair 次數；只驗 pipeline／資料穩定，非證明 alpha。

升 production 前重跑全部 P0：單元、因果、Active Share、fallback、40 × 24D replay、Production == Backtest、D-Plan 一致性。

## 11. 執行順序

1. Commit 本文件。
2. 下載股數。
	- 依第 4 節判定 H2。
	- 不看報酬。
3. 實作 `momv2/`。
	- 含訊號、策略、評估。
	- 執行單元／因果測試。
	- 改未來不得影響分數。
4. DEV：14 組＋Mom20，選參，`freeze.json` commit。
5. Validation 一次。
6. PASS 才 Test 一次。
7. STOP 或進 shadow。

## 12. DEV 凍結結果

完整表格：[research/results/momentum_v2/summary.md](../results/momentum_v2/summary.md)，凍結檔 `research/results/momentum_v2/freeze.json`。

| Component | 選出 | DEV 平均 Δ | 預設值的 DEV 平均 Δ |
|---|---|---:|---:|
| H1 | N = 60，B = 120 | +0.97% | −0.08%（20／60） |
| H2 | N = 20（預設，沒有設定合格） | −1.07% | −1.07% |
| H3 | `yoy` | +1.07% | +0.67%（`yoy+Δyoy`） |

```text
Composite = 平均( rank(Mom20), rank(resid 60／β 120), rank(−換手率 20), rank(營收 yoy) )
```

H2 三組 DEV 的 95% CI 均低於 0，假設不成立；但第 4、6 節預定只按覆蓋率保留（97.5% ≥95%），不能事後刪除。Composite 維持四項等權，由 Validation 判定。H1 為 60 日 residual；未扣 beta 的 Mom60 曾在 2025–2026 final test 輸 Mom20。

## 13. Validation：STOP

Composite 2022–2024：平均 Δ +0.23% < +0.5%，中位數 +1.17%，成本／失格通過，故 **STOP，Mom20 維持**。依第 10 節不跑 Test、不換權重、不刪 H2、不採單一 component。

診斷：H1 +2.02%、H3 +0.58%、H2 −1.06%；完整表格見上方 summary 連結。

## 14. 修訂紀錄

日期、修改、原因、當時是否看過結果：均 —。
