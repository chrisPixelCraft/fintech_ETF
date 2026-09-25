# Momentum-v2 spec：三個新資訊來源 ＋ 一個固定 Composite

一次回答：把 market-residual 強勢、低換手、月營收動能加到 Mom20 上的固定 Composite，是否比 Mom20 好？

- Production 完全不動，仍是 Mom20；只在分支 `claude/momentum-v2` 研究
- 每條規則在看到任何報酬前寫死於本文件
- DEV 調參一次、Validation 一次、Test 一次；任何一道 FAIL 就停止
- 凍結日期：2026-09-25

## 0. 範圍與既有結論

- 與 `claude/lgbm-v2-daily`（每日 LightGBM-v2）平行但互相獨立
  - 不修改 `hybrid/` 的程式，只 import
  - 該研究的結果不改變本文件任何規則
- 已結案、不重跑：
  - Mom 的 lookback 15/20/25/30D、skip 1/3/5D、multi-horizon、risk-adjusted
  - near-high、volume-trend、up-ratio
  - relative-return target、LightGBM 學習率
- Hybrid／LightGBM／crash detector 延後，只有 Composite 證明有新資訊才回來做

## 1. 結構

```text
每個決策日 D：D−1 收盤以前的資料 → 排名分數 → 共用組合層、Active Share、planner、D-Plan
```

- 可排名的股票與 Mom20 相同：近 20 日每天都可交易（`_eligible(view, 20)`）
- 組合層與 production 相同（`production/strategy.json`）
  - 前 25 檔等權，投入 95%
  - 續抱區 35 名，換手門檻 10%
  - 最後 3 天不交易
- 所有策略在同一組窗口、同一個可排名集合上和 Mom20 逐窗口配對

## 2. 共用記號

- `lr`：個股日對數報酬 `log(1 + action_neutral_return)`，被標記的日子為空值
- `lm`：0050 的日對數報酬
- `rank(x)`：當日可排名股票間的百分位排名（`pct=True`），越大越好；空值給 0.5
- 視窗內的統計量至少要 80% 的日子有值，否則為空值
- `Mom20`：近 20 日 `Σ lr`（與 production 完全相同）

## 3. 三個 hypothesis（只當診斷，不能單獨升級）

### H1：Market-residual momentum

```text
β      = cov(lr, lm) / var(lm)          近 B 日，兩者皆有值的日子
resid  = Σ (lr − β × lm)                近 N 日
H1 分數 = rank(resid)
```

- 只扣 0050 的 beta，名稱是「market-residual」，不宣稱是多因子 residual
- 診斷版本：直接用 `rank(resid)` 取 Top25

### H2：低換手的持續型贏家

```text
換手率  = 近 N 日平均成交量（可交易日） ÷ 發行股數(D−1)
低換手  = rank(−換手率)
H2 分數 = 平均( rank(Mom20), 低換手 )
```

- 比較的是跨股票的換手率水準，不是個股和自己過去比（那是已結案的 volume-trend）
- 方向固定：低換手較好

### H3：月營收動能

沿用 `hybrid.features.revenue_features`（與 Hybrid spec 第 4、5 節相同）：

- 月份 m 的營收在次月 11 日（含）收盤後才可用，D 只能用 D−1 以前已公布的月份
- `yoy` = 當月營收 ÷ 報表上的去年同月 − 1（去年同月 ≤ 0 為空值）
- `Δyoy` = 本月 `yoy` − 上月 `yoy`（兩個月必須相連）
- `record` = 當月營收 ≥ 前 12 個月最高者為 1，否則 0

```text
營收分數 = 依參數 S（第 5 節）取 rank 平均
H3 分數  = 平均( rank(Mom20), rank(營收分數) )
```

- MoM 不用：受農曆年等季節性影響太大

## 4. 資料與可用時間

| 資料 | 來源 | 可用時間 |
|---|---|---|
| 價格、成交量 | 既有 market panel（`competition.data`） | 當日收盤後 |
| 0050 報酬 | 同上 | 當日收盤後 |
| 月營收（2013-01 起） | 公開資訊觀測站 `t21sc03`（`data/hybrid/revenue.csv`） | 次月 11 日（含）收盤後 |
| 發行股數（2014-01 起） | TWSE `MI_QFIIS`、TPEx `qfii` 的「發行股數」 | 報表日收盤後 |

