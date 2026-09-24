# ETF 策略研究：AutoTS-first

> **一句話**：用 AutoTS 預測 150 檔股票未來 5 天的相對表現，每天挑前 25 檔，在競賽規則內跑 24 個交易日，目標是期末 NAV 最大。

## 先看這裡

- **主線**：AutoTS 策略，程式在 `autots_strategy/`
- **進度**：初步測試中，**還沒贏過簡單基準**
- **能不能提交**：還不行，狀態 `BLOCK_SUBMISSION`，因為每日 D-Plan 產生器還沒做
- **要跑程式**：看「[4. 怎麼跑](#4-怎麼跑)」
- **舊版本（v1–V5）**：全部在 `legacy/`，摘要在「[6. 舊版本](#6-舊版本)」

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
  - holdout：2025–2026-09
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

---

## 5. 檔案結構

```text
fintech_ETF/
├── autots_strategy/     ← AutoTS 策略：預測目標、AutoTS 包裝、打分、組合
├── competition/         ← 競賽核心，與策略無關：規則、資料截止、窗口、規劃、成交、帳本、回測
├── research/            ← 實驗入口、設定、基準策略、比較、實驗總表
├── tests/               ← 新主線的測試（含因果測試）
├── third_party/autots/  ← AutoTS 1.0.4 原始碼（MIT 授權）
├── docs/                ← 任務定義、AutoTS 內部機制、策略規格
├── data/                ← 日線快照、股票池、規則等輸入資料
├── official_docs/       ← 主辦方原始文件與 D-Plan schema
└── legacy/              ← V2–V5 舊程式、報告、測試（只搬位置，沒改內容）
```

---

## 6. 舊版本

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
