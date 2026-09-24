"""Generic 24-session episode simulator and an independent ledger verifier.

A strategy sees only ``market.asof(D-1)`` plus its settled book, returns target
weights; the planner turns them into official-formula orders; the ledger fills
them at day-t prices and settles.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from typing import Mapping, Protocol

import numpy as np
import pandas as pd

from competition import execution, ledger, planner
from competition.data import AsOfView, MarketData
from competition.episodes import Episode
from competition.rules import CompetitionRules


@dataclass(frozen=True)
class PortfolioState:
    date: pd.Timestamp            # trade date t being decided
    asof: pd.Timestamp            # information cutoff: close of t-1
    day_index: int                # 0-based position of t in the episode
    sessions_remaining: int       # sessions left including t
    holdings: dict                # settled shares after t-1
    cash: float
    nav: float                    # NAV at the t-1 close (excluding dividend receivable)
    weights: dict                 # holding weights at the t-1 close
    episode_id: str


class Strategy(Protocol):
    def decide(self, view: AsOfView, state: PortfolioState) -> Mapping[str, float]:
        """Target weights for trade date ``state.date``; absent names mean zero."""


@dataclass(frozen=True)
class ExecutionConfig:
    mode: str = 'auto'                     # auto | proxy | official
    proxy: str = execution.DEFAULT_PROXY   # open | hlc3 | ohlc4

    def __post_init__(self):
        if self.mode not in execution.MODES or self.proxy not in execution.PROXIES:
            raise ValueError(f'Bad execution config {self.mode}/{self.proxy}')


def _benchmarks(market: MarketData, episode: Episode) -> dict:
    lo, hi = market.position(episode.sessions[0]), market.position(episode.sessions[-1]) + 1
    bench = float(np.prod(1 + market.benchmark_ret.iloc[lo:hi].fillna(0.)) - 1)
    alive = market.valid.loc[episode.prior_session]
    growth = (1 + market.ret.iloc[lo:hi].loc[:, alive].fillna(0.)).prod() - 1
    return dict(benchmark_0050_return=bench, equal_weight_universe_return=float(growth.mean()))


def run_episode(market: MarketData, episode: Episode, strategy: Strategy, rules: CompetitionRules,
                policy: planner.PlannerPolicy = planner.PlannerPolicy(),
                exec_config: ExecutionConfig = ExecutionConfig()) -> dict:
    book = ledger.Book(cash=rules.initial_capital)
    whitelist = set(market.symbols)
    records, trades, orders, issues, holdings_rows = [], [], [], [], []
    prev = episode.prior_session
    for i, day in enumerate(episode.sessions):
        nav = book.nav()
        state = PortfolioState(date=day, asof=prev, day_index=i, sessions_remaining=len(episode.sessions) - i,
                               holdings=dict(book.holdings), cash=book.cash, nav=nav,
                               weights={s: q * book.marks[s] / nav for s, q in book.holdings.items()},
                               episode_id=episode.episode_id)
        started = time.perf_counter()
        target = strategy.decide(market.asof(prev), state)
        decide_seconds = time.perf_counter() - started
        prev_close = market.close.loc[prev].copy()
        for s in book.holdings:            # suspended holding: size from its last valid close
            if not np.isfinite(prev_close.get(s, np.nan)):
                prev_close[s] = book.marks[s]
        p = planner.plan(target, prev_close, nav, book.holdings, book.cash, rules, policy)
        price, source = execution.fill_prices(market, day, sorted(p.orders), exec_config.mode, exec_config.proxy)
        t = market.position(day)
        book, record, day_trades, day_issues = ledger.settle(
            book, day, p.orders, price, source, market.close.iloc[t], market.split.iloc[t],
            market.dividend.iloc[t], {s: float(prev_close[s]) for s in p.orders}, rules, whitelist,
            market.quality_flag.iloc[t], (policy.sell_price_buffer, policy.buy_price_buffer))
        record.update(plan_status=p.status, plan_reason=p.reason[:200], target_names=int(sum(
            float(w) > 0 for w in dict(target).values())), decide_seconds=decide_seconds)
        records.append(record)
        trades.extend(day_trades)
        issues.extend(day_issues)
        orders.extend(dict(date=record['date'], symbol=s, shares=q, sizing_close=float(prev_close[s]), sizing_nav=nav,
                           dplan_weight=p.audit.get('dplan_target_weight', {}).get(s, np.nan))
                      for s, q in sorted(p.orders.items()))
        holdings_rows.extend(dict(date=record['date'], symbol=s, shares=q, close=book.marks[s])
                             for s, q in sorted(book.holdings.items()))
        prev = day
        if book.warnings >= rules.warnings_for_disqualification:
            break
    frame = pd.DataFrame(records)
    disqualified = book.warnings >= rules.warnings_for_disqualification
    credit = 0. if disqualified else book.receivable
    path = np.r_[rules.initial_capital, frame.nav.to_numpy(float)]
    path[-1] += credit
    frame['drawdown'] = 1 - path[1:] / np.maximum.accumulate(path)[1:]
    sources = Counter(t['source'] for t in trades)
    summary = dict(
        episode_id=episode.episode_id, split=episode.split, start=str(episode.start.date()),
        end=str(episode.end.date()), sessions=len(frame), complete=len(frame) == len(episode.sessions),
        terminal_nav=float(path[-1]), terminal_return=float(path[-1] / rules.initial_capital - 1),
        max_drawdown=float(frame.drawdown.max()), dividend_credit=float(credit),
        turnover=float(frame.turnover.sum()), costs=float(frame.costs.sum()), fees=float(frame.fees.sum()),
        taxes=float(frame.taxes.sum()), warning_days=int(frame.warning.sum()), disqualified=disqualified,
        unfilled_orders=int(frame.unfilled.sum()), envelope_breaches=int(frame.envelope_breaches.sum()),
        fill_sources=dict(sorted(sources.items())), plan_status=dict(Counter(frame.plan_status)),
        quality_flag_days=int(frame.quality_flagged_holdings.ne('').sum()),
        mean_holdings=float(frame.holdings.mean()), mean_cash_ratio=float(frame.cash_ratio.mean()),
        decide_seconds=float(frame.decide_seconds.sum()), **_benchmarks(market, episode))
    cols = ['date', 'symbol', 'shares', 'price', 'source', 'notional', 'fee', 'tax']
    return dict(ledger=frame, trades=pd.DataFrame(trades, columns=cols), summary=summary,
                orders=pd.DataFrame(orders, columns=['date', 'symbol', 'shares', 'sizing_close', 'sizing_nav', 'dplan_weight']),
                issues=pd.DataFrame(issues, columns=['date', 'symbol', 'issue']),
                holdings=pd.DataFrame(holdings_rows, columns=['date', 'symbol', 'shares', 'close']))


def verify_episode(market: MarketData, episode: Episode, result: dict, rules: CompetitionRules,
                   tol: float = 1e-6) -> list[str]:
    """Rebuild cash, fees, shares and NAV from the trade table and market data alone."""
    problems = []
    frame, trades = result['ledger'], result['trades']
    cash, holdings, marks, receivable = rules.initial_capital, {}, {}, 0.
    for _, row in frame.iterrows():
        t = market.position(row.date)
        close, split, div = market.close.iloc[t], market.split.iloc[t], market.dividend.iloc[t]
        for s in list(holdings):
            receivable += holdings[s] * div[s]
            holdings[s] *= split[s]
            if not np.isfinite(close[s]):
                marks[s] /= split[s]
        for tr in trades[trades.date == row.date].itertuples():
            if abs(tr.shares) % rules.lot_size:
                problems.append(f'{row.date} {tr.symbol}: odd-lot trade {tr.shares}')
            rate = rules.commission_buy if tr.shares > 0 else rules.commission_sell
            fee, tax = abs(tr.shares) * tr.price * rate, abs(tr.shares) * tr.price * rules.tax_sell * (tr.shares < 0)
            if abs(fee - tr.fee) > tol * max(1., fee) or abs(tax - tr.tax) > tol * max(1., tax):
                problems.append(f'{row.date} {tr.symbol}: fee/tax mismatch')
            cash -= tr.shares * tr.price + fee + tax
            holdings[tr.symbol] = holdings.get(tr.symbol, 0.) + tr.shares
            if holdings[tr.symbol] < 1e-6:
                del holdings[tr.symbol]
            marks.setdefault(tr.symbol, tr.price)
        marks.update({s: close[s] for s in holdings if np.isfinite(close[s])})
        nav = cash + sum(q * marks[s] for s, q in holdings.items())
        if abs(nav - row.nav) > tol * rules.initial_capital or abs(cash - row.cash) > tol * rules.initial_capital:
            problems.append(f'{row.date}: NAV/cash mismatch {nav:.2f} vs {row.nav:.2f}')
        if not row.warning:
            weights = {s: q * marks[s] / nav for s, q in holdings.items()}
            if not rules.min_positions <= len(holdings) <= rules.max_positions:
                problems.append(f'{row.date}: {len(holdings)} holdings without a warning')
            if not rules.cash_min - 1e-6 <= cash < rules.cash_max_exclusive * nav:
                problems.append(f'{row.date}: cash ratio {cash / nav:.4f} without a warning')
            passive = str(row.passive_caps or '').split(';')
            over = [s for s, w in weights.items() if w > rules.cap(s) + 1e-10 and s not in passive]
            if over:
                problems.append(f'{row.date}: cap breach without warning {over}')
    if not frame.empty and abs(receivable - frame.receivable.iloc[-1]) > tol * rules.initial_capital:
        problems.append('dividend receivable mismatch')
    return problems
