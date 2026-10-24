"""Frozen K2 / T2 tournament decisions and point-in-time auxiliary data."""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from competition import portfolio
from competition.rules import ROOT, UNIVERSE_PATH

CACHE = ROOT / 'data/production_aux'
TSMC = '2330.TW'


def expected_month(day) -> pd.Period:
    day = pd.Timestamp(day)
    return day.to_period('M') - (1 if day.day >= 11 else 2)


def switch_state(state, trade_date, day_index, sessions_remaining, rank=None):
    """Return a proposed state; the caller persists it only with a produced D-Plan."""
    state = deepcopy(state)
    state.setdefault('mode', 'K2')
    state.setdefault('switched_on', None)
    state.setdefault('ranks', {})
    if state['mode'] not in ('K2', 'T2'):
        raise ValueError('Unknown tournament mode')
    if rank is not None:
        if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
            raise ValueError('rank must be a positive integer')
        state['ranks'][str(pd.Timestamp(trade_date).date())] = rank
    eligible = day_index >= 12 and sessions_remaining > 3 and state['mode'] == 'K2'
    reasons, switched = [], False
    if eligible:
        if rank is None:
            reasons.append('RANK_MISSING')
        elif rank <= 6:
            state.update(mode='T2', switched_on=str(pd.Timestamp(trade_date).date()))
            switched = True
    return state, switched, reasons


def _read(path, columns):
    return pd.read_csv(path, dtype={'ticker': str}) if path.exists() else pd.DataFrame(columns=columns)


@dataclass
class AuxData:
    flows: pd.DataFrame
    revenue: pd.DataFrame

    def panels(self, calendar, symbols):
        universe = pd.read_csv(UNIVERSE_PATH, dtype={'ticker': str})
        mapping = dict(zip(universe.ticker, universe.yahoo_symbol))
        f = self.flows.copy()
        f['date'] = pd.to_datetime(f.date)
        f = f[f.date <= calendar[-1]].copy()
        f['symbol'] = f.ticker.map(mapping)
        f = f.dropna(subset=['symbol'])
        trust = f.pivot_table(index='date', columns='symbol', values='trust_net', aggfunc='sum')
        trust = trust.reindex(index=calendar, columns=symbols)
        for source, suffix in (('twse', '.TW'), ('tpex', '.TWO')):
            reported = calendar.isin(pd.DatetimeIndex(f.loc[f.source == source, 'date'].unique()))
            cols = [s for s in symbols if s.endswith(suffix)]
            trust.loc[reported, cols] = trust.loc[reported, cols].fillna(0.)
        rev = self.revenue.copy()
        rev['period'] = pd.PeriodIndex(rev.month, freq='M')
        rev = rev[rev.period <= expected_month(calendar[-1])].copy()
        rev = rev.sort_values(['ticker', 'period']).reset_index(drop=True)
        index = rev.period.dt.year * 12 + rev.period.dt.month
        step = (index.groupby(rev.ticker).diff() == 1).astype(int)
        full = step.groupby(rev.ticker).transform(lambda s: s.rolling(11).sum()) == 11
        avg3 = rev.groupby('ticker').revenue.transform(lambda s: s.rolling(3).mean())
        rev['high3'] = (avg3 >= avg3.groupby(rev.ticker).transform(lambda s: s.rolling(12).max()) - 1e-9).astype(float)
        rev['high3'] = rev.high3.where(full & avg3.notna())
        rev['yoy'] = rev.revenue / rev.last_year_revenue.where(rev.last_year_revenue > 0) - 1
        rev['release'] = (rev.period + 1).dt.to_timestamp() + pd.Timedelta(days=10)
        grid = pd.MultiIndex.from_product([calendar, sorted(rev.ticker.unique())],
                                          names=['date', 'ticker']).to_frame(index=False)
        merged = pd.merge_asof(grid.sort_values('date'), rev.sort_values('release'), left_on='date',
                               right_on='release', by='ticker', direction='backward')
        merged['symbol'] = merged.ticker.map(mapping)
        merged = merged.dropna(subset=['symbol'])
        panels = {k: merged.pivot(index='date', columns='symbol', values=c).reindex(calendar)
                  for k, c in (('yoy', 'yoy'), ('high3', 'high3'), ('month', 'period'))}
        return trust, panels


