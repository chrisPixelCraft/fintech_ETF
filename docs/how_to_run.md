# 怎麼跑

現行規則與提交缺口見 [v4.3 核對](rules_v43.md)。目前 AS 官方算法未確認，live 會阻擋；offline 只輸出 `SIMULATION_ONLY`。

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
./run_daily.sh <交易日> --datapack <後台資料包.json> --etf-holdings <完整ETF前15大.csv> --rank <組內排名>

# 不連網試跑（用本地資料）
./run_daily.sh 2026-10-26 --offline
```

- 輸出在 `production_runs/<T>/`
  - 上傳 `D-Plan_*.json`
  - 原因寫在 `audit.md`
- 結束碼 0：僅 `READY_TO_SUBMIT`
- 結束碼 2：讀 `audit.md`
  - `SIMULATION_ONLY` 不能上傳
  - 缺官方 AS 算法會阻擋
- 狀態與處理方式
  - 見 [production_spec.md](production_spec.md) 第 14 節

## 凍結策略與排名

`production/strategy.json` 使用 tournament；回滾設定為 `production/strategy_mom20.json`。

| 項目 | 規則 |
|---|---|
| K2 | 10 核心 × 8.5% + 12 填充 × (10%/12)，共 22 檔 |
| T2 | 2330：23.5%；另 24 檔共 71.5% |
| 切換 | day_index ≥ 12、剩餘 > 3 日、rank ≤ 6 |
| 鎖定 | 切換後固定 T2 |
| 缺排名 | 保持模式；記錄 `RANK_MISSING` |

- `--rank N`：T−1 19:00 組內排名。
- Live 狀態：`production_runs/state/tournament.json`。
- Offline 狀態：`production_runs/<T>/state/tournament.json`。
- 合法 D-Plan 保存狀態，含 HOLD。
- K2 breach 重試 `k2_target`；HOLD 不撤銷目標。
- T2 保存切換日目標，逐日選最大可行換倉步驟。
- Audit：`SWITCH_STEP:k/len`；不可行則 HOLD。

## 輔助資料

- 歷史資料：`data/hybrid/{flows,revenue}.csv`。
- 增量快取：`data/production_aux/{flows,revenue}.csv`。
- Live 補抓 TWSE／TPEx 投信與 MOPS 營收。
- Offline 使用最新本地快照；不連網。
- 缺 T−1 投信或應有營收月份，走既有 fallback。
- 營收月 m：m+1 月 11 日起可用。

## 隊號與首日

- 隊號 `TEAM_11076`
  - 已填入 `production/settings.json`
- 首日 2026-10-26
  - 主辦方指定
  - 休市會自動跳過
- 見 [production_spec.md](production_spec.md) 第 13 節

## 常用指令

```bash
# 跑完整測試
.venv/bin/python -m unittest discover -s tests -q

# 跑一個實驗：正式策略 tournament，dev 切分，取 6 個窗口
PYTHONHASHSEED=0 .venv/bin/python -m research.run_experiment \
    --config production/strategy.json --split dev --episodes 6 --workers 6

# 比較兩次實驗（逐窗口配對）
.venv/bin/python -m research.compare research/runs/<run_A> research/runs/<run_B>

# production 對回測的一致性檢查
PYTHONHASHSEED=0 .venv/bin/python -m production.replay --split holdout --episodes all
```

- `run_experiment` 支援 tournament、momentum、basket
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
