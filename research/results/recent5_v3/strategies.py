"""Batch-3 candidates (frozen; see PREREGISTRATION.md). All keep C1's structure: 2330 at 23.5% plus 24 names
sharing 71.5% equally, 95% invested, last-3-session freeze.

- T1 TrustRev: FinLab "investment-trust buying + revenue at a high" adapted to the 150-name universe.
  Filter: 10-session trust net buy > 0, 3-month average revenue at its 12-month high, latest YoY > 20%.
  Rank by 10-session trust net buy / 20-session mean volume; take up to 24, fill the rest by large-cap rank.
- T2 LargeTrust: C1 pool (top 40 large caps ex-2330), pick the 24 with the highest 20-session trust net buy /
  20-session mean volume. Held for the window like C1.
- T3 LargeRev: same pool, pick the 24 with the highest revenue score (mean percentile of latest YoY and
  12-month record flag, as S1).
T1 and T3 re-screen once new monthly revenue becomes usable (11th of the month) and trade only if
one-way turnover >= 10%. T2 trades only on the first session or to repair a cap/cash breach (C1 rule).
Flows for session t are published after its close and used from the next decision on (rows <= view.date).
"""
from __future__ import annotations

from pathlib import Path
import importlib.util

import numpy as np
import pandas as pd

from competition import portfolio
from competition.rules import ROOT

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('strategies_v2', HERE.parent / 'recent5_new/strategies.py')
v2 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v2)

TSMC, TSMC_WEIGHT, OTHERS_WEIGHT = v2.TSMC, v2.TSMC_WEIGHT, v2.OTHERS_WEIGHT
POOL = 40


def _symbols() -> dict:
    universe = pd.read_csv(ROOT / 'data/reference/universe_competition_20260731.csv', dtype={'ticker': str})
    return dict(zip(universe.ticker, universe.yahoo_symbol))


def trust_panel(calendar: pd.DatetimeIndex, columns) -> pd.DataFrame:
    """Daily trust net buy (shares) on the backtest calendar; a reported day without a row counts as 0."""
    base = pd.read_csv(ROOT / 'data/hybrid/flows.csv', dtype={'ticker': str})
    tail = pd.read_csv(HERE / 'data/flows_20260924_20261022.csv', dtype={'ticker': str})
    assert base.date.max() < tail.date.min()
    f = pd.concat([base, tail], ignore_index=True)
    f['date'] = pd.to_datetime(f.date)
    f['symbol'] = f.ticker.map(_symbols())
    f = f.dropna(subset=['symbol'])
    wide = f.pivot_table(index='date', columns='symbol', values='trust_net', aggfunc='sum')
    wide = wide.reindex(index=calendar, columns=columns)
    for source, suffix in (('twse', '.TW'), ('tpex', '.TWO')):
        reported = calendar.isin(pd.DatetimeIndex(f.loc[f.source == source, 'date'].unique()))
        cols = [c for c in wide.columns if c.endswith(suffix)]
        wide.loc[reported, cols] = wide.loc[reported, cols].fillna(0.)
    return wide


def revenue_high_panel(calendar: pd.DatetimeIndex) -> dict[str, pd.DataFrame]:
    """Point-in-time latest YoY and '3-month average revenue at its 12-month high' (month m usable from m+1/11)."""
    base = pd.read_csv(ROOT / 'data/hybrid/revenue.csv', dtype={'ticker': str})
    extra = pd.read_csv(HERE.parent / 'recent5_new/data/revenue_202609.csv', dtype={'ticker': str})
    rev = pd.concat([base, extra], ignore_index=True).drop_duplicates(['month', 'ticker'], keep='last')
    rev['period'] = pd.PeriodIndex(rev.month, freq='M')
    rev = rev.sort_values(['ticker', 'period']).reset_index(drop=True)
    index = rev.period.dt.year * 12 + rev.period.dt.month
    step = (index.groupby(rev.ticker).diff() == 1).astype(int)
    full = step.groupby(rev.ticker).transform(lambda s: s.rolling(11).sum()) == 11   # 12 consecutive months
    avg3 = rev.groupby('ticker').revenue.transform(lambda s: s.rolling(3).mean())
    rev['high3'] = (avg3 >= avg3.groupby(rev.ticker).transform(lambda s: s.rolling(12).max()) - 1e-9).astype(float)
    rev['high3'] = rev.high3.where(full & avg3.notna())
    rev['yoy'] = rev.revenue / rev.last_year_revenue.where(rev.last_year_revenue > 0) - 1
    rev['release'] = (rev.period + 1).dt.to_timestamp() + pd.Timedelta(days=10)
    grid = pd.MultiIndex.from_product([calendar, sorted(rev.ticker.unique())], names=['date', 'ticker']).to_frame(index=False)
    m = pd.merge_asof(grid.sort_values('date'), rev.sort_values('release'), left_on='date', right_on='release',
                      by='ticker', direction='backward')
    m['symbol'] = m.ticker.map(_symbols())
    m = m.dropna(subset=['symbol'])
    return {k: m.pivot(index='date', columns='symbol', values=c).reindex(calendar)
            for k, c in (('yoy', 'yoy'), ('high3', 'high3'), ('month', 'period'))}


