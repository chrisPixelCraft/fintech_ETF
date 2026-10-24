# MACD Momentum：STOP，技術面永久關閉

## 7. 結果

[完整表](../results/macd_momentum/summary.md)：research/results/macd_momentum/summary.md。

| | 對 Mom20 平均 Δ | 95% CI | 2025–2026 | 周轉（Mom20 3.68） |
|---|---:|---:|---:|---:|
| **三尺度 MACD 重排** | **−0.24%** | −0.49% ~ +0.00% | −1.06% | 4.82 |
| 快 (8, 24) | −0.10% | −0.32% ~ +0.13% | −1.26% | 4.20 |
| 中 (16, 48) | −0.31% | −0.58% ~ −0.04% | −1.36% | 5.03 |
| 慢 (32, 96) | −0.61% | −0.90% ~ −0.32% | −1.35% | 5.37 |

8 項僅換手、合規過關；四段 3 負（+0.01%、−0.01%、−0.34%、−1.06%）。符合預測：越慢越差，2025–2026 最差。Mom20 最差 10 窗平均 Δ +0.36%，僅診斷。

**TECHNICAL_MOMENTUM_NO_INCREMENTAL_EDGE**：無第三次 MACD 測試。

Production 維持 Mom20；分支 `claude/macd-tsmc`，2026-09-25 凍結。規則先於 MACD 策略，正式方案一次。

## 0. 定位與偏誤

[Technical Momentum](technical_momentum_spec.md) 的 MACD 12/26/9 ÷ 價格（−0.36%）、histogram 斜率（−1.18%）假說已關閉。本次看過結果後，改借 Baz et al.（2015）、Deep Momentum Networks（Lim, Zohren, Roberts 2019）的波動標準化／多尺度訊號；不搬 LSTM、Sharpe loss、多空或波動目標。

2015–2026 全已見，僅 retrospective causal test，非 validation；不論結果均無第三次。

## 1. 訊號

`P` = action-neutral 報酬累乘還原價；EWMA 尺度 S，α = 1 ÷ S（`adjust=False`）。

```text
MACD(S, L) = EWMA(P, S) − EWMA(P, L)
q          = MACD ÷ 近 63 日 P 的標準差
Y          = q ÷ 近 252 日 q 的標準差
尺度       = (8, 24)、(16, 48)、(32, 96)
MACD 分數  = 平均( rank(Y_快), rank(Y_中), rank(Y_慢) )     rank 在前 40 名內，缺值給 0.5
```

滾動窗至少 80% 有值。資料自 **2013-01-01**（Y 約 1.3 年暖身），評估自 2015-01。

## 2. 策略結構

```text
150 檔 → Mom20 排名 → 前 40 名 → 依 MACD 分數重排 → 25 檔等權
```

MACD 同分按 Mom20；第 41 名後沿用 Mom20。Production 組合：25 等權、95% 投入、續抱 35 名、10% 換手、末 3 天凍結。Mom20 亦用 2013 起資料重跑；僅用近 20 日，應同 2014 起結果。

## 3. 策略角色

Mom20 基準；三尺度平均是唯一決策對象；單快／中／慢僅診斷。

## 4. 事前不利證據與預測

| 證據 | 結果 |
|---|---|
| 前 40 名內偏好低波動 | −1.78% |
| risk_adjusted（報酬 ÷ 波動） | −0.83% |
| 長窗口在 2025–2026 | Mom60 −1.78%、residual 120 日 −2.15% |
| 前 40 名重排的換手 | 4.2–8.6（Mom20 3.68） |

預測大概率 FAIL；慢尺度 (32, 96) 在 2025–2026 可能最差。

## 5. Gate（三尺度方案）

| 條件 | 門檻 |
|---|---|
| 全期配對平均 Δ | > +0.5% |
| 95% bootstrap CI 下界 | > 0 |
| 中位數 Δ | ≥ 0 |
| 四段一致性（2015–18、2019–21、2022–24、2025–26） | 至少 3 段平均 Δ > 0 |
| 不靠大贏窗口 | 拿掉最大 3 個後 > +0.25% |
| 換手 | ≤ 1.5 倍 Mom20 |
| 成本 | 多出的成本 < 平均 Δ |
| 合規 | 失格、警告不增加 |

診斷不改決策：排名相關、Mom20 最差 10 窗 Δ、四段及成交價來源分段。

## 6. 決策

```text
FAIL → TECHNICAL_MOMENTUM_NO_INCREMENTAL_EDGE：技術面研究永久關閉
       不換尺度、不改權重、不加 RSI／ADX、不改 Top40
PASS → Prospective shadow → 全部 P0 → 才考慮 production
```

## 8. 修訂紀錄

無。
