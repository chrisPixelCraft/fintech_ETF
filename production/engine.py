"""One trading day's decision: frozen strategy on the normal path, a legal fallback otherwise.

Normal path (identical to competition.backtest.run_episode for the same book):
T-1 view -> momentum score -> portfolio.target_weights -> Active Share check /
repair -> competition.planner.plan. Any failure moves to the fallback path,
which always tries to leave a submittable plan:

    cold start (no holdings)       -> COLD_START_FALLBACK (frozen list, equal weight)
    current book still compliant   -> FALLBACK_HOLD (no orders)
    otherwise                      -> FALLBACK_COMPLIANCE_REPAIR (minimum repair)
    nothing legal can be produced  -> EMERGENCY_REVIEW_REQUIRED
"""
from __future__ import annotations

import traceback
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from competition import ledger, planner, portfolio
from competition.backtest import PortfolioState
from competition.data import MarketData
from competition.rules import CompetitionRules
from production import active_share
from production.state import SettledBook, reconcile, symbol_to_ticker
from research.baselines import MomentumConfig, momentum_score

NORMAL, HOLD, REPAIR = 'NORMAL', 'FALLBACK_HOLD', 'FALLBACK_COMPLIANCE_REPAIR'
COLD_START, EMERGENCY = 'COLD_START_FALLBACK', 'EMERGENCY_REVIEW_REQUIRED'
SUBMITTABLE = (NORMAL, HOLD, REPAIR, COLD_START)
MIN_CLOSE_COVERAGE = 120        # of 150 names with a finite T-1 close; below this the data is treated as broken


@dataclass(frozen=True)
class DayInput:
    trade_date: pd.Timestamp
    prev_date: pd.Timestamp                 # T-1, the last settled session
    market: MarketData                      # must contain prev_date
    book: SettledBook                       # shadow ledger after the T-1 close
    strategy: dict                          # frozen strategy config (production/strategy.json)
    rules: CompetitionRules
    day_index: int                          # 0-based position in the contest
    sessions_remaining: int                 # including trade_date
    official_holdings: dict | None = None   # organizer holdings {symbol: shares}
    require_official: bool = False
    etfs: dict | None = None                # active_share.load_etf_holdings
    required_etfs: tuple = ()
    cold_start: tuple = ()                  # frozen cold-start symbols
    degraded: tuple = ()                    # upstream data problems that forbid the normal path


@dataclass
class DayResult:
    mode: str
    reasons: list
    target: dict                            # symbol -> weight handed to the planner
    plan: planner.Plan
    holdings: dict                          # book used for the decision (official when given)
    nav: float
    prev_close: pd.Series
    scores: pd.Series | None = None
    active_share: dict = field(default_factory=dict)
    data_gate: list = field(default_factory=list)
    reconciliation: str = 'NOT_CHECKED'

    @property
    def submittable(self) -> bool:
        return self.mode in SUBMITTABLE


def policy_of(strategy: dict) -> planner.PlannerPolicy:
    return planner.PlannerPolicy(**strategy.get('planner', {}))


def momentum_of(strategy: dict) -> MomentumConfig:
    if strategy.get('strategy') != 'momentum':
        raise ValueError('Production runs the frozen momentum strategy only')
    return MomentumConfig.from_dict(strategy['params'])


def sizing_close(market: MarketData, prev: pd.Timestamp, book: SettledBook) -> pd.Series:
    """T-1 close, with a held name's last valid close when T-1 has none (as in the backtest).
    Without a T-1 row the latest earlier row is used and the caller must not trade on it."""
    close = (market.close.loc[prev] if prev in market.close.index else market.close.loc[:prev].iloc[-1]).copy()
    for s in book.holdings:
        if not np.isfinite(close.get(s, np.nan)):
            close[s] = book.marks[s]
    return close


