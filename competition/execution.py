"""Fill-price resolution: official day-t average price, else a labelled proxy.

Semantics follow legacy/src/v4_execution.py resolve_execution_prices: a missing
price stays NaN (the order does not fill); no price is ever fabricated.
The proxy default comes from research/execution_proxy_calibration.md.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from competition.data import AsOfView, MarketData

OFFICIAL = 'official_vwap'
PROXIES = ('open', 'hlc3', 'ohlc4')
DEFAULT_PROXY = 'hlc3'          # lowest median |error| vs official VWAP on 2025+ (calibration file)
MODES = ('auto', 'proxy', 'official')


def proxy_prices(bars: MarketData | AsOfView, kind: str) -> pd.DataFrame:
    if kind == 'open':
        return bars.open
    if kind == 'hlc3':
        return (bars.high + bars.low + bars.close) / 3
    if kind == 'ohlc4':
        return (bars.open + bars.high + bars.low + bars.close) / 4
    raise ValueError(f'Unknown proxy {kind}')


def resolve_prices(official, alt, mode: str = 'auto', proxy: str = DEFAULT_PROXY):
    """Fill price and source label from aligned official / proxy prices (Series or DataFrame).

    auto: official VWAP when present, else proxy; proxy: always proxy;
    official: official only (missing -> NaN, source ``missing``).
    """
    if mode not in MODES:
        raise ValueError(f'Unknown execution mode {mode}')
    ok_official = official.gt(0) & np.isfinite(official)
    ok_alt = alt.gt(0) & np.isfinite(alt)
    if mode == 'official':
        use_official, use_alt = ok_official, ok_alt & False
    elif mode == 'proxy':
        use_official, use_alt = ok_official & False, ok_alt
    else:
        use_official, use_alt = ok_official, ~ok_official & ok_alt
    price = official.where(use_official, alt.where(use_alt))
    labels = np.where(use_official, OFFICIAL, np.where(use_alt, 'proxy_' + proxy, 'missing'))
    source = (pd.DataFrame(labels, index=official.index, columns=official.columns) if isinstance(official, pd.DataFrame)
              else pd.Series(labels, index=official.index))
    return price, source


def execution_prices(bars: MarketData | AsOfView, mode: str = 'auto',
                     proxy: str = DEFAULT_PROXY) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Wide (date x symbol) fill price and source for every session of ``bars``."""
    return resolve_prices(bars.official_vwap, proxy_prices(bars, proxy), mode, proxy)


def fill_prices(market: MarketData, date, symbols, mode: str = 'auto',
                proxy: str = DEFAULT_PROXY) -> tuple[pd.Series, pd.Series]:
    """Per-symbol fill price and source label for trade date ``date`` (see ``resolve_prices``)."""
    if mode not in MODES:
        raise ValueError(f'Unknown execution mode {mode}')
    symbols = list(symbols)
    i = market.position(date)
    official = market.official_vwap.iloc[i].reindex(symbols)
    alt = proxy_prices(market, proxy).iloc[i].reindex(symbols)
    return resolve_prices(official, alt, mode, proxy)


def calibrate(market: MarketData, start='2025-01-01', end=None) -> pd.DataFrame:
    """Relative error (proxy / official VWAP - 1) statistics per proxy on the official era."""
    rows = []
    vwap = market.official_vwap.loc[start:end]
    for kind in PROXIES:
        err = (proxy_prices(market, kind).loc[start:end] / vwap - 1).stack().dropna()
        rows.append(dict(proxy=kind, n=len(err), median_abs=err.abs().median(), mean_abs=err.abs().mean(),
                         p90_abs=err.abs().quantile(.9), mean_signed=err.mean(), median_signed=err.median()))
    return pd.DataFrame(rows).set_index('proxy')
