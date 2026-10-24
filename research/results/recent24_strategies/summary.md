# 原README策略：最近24交易日重測

**這一窗Residual 120日領先；AutoTS略高於0050。** 原表13個固定版本全數重測，期間2026-09-16–10-22，起始全現金10億元，每日只讀D−1可用資料，最後3日沿用各版本既定凍結交易規則。

## 結果

| 策略 | 淨報酬 | 最大回撤 | 費稅／初始NAV | 對0050 |
|---|---:|---:|---:|---:|
| Residual 120日（H1） | +3.58% | 6.06% | 0.32% | +2.97 pp |
| AutoTS h5 | +0.73% | 5.60% | 1.62% | +0.12 pp |
| Mom10 | -4.22% | 11.48% | 1.89% | -4.83 pp |
| Mom60 | -4.74% | 9.63% | 0.58% | -5.35 pp |
| 技術指標Composite | -5.19% | 9.06% | 1.81% | -5.80 pp |
| 多尺度MACD | -6.33% | 11.12% | 1.57% | -6.94 pp |
| Mom25 | -7.25% | 12.81% | 1.18% | -7.86 pp |
| LightGBM D lr0.01 | -7.56% | 14.30% | 1.74% | -8.17 pp |
| 台積電22.5%＋等權 | -7.82% | 12.06% | 0.69% | -8.43 pp |
| Residual 20日（扣等權） | -7.93% | 12.37% | 1.09% | -8.54 pp |
| 台積電22.5%＋混合權重 | -8.65% | 13.19% | 1.35% | -9.26 pp |
| Mom20 | -10.12% | 13.79% | 1.02% | -10.74 pp |
| Residual 20日（扣0050） | -11.26% | 14.86% | 1.11% | -11.87 pp |

0050同期市場參考為 **+0.6118%**；這是未扣策略交易成本的close-to-close含權息參考，不是競賽可交易投組。13組均0次規則警告、0未成交；歷史ETF持股缺乏，Active Share全部未驗證。

![最近24日策略比較](comparison.png)

## 公平性與原版設定

方法清單來自 `legacy/ml/momv2/main_figure.py:METHODS` 與 `legacy/ml/results/main_table.md`，不是看到近期結果後再挑版本。全部共用目前canonical下單、結算、費率、150檔池及同一份修正資料。組合層保留原版本差異：台積電核心策略維持22.5%及其原權重規則，其餘依原表等權／特徵設定。

| 方法 | 固定設定 |
|---|---|
| Mom20／25／10／60 | 各自原lookback；Top25／95%／keep35／10%換手門檻 |
| Residual20扣0050 | resid20／beta60 |
| Residual20扣等權 | resid20／beta60；leave-one-out市場 |
| Residual120 H1 | 原freeze：resid120／beta250 |
| Composite | 原19指標composite |
| MACD | 原8/24、16/48、32/96多尺度 |
| 台積電＋等權 | 原22.5%核心，其餘24檔 |
| 台積電＋混合 | 原0.85 shifted score＋0.15 inverse60d vol |
| AutoTS | 原autots_h5；原7候選／4 validation窗口選模程序 |
| LightGBM | 原lgbm_D_lr0.01；固定116樹／horizon10／每5日重訓 |

模型資料起點2019年。LGBM沿用2020–2024校準的樹數，不在本窗口early-stop挑參；所有訓練標籤須成熟至D−1。AutoTS保留原模型選擇流程，沒有因本窗報酬更換候選、seed或搜尋預算。固定流程在修復資料下選出ARIMA，原缺值資料曾選出WindowRegression；這是原演算法對輸入變化的反應。

## 本輪修復的資料問題

我在前次增量下載後拼接歷史資料時，沒有重建7/1相對6/30的第一日報酬，造成149檔股票加0050共150筆return=NaN，但價格仍有效。LGBM首日60日波動特徵因此全部缺值；這是本輪測試發現的資料問題。

修復只依6/30 close與7/1 close／split／dividend計算既有公式，未改價格、策略參數或原快取。13組統一改用相同新snapshot；修復前結果完整留在各組 `original_panel_evidence/`。Mom20完全不變；Mom60、H1、Composite、MACD與AutoTS受影響，因此最終表只採修復後數字。

修復資料SHA256：`fe3f91fa6e96aad89fff6bd8de9b768d31ff7897ac7b37b16a4494fec13de15d`。邊界修復可由 `production_runs/preflight_20261026/strategy_retest/repair_boundary.py` 精確重建；總表內含全部來源雜湊與13份config。

## 驗證與解讀

13組各24日共312筆日觀測，執行時各組獨立驗帳通過。主代理另從每組保存的CSV重新驗證股數、費稅、現金、NAV及末期股利入帳，容差0.01元；全部通過，並逐份核對共同輸入SHA。Mom20仍精確重現−10.1245967582%。模型每天的最大資料日期與成熟標籤日期均有保存；6組非Mom/residual另經子代理交叉QA。

這是單一已觀測窗口，H1的+3.58%與AutoTS的+0.73%不構成新策略優越性證明；AutoTS只高出0050約0.12個百分點。原H1在較長既有測試曾落後Mom20，不能只看這窗就改正式版本。策略設定與正式候選均未替換。

10/23全151筆資料volume=0，未視為有效收盤，因此最後一日仍是10/22。公司配股與現金權益尚未獨立對照官方結算；名目股數價格換算及2890六筆來源差異沿用前次診斷，不能把本結果視為官方認證。

## 證據與重現

[比較CSV](comparison.csv)、[全部數字與設定](comparison.json)。原始成交、持股、委託與逐日帳本保存在 `production_runs/preflight_20261026/strategy_retest/`。

```bash
.venv/bin/python production_runs/preflight_20261026/strategy_retest/repair_boundary.py
PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python production_runs/preflight_20261026/strategy_retest/momentum/run.py
PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python production_runs/preflight_20261026/strategy_retest/technical/run_retest.py
PYTHONHASHSEED=0 OPENBLAS_NUM_THREADS=1 .venv/bin/python production_runs/preflight_20261026/strategy_retest/models/run_models.py --strategy autots_h5 --full
PYTHONHASHSEED=0 OPENBLAS_NUM_THREADS=1 .venv/bin/python production_runs/preflight_20261026/strategy_retest/models/run_models.py --strategy lgbm_D_lr0.01 --full
PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python production_runs/preflight_20261026/strategy_retest/collect.py
```