def large_order(view) -> list[str]:
    """C1 large-cap rank: mean close x volume over the last 60 sessions, names fully valid over them."""
    valid = view.valid.iloc[-60:]
    names = valid.columns[valid.all()]
    values = (view.close[names] * view.volume[names]).iloc[-60:]
    names = values.columns[np.isfinite(values).all() & values.gt(0).all()]
    return portfolio.ranked(values[names].mean())


def _flow_ratio(trust: pd.DataFrame, view, names, window: int) -> pd.Series:
    rows = trust.loc[:view.date].iloc[-window:]
    assert rows.index[-1] == view.date
    flow = rows[names].sum(min_count=window)
    volume = view.volume[names].iloc[-20:].mean()
    return flow / volume.where(volume > 0)


class _Base:
    """C1 weights, last-3 freeze, cap/cash repair, optional re-screen after a new revenue month."""
    rescreen_on_revenue = False

    def __init__(self, calendar, rules, symbols):
        self.rules, self.log = rules, []
        self.trust = trust_panel(calendar, symbols)
        self.rev = revenue_high_panel(calendar)
        self.current, self.month = None, None

    def pick(self, view) -> tuple[list[str], dict]:
        raise NotImplementedError

    def _build(self, view):
        names, info = self.pick(view)
        assert len(names) == 24 and TSMC not in names and len(set(names)) == 24
        return v2._targets({TSMC: TSMC_WEIGHT, **{s: OTHERS_WEIGHT / 24 for s in names}}, self.rules), info

    def decide(self, view, state):
        month = self.rev['month'].loc[view.date].dropna().max()
        if self.current is None:
            self.current, info = self._build(view)
            self.month = month
            self.log.append(dict(date=str(state.date.date()), action='build', **info))
            return self.current.copy()
        if state.sessions_remaining <= 3:
            return dict(state.weights)
        if self.rescreen_on_revenue and month != self.month:
            self.month = month
            target, info = self._build(view)
            turnover = v2._turnover(target, state.weights)
            self.log.append(dict(date=str(state.date.date()), action='rescreen', turnover=turnover, **info))
            if turnover >= .10:
                self.current = target
                return target.copy()
        breach = (any(w > self.rules.cap(s) - .005 for s, w in state.weights.items())
                  or state.cash < self.rules.cash_min or state.cash / state.nav >= self.rules.cash_max_exclusive)
        if breach:
            self.log.append(dict(date=str(state.date.date()), action='repair'))
            return self.current.copy()
        return dict(state.weights)


class TrustRev(_Base):
    rescreen_on_revenue = True

    def pick(self, view):
        large = [s for s in large_order(view) if s != TSMC]
        names = [s for s in view.symbols if s != TSMC and view.valid[s].iloc[-20:].all()]
        ratio = _flow_ratio(self.trust, view, names, 10)
        yoy, high = self.rev['yoy'].loc[view.date], self.rev['high3'].loc[view.date]
        ok = [s for s in names if ratio.get(s, np.nan) > 0 and high.get(s) == 1 and yoy.get(s, np.nan) > .20]
        chosen = portfolio.ranked(ratio[ok])[:24]
        filled = [s for s in large if s not in chosen][:24 - len(chosen)]
        return chosen + filled, dict(passed_filter=len(ok), from_filter=len(chosen), filled_by_large=len(filled))


class LargeTrust(_Base):
    def pick(self, view):
        pool = [s for s in large_order(view) if s != TSMC][:POOL]
        ratio = _flow_ratio(self.trust, view, pool, 20)
        chosen = portfolio.ranked(ratio)[:24]
        return chosen, dict(pool=len(pool), scored=int(ratio.notna().sum()))


class LargeRev(_Base):
    rescreen_on_revenue = True

    def __init__(self, calendar, rules, symbols):
        super().__init__(calendar, rules, symbols)
        self.s1 = v2.revenue_panels(calendar)

    def pick(self, view):
        pool = [s for s in large_order(view) if s != TSMC][:POOL]
        parts = [v2._rank(self.s1[k].loc[view.date], pool) for k in ('rev_yoy', 'rev_record')]
        score = pd.concat(parts, axis=1).mean(axis=1, skipna=False)
        chosen = portfolio.ranked(score)[:24]
        return chosen, dict(pool=len(pool), scored=int(score.notna().sum()))
