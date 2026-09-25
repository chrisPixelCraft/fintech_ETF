# ETF 策略研究：150 檔、挑 25 檔、24 個交易日

每天從 150 檔股票挑出前 25 檔，在競賽規則內跑 24 個交易日，目標是期末 NAV 最大。

## 先看這裡

- 目前最好：20 日動能
  - 2025–2026 平均 +8.79%
  - AutoTS、LightGBM 都輸它
- 動能改良已結案
  - 6 項研究都沒贏
  - 見「[2. 動能改良研究](#2-動能改良研究)」
- 每日提交流程已完成
  - 入口 `./run_daily.sh`
  - 見 [docs/production_spec.md](docs/production_spec.md)
- 賽前設定已完成
  - `team_id`：`TEAM_11076`
  - 首個交易日：2026-10-26
  - 10/26 照常送 D-Plan
- 歷史資料全部用過
  - 2015–2026 都看過
  - 再調參會高估
- 跑程式看「[5. 怎麼跑](#5-怎麼跑)」
- 舊版本在 `legacy/`
  - 摘要見「[8. 舊版本](#8-舊版本)」

---

## 1. 目前結果

### 最終測試：2025-01 ～ 2026-09

設定寫在 [docs/final_test_plan.md](docs/final_test_plan.md)，結果在 [research/results/final_test/summary.md](research/results/final_test/summary.md)。

- 資料從 2019 年起
- 每 5 天滾動重訓
  - 用 2019 到前一天
- 40 個 24 日窗口
  - 每月月初、月中各一個
- 共用同一套組合
  - 前 25 檔等權，投入 95%

| 設定 | 平均報酬 | 中位數 | 對 20 日動能 | 95% 區間 | 勝率 |
|---|---:|---:|---:|---:|---:|
| **20 日動能** | **+8.79%** | **+10.99%** | — | — | — |
| 10 日動能 | +5.97% | +8.79% | −2.82% | −4.40% ~ −1.25% | 28% |
| 60 日動能 | +7.02% | +6.90% | −1.78% | −3.27% ~ −0.26% | 32% |
| AutoTS（預測 5 天） | +7.04% | +5.89% | −1.75% | −3.05% ~ −0.46% | 35% |
| LightGBM 最佳（D，學習率 0.01） | +7.62% | +8.78% | −1.17% | −2.80% ~ +0.56% | 35% |
| LightGBM 對照組（學習率 0.005） | +6.65% | +7.22% | −2.15% | −4.42% ~ +0.21% | 38% |

- 20 日動能是預設設定
  - 不是測試期挑的
- 學習率影響很小
  - 5 個學習率差 ≤0.5%
- LightGBM：D ≥ C > 對照組
  - 每個學習率都成立
- 所有方法都贏 0050
  - 部分來自存活偏誤
- 2025-08-01 缺 28 檔價格
  - 多數組別記 1 次警告
  - 沒有組別失格

完整 21 組和逐窗口報酬見 [summary.md](research/results/final_test/summary.md)，另有 [episode_returns.csv](research/results/final_test/episode_returns.csv)。

### LightGBM 研究經過

每一輪只改一件事，都和 20 日動能配對比較。

| 輪次 | 改了什麼 | 結果 | 結論 |
|---|---|---|---|
| [JPX #2 基準](research/results/lgbm_jpx2/summary.md) | • 照 JPX 第 2 名做 | • 對動能 −0.25% | • 停止 |
| [相對強弱標籤](research/results/lgbm_alpha/summary.md) | • 標籤減市場平均 | • 對動能 −0.29%<br>• 和原版一樣 | • 停止 |
| [成交對齊標籤](research/results/target_alignment/summary.md) | • 隔天成交價進場 | • 對動能 −0.53% | • 標籤不是瓶頸 |
| [學習率診斷](research/results/lr_diagnostic/) | • 學習率 0.1 ～ 0.0005 | • 排名相關都 0.02–0.03 | • 學習率不是瓶頸 |
| [資料使用](research/results/data_usage/summary.md) | • 資料從 2019 起<br>• 最近 20% 也訓練 | • C 對對照組 +0.90%<br>  ↳ 95% 區間 +0.13% ~ +1.68% | • C 有效 |
| [最終測試](research/results/final_test/summary.md) | • 21 組設定 | • LightGBM 最佳 −1.17% | • 動能仍最好 |

- 前三輪用 dev 70 窗口
  - 2019–2021，資料回推到 2016
- 資料使用用 dev 46 窗口
  - 2020–2021，資料從 2019 起

---

## 2. 動能改良研究

2026-09 做了 6 項研究，想找贏過 20 日動能的改法。每項都先把規則寫死在 spec，再只跑一次；沒過就停止，不再重試。

| 研究 | 改了什麼 | 結果 | 結論 |
|---|---|---|---|
| [P1 動能小實驗](research/results/momentum_sweep/summary.md) | • 回看 15–30 日<br>• skip、多期、品質 | • DEV 選出 mom30<br>• validation +0.37% | • 未過 +0.5%<br>• 維持 Mom20 |
| [Hybrid](research/results/hybrid/summary.md) | • 恐慌日改用<br>  ↳ LightGBM-v2 | • 2015–2024 +0.26% | • 未過，停止 |
| [Momentum-v2](research/results/momentum_v2/summary.md) | • Mom20 ＋ residual<br>• ＋低換手 ＋營收 | • validation +0.23% | • 未過，停止 |
| [H1 residual](research/results/h1_residual/summary.md) | • 只用 residual<br>  ↳ 120 日、β 250 日 | • 看過的資料 +1.78%<br>• 2025–2026 −2.15% | • 反轉，停止 |
| [Mom25](research/results/mom25_report/summary.md) | • 回看改 25 日 | • 2015–21 +0.19%<br>• 2022–24 +0.37%<br>• 2025–26 −0.29% | • 和 Mom20 打平 |
| [residual 修正](research/results/residual_fixes/summary.md) | • 等權、產業基準<br>• β 收縮 | • 最好 +0.06% | • 和動能打平 |

- 數字都是對 20 日動能的配對平均差
  - 24 日窗口，逐窗口配對
- 規則在各 spec
  - [momentum_v2_spec.md](docs/momentum_v2_spec.md)
  - [h1_residual_spec.md](docs/h1_residual_spec.md)
  - [residual_fixes_spec.md](docs/residual_fixes_spec.md)

### 學到什麼

**最佳回看天數會翻轉。** 2015–2024 窗口越長越好，2025–2026 只有 20 日有效：

| 對 20 日動能 | 2015–2024 | 2025–2026 |
|---|---:|---:|
| 25 日動能 | +0.19% ~ +0.37% | −0.29% |
| 60 日動能 | — | −1.78% |
| 120 日 residual | +1.17% ~ +1.78% | −2.15% |

- 來源：[mom25_report](research/results/mom25_report/summary.md)、[final_test](research/results/final_test/summary.md)、[h1_residual](research/results/h1_residual/summary.md)
- 比賽接在 2025–2026 之後
  - 20 日較可能適用

**扣大盤（residual）沒有額外價值。**

- 20／25 日時和動能打平
  - 全期 −0.07%、+0.01%
- 0050 被台積電主導
  - 2022 起相關 ≥ 0.91
  - 會扭曲 residual
- 改扣等權市場後
  - 2025–2026 從 −0.98% 到 −0.31%
  - 全期仍只打平
- 來源：[residual_fixes](research/results/residual_fixes/summary.md)、[residual_regimes](research/results/residual_regimes/summary.md)

**看過的資料不能當證據。** H1 是在 2022–2024 表現好之後才挑出來的，在那段 +1.78%，到沒看過的 2025–2026 變成 −2.15%。事先寫死規則，擋下了這次錯誤的替換。

**其他無效的訊號：**

- 低換手：每段都輸
  - validation −1.06%
- 產業 residual：無額外價值
- 月營收未測 2025–2026
  - validation +0.58%
  - 只是診斷，沒有 test

---

## 3. 比賽規則重點

| 項目 | 規定 |
|---|---|
| 賽期 | 2026-10-26 → 11-27，24 個交易日 |
| 本金 | 10 億元，全現金起始 |
| 股票池 | 固定 150 檔 |
| 持股數 | 每天 20–30 檔 |
| 單股上限 | NAV 10%，2330 為 25% |
| 現金 | 不能負數，且必須 < 25% |
| 交易 | 整張（1,000 股），只能做多 |
| 成交價 | 當日官方成交均價（成交金額 ÷ 成交股數） |

細節見 [docs/task.md](docs/task.md)。

---

## 4. 策略與安全機制

### 三種方法

| 方法 | 分數怎麼算 | 程式 |
|---|---|---|
| 動能 | • 過去 N 天報酬 | `research/baselines.py` |
| AutoTS | • 預測相對強弱<br>• 每窗口重選模型 | `autots_strategy/` |
| LightGBM | • 15 個價量特徵<br>• 預測未來 10 天報酬 | `lgbm_strategy/` |

三種方法共用組合層 `autots_strategy/portfolio.py`：

- 分數前 25 檔等權
- 排名前 35 就續抱
- 換手 <10% 不調整
- 最後 3 天不交易

### LightGBM 的三種訓練方式

每次重訓都依時間切：前 80% 訓練，最近 20% 驗證。

| 方式 | 樹的數量 | 最終模型用的資料 |
|---|---|---|
| 對照組 | • 提早停止選 | • 只用前 80% |
| C | • 提早停止選 | • 再用 100% 重訓 |
| D | • 事先固定<br>  ↳ 2020–2024 的中位數 | • 100%，不切驗證 |

- 特徵在 `lgbm_strategy/features.py`
  - 報酬、波動、均線乖離
- 標籤在 `lgbm_strategy/targets.py`
  - 原始、相對強弱、成交對齊
- D 的樹數在 [tree_calibration](research/results/tree_calibration/trees.json)
  - 0.005 → 246 棵

### 下單與結算（`competition/`）

- planner 換算整張股數
  - 假設買在漲停價
  - 現金也不會變負
- ledger 用官方均價結算
  - 沒有時用 HLC3
  - 不捏造價格
- 當天違規就整天作廢
  - 並記 1 次警告

### 防止偷看未來

- 決策只看 D−1 以前
- 不讀 `adj_close`
  - 它含未來股利
- 訓練標籤必須已確定
  - 晚於 D−1 就報錯
- 因果測試竄改未來資料
  - 決策必須完全不變
  - `tests/test_causality.py`、`tests/test_lgbm_causality.py`
- 測試期要加旗標
  - 存取記在 `research/holdout_access_log.jsonl`

### 資料切分

| 切分 | 期間 | 用途 |
|---|---|---|
| dev | 2010–2021 | • 試方法 |
| validation | 2022–2024 | • 選定後確認 |
| holdout | 2025–2026-09 | • 最後測試<br>• 已用過多次 |

- holdout 已用於
  - 最終測試、H1 test
  - Mom25、residual 報告
- LightGBM 資料從 2019 起
- 動能改良資料從 2014 起
  - 2014 只當暖身
- 150 檔是 2026 名單
  - 越早年份偏誤越大
- 設定方式：config 的 `data.start`

---

## 5. 怎麼跑

### 第一次設定

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
# 部分 clone：git 歷史含約 950MB 舊輸出，這樣只下載需要的檔案
git clone --filter=blob:none https://github.com/chrisPixelCraft/fintech_ETF.git
cd fintech_ETF

# 建環境
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-autots.txt

# 讓 Python 找到 repo 內的 AutoTS
echo "$PWD/third_party/autots" > .venv/lib/python3.12/site-packages/fintech_etf_third_party.pth

# 確認：應顯示 1.0.4
.venv/bin/python -c "import autots; print(autots.__version__)"
```

### 每日提交

```bash
# 交易日 T 的 05:00–08:55（台北）；結束碼 0 = 上傳 production_runs/<T>/D-Plan_*.json
./run_daily.sh 2026-10-26 --holdings <後台匯出的持股檔>
```

- 模式與處理方式見 [docs/production_spec.md](docs/production_spec.md) 第 14 節
- production 與回測逐日一致
  - 40 窗口、960 天
  - [production_replay](research/results/production_replay/summary_holdout.md)

### 常用指令

```bash
# 跑測試
.venv/bin/python -m unittest discover -s tests -q

# 跑一個實驗：dev 切分，平均取 6 個窗口
PYTHONHASHSEED=0 .venv/bin/python -m research.run_experiment \
    --config research/configs/baseline_lgbm_raw.json --split dev --episodes 6 --workers 6

# 比較兩次實驗
.venv/bin/python -m research.compare research/runs/<run_A> research/runs/<run_B>

# 最終測試：單元測試 → D 的樹數 → 21 組 × 2025–2026（約 3–4 小時，可中斷續跑）
bash research/final_test.sh
```

各輪研究的入口：

| 研究 | 指令 |
|---|---|
| JPX #2 基準 | `python -m research.lgbm_jpx2` |
| 相對強弱標籤 | `python -m research.lgbm_alpha` |
| 成交對齊標籤 | `python -m research.target_alignment` |
| 學習率診斷 | `python -m research.lr_diagnostic` |
| 資料使用 | `python -m research.data_usage` |
| P1 動能小實驗 | `python -m research.momentum_sweep` |
| Hybrid | `python -m hybrid.evaluate` |
| Momentum-v2 | `python -m momv2.evaluate coverage｜dev｜validation｜test` |
| H1 residual | `python -m momv2.h1_evaluate dev｜validation｜test` |
| residual 分組比較 | `python -m momv2.regime_report` |
| residual 修正 | `python -m momv2.fixes_report` |

- 各階段只能跑一次
  - 結果不同會拒絕寫入
- 外部資料下載
  - 月營收、法人：`python -m hybrid.sources`
  - 發行股數：`python -m momv2.sources fetch`

### 結果在哪

- 每窗口明細不進 git
  - 在 `research/runs/`
- 整理後的結果要 commit
  - 在 `research/results/`
- 每次執行記一列
  - `research/registry.csv`
- 中斷後重跑同一指令
  - 從完成的窗口接續

---

## 6. AutoTS 調參（finetune）

這是 AutoTS 的參數搜尋工具，程式在 `research/tune.py`。

```bash
# 1. 試跑（約 10–20 分鐘；結果不用 commit）
PROFILE=quick bash research/finetune.sh

# 2. 正式跑（M2 Max 開 10 個 workers，約一晚）
bash research/finetune.sh

# 3. commit 結果
git add research/results/tune_crazy && git commit -m "Add tuning results crazy" && git push
```

- 加 `TAG=名字` 另開一次
- 改 `WORKERS=8` 調核心數
- 中斷後重跑會接續

| PROFILE | 試幾組 | 前幾名跑全期 | 每組 seed 數 | 時間（10 workers） |
|---|---:|---:|---:|---|
| `quick` | 6 | 2（部分窗口） | 2 | 10–20 分鐘 |
| `normal` | 41 | 8 | 3 | 5–7 小時 |
| `crazy`（預設） | 81 | 16 | 5 | 10–14 小時 |

流程：
1. 跑三個簡單基準
2. 粗篩隨機設定，再在領先者附近微調
3. 前幾名跑完 2019–2024 全部 140 個窗口
4. 換 seed 重跑，用平均分排名
5. 用 2025–2026/9 算測試分數

評分是每個 24 日窗口對 20 日動能的超額報酬，平均和中位數各佔一半；有窗口被取消資格就是 −inf。結果寫在 `research/results/tune_<tag>/`（`summary.md`、`leaderboard.csv`、`best_config.json`），`crazy` 規模還沒跑完。

- 2025–2026 已用過
  - 測試分數會偏樂觀

---

## 7. 檔案結構

```text
fintech_ETF/
├── competition/         ← 競賽核心：規則、資料截止、窗口、下單規劃、成交、帳本、回測
├── autots_strategy/     ← AutoTS 策略，以及三種方法共用的組合層 portfolio.py
├── lgbm_strategy/       ← LightGBM：特徵、標籤、訓練資料、模型、策略
├── production/          ← 每日提交：資料、對帳、engine、fallback、Active Share、D-Plan、驗證、replay
├── hybrid/              ← Hybrid：月營收與法人資料、恐慌 gate、LightGBM-v2 walk-forward
├── momv2/               ← 動能改良：residual、換手、營收訊號，各研究的評估與報告
├── research/            ← 實驗入口、設定、基準策略、比較、實驗總表、各輪研究
│   ├── configs/         ← 實驗設定
│   └── results/         ← 整理後的結果（要 commit）
├── tests/               ← 測試（含因果測試）
├── docs/                ← 任務定義、各輪 spec、最終測試計畫
├── third_party/autots/  ← AutoTS 1.0.4 原始碼（MIT，含修補 P1）
├── data/                ← 日線快照、股票池、規則；hybrid/、momv2/ 是外部資料（不進 git）
├── official_docs/       ← 主辦方原始文件與 D-Plan schema
└── legacy/              ← V2–V5 舊程式、報告、測試
```

AutoTS 直接修改 repo 內的原始碼，不從 PyPI 安裝，修改紀錄在 [third_party/autots/UPSTREAM.md](third_party/autots/UPSTREAM.md)。修補 P1 修正原版在股票數不是 100 倍數時，默默丟掉第 101–150 檔的問題。

---

## 8. 舊版本

### v1／v2：長期回放

- 固定參數 `x0352`
  - 2025-01-02 → 2026-09-21
- 資料與成交口徑不同
  - 不能代表 24 日賽期

| 期間 | v2 `x0352` | v1 | 0050 |
|---|---:|---:|---:|
| 2025 年 | 78.95% | 66.36% | 33.46% |
| 2026 年至 9/21 | 128.65% | 109.28% | 65.81% |
| 全期帳面報酬 | 309.16% | 248.15% | 121.28% |

### V3、V4、V5

| 版本 | 做了什麼 | 結果 |
|---|---|---|
| V3 | • 改做 24 日賽期<br>• 搜尋 1,471 組參數 | • 無一組全合規<br>  ↳ `NO_ELIGIBLE_CANDIDATE` |
| V4 | • 建官方均價管線<br>• 建帳本驗證 | • 沒找到勝出策略<br>  ↳ `NO_V4_WINNER`<br>• 現行帳本源自此版 |
| V5 | • 參考冠軍策略<br>• 設計四族策略 | • 官方資料沒下載齊<br>• 沒跑出結果 |

- 各版程式與測試方式
  - [legacy/README.md](legacy/README.md)
- 完整舊輸出與帳本
  - 提交 [`2769f876`](https://github.com/chrisPixelCraft/fintech_ETF/tree/2769f876ec4b9795ce5d8cc4e64b8274da58099c)
