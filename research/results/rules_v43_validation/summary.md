# Mom20：新版規則驗證

**保留 Mom20；正式提交仍 HOLD。** 新版 DataPack／D-Plan v4.3 已接入，程式回歸通過；完整 ETF 前15大資料及官方 Active Share 算法未齊，因此不能宣稱全面合規或新版規則下最優。

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

正反例都能通過 JSON schema，因錯誤引用與委託推導屬語意檢查；這不代表反例可以正式提交。

機器證據：[integrated.json](integrated.json)、[integrated_days.csv](integrated_days.csv)、[verification.json](verification.json)。整合前證據保留在 [baseline.json](baseline.json)。整合後報告保存25份核心程式的內容雜湊、規則與策略雜湊；驗收時逐份比對相符。

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

策略公式、lookback、Top25、95%投入、keep35、10%換手門檻與最後3日凍結均未改。四個子任務分別核對規則、實作、基線回歸與獨立驗收；沒有啟動參數搜尋。

## 判斷與限制

E-R43-1：同一固定策略、同一資料及結算器下，production與canonical逐日結果相同。這支持實作一致性，不構成獨立官方結算認證。

E-R43-2：2025-08-01缺價造成當日交易回滾，當日0檔、100%現金並記一次警告。兩條程式路徑一致重現此問題，不能把一致性誤讀成每日合規。

E-R43-3：回歸未提供歷史ETF持股，AS覆蓋為UNVERIFIED。live另有阻擋，避免把歷史報酬當作AS證明。配股應收與真拆股在研究資料中未完整區分，缺價估值也與官方DataPack可能不同；實盤必須用官方DataPack。

40個holdout窗口已看過，且月初、月中窗口互相重疊；1,104日不是獨立樣本。固定2026股票池回溯存在存活者偏誤。部分成交價是proxy。因此+8.79%是既有歷史結果重現，不是新樣本績效，也不保證未來報酬。

D-R43-1：保留凍結Mom20候選；不因本輪結果重新挑參數。正式提交維持HOLD，直到主辦確認AS算法且取得適用日期的完整ETF資料。未提供真正參賽隊伍的DataPack，本輪以合成邊界案例驗證帳戶接入，沒有上傳或交易。

舊2010資料不足窗口觸發emergency時，直接呼叫 `dplan.build` 會有既有 `KeyError`；正式daily路徑不會為emergency建D-Plan。本輪用已定義的2019+ dev範圍，沒有擴修無關歷史介面。既有測試另有一個registry CSV未關閉的 `ResourceWarning`，未影響84項測試。

## 重現

```bash
.venv/bin/python -m unittest discover -s tests -q
PYTHONHASHSEED=0 PYTHONPATH=. .venv/bin/python \
  research/results/rules_v43_validation/run_baseline.py --output-prefix integrated
```

目前資料固定、參數固定、沒有訓練或多seed搜索；重跑目的為回歸，不估計策略在不同市場資料下的泛化變異。

alphaXiv MCP未在本次工具清單中提供，沒有聲稱使用它或引用未讀論文。這輪問題是規則與實作驗證，以原始附件、程式與實跑證據處理。
