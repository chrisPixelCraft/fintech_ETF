# AI CUP Trading Agent v2｜四策略平行實驗規格

更新：2026-09-21  
狀態：`CANDIDATE`；完成前瞻測試前不可宣稱有效。

## 0. 核心決策

v2 每日用同一份時間點資料，同時產生四個虛擬組合：

| 版本 | 唯一新增層 | 要回答的問題 |
|---|---|---|
| A | 個股動能 | 價格訊號能做到多少？ |
| B | 板塊門控 | 先選板塊是否增加價值？ |
| C | 隔夜訊號 | 08:55 前的新資訊是否有用？ |
| D | Regime＋多訊號 | 完整系統是否更穩、更賺？ |

四版共用資料快照、成交模型、費稅、配置、換手控制與合規檢查。測試期四版皆為 paper portfolio；正式比賽只允許一版為 `LIVE`，其餘為 `SHADOW`。Agent 不得每天事後挑選表現最好的版本；Meta-Agent 留到 v3。

```text
資料快照 → A/B/C/D → 組合建構 → 風控 → 合規 → 比較報告
```

## 1. 四個策略

### A｜Momentum Baseline

- 分數：20D／50D 報酬、量能、MACD、20／50 EMA、100／200 EMA、4H 確認。
- 150 檔內排序，選 20–30 檔。
- 用途：控制組，不讀板塊、隔夜、財報或法人資訊。

### B｜Sector Gate＋Momentum

- 先以官方產業分類計算板塊 20D／50D 動能。
- 新買股票只來自合格板塊；所有合格個股再用 A 的分數共同排序。
- `sector_score` 只作 gate，不再加進個股分數，避免價格動能重複計權。
- 板塊掉出候選不直接賣股；仍依個股退出條件與替換門檻處理。

### C｜Overnight＋Sector＋Momentum

- 在 B 上加入截止前可知的 NASDAQ、SOX、NVIDIA、TSM ADR、台指夜盤、USD/TWD。
- 隔夜訊號只調整板塊 gate／ranking，不直接產生交易。
- 每項資料必須記錄 `available_at`；08:55 後資訊禁止進入當日決策。

### D｜Regime-Aware Multi-Signal

- 先分類：`BULL_TREND`、`RANGE`、`HIGH_VOL`、`PANIC_REBOUND`、`EVENT`。
- 再組合 Price、Sector、Overnight、Earnings、Institutional Flow、Risk。
- Regime 僅切換事前固定的權重表，不得由 LLM 臨場任意改規則。
- D 一次新增多個元件，屬產品候選，不是乾淨單變因實驗；各子分數仍須完整記錄，供後續 ablation。

## 2. 共用引擎

### 2.1 Point-in-time Data

每次執行先凍結：

```yaml
decision_date:
cutoff_at: 08:55 Asia/Taipei
known_at:
available_at:
source_url:
sha256:
taxonomy_version:
```

缺值只能依事前規則降級或沿用舊訊號，並標記 `STALE`／`UNKNOWN`；禁止用今日分類、修正版財報或收盤後資料回填歷史。

### 2.2 Portfolio／Turnover

- 四版使用同一套 position sizing 與 cash policy。
- Challenger 超過最弱持股 `replacement_margin` 才換股。
- 每日一般替換上限固定；退出、新增股才交易，保留股不全面重配。
- 早上無法知道當日成交均價，須用事前固定的保守價格界限與費稅預留計算股數；無法證明現金非負時縮單或標記 `INFEASIBLE_CASH`。

### 2.3 Rule Checker

送出前必須全部通過：

- 僅限官方 150 檔；持股 20–30 檔。
- 現金 `>= 0` 且 `< NAV × 25%`。
- 台積電 `<= NAV × 25%`；其餘單股 `<= NAV × 10%`。
- 僅現股、整張；不得融資、融券、借券、放空、當沖或零股。
- 手續費 0.1425%；賣出證交稅 0.3%。
- 對每檔指定主動式 ETF，前十大持股 Active Share 均不得低於 20%。
- 決策報告、交易書、實際 order 與 ledger 必須一致。

任一硬規則失敗即 `BLOCK_SUBMISSION`。規則因價格上漲造成被動超限時，另記五個交易日修正期限；不可與主動加碼超限混為一談。

## 3. 每日流程

1. `snapshot`：鎖定 08:55 前可知資料與版本。
2. `signal`：計算 regime、sector、stock 與各子分數。
3. `run`：A／B／C／D 各自產生 target portfolio。
4. `plan`：依實際持股、換手門檻與現金預算產生交易書。
5. `check`：送單前執行合規與資料完整性檢查。
6. `settle`：收盤後以官方當日成交均價、費稅及公司行動更新 ledger。
7. `report`：更新四條 NAV 曲線與比較報告。

週末及台股休市日不產生官方交易書，只做資料健康檢查。

## 4. 每日輸出

每版固定輸出：

- 持股、分數、排名、目標權重、買賣張數與理由。
- NAV、日報酬、MDD、換手、費用、稅、波動、板塊 HHI。
- `rule_status`、`data_status`、fallback、不可行原因。

總表：

| Metric | A | B | C | D |
|---|---:|---:|---:|---:|
| NAV／Return | | | | |
| MDD | | | | |
| Turnover／Cost | | | | |
| Sector HHI | | | | |
| Violations／Missing | | | | |

## 5. 24 日 Final Report

1. **Performance**：Final NAV、Total Return、MDD、Daily Volatility。
2. **Trading**：Turnover、Fee、Tax、Trade Count。
3. **Risk**：最大單股、最大板塊、HHI、最大回撤區間。
4. **Ablation**：A→B 看 Sector；B→C 看 Overnight；C→D 只視為 bundle。
5. **Failures**：資料延遲、fallback、不可行日、違規攔截與漏交。

選擇 `LIVE` 的順序：先要求零違規、資料與交易書完整，再看 Final NAV；NAV 相同依競賽規則看較低 MDD。單一 24 日結果只能作部署決策，不足以證明長期 alpha。

## 6. 程式結構與完成條件

```text
src/
├─ data_snapshot.py
├─ regime.py
├─ strategies/{a,b,c,d}.py
├─ portfolio.py
├─ execution.py
├─ risk.py
├─ rules.py
└─ report.py
```

單一指令：

```bash
python run_daily.py --date YYYY-MM-DD --mode paper
```

完成定義：一次執行必須留下 1 份不可變資料快照、4 份候選組合、4 份交易書、4 份合規結果、1 份比較報告；相同快照重跑必須得到相同結果。

## 7. 實作順序

1. 共用 ledger、成交、費稅、Rule Checker。
2. A 跑通端到端與重現測試。
3. B 加板塊資料與 gate。
4. C 加隔夜資料與時間戳。
5. D 加 regime、財報、法人與固定權重表。
6. 凍結 config，連續平行跑滿測試期，最後才選 `LIVE`。

## 8. 官方規則依據

依《AI CUP 2026 玉山人工智慧公開挑戰賽－Agent 基金經理人：打造你的最佳 ETF 投資組合》競賽辦法：初賽為 2026/10/26–11/27，共 24 個交易日；每日於前一日 19:30 至當日 08:55 提交。交易全數成交，成交價採當日成交均價；排名以最終 NAV 為主，同 NAV 時以較低 MDD 勝出。至少成功提交 22 日才列入排名，累積三次警告取消資格。正式提交格式仍須以競賽平台最新範例與公告為準。
