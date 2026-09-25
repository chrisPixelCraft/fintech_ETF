# ETF 比賽：Mom20 策略

每天從固定 150 檔台股，選「最近 20 天漲最多」的 25 檔，跑 24 個交易日。目標是期末 NAV 最大。

## 30 秒看懂

- **策略：Mom20**
  - 最近 20 天漲最多的 25 檔
- **成績：每 24 天平均 +8.79%**
  - 2025–2026 測試期
  - 18 項研究都沒贏它
- **每天要做**
  - 跑 `./run_daily.sh`
  - 上傳 D-Plan
- **截止：每天 08:55**
- **隊伍 `TEAM_11076`**
  - 首日 2026-10-26

---

## 1. 比賽規則

### 違反會被取消資格

| 規則 | 限制 |
|---|---|
| 持股數 | 每天 **20–30 檔** |
| 單股上限 | **10%**，台積電 2330 **25%** |
| 現金 | **0–25%**，不能負數 |
| Active Share | 對每檔主動 ETF 前 10 大 **≥ 20%** |
| 警告 | 累積 **3 次**取消資格 |

- Active Share 連續 2 天違反
  - 可直接取消資格
- 漲價造成的超限
  - 5 個交易日內要減碼

### 基本設定

| 項目 | 內容 |
|---|---|
| 賽期 | 2026-10-26 → 11-27，24 個交易日 |
| 本金 | 10 億元，全現金開始 |
| 交易 | 只能做多、整張（1,000 股） |
| 成交價 | 當天官方成交均價 |
| 送件時間 | 前一天 19:30 → 當天 08:55 |

- 10/26 可能是補假
  - 從 10/26 計數，兩種情況都對
- 完整規則：[docs/task.md](docs/task.md)

---

## 2. 每天怎麼做

### 賽前（只做一次）

1. **重新凍結首日名單**
   ```bash
   .venv/bin/python -m production.etf_holdings --out etf.csv
   .venv/bin/python -m production.cold_start --etf-holdings etf.csv --yahoo-dir <最新 Yahoo 快取>
   ```
2. **commit 乾淨版本**
   - `code_version` 不能帶 `-dirty`
3. **清掉 dry run 狀態**
   - 正式狀態放 `production_runs/state/`
4. **試跑一次**
   ```bash
   ./run_daily.sh 2026-09-24 --offline
   ```

### 每個交易日（05:00–08:55）

1. **匯出持股**：從後台匯出前一天的結算持股
   - 首日 10/26 不用
2. **執行**
   ```bash
   ./run_daily.sh 2026-10-26                                  # 首日
   ./run_daily.sh 2026-10-27 --holdings <後台匯出的持股檔>     # 之後每天
   ```
3. **看結束碼**
   - `0` → 上傳 `production_runs/<日期>/D-Plan_TEAM_11076_<日期>.json`
   - `2` → 讀 `audit.md`；有 D-Plan 就先上傳，再查原因
4. **08:55 前上傳完成**

### D-Plan 狀態

| 狀態 | 意思 | 要做什麼 |
|---|---|---|
| `NORMAL` | 正常 | 上傳 |
| `COLD_START_FALLBACK` | 首日用凍結名單 | 上傳 |
| `FALLBACK_HOLD` | 資料有問題，沿用持股 | 上傳，再查原因 |
| `FALLBACK_COMPLIANCE_REPAIR` | 只做合規修補 | 上傳，再查原因 |
| `EMERGENCY_REVIEW_REQUIRED` | 產生不了合法計畫 | 08:55 前人工處理 |

- 程式每天自動抓主動 ETF 持股
  - 自動檢查並修補 Active Share
- 細節：[docs/production_spec.md](docs/production_spec.md) 第 11、14 節

---

## 3. 我們的策略：Mom20

| 項目 | 設定 |
|---|---|
| 選股 | 最近 20 天還原報酬最高的 25 檔 |
| 權重 | 等權，投入 95% |
| 續抱 | 已持有的股票排名 ≤ 35 就不賣 |
| 換手 | 變動 < 10% 就不調整 |
| 最後 3 天 | 不交易 |

- 設定檔：`production/strategy.json`
- 只用前一天以前的資料
- 出問題自動改用 fallback
- production 和回測逐日一致
  - [production_replay](research/results/production_replay/summary_holdout.md)

