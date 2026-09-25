# 怎麼跑

## 第一次設定

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
# 部分 clone：git 歷史含約 950MB 舊輸出，這樣只下載需要的檔案
git clone --filter=blob:none https://github.com/chrisPixelCraft/fintech_ETF.git
cd fintech_ETF

uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

- 只需要 6 個套件
  - 見 [requirements.txt](../requirements.txt)
- AutoTS 環境已移到 legacy
  - 見 [legacy/README.md](../legacy/README.md)

## 每日提交

```bash
# 交易日 T 的 05:00–08:55（台北）
./run_daily.sh 2026-10-26                                    # 首日，不用持股檔
./run_daily.sh 2026-10-27 --holdings <後台匯出的持股檔>      # 之後每天

# 不連網試跑（用本地資料）
./run_daily.sh 2026-09-24 --offline
```

- 輸出在 `production_runs/<T>/`
  - 上傳 `D-Plan_*.json`
  - 原因寫在 `audit.md`
- 結束碼 0：可以上傳
- 結束碼 2：先讀 `audit.md`
- 狀態與處理方式
  - 見 [production_spec.md](production_spec.md) 第 14 節

## 隊號與首日

- 隊號 `TEAM_11076`
  - 已填入 `production/settings.json`
- 首日 2026-10-26
  - 主辦方指定
  - 休市會自動跳過
- 見 [production_spec.md](production_spec.md) 第 13 節

## 常用指令

```bash
# 跑測試（71 個）
.venv/bin/python -m unittest discover -s tests -q

# 跑一個實驗：正式策略 Mom20，dev 切分，取 6 個窗口
PYTHONHASHSEED=0 .venv/bin/python -m research.run_experiment \
    --config production/strategy.json --split dev --episodes 6 --workers 6

# 比較兩次實驗（逐窗口配對）
.venv/bin/python -m research.compare research/runs/<run_A> research/runs/<run_B>

# production 對回測的一致性檢查
PYTHONHASHSEED=0 .venv/bin/python -m production.replay --split holdout --episodes all
```

- `run_experiment` 只跑 momentum、basket
  - 其他策略在 legacy
- `momentum_20d.json` 是較早的 dev 基準
  - 投入 88%、只有月初窗口，不等於正式策略
- holdout 要加 `--i-understand-holdout`

## 結果在哪

| 內容 | 位置 | 進 git |
|---|---|---|
| 每窗口明細 | `research/runs/` | 否 |
| 整理後結果 | `research/results/` | 是 |
| 每次執行一列 | `research/registry.csv` | 是 |
| 每日提交輸出 | `production_runs/` | 否 |

- 中斷後重跑同一指令
  - 從完成的窗口接續

## 檔案結構

```text
fintech_ETF/
├── run_daily.sh      ← 每日提交入口
├── competition/      ← 規則、資料、窗口、組合層、下單、成交、帳本、回測
├── production/       ← 每日提交：資料、對帳、engine、fallback、Active Share、D-Plan、驗證、replay
├── research/         ← 基準策略、run_experiment、compare、registry
│   ├── configs/      ← baselines/：momentum_20d、largecap_basket
│   └── results/      ← final_test、momentum_sweep、production_replay
├── tests/            ← 71 個測試（含因果測試）
├── docs/             ← 規則、production spec、策略、本頁
├── data/             ← 日線快照、股票池、規則
├── official_docs/    ← 主辦方文件與 D-Plan schema
└── legacy/           ← 舊版本與 AutoTS / LightGBM / Hybrid 研究
```
