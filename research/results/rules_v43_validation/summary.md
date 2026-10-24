# Mom20：新版規則驗證

**保留 Mom20，正式提交 HOLD。** DataPack／D-Plan v4.3 接入及回歸通過；缺完整 ETF 前15大與官方 AS 算法，不能宣稱全面合規或新版最優。

## 實跑結果

| 檢查 | 結果 |
|---|---|
| 整合單元測試 | 84／84通過；16.015秒 |
| 歷史窗口 | 46個；1,104日觀測 |
| 逐日一致性 | 全部 `CONSISTENCY_PASS` |
| NAV／現金最大差 | 0元 |
| 獨立驗帳問題 | 0 |
| 警告／失格 | 1／0 |
| 既有40窗平均報酬 | +8.7940% |
| 既有40窗中位報酬 | +10.9855% |
| 成交價來源 | 官方4,163筆；HLC3 proxy 673筆 |
| 官方schema範例 | 正、反例各1份，結構均通過 |
| 獨立審查 | 13項新測試、4項既有AS測試通過 |
| 完整回歸時間 | 23.854秒 |

JSON schema 不查引用／委託推導語意，故正反例皆過；反例仍不可提交。

證據：[integrated.json](integrated.json)、[integrated_days.csv](integrated_days.csv)、[verification.json](verification.json)。整合前： [baseline.json](baseline.json)。驗收已核對25份核心程式、規則及策略雜湊，均相符。

## 變更與證據

| 變更 | 程式與回歸 |
|---|---|
| 官方帳戶真值 | `production/state.py:load_datapack`；`test_datapack_v43.py` |
| 應收權益排除 | 官方cash／nav／shares；不同於aum |
| D-Plan v4.3 | `production/dplan.py:build`；官方schema逐byte相同 |
| AS前15大／15% | `production/active_share.py:check`；邊界測試 |
| 未齊資料阻擋 | 10檔、缺檔、未來日期、NaN不判正式通過 |
| 整張後重驗AS | `production/engine.py:_check_planned_active_share` |
| live提交關卡 | `production/run_daily.py:run`；fallback後再驗 |
| 離線隔離 | `SIMULATION_ONLY`；失敗重跑隔離舊輸出 |

公式、lookback、Top25、95%投入、keep35、10%換手、最後3日凍結不變。四子任務核對規則／實作／基線回歸／獨立驗收，未搜參數。

## 判斷與限制

E-R43-1：同策略／資料／結算器，production與canonical逐日相同；支持實作一致，非獨立官方結算認證。

E-R43-2：2025-08-01缺價回滾，0檔、100%現金、一次警告；兩路徑均重現。一致性不等於每日合規。

E-R43-3：無歷史ETF持股，AS為UNVERIFIED，live另阻擋；報酬非AS證明。研究資料未完整區分配股應收／真拆股，缺價估值亦可能異於官方；實盤必用官方DataPack。

40個holdout已見、月初／月中窗重疊，1,104日非獨立；2026池回套有存活偏誤、部分成交為proxy。因此+8.79% 僅重現舊結果，非新樣本或未來保證。

D-R43-1：保留凍結Mom20、不重挑參數；待主辦確認AS算法及取得適用日期完整ETF資料前HOLD。無真實隊伍DataPack，僅合成邊界案例驗證接入；未上傳／交易。

2010不足資料窗的emergency直接呼叫 `dplan.build` 有既有 `KeyError`；daily不為emergency建D-Plan。本輪用既定2019+ dev，未擴修歷史介面。registry CSV未關閉的既有 `ResourceWarning` 不影響84項測試。

## 重現

```bash
.venv/bin/python -m unittest discover -s tests -q
PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python \
  research/results/rules_v43_validation/run_baseline.py --output-prefix integrated
```

固定資料／參數，無訓練或多seed搜索；重跑僅驗回歸，不估市場泛化變異。

alphaXiv MCP未提供，未聲稱使用或引用未讀論文；以原附件、程式、實跑證據驗規則／實作。
