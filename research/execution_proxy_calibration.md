# Execution proxy calibration

**Default proxy: `hlc3 = (high + low + close) / 3`.** Competition fills use day-t official average price (turnover / volume). Local coverage: 2024 20%, 2025+ about 98%; earlier episodes need Yahoo-bar proxies.

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

HLC3 leads in every sub-period:

| Period | open | hlc3 | ohlc4 |
|---|---|---|---|
| 2024 (20% coverage) | 0.939% | 0.177% | 0.303% |
| 2025 | 0.822% | 0.143% | 0.256% |
| 2026-01 → 09 | 1.237% | 0.220% | 0.389% |

Cells show median absolute error.

## How it is used

- `execution.mode = auto` fills at official VWAP when present, else at `hlc3`
- Fills record source (`official_vwap` or `proxy_hlc3`); each summary counts them in `fill_sources`
- Missing price → unfilled; never fabricate

## Caveats

- Day-t high/low/close are valid only for fills; never feed them into decisions or signals.
- Calibration covers only 2024–2026; earlier intraday paths may differ. Near-zero signed bias in each period suggests no systematic return tilt.
- Open has 5–6× the error, adding about ±1% noise per trade.