def data_gate(market: MarketData, prev: pd.Timestamp, book: SettledBook) -> list[str]:
    """Problems that make the T-1 data unusable for the strategy (empty = usable)."""
    problems = []
    if prev not in market.calendar or market.close.index[-1] < prev:
        return [f'NO_ROW_FOR_T-1:{prev.date()}']
    close = market.close.loc[prev]
    finite = int(np.isfinite(close).sum())
    if finite < MIN_CLOSE_COVERAGE:
        problems.append(f'LOW_CLOSE_COVERAGE:{finite}')
    if (close[np.isfinite(close)] <= 0).any():
        problems.append('NON_POSITIVE_CLOSE')
    missing = [s for s in book.holdings if not np.isfinite(close.get(s, np.nan)) and s not in book.marks]
    if missing:
        problems.append('HELD_WITHOUT_PRICE:' + ';'.join(sorted(missing)))
    return problems


def _weights(book: SettledBook, nav: float) -> dict:
    return {s: q * book.marks[s] / nav for s, q in book.holdings.items()}


def _tickers(weights: dict) -> dict:
    return {symbol_to_ticker(s): w for s, w in weights.items()}


def _check_active_share(target: dict, inp: DayInput) -> dict:
    return active_share.check(_tickers(target), inp.etfs, list(inp.required_etfs),
                              str(inp.trade_date.date())).as_dict()


def normal_path(inp: DayInput, book: SettledBook, nav: float, close: pd.Series) -> DayResult:
    cfg = momentum_of(inp.strategy)
    state = PortfolioState(date=inp.trade_date, asof=inp.prev_date, day_index=inp.day_index,
                           sessions_remaining=inp.sessions_remaining, holdings=dict(book.holdings), cash=book.cash,
                           nav=nav, weights=_weights(book, nav), episode_id=str(inp.trade_date.date()))
    view = inp.market.asof(inp.prev_date)
    scores = momentum_score(view, cfg)

    def build(sc):
        return portfolio.target_weights(sc, state.weights, state.sessions_remaining, inp.rules, cfg.portfolio)

    target = build(scores)
    report = _check_active_share(target, inp)
    reasons = []
    if report['status'] in (active_share.INVALID, active_share.CAUTION) and inp.etfs:
        target, dropped = active_share.repair(scores, build, inp.etfs, symbol_to_ticker)
        reasons.append('ACTIVE_SHARE_REPAIR_DROPPED:' + ';'.join(dropped))
        report = _check_active_share(target, inp)
    if report['status'] == active_share.INVALID:
        raise RuntimeError('ACTIVE_SHARE_INVALID_AFTER_REPAIR')
    plan = planner.plan(target, close, nav, book.holdings, book.cash, inp.rules, policy_of(inp.strategy))
    if plan.status == 'INFEASIBLE':
        raise RuntimeError('PLAN_INFEASIBLE:' + plan.reason)
    mode = HOLD if plan.status == 'HOLD_FALLBACK' else NORMAL
    if mode == HOLD:
        reasons.append(plan.reason)
    return DayResult(mode, reasons, target, plan, dict(book.holdings), nav, close, scores, report)


def _hold_plan(book: SettledBook) -> planner.Plan:
    return planner.Plan({}, dict(book.holdings), 'OK', 'FALLBACK_HOLD', dict(repairs=[], dplan_target_weight={}))


def _repair_target(book: SettledBook, nav: float, inp: DayInput) -> dict:
    """Minimum-change legal target: caps respected, 20-30 names, invested inside the cash band."""
    pcfg = momentum_of(inp.strategy).portfolio
    weights = {s: min(w, inp.rules.cap(s) * pcfg.cap_scale) for s, w in _weights(book, nav).items() if w > 0}
    if len(weights) > inp.rules.max_positions:
        weights = dict(sorted(weights.items(), key=lambda kv: -kv[1])[:inp.rules.max_positions])
    fill = [s for s in inp.cold_start if s not in weights]
    while len(weights) < inp.rules.min_positions and fill:
        weights[fill.pop(0)] = 0.
    total = sum(weights.values())
    if total < pcfg.invested - .05 or any(w == 0 for w in weights.values()):
        caps = pd.Series({s: inp.rules.cap(s) * pcfg.cap_scale for s in weights})
        pref = pd.Series({s: max(w, 1. / len(weights)) for s, w in weights.items()})
        weights = portfolio.cap_fill(pref, pcfg.invested, caps).to_dict()
    return weights


