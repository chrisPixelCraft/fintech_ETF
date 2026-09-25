# ML 研究封存：AutoTS、LightGBM、Hybrid

這裡放 2026-09 試過的機器學習選股方法。每一輪都和 20 日動能（Mom20）逐窗口配對比較，沒有一個穩定贏過它，所以正式提交仍用 Mom20。

程式只搬了位置、沒改 import，要在提交 `05745bf` 才能跑（見「[怎麼重跑](#怎麼重跑)」）。

## 先看這裡

- 所有方法都輸 Mom20
  - 2025–2026 最終測試
  - LightGBM 最佳 −1.17%
  - AutoTS −1.75%
- 最後兩輪也 STOP
  - Hybrid、每日 LightGBM-v2
  - 都沒達 +0.5% 門檻
- 資料已被重複使用
  - 2025–2026 用過多次
  - 2015–2024 用過兩次
  - 再依它調整會高估
- Mom20 的主要證據在根目錄
  - [final_test](../../research/results/final_test/summary.md)
  - [momentum_sweep](../../research/results/momentum_sweep/summary.md)
  - [production_replay](../../research/results/production_replay/)

## 各輪研究

「Δ」是對 Mom20 的配對平均差。每一輪只改一件事。

### AutoTS

| 研究 | 問題 | 結果 | 連結 |
|---|---|---|---|
| AutoTS 基準 | • 預測相對強弱<br>• 每窗口重選模型 | • dev 70 窗口 Δ −0.41%<br>• 最終測試 Δ −1.75% | [spec](docs/autots_first_etf_spec.md)、[內部說明](docs/autots_internals.md) |
| 調參 `quick` | • 小規模參數搜尋<br>  ↳ 7 組設定 | • 2010–2024 超額 −0.01%<br>• 2025–2026 超額 +0.65%<br>  ↳ 只有 4 窗口 | [tune_quick](results/tune_quick/summary.md) |

- `normal`、`crazy` 規模沒跑完
- 調參程式是 `studies/tune.py`
  - 啟動腳本 `studies/finetune.sh`

### LightGBM：dev 階段

| 研究 | 改了什麼 | 結果 | 結論 |
|---|---|---|---|
| [JPX #2 基準](results/lgbm_jpx2/summary.md)（[spec](docs/jpx%232_spec.md)） | • 照 JPX 第 2 名做 | • Δ −0.25%<br>  ↳ 95% 區間 −1.08% ~ +0.64% | • STOP |
| [相對強弱標籤](results/lgbm_alpha/summary.md)（[spec](docs/jpx%235_alpha_spec.md)） | • 標籤減市場平均 | • Δ −0.29%<br>• 對原版 −0.04% | • STOP |
| [成交對齊標籤](results/target_alignment/summary.md)（[spec](docs/jpx%235_target_align_spec.md)） | • 隔天成交價進場<br>  ↳ 用 HLC3 代替 | • Δ −0.53% | • 標籤不是瓶頸 |
| [學習率診斷](results/lr_diagnostic/) | • 學習率 0.1 ～ 0.0005 | • OOS rank IC 0.023–0.032 | • 學習率不是瓶頸 |
| [資料使用](results/data_usage/summary.md) | • 資料從 2019 起<br>• 最近 20% 也訓練（C） | • C 對對照組 +0.90%<br>  ↳ 95% 區間 +0.13% ~ +1.68% | • 採用 C |
| [樹數校準](results/tree_calibration/trees.json) | • 替 D 固定樹數<br>  ↳ 只用 2025 年前 | • 0.005 → 246 棵<br>• 0.01 → 116 棵 | • 供最終測試用 |

- 前三輪用 dev 70 窗口
  - 2019–2021，月初＋月中
- 資料使用用 dev 46 窗口
  - 2020–2021
- 這些都沒跑 validation
  - dev 門檻就沒過

### 最終測試：2025-01 ～ 2026-09

設定在 [final_test_plan.md](docs/final_test_plan.md)，程式是 `studies/final_test.py`。結果留在根目錄的 [final_test](../../research/results/final_test/summary.md)。

- 40 個 24 日窗口
- 5 種方法，共 21 組
- Mom20 平均 +8.79%
- LightGBM 最佳 −1.17%
  - D，學習率 0.01
  - 95% 區間 −2.80% ~ +0.56%
- AutoTS Δ −1.75%

### 動能變體（momentum sweep）

程式是 `studies/momentum_sweep.py`，結果在根目錄 [momentum_sweep](../../research/results/momentum_sweep/summary.md)。

- dev 選出 mom30
  - 2019–2021，Δ +0.73%
- validation 沒過
  - 2022–2024，Δ +0.37%
  - 門檻要 > +0.5%
- Mom20 維持不變

### v2 特徵：Hybrid 與每日 LightGBM-v2

v2 特徵加入月營收與法人買賣超，定義在 [hybrid_spec.md](docs/hybrid_spec.md) 第 5 節。兩輪都用 2015–2024 的 236 個窗口，規則事先凍結、只跑一次。

| 研究 | 問題 | 結果 | 連結 |
|---|---|---|---|
| Hybrid | • 市場恐慌日才換排名<br>• 平常日就是 Mom20 | • v2A Δ +0.26%<br>• v2B Δ +0.25%<br>• 未達 +0.5%<br>• 多出成本 > 增益 | [summary](results/hybrid/summary.md)、[spec](docs/hybrid_spec.md) |
| 每日 LightGBM-v2 | • 每天都用 v2A 排名 | • Δ +0.23%<br>• 未達 +0.5% | [summary](results/lgbm_v2_daily/summary.md)、[spec](docs/lgbm_v2_daily_spec.md) |
| 每日 v2：2025–2026 | • STOP 後使用者要求加跑<br>  ↳ 只報告 | • Δ −1.69%<br>• 勝率 32% | [pure_test](results/lgbm_v2_daily/pure_test.md) |

- 恐慌日定義
  - 0050 近 20 日下跌
  - 且波動高於歷史中位數
- 2025–2026 加跑不改結論
  - 記在 spec 第 7 節

## 怎麼重跑

程式裡的 import 仍是搬移前的路徑（例如 `research.lgbm_jpx2`、`autots_strategy`），要回到提交 `05745bf` 執行：

```bash
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

在該提交裡的入口：

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

- 結果會寫到 `research/results/`
  - 該提交的路徑，不是這裡
- 中斷後重跑同一指令
  - 從完成的窗口接續
- 已凍結的評估別重跑
  - Hybrid、每日 v2 都只准一次

## 目錄

```text
legacy/ml/
├── autots_strategy/          ← AutoTS 策略（預測、分數、標籤）
├── lgbm_strategy/            ← LightGBM：特徵、標籤、訓練資料、模型
├── hybrid/                   ← v2 特徵、恐慌 gate、walk-forward、兩個評估入口
├── studies/                  ← 各輪研究入口、調參、最終測試、動能變體
├── configs/                  ← 實驗設定（AutoTS、各 LightGBM 標籤）
├── tests/                    ← 測試（含因果測試）
├── docs/                     ← 各輪 spec、最終測試計畫、infra 盤點
├── results/                  ← 各輪整理後的結果
├── third_party/autots/       ← AutoTS 1.0.4 原始碼（MIT，含修補 P1）
└── requirements-autots.txt   ← 這個時期的套件版本
```

- AutoTS 直接改 repo 內原始碼
  - 紀錄在 [UPSTREAM.md](third_party/autots/UPSTREAM.md)
- 修補 P1 修正一個靜默錯誤
  - 股票數非 100 倍數時
  - 原版丟掉第 101–150 檔
- 組合層 `portfolio.py` 仍在用
  - 已移到根目錄 `competition/`
