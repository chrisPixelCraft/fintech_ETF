# Full-tuned CLI 研究輸入（已由新版取代）

這份輸入與既有 packet 只保留歷史。其 code closure 早於來源時間驗證、跨日買賣 phase、accepted-plan supersession 與被動超限歷史修正；目前示範請看 [最終 verified receipt](../../runs/full_tuned_demo_verified_final_2026-09-21/receipt.json)。

- 訊號日：`2026-09-21`
- 擬議次日：`2026-09-22`；沒有把它寫成已驗證交易日
- 帳本：研究假設本金 NTD 1,000,000,000、無持股；不是主辦方結算帳本
- 本機實際取得時間：`2026-09-22T14:36:20+08:00`
- 日線與時線保留在 repository 原始路徑，SHA-256 綁在 `source_manifest.json`

[已產生 packet 的 receipt](../../runs/full_tuned_demo_2026-09-21/receipt.json) 使用固定 `full_tuned_v2_f0019`，含 20 筆由公式導出的買單；它的狀態是 `BLOCK / BLOCK_SUBMISSION`。實際阻擋包括：非 LIVE 狀態、帳本未對帳、Active Share 未知、公司事件未確認、交易日未驗證、本地檔案不是官方來源，以及產生時間晚於 08:55。這些阻擋是示範的預期結果，沒有用猜測資料改成綠燈。
