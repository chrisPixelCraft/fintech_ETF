# 策略：K2 錦標賽版與結果

2026-10-24 起正式策略改為 K2，並在後半段依排行榜切換到 T2。Mom20 保留為回滾設定 `production/strategy_mom20.json`。

2026-10-24 接入 DataPack／D-Plan v4.3。AS 官方算法未確認，live 會阻擋；見 [規則核對](rules_v43.md) 與 [測試報告](../research/results/rules_v43_validation/summary.md)。

## 正式策略：K2＋排行榜切換

設定檔：[production/strategy.json](../production/strategy.json)；程式：[production/tournament.py](../production/tournament.py)。

| 層 | K2（預設） | T2（切換後） |
|---|---|---|
| 選股 | • 投信10日買超 >0<br>• 3月均營收創12月新高<br>• 營收 YoY >20%<br>• 依投信買超／均量排序 | • 大型股前40名<br>• 依投信20日買超排序 |
| 權重 | • 前10檔各 8.5%<br>• 第11–22檔各約 0.83%<br>• 多留2檔緩衝 | • 台積電 23.5%<br>• 其餘24檔等權 |
| 調整 | • 新月營收後重篩<br>• 換手 ≥10% 才換 | • 建倉後持有 |
| 共用 | • 投入 95%<br>• 超限才修正<br>• 最後3日不交易 | 同左 |

- 切換條件
	- 第13個交易日起
	- 前晚排名在組內前6
	- 切換後鎖定，不再換回
- 切換分天執行
	- 每天做到下單模組允許的最大步
	- 重播中2天換完
- 排名由 `--rank` 輸入
	- 缺排名就維持原模式

### 選它的依據

- 比賽取各組前6
	- 目標是進前6的機率
- 276 窗口（2015–2026）K2 對 0050
	- 平均 +1.51pp，勝率 62%
	- 大贏率 22.1%，大輸率 10.1%
	- 尾端差 +12.0pp，四者最高
- 事前規則未全過
	- 大贏率差門檻 2.5pp
	- 使用者決定採用
- 證據：[conc_tournament](../research/results/conc_tournament/CODEX_REPORT.md)、[近5期第三批](../research/results/recent5_v3/CODEX_REPORT.md)

- 22檔緩衝（2026-10-25 加入）
	- 避免一檔買不到就少於20檔
	- 各期尾端差幾乎不變
	- 舊20檔設定：`production/strategy_k2_20.json`
	- 證據：[k3_weights](../research/results/k3_weights/CODEX_REPORT.md)
- 加權版 K3w 未採用
	- 2022–2024 驗證通過
	- 最近兩期各輸 0050 約10pp
	- 使用者決定維持 K2

### 驗證

- 22檔版測試 101/101 通過
	- W4、W5 重播與回測一致
	- 見 [CODEX_K2B_REPORT.md](../research/results/k3_weights/CODEX_K2B_REPORT.md)
- 20檔版測試 100/100 通過
- W4、W5 production 重播與回測一致
	- 報酬差 0，HOLD 日相同
- 10/26 離線試跑：K2、20筆委託、D-Plan 有效
- 見 [CODEX_PRODUCTION_REPORT.md](../research/results/conc_tournament/CODEX_PRODUCTION_REPORT.md)

### 限制

- 股票池為2026名單
	- 集中版受存活偏誤最大
- 2025–2026 為已用過的 holdout
- 分天切換沒有歷史回測
- 營收公布日假設11日

## 舊正式策略：Mom20 定義

設定檔：[production/strategy.json](../production/strategy.json)。

| 層 | 設定 |
|---|---|
| 分數 | • 過去 20 天 log 報酬<br>  ↳ 已校正除權息與分割 |
| 選股 | • 前 25 檔等權 |
| 投入 | • 目標 95% NAV |
| 續抱 | • 排名前 35 就不賣 |
| 換手 | • 換手 <10% 不調整 |
| 收尾 | • 最後 3 天不交易 |

- 程式在 `research/baselines.py`
- 組合層在 `competition/portfolio.py`
- 每天只滾動資料
  - 參數不再調整
  - 見 [production_spec.md](production_spec.md) 第 1 節

## 下單與結算

程式在 `competition/`。

- planner 換算整張股數
  - 假設買在漲停價
  - 現金不會變負
- ledger 用官方均價結算
  - 沒有時用 HLC3
  - 不捏造價格
- HLC3 誤差最小
  - 見 [execution_proxy_calibration.md](../research/execution_proxy_calibration.md)
- 當天違規就整天作廢
  - 並記 1 次警告

## 防止偷看未來

- 決策只看 D−1 以前
- 不讀 `adj_close`
  - 它含未來股利
- 因果測試竄改未來資料
  - 決策必須完全不變
  - `tests/test_causality.py`
- holdout 要加旗標
  - `--i-understand-holdout`
  - 記在 `research/holdout_access_log.jsonl`

## 資料切分