---

## 4. 結果

![2025–2026 每 24 個交易日的平均報酬：Mom20 最高](docs/figures/main_figure.png)

**沒有任何方法贏過 Mom20。**

| 方法 | 平均報酬 | 比 Mom20 | 結論 |
|---|---:|---:|---|
| **Mom20（Ours）** | **+8.79%** | — | **正式策略** |
| **價格訊號** | | | |
| ↳ Mom25 | +8.50% | −0.29% | 打平 |
| ↳ Residual 20 日 | +8.48% | −0.31% | 打平 |
| ↳ 多尺度 MACD | +7.73% | −1.06% | 輸 |
| ↳ 60 日動能 | +7.02% | −1.78% | 輸 |
| ↳ H1 residual 120 日 | +6.65% | −2.15% | 輸 |
| ↳ 技術指標重排 | +6.36% | −2.43% | 輸 |
| ↳ 10 日動能 | +5.97% | −2.82% | 輸 |
| **組合層** | | | |
| ↳ 台積電 22.5% 核心 | +7.79% | −1.00% | 輸 |
| ↳ 台積電核心＋分數加權 | +7.27% | −1.52% | 輸，警告多 |
| **模型** | | | |
| ↳ LightGBM | +7.62% | −1.17% | 輸 |
| ↳ AutoTS | +7.04% | −1.75% | 輸 |

- 2025–2026，40 個 24 日窗口
- 同一窗口、同一套組合與成本

<details>
<summary><b>展開：全部 18 項研究</b></summary>

數字是比同期 Mom20 多或少；各研究期間不同。