def load_aux(cache=CACHE):
    from legacy.ml.hybrid.sources import FLOW_COLUMNS, REVENUE_COLUMNS
    cache = Path(cache)
    flows = pd.concat([_read(ROOT / 'data/hybrid/flows.csv', FLOW_COLUMNS),
                       _read(cache / 'flows.csv', FLOW_COLUMNS)], ignore_index=True)
    revenue = pd.concat([_read(ROOT / 'data/hybrid/revenue.csv', REVENUE_COLUMNS),
                         _read(cache / 'revenue.csv', REVENUE_COLUMNS)], ignore_index=True)
    return AuxData(flows.drop_duplicates(['date', 'ticker', 'source'], keep='last'),
                   revenue.drop_duplicates(['month', 'ticker'], keep='last'))


def aux_gate(aux, prev):
    key = str(pd.Timestamp(prev).date())
    reasons = [f'FLOWS_T-1_MISSING:{source}:{key}' for source in ('twse', 'tpex')
               if not ((aux.flows.date == key) & (aux.flows.source == source)).any()]
    month = str(expected_month(prev))
    if month not in set(aux.revenue.month):
        reasons.append(f'REVENUE_MONTH_MISSING:{month}')
    return reasons


def refresh(calendar, prev, offline=False, cache=CACHE):
    """Append missing exchange sessions and published months; never write base history."""
    from legacy.ml.hybrid import sources
    cache = Path(cache)
    aux, report = load_aux(cache), []
    if not offline:
        cache.mkdir(parents=True, exist_ok=True)
        universe = sources.tickers()
        for source in ('twse', 'tpex'):
            have = set(aux.flows.loc[aux.flows.source == source, 'date'])
            floor = pd.Timestamp(min(have)) if have else pd.Timestamp(sources.START[source])
            for day in calendar[(calendar >= floor) & (calendar <= prev)]:
                key = str(day.date())
                if key in have:
                    continue
                url = sources.TWSE_URL.format(d=day) if source == 'twse' else sources.TPEX_URL.format(roc=day.year - 1911, d=day)
                try:
                    rows = sources.PARSE[source](json.loads(sources._get(url, attempts=1)), universe)
                    if not rows:
                        raise ValueError('empty exchange response')
                    sources._append(cache / 'flows.csv', [dict(date=key, source=source, **r) for r in rows], sources.FLOW_COLUMNS)
                    report.append(dict(source=source, date=key, rows=len(rows)))
                except Exception as error:
                    report.append(dict(source=source, date=key, error=repr(error)))
        have = set(aux.revenue.month)
        for month in pd.period_range(min(have), expected_month(prev), freq='M'):
            if str(month) in have:
                continue
            rows, failures = [], []
            for market in ('sii', 'otc'):
                for kind in (0, 1):
                    url = sources.REVENUE_URL.format(market=market, roc=month.year - 1911, month=month.month, kind=kind)
                    try:
                        rows += sources.parse_revenue(sources._get(url, attempts=1), str(month), universe)
                    except requests.HTTPError as error:
                        if error.response is None or error.response.status_code != 404:
                            failures.append(repr(error))
                    except Exception as error:
                        failures.append(repr(error))
            # Do not mark a partially downloaded month as complete.
            if rows and not failures:
                sources._append(cache / 'revenue.csv', rows, sources.REVENUE_COLUMNS)
            report.append(dict(month=str(month), rows=len(rows), failures=failures))
        aux = load_aux(cache)
    return aux, aux_gate(aux, prev), report


def large_order(view):
    valid = view.valid.iloc[-60:]
    names = valid.columns[valid.all()]
    values = (view.close[names] * view.volume[names]).iloc[-60:]
    names = values.columns[np.isfinite(values).all() & values.gt(0).all()]
    return portfolio.ranked(values[names].mean())


def flow_ratio(trust, view, names, window):
    rows = trust.loc[:view.date].iloc[-window:]
    flow = rows[names].sum(min_count=window)
    volume = view.volume[names].iloc[-20:].mean()
    return flow / volume.where(volume > 0)