| 切分 | 期間 | 用途 |
|---|---|---|
| dev | 2010–2021 | • 試方法 |
| validation | 2022–2024 | • 選定後確認 |
| holdout | 2025–2026-09 | • 最後測試<br>• 已用過 |

- 切分定義在 `competition/episodes.py`
- 150 檔是 2026 名單
  - 越早年份偏誤越大
- 設定資料起點：config 的 `data.start`

## 結果

### 最終測試：2025-01 ～ 2026-09

- 40 個 24 日窗口
  - 每月月初、月中各一
- 資料從 2019 年起
- 共用同一套組合層

| 設定 | 平均 | 中位數 | 對 Mom20 | 95% 區間 | 勝率 |
|---|---:|---:|---:|---:|---:|
| **Mom20** | **+8.79%** | **+10.99%** | — | — | — |
| 10 日動能 | +5.97% | +8.79% | −2.82% | −4.40% ~ −1.25% | 28% |
| 60 日動能 | +7.02% | +6.90% | −1.78% | −3.27% ~ −0.26% | 32% |
| AutoTS（預測 5 天） | +7.04% | +5.89% | −1.75% | −3.05% ~ −0.46% | 35% |
| LightGBM 最佳 | +7.62% | +8.78% | −1.17% | −2.80% ~ +0.56% | 35% |

- Mom20 是預設設定
  - 不是測試期挑的
- 所有方法都贏 0050
  - 部分來自存活偏誤
- 2025-08-01 缺 28 檔價格
  - 多數組別記 1 次警告
  - 沒有組別失格
- 全部 21 組見 [final_test/summary.md](../research/results/final_test/summary.md)
- 逐窗口報酬見 [episode_returns.csv](../research/results/final_test/episode_returns.csv)

### 之後的研究：都 STOP

| 研究 | 期間 | 對 Mom20 | 結論 |
|---|---|---:|---|
| [P1 動能小實驗](../research/results/momentum_sweep/summary.md) | • dev 選 mom30<br>• validation 2022–2024 | +0.37% | • 未達 +0.5%<br>• Mom20 維持 |
| [Hybrid](../legacy/ml/results/hybrid/summary.md) | • 2015–2024<br>• 236 窗口 | +0.26% | • 未達 +0.5%<br>• 成本高於增益 |
| [LightGBM-v2 每日](../legacy/ml/results/lgbm_v2_daily/summary.md) | • 2015–2024<br>• 236 窗口 | +0.23% | • 未達 +0.5% |
| [LightGBM-v2 純測試](../legacy/ml/results/lgbm_v2_daily/pure_test.md) | • 2025–2026<br>• STOP 後才跑 | −1.69% | • 只報告 |

- 通過門檻都是平均 Δ > +0.5%
- 2025–2026 已經用過
  - 依它調整會高估

### 動能改良研究：都沒換掉 Mom20

2015–2026 共 276 個 24 日窗口，逐窗口和 Mom20 配對。規則都事先寫死；程式與 spec 已封存在 legacy。

| 研究 | 全期 Δ | 2025–2026 Δ | 結論 |
|---|---:|---:|---|
| [Momentum-v2](../legacy/ml/results/momentum_v2/summary.md) | — | — | • validation +0.23%<br>• STOP |
| [H1 residual 120 日](../legacy/ml/results/h1_residual/summary.md) | +0.84% | −2.15% | • test 反轉<br>• STOP |
| [Mom25](../legacy/ml/results/mom25_report/summary.md) | +0.16% | −0.29% | • 打平<br>  ↳ 三段 CI 都含 0 |
| [Residual 修正 ew20](../legacy/ml/results/residual_fixes/summary.md) | +0.06% | −0.31% | • 打平<br>• 只列 shadow 候選 |
| [技術指標 Composite](../legacy/ml/results/technical_momentum/summary.md) | −1.00% | −2.43% | • STOP |
| [多尺度 MACD](../legacy/ml/results/macd_momentum/summary.md) | −0.24% | −1.06% | • STOP |
| [台積電 22.5% 核心](../legacy/ml/results/tsmc_core/summary.md) | −0.08% | −1.52% | • STOP<br>• 警告 11 次 |

- Momentum-v2 只跑到 validation
  - 2022–2024，70 窗口
  - 沒過 +0.5% 就停
- H1 的 validation 不乾淨
  - 看過才挑 H1
  - +1.78% 在 test 變 −2.15%
- [Residual 何時贏](../legacy/ml/results/residual_regimes/summary.md)
  - 7 種市場狀態分組
  - Spearman |ρ| ≤ 0.15
  - 只看不選
- [台積電為何幫助小](../legacy/ml/results/tsmc_core/analysis.md)
- 總表與圖
  - [main_table.md](../legacy/ml/results/main_table.md)
  - [main_figure.png](../legacy/ml/docs/figures/main_figure.png)
  - [全部實驗](../legacy/ml/results/all_experiments.md)

### Production 一致性

- production 與回測逐日一致
  - 40 窗口、960 天
  - NAV 差 0 元
- 見 [production_replay](../research/results/production_replay/summary_holdout.md)
