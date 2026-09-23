# 完整調參 A：每日產單流程

這個入口從前一交易日的原始日線、時線與帳本產生完整 D-Plan。它不會送單、不會上傳，也不會把本機檢查寫成主辦方認證。固定參數必須先通過 release audit；audit、設定或依賴程式任何一項雜湊不符，程式會停止。

## 一次執行

在專案根目錄執行：

```bash
python3 v2_offcial_best_deep_tuning.py plan \
  --state daily_auto/inbox/state.json \
  --daily daily_auto/inbox/daily.csv \
  --hourly daily_auto/inbox/hourly.csv \
  --source-manifest daily_auto/inbox/source_manifest.json \
  --operations-root daily_auto/private/operations \
  --ledger-id RECORD_ID_FROM_OPERATIONS_STORE \
  --calendar-record-id OFFICIAL_CALENDAR_RECORD_ID \
  --previous-packet daily_auto/runs/2026-10-27/full_tuned \
  --output daily_auto/runs/2026-10-28/full_tuned
```

`--operations-root` 與 `--ledger-id` 必須一起提供。程式只讀取已存在的 ledger record，並用官方 record 的現金、應收股息與實際股數覆蓋本地副本，再做第二次逐值對帳。這不代表平台目前保持連線，也不包含任何提交行為。

沒有可信 ledger record 時仍可做研究或待檢查輸出，但帳本會被標成未對帳，`submission_status` 必定是 `BLOCK_SUBMISSION`。命令不接受任意策略、候選參數或手工 score 檔；正式 CLI 只載入固定 `v2_offcial_best_deep_tuning` 與 `OfficialPlanner`。

規劃器有跨日的賣出／買入階段。已有持股時，`--previous-packet` 必須指向前一交易日不可變的產單包；程式會核對該 D-Plan 的精確 SHA、主辦方接受回執、前後兩本可信結算帳與實際股數變化。只有前包原因為 `SELL_THEN_WAIT_SETTLEMENT` 才恢復買入階段。缺任一證據就產生空單並阻擋，不接受手填 `buy_phase`。明確空倉且現金、NAV 都等於固定初始本金時，才能以賣出階段作首次啟動。

## 輸入必須完整

`state.json` 使用 [state.template.json](examples/state.template.json) 的結構。正式狀態為 `LIVE`；歷史示範必須明列 `HISTORICAL_REPLAY`。持股股數必須是正整數。公司行動形成的整數零股會原數保留，程式不會假裝 `SELL_ALL`；非整數股數會直接停止正式產單。

日線至少包含 `date,symbol,open,high,low,close,volume`。時線至少包含 `symbol,timestamp,open,high,low,close,volume`，其中 `timestamp` 必須帶時區。日收盤資料的 `content_as_of` 不得早於台北時間 13:30；四小時條件只消費 09:00、10:00、11:00、12:00 起始且已完整結束的四根 bar。來源檔中其他時點仍原樣封存並記錄排除筆數，不重新解釋成已完成的小時 bar。資料會先截到 `state.as_of`，當日 150 檔官方身分必須全數有報價。歷史代碼 `5371` 只依已稽核的公司身分對應輸出成官方現行代碼 `3718`。

`source_manifest.json` 的最小結構如下；值必須來自實際取得的檔案及時間，不可把事後下載時間改寫成較早時間：

```json
{
  "as_of": "2026-10-27",
  "known_at": "2026-10-27T19:30:00+08:00",
  "sources": [
    {
      "role": "daily",
      "authority": "twse",
      "source_url": "https://www.twse.com.tw/...",
      "content_as_of": "2026-10-27T13:30:00+08:00",
      "known_at": "2026-10-27T19:20:00+08:00",
      "sha256": "64位小寫十六進位"
    },
    {
      "role": "hourly",
      "authority": "vendor",
      "source_url": "https://資料來源/...",
      "content_as_of": "2026-10-27T13:30:00+08:00",
      "known_at": "2026-10-27T19:25:00+08:00",
      "sha256": "64位小寫十六進位"
    }
  ]
}
```

每個 source 的 SHA-256 必須對應到一個實際封存的輸入檔。來源時間、signal 時間或 state 證據晚於程式開始時間時，流程會以 look-ahead 錯誤停止。

`passive_cap_days` 不能手填。它只能由連續的可信結算帳、官方交易日與收盤估值重建：前後持股股數與現金都完全相同時，才把超限視為價格漂移；在官方連續交易日逐日累加，五日寬限後第六日列為逾期。歷史缺口、交易造成的超限或估值證據不足都標成未知。若持股已超過權重上限而天數未知，本機檢查會阻擋。官方警告次數缺欄位就是 `UNKNOWN`，不會當成 0；只有官方帳本實際提供的次數才可判斷三次警告門檻。

## 如何看結果

輸出目錄只建立一次；同名目錄存在時不覆寫。主要檔案如下：

| 檔案 | 用途 |
|---|---|
| `D-Plan_<隊號>_<日期>.json` | 完整來源、觀察、推論、異動、續抱與委託鏈 |
| `preflight.json` | schema、公式、現金、持股數、權重、公司事件及 Active Share 的本機檢查 |
| `receipt.json` | 設定、程式、輸入與所有輸出檔的雜湊；明列未使用網路送單 |
| `input_snapshot/` | state、固定 config、計算後 signals，以及原始 daily/hourly/manifest、ledger、adapter/spec 與本地 OperationsStore 稽核鏈位元組 |
| `code_snapshot/` | 本次實際綁定的產單與 release audit Python 來源 |

退出碼 `0` 只表示本機 preflight 沒有阻擋；`2` 表示資料、證據或規則檢查未通過。即使是 `0`，`validate.py` 仍固定輸出 `BLOCK_SUBMISSION`，因為目前沒有主辦方正式 verifier、上傳 API 或成功回執整合。

Active Share 證據或指定交易日的公司行動證據缺失時，禁止提交。零筆委託也是完整結果：D-Plan 仍保留每檔續抱理由與雜湊，不能改用舊日期檔案，也不能人工增刪個股或股數。

## 本機驗證

```bash
python3 -m unittest tests.test_daily_auto_full_tuned -v
```

測試涵蓋空單、同日不同股票買賣、公式一致、前視阻擋、未確認 AS／公司事件、整數零股保留、非整數股拒絕、OperationsStore 對帳，以及原始資料與程式碼封存。測試不連網、不排程、不提交。
