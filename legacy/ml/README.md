# ML 與動能改良研究封存

2026-09 機器學習選股與後續動能改良（`momv2/`）封存。各輪與 20 日動能 Mom20 逐窗配對，無方法穩定勝出，正式提交仍用 Mom20。

僅搬位置、未改 import；須回舊提交[重跑](#怎麼重跑)：

- ML 各輪：`05745bf`
- 動能改良（`momv2/`）：`6d2a7c4`

## 先看這裡

**維持 Mom20。** 2025–2026 最終測試 LightGBM 最佳 −1.17%、AutoTS −1.75%；Hybrid／每日 LightGBM-v2 未達 +0.5%，均 STOP。8 項動能改良見[下表](#動能改良momv2)。

- [main_table.md](results/main_table.md)：方法比較。
- [all_experiments.md](results/all_experiments.md)：全部設定。
- [main_figure.png](docs/figures/main_figure.png)：主圖。

2025–2026 已多次使用，2015–2024 已用兩次；再調整可能高估。Mom20 主證據：[final_test](../../research/results/final_test/summary.md)、[momentum_sweep](../../research/results/momentum_sweep/summary.md)、[production_replay](../../research/results/production_replay/)。

## 各輪研究

Δ = 對 Mom20 配對平均差；每輪只改一件事。

### AutoTS

| 研究 | 問題 | 結果 | 連結 |
|---|---|---|---|
| AutoTS 基準 | • 預測相對強弱<br>• 每窗口重選模型 | • dev 70 窗口 Δ −0.41%<br>• 最終測試 Δ −1.75% | [spec](docs/autots_first_etf_spec.md)、[內部說明](docs/autots_internals.md) |
| 調參 `quick` | • 小規模參數搜尋<br>  ↳ 7 組設定 | • 2010–2024 超額 −0.01%<br>• 2025–2026 超額 +0.65%<br>  ↳ 只有 4 窗口 | [tune_quick](results/tune_quick/summary.md) |

`normal`、`crazy` 未跑完。程式 `studies/tune.py`，入口 `studies/finetune.sh`。

### LightGBM：dev 階段

| 研究 | 改了什麼 | 結果 | 結論 |
|---|---|---|---|
| [JPX #2 基準](results/lgbm_jpx2/summary.md)（[spec](docs/jpx%232_spec.md)） | • 照 JPX 第 2 名做 | • Δ −0.25%<br>  ↳ 95% 區間 −1.08% ~ +0.64% | • STOP |
| [相對強弱標籤](results/lgbm_alpha/summary.md)（[spec](docs/jpx%235_alpha_spec.md)） | • 標籤減市場平均 | • Δ −0.29%<br>• 對原版 −0.04% | • STOP |
| [成交對齊標籤](results/target_alignment/summary.md)（[spec](docs/jpx%235_target_align_spec.md)） | • 隔天成交價進場<br>  ↳ 用 HLC3 代替 | • Δ −0.53% | • 標籤不是瓶頸 |
| [學習率診斷](results/lr_diagnostic/) | • 學習率 0.1 ～ 0.0005 | • OOS rank IC 0.023–0.032 | • 學習率不是瓶頸 |
| [資料使用](results/data_usage/summary.md) | • 資料從 2019 起<br>• 最近 20% 也訓練（C） | • C 對對照組 +0.90%<br>  ↳ 95% 區間 +0.13% ~ +1.68% | • 採用 C |
| [樹數校準](results/tree_calibration/trees.json) | • 替 D 固定樹數<br>  ↳ 只用 2025 年前 | • 0.005 → 246 棵<br>• 0.01 → 116 棵 | • 供最終測試用 |

前三輪：2019–2021 月初＋月中 70 dev 窗；資料使用：2020–2021，46 dev 窗。均未過 dev 門檻，未跑 validation。

### 最終測試：2025-01 ～ 2026-09

設定：[final_test_plan.md](docs/final_test_plan.md)；程式：`studies/final_test.py`；結果：[final_test](../../research/results/final_test/summary.md)。

40 個 24 日窗口，5 種方法、21 組；Mom20 +8.79%。LightGBM 最佳 D（學習率 0.01）：Δ −1.17%，95% 區間 −2.80% ~ +0.56%；AutoTS Δ −1.75%。

### 動能變體（momentum sweep）

程式 `studies/momentum_sweep.py`；結果 [momentum_sweep](../../research/results/momentum_sweep/summary.md)。

Dev 2019–2021 選 mom30（Δ +0.73%）；validation 2022–2024 +0.37%，未達 > +0.5%，維持 Mom20。

### v2 特徵：Hybrid 與每日 LightGBM-v2

v2 新增月營收／法人買賣超（[hybrid_spec.md](docs/hybrid_spec.md) 第 5 節）。兩輪各跑一次：2015–2024，236 窗，事前凍結規則。

| 研究 | 問題 | 結果 | 連結 |
|---|---|---|---|
| Hybrid | • 市場恐慌日才換排名<br>• 平常日就是 Mom20 | • v2A Δ +0.26%<br>• v2B Δ +0.25%<br>• 未達 +0.5%<br>• 多出成本 > 增益 | [summary](results/hybrid/summary.md)、[spec](docs/hybrid_spec.md) |
| 每日 LightGBM-v2 | • 每天都用 v2A 排名 | • Δ +0.23%<br>• 未達 +0.5% | [summary](results/lgbm_v2_daily/summary.md)、[spec](docs/lgbm_v2_daily_spec.md) |
| 每日 v2：2025–2026 | • STOP 後使用者要求加跑<br>  ↳ 只報告 | • Δ −1.69%<br>• 勝率 32% | [pure_test](results/lgbm_v2_daily/pure_test.md) |

恐慌日 = 0050 近 20 日跌且波動高於歷史中位數。2025–2026 加跑不改判定（spec 第 7 節）。

### 動能改良（momv2）

2015–2026，276 個 24 日窗，與 Mom20 配對；各 spec gate 事前固定、只跑一次。Δ：[main_table.md](results/main_table.md)。

| 研究 | 問題 | 結果 | 連結 |
|---|---|---|---|
| Momentum-v2 | • H1 residual＋H2 周轉＋H3 營收<br>• 合成分數能否贏 | • validation Δ +0.23%<br>• STOP | [summary](results/momentum_v2/summary.md)、[spec](docs/momentum_v2_spec.md) |
| H1 residual | • 只用市場殘差動能 | • validation +1.78%<br>  ↳ 看過才挑，不乾淨<br>• test Δ −2.15%<br>• STOP | [summary](results/h1_residual/summary.md)、[spec](docs/h1_residual_spec.md) |
| Mom25 | • 25 日比 20 日強嗎<br>• 只看不選 | • 全期 +0.16%<br>• 2025–2026 −0.29%<br>• 三段 CI 都含 0 | [summary](results/mom25_report/summary.md) |
| Residual 修正 | • 6 種 residual 修法 | • ew20 全期 +0.06%<br>• 只列 shadow 候選<br>• production 不換 | [summary](results/residual_fixes/summary.md)、[spec](docs/residual_fixes_spec.md) |
| Residual 何時贏 | • 7 種市場狀態分組 | • Spearman \|ρ\| ≤ 0.15<br>• 只看不選 | [summary](results/residual_regimes/summary.md) |
| 技術指標 | • 19 個指標重排前 40 | • 全期 Δ −1.00%<br>• 2025–2026 −2.43%<br>• STOP | [summary](results/technical_momentum/summary.md)、[spec](docs/technical_momentum_spec.md) |
| 多尺度 MACD | • 三尺度 MACD 重排前 40 | • 全期 Δ −0.24%<br>• 2025–2026 −1.06%<br>• STOP | [summary](results/macd_momentum/summary.md)、[spec](docs/macd_momentum_spec.md) |
| 台積電核心 | • 2330 固定 22.5%<br>• 其餘分數加權 | • 全期 Δ −0.08%<br>• 2025–2026 −1.52%<br>• STOP | [summary](results/tsmc_core/summary.md)、[spec](docs/tsmc_core_spec.md)、[analysis](results/tsmc_core/analysis.md) |

門檻平均 Δ > +0.5%，另查 CI／中位數等；期間全已見，僅歷史證據。

## 怎麼重跑

Import 沿用舊路徑（`research.lgbm_jpx2`、`autots_strategy`、`momv2`），須回舊提交：

| 提交 | 能跑什麼 | 缺什麼 |
|---|---|---|
| `05745bf` | • AutoTS、LightGBM<br>• Hybrid、每日 LightGBM-v2 | • 沒有 `momv2/` |
| `6d2a7c4` | • `momv2/` 動能改良 | • 沒有每日 LightGBM-v2 |

`05745bf` 範例（`6d2a7c4` 換提交即可）：

```bash
# main 沒有舊歷史；舊提交都在 tag archive/pre-cleanup
git fetch origin tag archive/pre-cleanup
git worktree add ../etf-05745bf 05745bf
cd ../etf-05745bf

# 環境：Python 3.12 + uv
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-autots.txt
echo "$PWD/third_party/autots" > .venv/lib/python3.12/site-packages/fintech_etf_third_party.pth
.venv/bin/python -c "import autots; print(autots.__version__)"   # 應顯示 1.0.4

# 測試
.venv/bin/python -m unittest discover -s tests -q
```

`05745bf` 裡的入口：

| 研究 | 指令 |
|---|---|
| JPX #2 基準 | `python -m research.lgbm_jpx2` |
| 相對強弱標籤 | `python -m research.lgbm_alpha` |
| 成交對齊標籤 | `python -m research.target_alignment` |
| 學習率診斷 | `python -m research.lr_diagnostic` |
| 資料使用 | `python -m research.data_usage` |
| 最終測試 | `bash research/final_test.sh` |
| AutoTS 調參 | `PROFILE=quick bash research/finetune.sh` |
| Hybrid | `python -m hybrid.evaluate` |
| 每日 LightGBM-v2 | `python -m hybrid.evaluate_daily` |

`6d2a7c4` 裡的入口（指令前加 `PYTHONHASHSEED=0`）：

| 研究 | 指令 |
|---|---|
| Momentum-v2 | `python -m momv2.evaluate {coverage,dev,validation,test}` |
| H1 residual | `python -m momv2.h1_evaluate {dev,validation,test}` |
| Residual 修正 | `python -m momv2.fixes_report` |
| Residual 何時贏 | `python -m momv2.regime_report` |
| 技術指標 | `python -m momv2.tech_evaluate` |
| MACD、台積電核心 | `python -m momv2.macd_tsmc_evaluate` |
| 台積電原因分析 | `python -m momv2.tsmc_analysis` |
| H2 發行股數資料 | `python -m momv2.sources {fetch,build}` |
| 主圖與 main_table | `python -m momv2.main_figure` |
| all_experiments | `python -m momv2.all_experiments` |

Mom25 無入口，由未 commit 暫存腳本產生；`9097068` 僅結果。評估指令可加 `--workers 8`；輸出至該提交的 `research/results/`。中斷後同指令可續跑。

凍結評估勿重跑：Hybrid／每日 v2 限一次，momv2 各 stage 拒絕覆寫。

## 目錄

```text
legacy/ml/
├── autots_strategy/          ← AutoTS 策略（預測、分數、標籤）
├── lgbm_strategy/            ← LightGBM：特徵、標籤、訓練資料、模型
├── hybrid/                   ← v2 特徵、恐慌 gate、walk-forward、兩個評估入口
├── momv2/                    ← 動能改良：訊號、技術指標、MACD、台積電核心、主圖與總表
├── studies/                  ← 各輪研究入口、調參、最終測試、動能變體
├── configs/                  ← 實驗設定（AutoTS、各 LightGBM 標籤）
├── tests/                    ← 測試（含因果測試、momv2、技術指標、MACD／台積電）
├── docs/                     ← 各輪 spec、最終測試計畫、infra 盤點、figures/main_figure.png
├── results/                  ← 各輪整理後的結果、main_table、all_experiments
├── third_party/autots/       ← AutoTS 1.0.4 原始碼（MIT，含修補 P1）
└── requirements-autots.txt   ← 這個時期的套件版本
```

AutoTS 修改 repo 原始碼，見 [UPSTREAM.md](third_party/autots/UPSTREAM.md)。P1 修正非 100 倍數股票池會靜默丟掉第 101–150 檔的錯誤。共用組合層 `portfolio.py` 已移至根目錄 `competition/`，仍在使用。
