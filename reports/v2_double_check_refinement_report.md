# v2 double-check：第三輪局部細搜結果

**研究候選；正式 policy 仍為 `BLOCK_SUBMISSION`。** 獨立發布稽核結果見[稽核檔](../outputs/v2_double_check_refinement/audit.json)。Active Share、官方帳本與平台收件證據不能靠回測補足。

本輪完整覆蓋四軸 81 個位置，1 組沿用已稽核的 `x0352`；80 組新設定各在兩池回放，共 160 次新試驗。連同前兩輪累計 1,489 組不同設定。官方事後池有 4／80 組新候選通過已量測門檻。最終選擇 `x0352`（維持原 x0352）。

## 與原候選比較

| 股票池 | 候選 | 帳面總報酬 | 最大回撤 | 已量測門檻合格 | 超過當日總成交量的成交 | 最大成交量參與率 |
|---|---|---:|---:|---|---:|---:|
| official_ex_post | `原 x0352` | 309.16% | 22.22% | 是 | 13 | 7.07× |
| official_ex_post | `x0352` | 309.16% | 22.22% | 是 | 13 | 7.07× |
| historical_pit | `原 x0352` | 156.68% | 22.89% | 是 | 21 | 12.92× |
| historical_pit | `x0352` | 156.68% | 22.89% | 是 | 21 | 12.92× |

僅官方事後池決定參數，歷史池原樣重播。成交量參與率不是官方明定門檻，但超過當日總量的模擬成交不能視為真實可成交收益。

## 年度經濟淨值比較

依各日 `economic_nav` 計算；2026 年截至 9 月 21 日，並非全年或年化報酬。

| 期間 | 本輪獲選 v2 | 原 x0352 | v1 | 0050 |
|---|---:|---:|---:|---:|
| 2025 年 | 78.95% | 78.95% | 66.36% | 33.46% |
| 2026 年截至 9 月 21 日 | 128.65% | 128.65% | 109.28% | 65.81% |

v2 與 v1 使用官方事後股票池，0050 是同期間 ETF 基準。v1 有已量測違規，0050 不符合競賽個股與持股檔數要求；兩者只供研究對照。

## 新候選合格表

| 排名 | 候選 | 報酬 | 最大回撤 | 雙邊換手率 |
|---:|---|---:|---:|---:|
| 1 | `y0029` | 289.24% | 29.01% | 24.32 |
| 2 | `y0049` | 277.43% | 21.42% | 25.96 |
| 3 | `y0046` | 267.39% | 23.52% | 26.85 |
| 4 | `y0005` | 255.29% | 21.91% | 27.57 |

不合格原因可重疊。官方事後池新候選的逐項問題數：

| 已量測門檻 | 有問題候選數 |
|---|---:|
| `measured_hard_breach_days` | 27 |
| `no_valid_plan_days` | 74 |
| `unfilled_orders` | 7 |
| `simulated_warning_days` | 27 |
| `stale_held_price_days` | 70 |
| `hold_without_envelope_days` | 3 |
| `execution_price_bound_breaches` | 61 |
| `raw_rule_breach_days` | 27 |

## 獲選參數與解讀

| 本輪調整軸 | 獲選值 |
|---|---:|
| `momentum_weight` | 0.55 |
| `long_return_fraction` | 0.2 |
| `volume_low` | 0.8 |
| `volume_high` | 2.5 |

[四軸細搜規格](../docs/v2_double_check_refinement_protocol.md)以外的參數固定於 `x0352`。本輪維持八項零計數、417 個交易日完整性及無失格條件；不合格的高報酬組合不入選。

2025-01-02～2026-09-21 的 417 日皆已參與開發。2026 年公布的官方股票池回填至 2025 年，含成分股前視偏誤；歷史池也非未見測試。重複選參進一步增加過度擬合風險。均價成交模型沒有滑價與市場衝擊，帳面報酬不可當作實際可成交報酬。

[試驗表](../outputs/v2_double_check_refinement/trials.csv) · [網格位置](../outputs/v2_double_check_refinement/grid.json) · [獲選設定](../outputs/v2_double_check_refinement/selection.json) · [月度淨值](../outputs/v2_double_check_refinement/monthly.csv) · [逐條規則](../docs/v2_double_check_rules.md)
