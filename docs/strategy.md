# 策略：Mom20 與結果

正式策略是 20 日動能（Mom20），已凍結。之後三個研究都沒贏它。

## Mom20 定義

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

### Production 一致性

- production 與回測逐日一致
  - 40 窗口、960 天
  - NAV 差 0 元
- 見 [production_replay](../research/results/production_replay/summary_holdout.md)
