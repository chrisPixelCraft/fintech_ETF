# v3：第四輪可行性搜尋

**`NO_ELIGIBLE_CANDIDATE`／`BLOCK_SUBMISSION`**。新增 **384 組**，排除前三輪 1,087 組；四輪共 **1,471 組**。

三個開發代表鄰域各128組。重現154個基準窗，再測必要窗 **2015-08-03 起24個交易日**；失敗即無法達原100%開發通過門檻。

## 實際結果

| 參數來源 | 新組數 | 完成24日 | 無原始超限 | 全項通過 | 零股持倉 |
|---|---:|---:|---:|---:|---:|
| 第一輪開發代表鄰域 | 128 | 128 | 128 | 0 | 128 |
| 第二輪開發代表鄰域 | 128 | 128 | 128 | 0 | 128 |
| 第三輪開發代表鄰域 | 128 | 128 | 128 | 0 | 128 |

無原始超限非全項通過；零股與其他欄重疊，不可相加。[彙整 CSV](24d_round4_summary.csv)與[逐窗口資料](../outputs/24d_round4/bottleneck.csv)涵蓋全部嘗試。

| 原因組合 | 新參數組數 |
|---|---:|
| 僅零股持倉門檻 | 384 |

成交全為1,000股倍數，384組有公司行動零股；這不等於零股委託。官方契約未明，保留保守門檻，非確認官方違規。

## 結論與未執行項目

384組均敗於必要窗，依[early pruning](../docs/v3_tuning.md)停止、保留原設定。已完成可行性篩選，非全期報酬排名。

新參數全開發／驗證、2023–2024、近期／季節窗均 **NOT_RUN**，無中位數、年度或累積報酬。154基準窗含舊開發／驗證，僅核對口徑。

全期比較見[第三輪](24d_round3.md)、[CSV](24d_round3_comparison.csv)，非本輪成績。單窗可否定100%資格，不能推論全期報酬或全參數空間無解。

## 搜尋邊界與限制

只調整 `return_short`、`return_long`、`momentum_weight`、`long_return_fraction`、`max_replacements_per_day`、`replacement_margin`，其餘於各來源固定。Seed 2409202604、範圍依規格；來源僅按開發排名，不用驗證／近期報酬。

多參數局部抽樣，非單參數因果或窮舉。開發2010–2018、驗證2019–2022不變；驗證／後期已見，非首次未見 holdout。2026名單回套、後期選基準另有前視／選擇偏誤。

Yahoo快照截至2026-09-23；每窗口從10億元零持股開始，D−1決策、次日開盤價近似成交，雙邊手續費0.1425%及賣出稅0.3%。無市場衝擊模型；缺Active Share、官方均價結算／公司行動／收件證據。本地稽核非正式合規。v2 長期 309.16%不變，不能比單一24日窗。

先釐清公司行動／零股契約，再決定擴搜；不得移除門檻製造合格候選。

## 證據與重現

已驗證389組帳本、542次窗口嘗試。查看[設定](../config/24d_round4_study.json)、[凍結紀錄](../outputs/24d_round4/final_selection.json)、[驗證結果](../outputs/24d_round4/verification.json)及[結果雜湊](../outputs/24d_round4/result_manifest.json)。

```bash
python scripts/verify_24d_round4.py --rebuild
python scripts/report_24d_round4.py --verify
```

另建輸出重跑；同一命令可接續未完成組別：

```bash
python scripts/expand_24d_round4.py --workers 4 --output outputs/24d_round4_replay
python scripts/verify_24d_round4.py --output outputs/24d_round4_replay --rebuild
```
