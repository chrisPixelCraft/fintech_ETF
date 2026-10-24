# 玉山 ETF 競賽：每日選股

150 檔挑 22 檔，跑 24 個交易日；目標是組內前 6 名。

- 目前策略：K2＋排行榜切換
  - K2：投信買超＋營收創新高，集中前 10 檔，共 22 檔
  - 第 13 日起排名前 6 → 分天換成 T2 鎖住
  - Mom20 保留為回滾設定
  - [策略細節](docs/strategy.md)
- 2025–2026 平均：K2 +9.20% 最高
  - 只比 Mom20 多 0.24pp
  - 0050 同期 +4.70%
  - [數據與檢核](research/results/benchmark_20261025/CODEX_REPORT.md)
- 近期表現
  - 最近 24 日：K2 −1.46%，0050 +0.61%
  - 近 5 期：贏 0050 3 期，平均 −0.11pp
  - [最近 24 日圖](research/results/benchmark_20261025/figures/latest24.png)
  - [近 5 期圖](research/results/benchmark_20261025/figures/recent5.png)（[完整 22 種](research/results/benchmark_20261025/figures/recent5_all.png)）
- 測過但未採用
  - K3w（動能25%＋營收75%）：驗證期通過，最近兩期各輸 0050 約 10pp
  - [加權搜尋](research/results/k3_weights/figures/weights.png)、[大贏大輸比較](research/results/k3_composite/figures/tails.png)
- **新版規則仍待外部確認**
  - 已接 DataPack／D-Plan v4.3
  - AS 前15大／15%；算法未確認會阻擋
  - [規則與操作](docs/rules_v43.md)
  - [本輪測試](research/results/rules_v43_validation/summary.md)
- 每日提交：`./run_daily.sh <T> --datapack <DataPack.json> --etf-holdings <etf_top15.csv> --rank <組內排名>`
  - 05:00–08:55 上傳 D-Plan
  - 首日 10/26，隊號 `TEAM_11076`
  - [怎麼跑](docs/how_to_run.md)
- [比賽規則](docs/task.md)
- [舊研究](legacy/README.md)

![2025–2026 平均報酬：K2（22 檔）最高，與 Mom20 接近](research/results/benchmark_20261025/figures/holdout40.png)
