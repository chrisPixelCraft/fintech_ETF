# AutoTS 調參結果：`quick`

**COMPLETE；2025–2026 測試 PASS。**最佳 `c8f116731e`，設定：`research/results/tune_quick/best_config.json`。

- 2010–2024：平均 -0.16%、超額 -0.01%、勝率 50%（8 窗口）。
- Seed 穩定度：1 seed，-0.04% ± 0.00%；此設定不受 seed 影響。
- 2025–2026：平均 +8.81%、超額 +0.65%、勝率 50%（4 窗口）。

2026-09-24 14:18 UTC 開始，14:30 UTC 更新，累計 0.19 小時。環境：profile `quick`、10 workers、commit `70ad45a1`（未提交修改）、Python 3.12.10、Darwin arm64。

挑參資料：2010–2024（dev 143 + validation 35）；測試：2025–2026/9（holdout 20）。共試 7 組；每個 24 日窗口對 momentum_20d 的平均／中位數超額各半，任一失格即 -inf。

## 簡單基準

| 基準 | 2010–2024 平均報酬 | 2025–2026 平均報酬 |
|---|---:|---:|
| momentum_20d | +1.39% | +7.79% |
| largecap_basket | +1.27% | +6.84% |
| autots_lastvalue_naive | +1.08% | +5.33% |

## 2025–2026 測試（前幾名）

| 設定 | 分數 | 平均報酬 | 中位數報酬 | 勝過動能 | 平均回撤 | 窗口數 |
|---|---:|---:|---:|---:|---:|---:|
| `c8f116731e` | +0.53% | +8.81% | +8.98% | 50% | 7.93% | 4 |

## 2010–2024 全部窗口排行

| 設定 | 分數 | 平均報酬 | 中位數報酬 | 勝過動能 | 平均回撤 | 窗口數 |
|---|---:|---:|---:|---:|---:|---:|
| `c8f116731e` | -0.04% | -0.16% | -0.13% | 50% | 6.78% | 8 |
| `51c5da8982` | -1.39% | -2.00% | -0.88% | 38% | 6.18% | 8 |

## 粗篩排行（20 個窗口）

| 設定 | 分數 | 平均報酬 | 中位數報酬 | 勝過動能 | 平均回撤 | 窗口數 |
|---|---:|---:|---:|---:|---:|---:|
| `c8f116731e` | -0.05% | -2.01% | -3.39% | 50% | 7.57% | 4 |
| `51c5da8982` | -1.48% | -3.63% | -4.30% | 50% | 7.37% | 4 |
| `67baa7d351` | -1.59% | -3.50% | -4.16% | 25% | 6.81% | 4 |
| `0c9b31737d` | -1.69% | -3.85% | -4.00% | 50% | 7.00% | 4 |
| `6160849efa` | -1.91% | -3.63% | -5.10% | 25% | 7.52% | 4 |
| `58c6b8841f` | -3.53% | -5.25% | -6.93% | 25% | 8.93% | 4 |

## 各設定參數

| 設定 | 主要參數 |
|---|---|
| `c8f116731e` | target=relative_log_price/0050, horizon=10, lookback=240, template=stat, mode=fixed_template, score=rank_blend, n_holdings=20, weighting=score |
| `51c5da8982` | target=relative_log_price/ew, horizon=5, lookback=240, template=full, mode=fixed_template, score=z, n_holdings=25, weighting=equal |
| `67baa7d351` | target=relative_log_price/0050, horizon=10, lookback=240, template=window_regression, mode=fixed_template, score=rank_blend, n_holdings=20, weighting=score |
| `0c9b31737d` | target=relative_log_price/ew, horizon=5, lookback=240, template=full, mode=fixed_template, score=z, n_holdings=25, weighting=equal |
| `6160849efa` | target=log_price/ew, horizon=20, lookback=180, template=full, mode=fixed_template, score=mu, n_holdings=25, weighting=inverse_vol |
| `58c6b8841f` | target=relative_log_price/ew, horizon=5, lookback=180, template=window_regression, mode=fixed_template, score=z, n_holdings=20, weighting=equal |

## 最佳參數

```json
{
 "cap_scale": 0.9,
 "freeze_last_days": 0,
 "horizon": 10,
 "invested": 0.95,
 "keep_extra": 15,
 "lookback": 240,
 "max_generations": 1,
 "metric": "topk_spread",
 "mode": "fixed_template",
 "n_holdings": 20,
 "predict_every": 3,
 "rebalance_threshold": 0.15,
 "refit_every": 12,
 "score": "rank_blend",
 "seed": 2026,
 "target": "relative_log_price/0050",
 "template": "stat",
 "validation_step": 10,
 "validation_windows": 3,
 "vol_window": 60,
 "weighting": "score"
}
```

## 注意

7 組選最佳使 2010–2024 分數偏樂觀；2025–2026 較客觀，看完不得回調。2024 以前用高低收平均代理價，之後才用官方均價。150 檔回推 2009 有存活偏誤：絕對報酬偏高，策略比較影響較小。