**發行股數**
- 每月第一個交易日各抓一次快照（`momv2/sources.py`），每筆保留原始回應的 sha256
- 日期 t 使用「快照日 ≤ t」中最新的一筆
- 再乘上快照日之後、t 以前（含）所有股本變動的倍數（market panel 的 `split`：配股、減資、分割）
- 配股新股通常晚於除權日掛牌，因此少數日子發行股數會偏差幾個百分點；只影響跨股票排名的細微位置
- 某股票沒有快照時換手率為空值，排名給 0.5

**H2 保留條件（看任何報酬前判定）**
- 2015-01 到 2026-09 間，每個有價格的（股票, 月份）中，≥ 95% 有可用的發行股數
- 不滿足就取消 H2，不用任何 proxy；Composite 改為第 6 節的三項版本

**月營收的已知限制（揭露）**
- 沒有每家公司真正的公布時間，只用「次月 11 日」規則；早公布的公司被延後使用，不會偷看
- 月報表可能顯示更正後的數字：比對「m 月報表的當月營收」與「m+12 月報表上的去年同月」，約 3.7% 的配對差距 > 0.1%、0.75% 差距 > 5%；「上月營收」比對則完全一致
- 這代表跨年度的比較基準偶有調整；使用的 YoY 分母取自同一張報表，與當時公布的內容一致

## 5. 參數

### 可以調（只在 DEV 上調，每個 component 用自己的診斷版本獨立調）

| 策略 | 參數 | 網格 | 預設值 |
|---|---|---|---|
| H1 | residual 累積天數 N | 20 / 25 / 40 / 60 | 20 |
| H1 | β 估計天數 B | 60 / 120 | 60 |
| H2 | 換手率平均天數 N | 20 / 25 / 60 | 20 |
| H3 | 營收分數組成 S | `yoy`／`yoy+Δyoy`／`yoy+Δyoy+record` | `yoy+Δyoy` |

- H1 8 組、H2 3 組、H3 3 組，共 14 組，另加 Mom20 基準
- 不做聯合搜尋

### 固定

| 參數 | 值 |
|---|---|
| Composite 權重 | 等權 |
| Composite 裡的 Mom 項 | Mom20（Mom25 已結案） |
| 組合層 | 與 production 相同 |
| 市場基準 | 0050 |
| 缺值 | 排名給 0.5 |
| 視窗有效比例 | ≥ 80% |
| H2 方向 | 低換手較好 |

### 絕對不能動

- PIT 規則、成本模型、窗口定義、配對方式、通過條件

## 6. Composite：唯一可以升級的策略

```text
Score = 平均( rank(Mom20), rank(resid), rank(−換手率), rank(營收分數) )
```

- 每個 component 使用 DEV 選出的參數
- H2 被取消時：`Score = 平均( rank(Mom20), rank(resid), rank(營收分數) )`

## 7. 期間

| 期間 | 角色 | 次數 |
|---|---|---|
| 2014 | 暖身（β、換手率、營收 YoY 的歷史） | — |
| 2015–2021（DEV） | 只用來調參 | 14 組各跑一次 |
| 2022–2024（Validation） | 第一道 gate | Composite 一次 |
| 2025-01 到 2026-09（Test） | 第二道 gate，決定性證據 | 一次，Validation PASS 才跑 |

- 市場資料從 2014-01-01 起（config `data.start`）
- 窗口：24 交易日，每月月初、月中起點；跨切分的窗口依既有規則排除
- 每個階段都公開 H1、H2、H3、Composite 四個結果
- 150 檔是 2026 名單，早年存活偏誤較大；所有策略同一股票池，配對比較受影響較小

**揭露**
- 2022–2024 已被 Mom20 P1、Hybrid 打開過，這是 retrospective evidence，不是乾淨的 OOS
- 2015–2024 已被 Hybrid 用過；LightGBM-v2 用過 `resid_mom60` 與 `rev_*`（只在 panic 日）
- 當初選 Mom20 有參考 2025–2026 的 final test；但 Composite、H1、H2、H3 從未在該段評估

## 8. DEV 選參規則

對每個 component，在 DEV 上和 Mom20 逐窗口配對：

