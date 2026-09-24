# ETF 策略研究：AutoTS-first

目標：在 AI CUP 2026 玉山 ETF 賽期（2026-10-26 → 11-27，24 個交易日）用 AutoTS 預測個股相對表現，從 10 億元全現金起始，建立符合競賽規則、最大化期末 NAV 的投資組合。規則摘要見 [docs/task.md](docs/task.md)：150 檔股票池、每日持有 20–30 檔、單股 ≤ 10%（2330 ≤ 25%）、現金 < 25%、整張交易、成交價為當日官方成交均價。

## AutoTS 策略

AutoTS 是這條主線的預測核心。我們把 AutoTS 1.0.4 原始碼放在 [`third_party/autots/`](third_party/autots/UPSTREAM.md)，直接在上面修改，每個修改都有紀錄。目前只有一個修補 P1：原版遇到 150 檔這種非 100 倍數的序列數時，會默默丟掉第 101–150 檔。

每個決策日 D 的流程：

1. **資料截止**：`competition/data.py` 只給策略 D−1 收盤以前的資料；含未來股利的 `adj_close` 一律不讀。
2. **預測目標**：個股相對等權市場的 log 價格，預測 5 個交易日後的變化（`autots_strategy/targets.py`）。
3. **選模型**：每個 24 日窗口開始時，用 4 個各 24 天的歷史驗證窗，以「預測排序與實際排序的相關係數」（rank IC）為分數，從 7 個模型中挑一個：LastValueNaive、AverageValueNaive（20／60 日）、SeasonalNaive、ETS、ARIMA、WindowRegression（Ridge）。
4. **預測與打分**：每 5 天用挑中的模型重新預測，轉成 z-score。
5. **組合**：取前 25 檔等權，並用持有緩衝和換手門檻減少不必要的交易（`autots_strategy/portfolio.py`）。
6. **競賽核心**：`competition/planner.py` 用官方公式換算整張股數，違規時先自動修正，修不好就維持原持股，仍不合規則標記為不可行（fail-closed）；`competition/ledger.py` 以當日官方成交均價結算，沒有官方價時使用明確標示的 HLC3 代理價，不會捏造價格。

另外兩點：

- **防止偷看未來**：關閉 AutoTS 會用到未來資料的前處理，也不傳入未來回歸變數；`tests/test_causality.py` 會竄改未來資料，確認決策完全不變。
- **資料切分**：dev 2010–2021、validation 2022–2024、holdout 2025–2026-09。holdout 必須加旗標才能跑，每次存取都會留下紀錄。

**目前進度**：只在 dev 的 6 個窗口做過初步比較。AutoTS 基準平均報酬為 −2.4% 至 −3.8%，還沒贏過 20 日動能（−1.1%）和大型股籃子（−1.6%）這兩個簡單基準，完整紀錄見 [research/registry.csv](research/registry.csv)。每日 D-Plan 產生器尚未建立，正式提交維持 `BLOCK_SUBMISSION`。

## 怎麼跑

需要 Python 3.12 與 [uv](https://docs.astral.sh/uv/)。完整 git 歷史含約 950MB 的舊研究輸出，建議用部分 clone：

```bash
git clone --filter=blob:none https://github.com/chrisPixelCraft/fintech_ETF.git && cd fintech_ETF

# 一次性環境設定：AutoTS 用 repo 內的版本，不從 PyPI 安裝
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-autots.txt
echo "$PWD/third_party/autots" > .venv/lib/python3.12/site-packages/fintech_etf_third_party.pth
.venv/bin/python -c "import autots; print(autots.__version__)"   # 應顯示 1.0.4

# 測試
.venv/bin/python -m unittest discover -s tests -q

# 跑一個設定：dev 切分中平均取 6 個 24 日窗口
PYTHONHASHSEED=0 .venv/bin/python -m research.run_experiment \
    --config research/configs/baseline_autots.json --split dev --episodes 6 --workers 6

# 在共同完成的窗口上配對比較兩次執行（bootstrap 信賴區間）
.venv/bin/python -m research.compare research/runs/<run_A> research/runs/<run_B>
```

- 每次執行的結果寫入 `research/runs/<run_id>/`（不進 Git），包含每個窗口的帳本、交易、委託與摘要，同時在 `research/registry.csv` 追加一列。
- 同一指令中斷後重跑，會從已完成的窗口接續。
- 三個簡單基準的設定在 `research/configs/baselines/`：20 日動能、大型股籃子，以及只用 LastValueNaive 的無訊號對照組。

## 檔案結構

```text
fintech_ETF/
├── autots_strategy/     AutoTS 策略：預測目標、AutoTS 包裝、打分、組合
├── competition/         與策略無關的競賽核心：規則、資料截止、窗口、規劃、成交、帳本、回測
├── research/            實驗入口、設定、基準策略、配對比較、實驗紀錄 registry.csv
├── tests/               新主線的單元測試（含因果測試）
├── third_party/autots/  AutoTS 1.0.4 原始碼與本地修補紀錄（MIT 授權）
├── docs/                任務定義 task.md、AutoTS 內部機制、策略規格、資料與規則說明
├── data/                Yahoo 日線快照、官方股票池與規則等輸入資料
├── official_docs/       主辦方原始文件與 D-Plan schema
├── legacy/              V2–V5 的程式、設定、報告與測試（只搬位置，未改寫）
└── requirements-autots.txt
```

## 前幾版做了什麼

v1／v2 是長期回放研究，固定參數 `x0352` 在 2025-01-02 → 2026-09-21 的比較如下。它用的是另一套資料與成交口徑，不能代表 24 日賽期的表現：

| 期間 | v2 `x0352` | v1 | 0050 |
|---|---:|---:|---:|
| 2025 年 | 78.95% | 66.36% | 33.46% |
| 2026 年至 9/21 | 128.65% | 109.28% | 65.81% |
| 全期帳面報酬 | 309.16% | 248.15% | 121.28% |

- **V3**：改做 24 日短賽期，以 Yahoo 日線、D−1 決策、次日開盤近似成交，四輪共搜尋 1,471 組參數，沒有任何一組通過全部逐日合規門檻（`NO_ELIGIBLE_CANDIDATE`）。
- **V4**：建立 TWSE／TPEx 官方成交均價資料管線與獨立帳本驗證，但策略研究沒有找到勝出者（`NO_V4_WINNER`）；現在的成交與帳本邏輯即改寫自這一版。
- **V5**：參考冠軍策略預先設計動能、集成、排名頻率、直接效用四族，程式與測試完成，但官方成交資料沒有下載齊，沒有跑出任何結果。

各版的程式、報告與測試方式見 [legacy/README.md](legacy/README.md)。完整的舊輸出、帳本與官方原始快取保存在提交 [`2769f876`](https://github.com/chrisPixelCraft/fintech_ETF/tree/2769f876ec4b9795ce5d8cc4e64b8274da58099c)。
