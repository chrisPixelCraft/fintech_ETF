# Execution proxy calibration

The competition fills every order at the day-t official average price (turnover / volume). Local official prices exist only from 2024 (20% coverage) and 2025+ (about 98%). Earlier episodes need a proxy built from Yahoo daily bars. This note picks that proxy.

- Code: `competition.execution.calibrate(market, start, end)`
- Error: `proxy / official_vwap - 1`, per stock-day, over the 150-name universe
- Official VWAP is converted to Yahoo share units with the same-day close ratio (`competition/data.py`)
- Computed on 2026-09-24 from the data hashes recorded in run manifests

## Result: `hlc3 = (high + low + close) / 3` is the default proxy

| Proxy | Period | Stock-days | Median abs error | Mean abs error | P90 abs error | Mean signed error |
|---|---|---|---|---|---|---|
| open | 2025-01 → 2026-09 | 61,586 | 0.971% | 1.421% | 3.346% | +0.069% |
| **hlc3** | 2025-01 → 2026-09 | 61,586 | **0.169%** | **0.271%** | **0.630%** | −0.013% |
| ohlc4 | 2025-01 → 2026-09 | 61,586 | 0.302% | 0.460% | 1.074% | +0.007% |

The ranking holds in every sub-period:

| Period | open | hlc3 | ohlc4 |
|---|---|---|---|
| 2024 (20% coverage) | 0.939% | 0.177% | 0.303% |
| 2025 | 0.822% | 0.143% | 0.256% |
| 2026-01 → 09 | 1.237% | 0.220% | 0.389% |

Values are median absolute errors.

## How it is used

- `execution.mode = auto` fills at official VWAP when present, else at `hlc3`
- Every fill records its source (`official_vwap` or `proxy_hlc3`); each summary counts them in `fill_sources`
- A missing price leaves the order unfilled; no price is fabricated

## Caveats

- The proxy uses day-t high, low and close. That is fine for a fill price, which never feeds a decision. It must never be used as a signal.
- The calibration covers 2024–2026 only. Pre-2024 intraday price paths may differ. Signed bias is near zero in every period, so the proxy should not tilt returns systematically.
- The open proxy has 5–6× the error. Open-price fills would add about ±1% noise to each trade.