1. 合格：配對中位數 Δ ≥ 0、周轉 ≤ 1.5 倍 Mom20、失格率不高於 Mom20
2. 預設值優先：合格且平均 Δ 比預設值高出 **0.2% 以上** 的設定才能取代預設值
3. 多個符合時取平均 Δ 最高者；完全相同時取網格順序在前者（較短窗口、較簡單）
4. 沒有符合者就用預設值（即使預設值本身不合格，Composite 仍由 gate 判定）

- 選出的參數寫入 `research/results/momentum_v2/freeze.json` 並 commit，之後才能跑 Validation
- 看完 Validation 不能回頭改參數

## 9. 通過條件（只套用在 Composite）

`Δ = R(Composite) − R(Mom20)`，逐窗口配對。

**第一道：Validation 2022–2024**

| 條件 | 門檻 |
|---|---|
| 配對平均 Δ | > +0.5% |
| 配對中位數 Δ | ≥ 0 |
| 失格次數 | 不多於 Mom20 |
| 多出來的成本 | < 平均 Δ |

**第二道：Test 2025-01 到 2026-09**

| 條件 | 門檻 |
|---|---|
| 配對平均 Δ | > 0 |
| 配對中位數 Δ | ≥ 0 |
| 失格次數 | 不多於 Mom20 |
| 多出來的成本 | < 平均 Δ |

- Test 窗口約 38 個，門檻只要求優勢沒有消失或反轉
- DEV 上 Composite 的表現、2015–2018／2019–2021 分段只報告，不當 gate

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

- 任何一道 FAIL 後不再調、不再重跑、不換權重
- H1、H2、H3 單獨表現再好，也不能觸發升級
- Shadow：每天和 Mom20 並行產生模擬 D-Plan，不實際交易；記錄 signal、排名、持股、換手、成本、後續報酬、Active Share repair 次數；目的是檢查 pipeline 與資料穩定性，不是證明 alpha
- 升 production 必須重新通過全部 P0：單元測試、因果測試、Active Share、fallback、40 × 24D replay、Production == Backtest、D-Plan 一致性

## 11. 執行順序

1. 本文件 commit
2. 發行股數下載與 H2 保留判定（第 4 節），不看任何報酬
3. 實作 `momv2/`：訊號、策略、評估；單元測試與因果測試（竄改未來資料，分數不變）
4. DEV：14 組 ＋ Mom20，選參，`freeze.json` commit
5. Validation 一次
6. PASS 才跑 Test 一次
7. 依結果 STOP 或進 shadow

## 12. DEV 選參結果（凍結於 Validation 之前）

完整表格：[research/results/momentum_v2/summary.md](../research/results/momentum_v2/summary.md)，凍結檔 `research/results/momentum_v2/freeze.json`。

| Component | 選出 | DEV 平均 Δ | 預設值的 DEV 平均 Δ |
|---|---|---:|---:|
| H1 | N = 60，B = 120 | +0.97% | −0.08%（20／60） |
| H2 | N = 20（預設，沒有設定合格） | −1.07% | −1.07% |
| H3 | `yoy` | +1.07% | +0.67%（`yoy+Δyoy`） |

```text
Composite = 平均( rank(Mom20), rank(resid 60／β 120), rank(−換手率 20), rank(營收 yoy) )
```

- H2 在 DEV 上三組都明顯為負（95% CI 都在 0 以下），假設在 DEV 上不成立
- 但第 4、6 節事先規定 H2 保留與否只看資料覆蓋率（97.5% ≥ 95%）；看到結果後拿掉 H2 就是事後挑選，因此 Composite 維持四項等權，由 Validation gate 判定
- H1 選到 60 日 residual，比 Mom20 長；Mom60（未扣 beta）在 2025–2026 final test 輸給 Mom20，揭露備查

## 13. Validation 結果：STOP

- Composite 2022–2024：配對平均 Δ +0.23%（< +0.5%），中位數 Δ +1.17%，成本與失格通過 → **STOP，Mom20 維持**
- 依第 10 節：Test 不跑；不換權重、不拿掉 H2、不改用單一 component
- 診斷（只報告）：H1 +2.02%、H3 +0.58%、H2 −1.06%
- 完整表格：[research/results/momentum_v2/summary.md](../research/results/momentum_v2/summary.md)

## 14. 修訂紀錄

| 日期 | 修改 | 原因 | 當時是否看過結果 |
|---|---|---|---|
| — | — | — | — |