class TournamentStrategy:
    def __init__(self, rules, aux=None):
        self.rules, self.aux = rules, aux if aux is not None else load_aux()
        self.log = []
        self.state = {}
        self._panel_key, self._panels = None, None

    def decide(self, view, state, mode='K2', switched=False, tournament_state=None):
        saved = self.state if tournament_state is None else tournament_state
        key = (tuple(view.close.index), tuple(view.symbols))
        if key != self._panel_key:
            self._panels = self.aux.panels(view.close.index, view.symbols)
            self._panel_key = key
        trust, rev = self._panels
        names = [s for s in view.symbols if view.valid[s].iloc[-20:].all()]
        ratio = flow_ratio(trust, view, names, 10 if mode == 'K2' else 20)
        yoy, high = rev['yoy'].loc[view.date], rev['high3'].loc[view.date]
        filter_ratio = ratio if mode == 'K2' else flow_ratio(trust, view, names, 10)
        passed = {s: bool(filter_ratio.get(s, np.nan) > 0 and high.get(s) == 1 and yoy.get(s, np.nan) > .20) for s in names}
        info = dict(mode=mode, trust_ratio=ratio.to_dict(), filter_status=passed, action='hold')
        current = dict(state.weights)
        month = rev['month'].loc[view.date].dropna().max()
        month = None if pd.isna(month) else str(month)
        breach = (any(w > self.rules.cap(s) - .005 for s, w in current.items())
                  or state.cash < self.rules.cash_min or state.cash / state.nav >= self.rules.cash_max_exclusive)

        def build():
            large = large_order(view)
            if mode == 'K2':
                ok = portfolio.ranked(ratio[[s for s in names if passed[s]]])
                ordered = ok + [s for s in large if s not in ok]
                params = json.loads((ROOT / 'production/strategy.json').read_text())['params']['K2']
                core_n, filler_n = params['core_n'], params['filler_n']
                if len(ordered) < core_n + filler_n:
                    raise ValueError(f'K2 needs {core_n + filler_n} fully valid names')
                desired = {**{s: params['core_weight'] for s in ordered[:core_n]},
                           **{s: params['filler_weight'] for s in ordered[core_n:core_n + filler_n]}}
            else:
                pool = [s for s in large if s != TSMC][:40]
                chosen = portfolio.ranked(flow_ratio(trust, view, pool, 20))[:24]
                if len(chosen) != 24:
                    raise ValueError('T2 needs 24 scored names')
                desired = {TSMC: .235, **{s: .715 / 24 for s in chosen}}
            return self._targets(desired)

        if mode == 'K2':
            if not saved.get('k2_target'):
                saved.update(k2_target=build(), k2_month=month)
                info['action'] = 'build'
                return dict(saved['k2_target']), ratio, info
            if state.sessions_remaining <= 3:
                return current, ratio, info
            if month != saved.get('k2_month'):
                saved['k2_month'] = month
                target = build()
                turnover = .5 * sum(abs(target.get(s, 0.) - current.get(s, 0.))
                                     for s in set(target) | set(current))
                if turnover >= .10:
                    saved['k2_target'] = target
                    info['action'] = 'rescreen'
                    return dict(target), ratio, info
            if breach:
                info['action'] = 'repair'
                return dict(saved['k2_target']), ratio, info
            return current, ratio, info

        if state.sessions_remaining <= 3:
            return current, ratio, info
        if switched or not saved.get('t2_target'):
            saved.update(t2_target=build(), t2_complete=False)
        target = saved['t2_target']
        if set(current) == set(target):
            saved['t2_complete'] = True
        if not saved['t2_complete']:
            info['action'] = 'switch_step'
            return dict(target), ratio, info
        if breach:
            info['action'] = 'repair'
            return dict(target), ratio, info
        return current, ratio, info

    def switch_candidates(self, current, target, ratio):
        sells = sorted(set(current) - set(target), key=lambda s: (-current[s], s))
        buys = sorted(set(target) - set(current),
                      key=lambda s: (s != TSMC, -target[s], -ratio.get(s, -np.inf), s))
        for k in range(len(sells), 0, -1):
            candidate = {s: target.get(s, w) for s, w in current.items() if s not in sells[:k]}
            for s in buys:
                if sum(candidate.values()) + target[s] <= .95 + 1e-9:
                    candidate[s] = target[s]
            if k == len(sells):
                candidate = dict(target)
            if self.rules.min_positions <= len(candidate) <= self.rules.max_positions:
                yield k, len(sells), candidate
        # An empty book has no sells; its single build candidate is A.
        if not current:
            yield 0, 0, dict(target)

    def _targets(self, desired):
        if not self.rules.min_positions <= len(desired) <= self.rules.max_positions:
            raise ValueError('Tournament position count outside rules')
        return portfolio.cap_fill(pd.Series(desired), .95,
                                  pd.Series({s: self.rules.cap(s) for s in desired})).to_dict()



class TournamentBacktest(TournamentStrategy):
    """Rank-free adapter for competition.backtest (K2 only)."""
    def decide(self, view, state):
        target, _, info = super().decide(view, state)
        self.log.append(dict(date=str(state.date.date()), **info))
        return target
