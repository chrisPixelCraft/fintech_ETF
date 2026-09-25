"""LightGBM-v2 feature panel (docs/hybrid_spec.md section 5): one row per (date t, symbol), known after t's close.

Every price feature is a per-symbol rolling statistic of rows <= t (a window
needs at least MIN_SHARE of its sessions observed; this share is the only
choice the spec leaves open). Cross-sectional features rank or average the
same date only. Revenue month m joins from the 11th of month m+1; flows of day
t join at t. A flow report that exists but omits a stock means zero net
buying; days before a report series starts stay missing.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from competition.data import MarketData
from lgbm_strategy.features import signal_price
from lgbm_strategy.targets import raw_forward_return

from hybrid.gate import panic_table

MIN_SHARE = .8
RETURNS = (1, 3, 5, 10, 20, 60)
RAW_PRICE = ('open', 'high', 'low', 'close', 'volume')
PRICE = (*(f'r{k}' for k in RETURNS), 'r20_rank', 'beta60', 'resid_mom60', 'idio_vol60', 'down_vol20', 'dd60',
         'dist_high252', 'abn_volume', 'liquidity', 'breadth', 'dispersion', 'mkt_vol20')
REVENUE = ('rev_yoy', 'rev_mom', 'rev_yoy_acc', 'rev_yoy_rank', 'rev_record', 'rev_days')
FLOWS = ('foreign_5d', 'foreign_20d', 'trust_5d', 'trust_20d')
V2A = (*PRICE, *REVENUE, *FLOWS)
V2B = (*V2A, *RAW_PRICE)
VARIANTS = {'v2A': V2A, 'v2B': V2B}
HORIZON = 10


def _roll(frame, window):
    return frame.rolling(window, min_periods=math.ceil(MIN_SHARE * window))


def price_features(market: MarketData) -> dict[str, pd.DataFrame]:
    price = signal_price(market.ret)
    lr = np.log1p(market.ret)
    m = np.log1p(market.benchmark_ret)
    out = {f'r{k}': price / price.shift(k) - 1 for k in RETURNS}
    out['r20_rank'] = out['r20'].rank(axis=1, pct=True)
    mm = pd.DataFrame(np.broadcast_to(m.to_numpy()[:, None], lr.shape), index=lr.index, columns=lr.columns)
    mm = mm.where(lr.notna())                                   # same observations as the stock
    ex, ey = _roll(lr, 60).mean(), _roll(mm, 60).mean()
    cov = _roll(lr * mm, 60).mean() - ex * ey
    var_y = _roll(mm * mm, 60).mean() - ey * ey
    var_x = _roll(lr * lr, 60).mean() - ex * ex
    beta = cov / var_y.where(var_y > 0)
    out['beta60'] = beta
    out['resid_mom60'] = _roll(lr, 60).sum() - beta * _roll(mm, 60).sum()
    out['idio_vol60'] = np.sqrt((var_x - 2 * beta * cov + beta * beta * var_y).clip(lower=0))
    out['down_vol20'] = np.sqrt(_roll(lr.clip(upper=0) ** 2, 20).mean())
    out['dd60'] = price / _roll(price, 60).max() - 1
    out['dist_high252'] = price / _roll(price, 252).max() - 1
    out['abn_volume'] = _roll(market.volume, 5).mean() / _roll(market.volume, 60).mean()
    out['liquidity'] = np.log(_roll(market.close * market.volume, 20).mean())
    r20 = out['r20']
    wide = lambda s: pd.DataFrame(np.broadcast_to(s.to_numpy()[:, None], r20.shape), index=r20.index,
                                  columns=r20.columns)
    out['breadth'] = wide((r20 > 0).astype(float).where(r20.notna()).mean(axis=1))
    out['dispersion'] = wide(r20.std(axis=1))
    out['mkt_vol20'] = wide(panic_table(market.benchmark_ret).vol20)
    for name in RAW_PRICE:
        out[name] = getattr(market, name).astype(float)
    return out


def revenue_features(revenue: pd.DataFrame, dates: pd.DatetimeIndex, symbols: dict) -> dict[str, pd.DataFrame]:
    """Point-in-time monthly revenue: on date t each stock carries its latest month released on or before t
    (month m is released on the 11th of month m+1)."""
    rev = revenue.assign(ticker=revenue.ticker.astype(str)).drop_duplicates(['month', 'ticker'], keep='last')
    rev['period'] = pd.PeriodIndex(rev.month, freq='M')
    rev = rev.sort_values(['ticker', 'period']).reset_index(drop=True)
    rev['release'] = (rev.period + 1).dt.to_timestamp() + pd.Timedelta(days=10)
    rev['yoy'] = rev.revenue / rev.last_year_revenue.where(rev.last_year_revenue > 0) - 1
    rev['mom'] = rev.revenue / rev.prev_revenue.where(rev.prev_revenue > 0) - 1
    consecutive = rev.groupby('ticker').period.diff() == 1          # previous row is month m-1
    rev['yoy_acc'] = (rev.yoy - rev.groupby('ticker').yoy.shift()).where(consecutive)
    prior_max = rev.groupby('ticker').revenue.transform(lambda s: s.shift().rolling(12, min_periods=12).max())
    rev['record'] = (rev.revenue >= prior_max).astype(float).where(prior_max.notna())
    grid = pd.MultiIndex.from_product([dates, sorted(rev.ticker.unique())], names=['date', 'ticker']).to_frame(index=False)
    merged = pd.merge_asof(grid.sort_values('date'), rev.sort_values('release'), left_on='date', right_on='release',
                           by='ticker', direction='backward')
    merged['days'] = (merged.date - merged.release).dt.days.astype(float)
    merged['symbol'] = merged.ticker.map(symbols)
    merged = merged.dropna(subset=['symbol'])
    out = {name: merged.pivot(index='date', columns='symbol', values=column).reindex(dates)
           for name, column in (('rev_yoy', 'yoy'), ('rev_mom', 'mom'), ('rev_yoy_acc', 'yoy_acc'),
                                ('rev_record', 'record'), ('rev_days', 'days'))}
    out['rev_yoy_rank'] = out['rev_yoy'].rank(axis=1, pct=True)
    return out


def flow_features(flows: pd.DataFrame, volume: pd.DataFrame, symbols: dict) -> dict[str, pd.DataFrame]:
    """Net buying over 5/20 sessions over 20-session average volume; zero when a report omits a stock."""
    f = flows.copy()
    f['date'] = pd.to_datetime(f.date)
    f['symbol'] = f.ticker.astype(str).map(symbols)
    f = f.dropna(subset=['symbol'])
    reported = {src: set(g.date) for src, g in f.groupby('source')}
    market_of = {s: ('tpex' if s.endswith('.TWO') else 'twse') for s in volume.columns}
    out = {}
    for column, name in (('foreign_net', 'foreign'), ('trust_net', 'trust')):
        wide = f.pivot_table(index='date', columns='symbol', values=column, aggfunc='sum')
        wide = wide.reindex(index=volume.index, columns=volume.columns)
        for s in wide.columns:
            days = reported.get(market_of[s], set())
            has_report = wide.index.isin(list(days))
            wide[s] = wide[s].where(~has_report, wide[s].fillna(0.))    # reported day, not listed -> 0
        base = _roll(volume, 20).mean()
        out[f'{name}_5d'] = _roll(wide, 5).sum() / base
        out[f'{name}_20d'] = _roll(wide, 20).sum() / base
    return out


def build_panel(market: MarketData, revenue: pd.DataFrame, flows: pd.DataFrame, symbols: dict) -> pd.DataFrame:
    """Long panel (date, symbol, every feature, ready, label, label_end_index)."""
    frames = price_features(market)
    frames.update(revenue_features(revenue, market.calendar, symbols))
    frames.update(flow_features(flows, market.volume, symbols))
    cols = sorted(market.symbols)
    stacked = {name: frames[name].reindex(index=market.calendar, columns=cols).stack(future_stack=True)
               for name in V2B}
    panel = pd.DataFrame(stacked).astype(float)
    panel['ready'] = (market.valid.reindex(columns=cols).stack(future_stack=True).fillna(False).astype(bool)
                      & panel.r20.notna())
    panel['label'] = raw_forward_return(market.ret, HORIZON).reindex(columns=cols).stack(future_stack=True)
    panel.index.names = ['date', 'symbol']
    return panel