| 研究 | 比 Mom20 | 結論 |
|---|---:|---|
| **模型（2019–2021）** | | |
| ↳ [AutoTS](research/results/lgbm_jpx2/summary.md) | −0.41% | 輸 |
| ↳ [AutoTS 調參](research/results/tune_quick/summary.md) | −0.01% | 樣本太小 |
| ↳ [LightGBM JPX #2](research/results/lgbm_jpx2/summary.md) | −0.25% | 輸 |
| ↳ [相對強弱標籤](research/results/lgbm_alpha/summary.md) | −0.29% | 輸 |
| ↳ [成交對齊標籤](research/results/target_alignment/summary.md) | −0.53% | 輸 |
| ↳ [學習率](research/results/lr_diagnostic/) | 無差別 | 不是瓶頸 |
| ↳ [資料使用](research/results/data_usage/summary.md) | +1.13% | 採用，但最終測試仍輸 |
| **最終測試（2025–2026）** | | |
| ↳ [21 組設定](research/results/final_test/summary.md) | 最好 −1.17% | Mom20 最好 |
| **動能改良（2015–2026）** | | |
| ↳ [P1 動能參數](research/results/momentum_sweep/summary.md) | +0.37% | 未過門檻 |
| ↳ [Hybrid](research/results/hybrid/summary.md) | +0.26% | 未過門檻 |
| ↳ [Momentum-v2](research/results/momentum_v2/summary.md) | +0.23% | 未過門檻 |
| ↳ [H1 residual](research/results/h1_residual/summary.md) | −2.15% | 輸 |
| ↳ [Mom25](research/results/mom25_report/summary.md) | −0.29% | 打平 |
| ↳ [Residual 修正](research/results/residual_fixes/summary.md) | +0.06% | 打平 |
| ↳ [技術指標](research/results/technical_momentum/summary.md) | −1.00% | 輸 |
| ↳ [多尺度 MACD](research/results/macd_momentum/summary.md) | −0.24% | 輸 |
| ↳ [台積電核心](research/results/tsmc_core/summary.md) | −0.08% | 未過 |

- 通過門檻：+0.5%
- 全部 124 個設定：[all_experiments.md](research/results/all_experiments.md)

</details>

<details>
<summary><b>展開：舊版長期回放（v1／v2）</b></summary>

| 期間 | v2 | v1 | 0050 |
|---|---:|---:|---:|
| 2025 年 | +78.95% | +66.36% | +33.46% |
| 2026 年至 9/21 | +128.65% | +109.28% | +65.81% |
| 全期 | +309.16% | +248.15% | +121.28% |

- 固定參數 `x0352`
- 口徑和 24 日賽期不同，不能直接比較

</details>

---

## 5. 學到什麼

- **20 天剛好適合最近的行情**
  - 2015–2024：天數越長越好
  - 2025–2026：只有 20 天有效
- **加東西不會變好**
  - 技術指標、MACD、模型都輸
  - 重排會增加換手成本
- **台積電加碼幫助很小**
  - 要比 Mom20 其他持股強才有用
  - 2025–2026 反而少 1%
  - [原因分析](research/results/tsmc_core/analysis.md)
- **看過的資料不能當證據**
  - H1：+1.78% → −2.15%
- **拿獎的關鍵是不出錯**
  - Mom20 合規最乾淨
  - 每天準時送出 D-Plan

---

## 6. 開發者資訊

<details>
<summary><b>環境安裝</b></summary>

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone --filter=blob:none https://github.com/chrisPixelCraft/fintech_ETF.git
cd fintech_ETF
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-autots.txt
echo "$PWD/third_party/autots" > .venv/lib/python3.12/site-packages/fintech_etf_third_party.pth
```

</details>

<details>
<summary><b>常用指令</b></summary>

```bash
# 測試
.venv/bin/python -m unittest discover -s tests -q

# 跑一個實驗
PYTHONHASHSEED=0 .venv/bin/python -m research.run_experiment \
    --config research/configs/baseline_lgbm_raw.json --split dev --episodes 6 --workers 6

# 比較兩次實驗
.venv/bin/python -m research.compare research/runs/<run_A> research/runs/<run_B>
```

- 窗口明細在 `research/runs/`
  - 不進 git
- 整理後的結果在 `research/results/`
  - 要 commit
- 每次執行記一列
  - `research/registry.csv`
- 中斷後重跑同一指令會接續
- 圖表：`python -m momv2.main_figure`
- 全部實驗表：`python -m momv2.all_experiments`

</details>

<details>
<summary><b>資料與防止偷看未來</b></summary>

| 切分 | 期間 | 狀態 |
|---|---|---|
| dev | 2010–2021 | 已用來調參 |
| validation | 2022–2024 | 已打開多次 |
| holdout | 2025–2026-09 | 已用於最終測試與多項研究 |

- 決策只看前一天以前
- 不讀 `adj_close`
  - 它含未來股利
- 因果測試竄改未來資料
  - 決策必須完全不變
- 測試期存取有紀錄
  - `research/holdout_access_log.jsonl`
- 150 檔是 2026 名單
  - 早年有存活偏誤

</details>

<details>
<summary><b>檔案結構</b></summary>

```text
fintech_ETF/
├── production/          ← 每日提交：資料、對帳、engine、fallback、Active Share、D-Plan
├── competition/         ← 競賽核心：規則、資料截止、下單規劃、成交、帳本、回測
├── research/            ← 實驗入口、設定、基準策略、結果
├── momv2/               ← 動能改良研究、圖表與總表
├── hybrid/              ← Hybrid 研究：月營收與法人資料、LightGBM-v2
├── lgbm_strategy/       ← LightGBM 策略
├── autots_strategy/     ← AutoTS 策略與共用組合層 portfolio.py
├── tests/               ← 測試（含因果測試）
├── docs/                ← 任務定義、production spec、各研究 spec
├── official_docs/       ← 主辦方文件與 D-Plan schema
├── third_party/autots/  ← AutoTS 1.0.4 原始碼（含修補）
├── data/                ← 資料快照、股票池、規則
└── legacy/              ← V1–V5 舊版本
```

</details>

<details>
<summary><b>舊版本（V1–V5）</b></summary>

| 版本 | 做了什麼 | 結果 |
|---|---|---|
| v1／v2 | 長期回放，固定參數 `x0352` | 口徑不同，不能代表 24 日賽期 |
| V3 | 24 日賽期，搜尋 1,471 組參數 | 無一組全合規 |
| V4 | 官方均價管線、帳本驗證 | 沒有勝出策略；現行帳本源自此版 |
| V5 | 參考冠軍策略 | 資料未齊，沒跑出結果 |

- 說明：[legacy/README.md](legacy/README.md)
- 完整舊輸出：commit [`2769f876`](https://github.com/chrisPixelCraft/fintech_ETF/tree/2769f876ec4b9795ce5d8cc4e64b8274da58099c)

</details>