def fallback_path(inp: DayInput, book: SettledBook, nav: float, close: pd.Series, reasons: list) -> DayResult:
    policy, pcfg = policy_of(inp.strategy), momentum_of(inp.strategy).portfolio
    if not book.holdings:
        if len(inp.cold_start) < inp.rules.min_positions:
            return DayResult(EMERGENCY, reasons + ['NO_COLD_START_LIST'], {}, _hold_plan(book), {}, nav, close)
        names = list(inp.cold_start)
        target = portfolio.cap_fill(pd.Series(1., index=names), pcfg.invested,
                                    pd.Series({s: inp.rules.cap(s) * pcfg.cap_scale for s in names})).to_dict()
        plan = planner.plan(target, close, nav, {}, book.cash, inp.rules, policy)
        mode = COLD_START if plan.status in ('OK', 'REPAIRED') else EMERGENCY
        return DayResult(mode, reasons, target, plan, {}, nav, close, active_share=_check_active_share(target, inp))
    current = _weights(book, nav)
    checks = ledger.rule_check(book.holdings, book.cash, book.marks, inp.rules, set(inp.market.symbols))
    report = _check_active_share(current, inp)
    if not checks['violations'] and report['status'] != active_share.INVALID:
        return DayResult(HOLD, reasons, current, _hold_plan(book), dict(book.holdings), nav, close, active_share=report)
    target = _repair_target(book, nav, inp)
    plan = planner.plan(target, close, nav, book.holdings, book.cash, inp.rules, policy)
    mode = REPAIR if plan.status in ('OK', 'REPAIRED') else EMERGENCY
    return DayResult(mode, reasons + ['BOOK_VIOLATIONS:' + ';'.join(checks['violations'])], target, plan,
                     dict(book.holdings), nav, close, active_share=_check_active_share(target, inp))


def run_day(inp: DayInput) -> DayResult:
    """Decide trade date T from information through T-1; never raises for data or strategy errors."""
    book, reasons, rec = inp.book, [], 'NOT_CHECKED'
    if inp.official_holdings is not None:
        match = reconcile(inp.official_holdings, book.holdings)
        rec = match.summary()
        if not match.ok:
            reasons.append('STATE_MISMATCH')
            marks = {**book.marks, **{s: float(inp.market.close.loc[inp.prev_date, s])
                                      for s in inp.official_holdings if s not in book.marks}}
            book = SettledBook(book.date, dict(inp.official_holdings), book.cash, book.receivable, marks,
                               book.cap_ages, book.warnings)
    elif inp.require_official:
        reasons.append('OFFICIAL_STATE_MISSING')
    reasons += [f'DEGRADED:{d}' for d in inp.degraded]
    close = sizing_close(inp.market, inp.prev_date, book)
    nav = book.nav
    gate = data_gate(inp.market, inp.prev_date, book)
    if gate:
        reasons.append('DATA_GATE:' + ','.join(gate))
    result = None
    if not reasons:
        try:
            result = normal_path(inp, book, nav, close)
        except Exception as error:                          # recorded; the fallback still submits
            reasons.append(f'NORMAL_PATH_FAILED:{error!r}')
            reasons.append(traceback.format_exc(limit=3))
    if result is None:
        try:
            result = fallback_path(inp, book, nav, close, reasons)
        except Exception as error:
            result = DayResult(EMERGENCY, reasons + [f'FALLBACK_FAILED:{error!r}'], {}, _hold_plan(book),
                               dict(book.holdings), nav, close)
        if inp.prev_date not in inp.market.close.index and result.plan.orders:
            result = DayResult(EMERGENCY, result.reasons + ['STALE_SIZING_CLOSE_WITH_ORDERS'], {}, _hold_plan(book),
                               dict(book.holdings), nav, close)
    result.data_gate, result.reconciliation = gate, rec
    return result
