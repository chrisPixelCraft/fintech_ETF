# ETF 策略研究：AutoTS-first

> **一句話**：用 AutoTS 預測 150 檔股票未來 5 天的相對表現，每天挑前 25 檔，在競賽規則內跑 24 個交易日，目標是期末 NAV 最大。

## 先看這裡

- **主線**：AutoTS 策略，程式在 `autots_strategy/`
- **進度**：初步測試中，**還沒贏過簡單基準**
- **能不能提交**：還不行，狀態 `BLOCK_SUBMISSION`，因為每日 D-Plan 產生器還沒做
- **要跑程式**：看「[4. 怎麼跑](#4-怎麼跑)」
- **要調參**：看「[5. 如何調參](#5-如何調參finetune)」
- **舊版本（v1–V5）**：全部在 `legacy/`，摘要在「[7. 舊版本](#7-舊版本)」

---

## 1. 目前進度

- **測試範圍**：dev 切分中的 6 個 24 日窗口，只是初步比較
- **平均報酬**：

  | 策略 | 平均報酬 |
  |---|---:|
  | AutoTS 基準 | −2.4% ～ −3.8% |
  | 20 日動能 | −1.1% |
  | 大型股籃子 | −1.6% |

- **結論**：AutoTS **目前輸給簡單基準**
- **還缺**：每日 D-Plan 產生器
- **完整紀錄**：[research/registry.csv](research/registry.csv)

---

## 2. 比賽規則重點

- **賽期**：2026-10-26 → 11-27，24 個交易日
- **本金**：10 億元，全現金起始
- **股票池**：固定 150 檔
- **持股數**：每天 20–30 檔
- **單股上限**：NAV 10%，2330 為 25%
- **現金**：必須 < 25%
- **交易**：整張（1,000 股），只能做多
- **成交價**：當日官方成交均價（成交金額 ÷ 成交股數）
- **細節**：[docs/task.md](docs/task.md)

---

## 3. AutoTS 策略

### 3.1 每個決策日 D 做什麼

1. **拿資料**
   - 只看得到 D−1 收盤以前的資料
   - 含未來股利的 `adj_close` 一律不讀
2. **算預測目標**
   - 個股相對「等權市場」的 log 價格
   - 預測 5 個交易日後的變化
3. **選模型**（每個 24 日窗口開始時選一次）
   - 候選 7 個：
     - LastValueNaive
     - AverageValueNaive（20 日、60 日）
     - SeasonalNaive
     - ETS
     - ARIMA
     - WindowRegression（Ridge）
   - 評分方式：rank IC，也就是預測排序和實際排序的相關程度
   - 驗證方式：4 個歷史窗口，每個 24 天
4. **預測**
   - 每 5 天預測一次
   - 預測值轉成 z-score
5. **選股**
   - 前 25 檔，等權重
   - 持有緩衝：排名還在前 35 名內就續抱
   - 換手門檻：要換的部位不到 10% 就不調整
6. **下單與結算**（`competition/`）
   - **planner**：用官方公式換算整張股數
     - 違規時先自動修正
     - 修不好就維持原持股
     - 仍不合規就標記為不可行
   - **ledger**：用當日官方成交均價結算
     - 沒有官方價時，用有明確標示的 HLC3 代理價
     - 不會捏造價格

### 3.2 安全機制

- **防止偷看未來**
  - 關閉 AutoTS 會用到未來資料的前處理
  - 不傳入未來的回歸變數
  - `tests/test_causality.py` 會竄改未來資料，確認決策完全不變
- **資料切分**
  - dev：2010–2021
  - validation：2022–2024
  - holdout：2025–2026-09，最接近賽期，當最後的測試
    - 必須加旗標才能跑
    - 每次存取都會留下紀錄

### 3.3 AutoTS 原始碼

- **位置**：[`third_party/autots/`](third_party/autots/UPSTREAM.md)，版本 1.0.4
- **使用方式**：直接修改這份原始碼，不從 PyPI 安裝
- **修改紀錄**：`third_party/autots/UPSTREAM.md`
- **目前修補**：P1
  - 原版遇到 150 檔這種不是 100 倍數的數量時，會默默丟掉第 101–150 檔

---

## 4. 怎麼跑

### 4.1 第一次設定

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

### 4.2 日常使用

```bash
# 跑測試
.venv/bin/python -m unittest discover -s tests -q

# 跑一個實驗：dev 切分，平均取 6 個窗口
PYTHONHASHSEED=0 .venv/bin/python -m research.run_experiment \
    --config research/configs/baseline_autots.json --split dev --episodes 6 --workers 6

# 比較兩次實驗
.venv/bin/python -m research.compare research/runs/<run_A> research/runs/<run_B>
```

### 4.3 結果在哪

- **每次實驗**：`research/runs/<run_id>/`，不進 Git
  - 每個窗口的帳本、交易、委託、摘要
- **實驗總表**：`research/registry.csv`，每跑一次加一列
- **中斷了**：重跑同一指令，會從已完成的窗口接續
- **簡單基準**：設定在 `research/configs/baselines/`
  - 20 日動能
  - 大型股籃子
  - 無訊號對照組（只用 LastValueNaive）

## 5. 如何調參（finetune）

### 5.1 三步驟

```bash
# 1. 先試跑，確認環境沒問題（約 10–20 分鐘；結果不用 commit）
PROFILE=quick bash research/finetune.sh

# 2. 正式跑（M2 Max 開 10 個 workers，約一晚）
bash research/finetune.sh

# 3. 把結果整理 commit 上去
git add research/results/tune_crazy
git commit -m "Add tuning results crazy"
git push
```

- 資料已經在 repo 裡，不用另外下載
- 中斷了：重跑同一指令，會從斷點接續
- 想另開一次搜尋：加 `TAG=新名字`
- 核心數不同：改 `WORKERS=8` 之類

### 5.2 看進度

終端機最下面會有一行即時進度：

```text
[2/4 confirm 3/16] c8f116731e | train 98/143 val 0/35 | ████████░░░░░░░░░░░░ 41% 2841/7012 | 3h02m, ETA ~6h39m
```

- `[2/4 confirm 3/16]`：第 2 階段（共 4 個階段），這個階段的第 3 組設定，共 16 組
- `c8f116731e`：目前這組設定的代號
- `train` / `val` / `test`：這組設定在 dev（2010–2021）、validation（2022–2024）、holdout（2025–2026）各跑了幾個窗口
- 進度條：整次調參要算的 AutoTS 窗口完成多少
  - 總數是上限估計；某組設定換 seed 結果不變時，總數會自動變小
- `3h02m, ETA ~6h39m`：已經跑了多久、大約還要多久（用這次執行的速度估算）
- 在另一個終端機查看：`cat research/results/tune_crazy/progress.txt`
- 中斷後重跑，進度會從實際停下的地方繼續算

階段對照：`0/4 baselines` → `1/4 screen`、`1/4 local` → `2/4 confirm` → `3/4 seeds` → `4/4 test`

### 5.3 三種規模

| PROFILE | 試幾組設定 | 前幾名跑完整期間 | 每組試幾個 seed | 粗估時間（10 workers） |
|---|---:|---:|---:|---|
| `quick` | 6 | 2（只跑部分窗口） | 2 | 10–20 分鐘 |
| `normal` | 41 | 8 | 3 | 5–7 小時 |
| `crazy`（預設） | 81 | 16 | 5 | 10–14 小時 |

### 5.4 它做了什麼

- **資料怎麼用**
  - 挑參數：2010–2024（dev 143 + validation 35 個窗口）
  - 測試：2025–2026/9（holdout 20 個窗口），最接近賽期，最後只算一次，不改變選擇
- **流程**（`research/tune.py`）
  1. 跑三個簡單基準：20 日動能、大型股籃子、無訊號對照組
  2. 粗篩：目前設定 + 隨機設定，再在領先者附近微調，每組跑 20 個窗口
  3. 前幾名跑完 2010–2024 全部 178 個窗口
  4. seed 穩定度：換 seed 重跑，用平均分排名，不挑單一最好的 seed
  5. 用 2025–2026/9 算測試分數，和簡單基準比較
- **評分**
  - 每個 24 日窗口對 20 日動能的超額報酬，平均和中位數各佔一半
  - 有任何窗口被取消資格，就是 -inf
- **會調的參數**
  - 預測：目標序列、預測天數、歷史長度、驗證窗口、評分方式、模型組合、AutoTS 搜尋模式、seed
  - 組合：持股數、持有緩衝、權重方式、投入比例、單股上限縮放、換手門檻、最後幾天不交易

### 5.5 結果檔（commit 這個資料夾）

`research/results/tune_<tag>/`，每跑完一個階段就更新一次，中途停掉也看得到目前結果：

- `summary.md`：中文結果整理，AI 可以直接讀它來更新 README
- `summary.json`：同樣內容的機器可讀版
- `leaderboard.csv`：每組設定的分數
- `best_config.json`：最佳設定，可直接給 `research.run_experiment --config` 使用
- `log.txt`：執行過程紀錄
- `progress.txt`：最新一行進度

每個窗口的帳本等大型中間檔放在 `research/runs/tune_<tag>/`，不進 git。

### 5.6 怎麼看結果

- **2025–2026 測試 PASS**：最佳設定在最接近賽期的資料上贏過 20 日動能，可以考慮採用
- **FAIL**：輸給簡單基準，先不要採用，回頭檢查方法
- **看完測試分數後不要再回頭調參**：否則這個分數就不再客觀

---

## 6. 檔案結構

```text
fintech_ETF/
├── autots_strategy/     ← AutoTS 策略：預測目標、AutoTS 包裝、打分、組合
├── competition/         ← 競賽核心，與策略無關：規則、資料截止、窗口、規劃、成交、帳本、回測
├── research/            ← 實驗入口、設定、基準策略、比較、實驗總表、調參（finetune.sh、tune.py）
│   └── results/         ← 調參結果整理（summary.md 等，要 commit）
├── tests/               ← 新主線的測試（含因果測試）
├── third_party/autots/  ← AutoTS 1.0.4 原始碼（MIT 授權）
├── docs/                ← 任務定義、AutoTS 內部機制、策略規格
├── data/                ← 日線快照、股票池、規則等輸入資料
├── official_docs/       ← 主辦方原始文件與 D-Plan schema
└── legacy/              ← V2–V5 舊程式、報告、測試（只搬位置，沒改內容）
```

---

## 7. 舊版本

### v1／v2：長期回放

- 固定參數 `x0352`，期間 2025-01-02 → 2026-09-21
- 資料與成交口徑不同，**不能代表 24 日賽期**

| 期間 | v2 `x0352` | v1 | 0050 |
|---|---:|---:|---:|
| 2025 年 | 78.95% | 66.36% | 33.46% |
| 2026 年至 9/21 | 128.65% | 109.28% | 65.81% |
| 全期帳面報酬 | 309.16% | 248.15% | 121.28% |

### V3、V4、V5

- **V3**：改做 24 日短賽期，搜尋 1,471 組參數，沒有一組通過全部合規門檻（`NO_ELIGIBLE_CANDIDATE`）
- **V4**：建好官方成交均價資料管線和帳本驗證，但沒找到勝出策略（`NO_V4_WINNER`）；現在的成交與帳本邏輯就是從這版改寫
- **V5**：參考冠軍策略設計四族策略，程式完成，但官方成交資料沒下載齊，沒跑出結果

### 去哪裡看更多

- **各版程式與測試方式**：[legacy/README.md](legacy/README.md)
- **完整舊輸出、帳本、官方原始快取**：提交 [`2769f876`](https://github.com/chrisPixelCraft/fintech_ETF/tree/2769f876ec4b9795ce5d8cc4e64b8274da58099c)
