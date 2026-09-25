# ETF 策略：150 檔挑 25 檔、24 個交易日

每天從固定 150 檔台股選 25 檔，在競賽規則內跑 24 個交易日，目標是期末 NAV 最大。正式策略是 **20 日動能（Mom20）**。

## 先看這裡

- 正式策略：Mom20
  - 2025–2026 平均 +8.79%／24 日
  - 所有替代方案都沒贏它
  - 見「[2. 主要結果](#2-主要結果)」
- 賽前設定已完成
  - `team_id`：`TEAM_11076`
  - 首個交易日：2026-10-26
- 每日入口：`./run_daily.sh`
  - 見「[1. 產生 D-Plan](#1-產生-d-plan)」
- 2015–2026 資料全部看過
  - 不再從歷史調參
- 舊版本在 `legacy/`

---

## 1. 產生 D-Plan

### 賽前一次

1. 重新凍結首日建倉名單
   ```bash
   .venv/bin/python -m production.etf_holdings --out etf.csv
   .venv/bin/python -m production.cold_start --etf-holdings etf.csv --yahoo-dir <最新 Yahoo 快取>
   ```
2. commit 乾淨版本
   - D-Plan 的 `code_version` 不可帶 `-dirty`
3. 清掉 dry run 狀態
   - 正式狀態放 `production_runs/state/`
4. 先跑一次 dry run，確認能產生合法 D-Plan
   ```bash
   ./run_daily.sh 2026-09-24 --offline       # 用本機資料，不連網
   ```

### 每個交易日 T

官方收件時間：前一日 19:30 到 T 日 08:55。本流程在 T 日 05:00–08:55 執行。

```bash
# 首日 10/26：還沒有持股，不用 --holdings
./run_daily.sh 2026-10-26

# 之後每天：先從後台匯出前一日結算持股
./run_daily.sh 2026-10-27 --holdings <後台匯出的持股檔>
```

| 結束碼 | 意思 | 要做什麼 |
|---|---|---|
| 0 | `READY_TO_SUBMIT` | 上傳 `production_runs/<T>/D-Plan_TEAM_11076_<T>.json` |
| 2 | 需要人工看 | 讀 `production_runs/<T>/audit.md`；有 D-Plan 就先上傳，再查原因 |

D-Plan 內的狀態：

| 狀態 | 意思 |
|---|---|
| `NORMAL` | Mom20 正常執行 |
| `COLD_START_FALLBACK` | 首日用凍結名單建倉 |
| `FALLBACK_HOLD` | 資料或對帳有問題，沿用持股 |
| `FALLBACK_COMPLIANCE_REPAIR` | 只做最少的合規修補 |
| `EMERGENCY_REVIEW_REQUIRED` | 連合法 fallback 都產生不了，08:55 前人工處理 |

- 每天自動抓主動型 ETF 前 10 大
  - 檢查並修補 Active Share
- 送件是人工上傳
- 細節：[docs/production_spec.md](docs/production_spec.md) 第 11、14 節

---

## 2. 主要結果

![2025–2026 每 24 個交易日的平均報酬：Mom20 最高](docs/figures/main_figure.png)

**結論：沒有任何方法贏過 Mom20。**

### 表 1：2025–2026 測試（40 個窗口）

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

- 報酬是每 24 個交易日的平均
- 同一窗口、同一套組合與成本

### 表 2：全部研究

數字是比同期 Mom20 多或少；各研究期間不同，細節點連結。

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

- 通過門檻是 +0.5%
- 全部 124 個設定：[all_experiments.md](research/results/all_experiments.md)

### 表 3：舊版長期回放

| 期間 | v2 | v1 | 0050 |
|---|---:|---:|---:|
| 2025 年 | +78.95% | +66.36% | +33.46% |
| 2026 年至 9/21 | +128.65% | +109.28% | +65.81% |
| 全期 | +309.16% | +248.15% | +121.28% |

- 固定參數 `x0352`
- 口徑和 24 日賽期不同
  - 不能直接比較

---

## 3. 正式策略

| 項目 | 設定 |
|---|---|
| 選股 | 近 20 個交易日還原報酬最高的前 25 檔 |
| 權重 | 等權，投入 95% |
| 續抱 | 已持有的股票排名 ≤ 35 就不賣 |
| 換手門檻 | 換手 < 10% 就不調整 |
| 最後 3 天 | 不交易 |

- 設定檔：`production/strategy.json`
- 決策只用 D−1 以前的資料
- 下單以漲停價計算股數
  - 現金不會變負
- 資料或合規出問題
  - 自動改用 fallback
- production 與回測逐日一致
  - 40 窗口、960 天：[production_replay](research/results/production_replay/summary_holdout.md)

---

## 4. 比賽規則重點

| 項目 | 規定 |
|---|---|
| 賽期 | 2026-10-26 → 11-27，官方稱 24 個交易日 |
| 本金 | 10 億元，全現金起始 |
| 持股數 | 每天 20–30 檔 |
| 單股上限 | NAV 10%，2330 為 25% |
| 現金 | 不能負數，且 < 25% |
| 交易 | 整張、只做多 |
| 成交價 | 當日官方成交均價 |
| Active Share | 對每檔主動 ETF 前 10 大 ≥ 20%，連續 2 天違反可取消資格 |
| 警告 | 累積 3 次取消資格 |

- 10/26 可能是證交所補假
  - 從 10/26 計數，兩種情況都對
- 細節：[docs/task.md](docs/task.md)

---

## 5. 研究結論

全部研究見「[2. 主要結果](#2-主要結果)」的表 2；規則在 `docs/` 各研究的 spec。

### 學到什麼

- **最佳回看天數會翻轉**
  - 2015–2024 長窗口較好
  - 2025–2026 只有 20 日有效
- **扣大盤沒有額外價值**
  - 20／25 日時和動能打平
  - 0050 被台積電主導
- **技術指標沒有增量**
  - 避開過熱反而輸 −2.22%
  - 重排大幅提高換手
- **台積電加碼幫助很小**
  - 比的是 Mom20 其他持股
  - 2025–2026 反而 −1.00%
  - 原因：[analysis](research/results/tsmc_core/analysis.md)
- **看過的資料不能當證據**
  - H1：+1.78% → −2.15%
- **穩定度**
  - 台積電 22.5%＋等權波動最小
  - 但 2025–2026 少約 1%
  - Mom20 虧損窗口最少

---

## 6. 資料與防止偷看未來

| 切分 | 期間 | 狀態 |
|---|---|---|
| dev | 2010–2021 | 已用來調參 |
| validation | 2022–2024 | 已打開多次 |
| holdout | 2025–2026-09 | 已用於最終測試與多項研究 |

- 決策只看 D−1 以前
- 不讀 `adj_close`
  - 它含未來股利
- 因果測試竄改未來資料
  - 決策必須完全不變
- 測試期存取有紀錄
  - `research/holdout_access_log.jsonl`
- 150 檔是 2026 名單
  - 早年有存活偏誤

---

## 7. 環境與常用指令

需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone --filter=blob:none https://github.com/chrisPixelCraft/fintech_ETF.git
cd fintech_ETF
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements-autots.txt
echo "$PWD/third_party/autots" > .venv/lib/python3.12/site-packages/fintech_etf_third_party.pth
```

```bash
# 測試
.venv/bin/python -m unittest discover -s tests -q

# 跑一個實驗
PYTHONHASHSEED=0 .venv/bin/python -m research.run_experiment \
    --config research/configs/baseline_lgbm_raw.json --split dev --episodes 6 --workers 6

# 比較兩次實驗
.venv/bin/python -m research.compare research/runs/<run_A> research/runs/<run_B>
```

- 每窗口明細不進 git
  - 在 `research/runs/`
- 整理後的結果要 commit
  - 在 `research/results/`
- 每次執行記一列
  - `research/registry.csv`
- 中斷後重跑同一指令
  - 從完成的窗口接續
- 各研究的入口寫在 `momv2/`、`hybrid/` 各模組開頭

---

## 8. 檔案結構

```text
fintech_ETF/
├── production/          ← 每日提交：資料、對帳、engine、fallback、Active Share、D-Plan
├── competition/         ← 競賽核心：規則、資料截止、下單規劃、成交、帳本、回測
├── research/            ← 實驗入口、設定、基準策略、結果
├── momv2/               ← 動能改良研究：訊號、策略、評估
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

---

## 9. 舊版本

| 版本 | 做了什麼 | 結果 |
|---|---|---|
| v1／v2 | 長期回放，固定參數 `x0352` | 見表 3；口徑不同，不能代表 24 日賽期 |
| V3 | 24 日賽期，搜尋 1,471 組參數 | 無一組全合規 |
| V4 | 官方均價管線、帳本驗證 | 沒有勝出策略；現行帳本源自此版 |
| V5 | 參考冠軍策略 | 資料未齊，沒跑出結果 |

- 說明：[legacy/README.md](legacy/README.md)
- 完整舊輸出：commit [`2769f876`](https://github.com/chrisPixelCraft/fintech_ETF/tree/2769f876ec4b9795ce5d8cc4e64b8274da58099c)
