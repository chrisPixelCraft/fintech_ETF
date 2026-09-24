"""D-1 order planner: official D-Plan lot formula plus fail-closed rule repair.

Adapted from legacy/src/v5_planner.py (plan/_Book/_failures), with
representable_weight/whole_lots inlined from legacy/src/official_v2_review.py
and the float-residue wrapper from legacy/src/official_deep_tuning.py.
The legacy 'lots_around' study mode is dropped; odd holdings are always held.

Only D-1 information is used (previous close, previous NAV, settled holdings
and cash). Execution-day prices are unknown, so a symmetric envelope (buys at up
to +10%, sells down to -10%, the daily price limit) guards cash and caps.

Official sizing (D-Plan guide; competition_rules.json execution):
    target_shares = floor(target_weight * prev_nav / prev_close / 1000) * 1000
    order_shares  = target_shares - current_shares

Odd corporate-action holdings (rules U7, unresolved): a holding that is not a
whole number of lots is kept unchanged, still counts toward NAV, count and cap;
plans touching one are tagged ASSUMPTION_ODD_LOT.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal
from typing import Mapping

import numpy as np
import pandas as pd

from competition.rules import CompetitionRules

ASSUMPTION_ODD_LOT = 'ASSUMPTION_ODD_LOT'
ODD_TOL = 1e-6


@dataclass(frozen=True)
class PlannerPolicy:
    """Policy knobs (not rules). The envelope matches the +/-10% daily limit."""
    rebalance_band: float = .005        # per-name change below this NAV fraction is skipped
    cash_ratio_margin: float = .005     # best-case cash ratio must stay below 25% - margin
    buy_price_buffer: float = 1.10
    sell_price_buffer: float = .90
    max_repair_steps: int = 20000

    def __post_init__(self):
        if not 0 < self.sell_price_buffer <= 1 <= self.buy_price_buffer:
            raise ValueError('Invalid price envelope')
        if self.rebalance_band < 0 or self.cash_ratio_margin < 0:
            raise ValueError('Policy margins must be non-negative')


@dataclass
class Plan:
    orders: dict            # symbol -> signed shares, multiple of lot size
    target_shares: dict     # symbol -> intended post-trade shares (incl. odd holdings)
    status: str             # OK | REPAIRED | HOLD_FALLBACK | INFEASIBLE
    reason: str
    audit: dict = field(default_factory=dict)


def is_whole_lot(quantity: float, lot: int = 1000) -> bool:
    return abs(float(quantity) - round(float(quantity) / lot) * lot) <= ODD_TOL


def representable_weight(target: float, price: float, nav: float, cap: float, lot: int = 1000) -> float:
    """D-Plan weight whose official floor formula recovers ``target`` shares exactly.

    Picks the midpoint of the same target-lot bin (capped at ``cap``). Sub-microshare
    float residue from corporate-action ratios is removed first; a real odd target raises.
    """
    exact = round(float(target) / lot) * lot
    if abs(float(target) - exact) > ODD_TOL:
        raise ValueError('DPLAN_ODD_TARGET_UNREPRESENTABLE')
    if exact == 0:
        return 0.
    q, p, n, maximum = (Decimal(str(x)) for x in (exact, price, nav, cap))
    lower = q * p / n
    upper = min((q + lot) * p / n, maximum)
    if upper <= lower:
        raise ValueError('NO_INTERIOR_TARGET_WEIGHT_WITHIN_CAP')
    weight = float((lower + upper) / 2)
    recovered = (Decimal(str(weight)) * n / p / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
    if recovered != q:
        raise ValueError('DPLAN_WEIGHT_ROUNDTRIP_FAILED')
    return weight


class _Book:
    """Per-name lots (int) plus fixed odd shares; D-1 prices only."""

    def __init__(self, names, price, cur, extra, tradable, prev_nav, cash, rules, policy):
        self.names, self.p, self.cur, self.extra, self.tradable = names, price, cur, extra, tradable
        self.nav0, self.cash0, self.r, self.pol = prev_nav, cash, rules, policy
        self.lot = rules.lot_size
        self.L = dict(cur)

    def shares(self, s):
        return self.L[s] * self.lot + self.extra[s]

    def held_names(self):
        return [s for s in self.names if self.shares(s) > ODD_TOL]

    def flows(self, buy_mult=1., sell_mult=1.):
        r, cash, fees = self.r, self.cash0, 0.
        for s in self.names:
            delta = (self.L[s] - self.cur[s]) * self.lot
            if delta > 0:
                amount = delta * self.p[s] * buy_mult
                cash -= amount * (1 + r.commission_buy)
                fees += amount * r.commission_buy
            elif delta < 0:
                amount = -delta * self.p[s] * sell_mult
                cash += amount * (1 - r.commission_sell - r.tax_sell)
                fees += amount * (r.commission_sell + r.tax_sell)
        return cash, fees

    def value(self):
        return sum(self.shares(s) * self.p[s] for s in self.names)

    def worst_cash(self):
        return self.flows(self.pol.buy_price_buffer, self.pol.sell_price_buffer)[0]

    def best_cash_ratio(self):
        cash = self.flows(self.pol.sell_price_buffer, self.pol.buy_price_buffer)[0]
        nav = cash + self.value()
        return cash / nav if nav > 0 else np.inf

    def estimate(self):
        cash, fees = self.flows()
        nav = cash + self.value()
        weights = {s: self.shares(s) * self.p[s] / nav for s in self.held_names()} if nav > 0 else {}
        return dict(cash=cash, fees=fees, nav=nav, cash_ratio=cash / nav if nav > 0 else np.inf,
                    count=len(weights), weights=weights, worst_cash=self.worst_cash(),
                    best_case_cash_ratio=self.best_cash_ratio())

    def buy_cap_lots(self, s):
        """Largest lots whose value at the upper price bound stays within cap of prev NAV."""
        limit = self.r.cap(s) * self.nav0 / (self.p[s] * self.pol.buy_price_buffer) - self.extra[s]
        return max(0, int(math.floor(limit / self.lot + 1e-12)))

    def strict_cap_ok(self, s):
        return self.shares(s) * self.p[s] < self.r.cap(s) * self.nav0 * (1 - 1e-9)


def _failures(book: _Book, est: dict, rules: CompetitionRules, policy: PlannerPolicy) -> list[str]:
    failures = []
    if est['cash'] < rules.cash_min - 1e-6:
        failures.append('NEGATIVE_CASH_ESTIMATE')
    if est['worst_cash'] < rules.cash_min - 1e-6:
        failures.append('WORST_CASE_NEGATIVE_CASH')
    if not est['cash_ratio'] < rules.cash_max_exclusive:
        failures.append('CASH_GE_25_PERCENT')
    if not est['best_case_cash_ratio'] < rules.cash_max_exclusive - policy.cash_ratio_margin:
        failures.append('BEST_CASE_CASH_RATIO_HIGH')
    if not rules.min_positions <= est['count'] <= rules.max_positions:
        failures.append('HOLDING_COUNT')
    for s, weight in est['weights'].items():
        if weight > rules.cap(s) + 1e-10:
            failures.append('WEIGHT_CAP:' + s)
    for s in book.names:
        if book.L[s] != book.cur[s] and not book.strict_cap_ok(s):
            failures.append('ORDER_AT_OR_ABOVE_CAP:' + s)
    return failures


def plan(target_weight: Mapping[str, float] | pd.Series, prev_close: Mapping[str, float] | pd.Series,
         prev_nav: float, holdings: Mapping[str, float], cash: float, rules: CompetitionRules,
         policy: PlannerPolicy = PlannerPolicy()) -> Plan:
    """Turn D-1 target weights into whole-lot orders that satisfy the hard rules.

    target_weight: symbol -> weight (absent = 0 = exit). prev_close must cover every held name.
    """
    lot = rules.lot_size
    audit = dict(assumptions=[], repairs=[], dropped_targets=[], odd_lot=[])
    prev_nav, cash = float(prev_nav), float(cash)
    tw = pd.Series(target_weight, dtype=float)
    tw.index = tw.index.astype(str)
    if tw.index.has_duplicates:
        raise ValueError('Duplicate target symbols')
    tw = tw.replace([np.inf, -np.inf], np.nan).fillna(0.)
    if (tw < 0).any():
        audit['repairs'].append('LONG_ONLY_NEGATIVE_WEIGHTS_ZEROED:' + ';'.join(sorted(tw.index[tw < 0])))
        tw = tw.clip(lower=0.)
    prices = pd.Series(prev_close, dtype=float)
    prices.index = prices.index.astype(str)
    held = {str(s): float(q) for s, q in holdings.items() if float(q) > ODD_TOL}
    if not np.isfinite(prev_nav) or prev_nav <= 0 or not np.isfinite(cash):
        return Plan({}, held, 'INFEASIBLE', 'INVALID_BOOK', audit)

    def good_price(s):
        return s in prices.index and np.isfinite(prices[s]) and prices[s] > 0

    missing_held = sorted(s for s in held if not good_price(s))
    if missing_held:
        return Plan({}, held, 'INFEASIBLE', 'MISSING_HELD_CLOSE:' + ';'.join(missing_held), audit)
    wanted = [s for s in tw.index if tw[s] > 0]
    audit['dropped_targets'] = [s + ':MISSING_CLOSE' for s in wanted if not good_price(s)]
    names = sorted(set(held) | {s for s in wanted if good_price(s)})
    cur, extra, tradable = {}, {}, {}
    for s in names:
        q = held.get(s, 0.)
        if is_whole_lot(q, lot):
            cur[s], extra[s], tradable[s] = int(round(q / lot)), 0., True
        else:
            cur[s], extra[s], tradable[s] = 0, q, False
            audit['odd_lot'].append(dict(symbol=s, shares=q, remainder=float(q % lot),
                                         target_weight=float(tw.get(s, 0.)), action='HELD_UNCHANGED'))
    if audit['odd_lot']:
        audit['assumptions'].append(ASSUMPTION_ODD_LOT)
    w = {s: float(tw.get(s, 0.)) for s in names}
    book = _Book(names, {s: float(prices[s]) for s in names}, cur, extra, tradable, prev_nav, cash, rules, policy)

    # 1. Official formula for every tradable name.
    for s in names:
        if tradable[s]:
            book.L[s] = int(math.floor(w[s] * prev_nav / book.p[s] / lot + 1e-12))
    formula_lots = dict(book.L)
    repaired = []
    # No-trade band: a held name whose formula change is below the band keeps its shares.
    banded = [s for s in names if tradable[s] and book.cur[s] > 0 and book.L[s] > 0 and book.L[s] != book.cur[s]
              and abs(book.L[s] - book.cur[s]) * lot * book.p[s] < policy.rebalance_band * prev_nav]
    for s in banded:
        book.L[s] = book.cur[s]
    if banded:
        audit['banded'] = banded

    # 2. Caps: buys sized at the upper price bound; orders strictly below cap.
    for s in names:
        if not tradable[s]:
            if book.shares(s) * book.p[s] >= rules.cap(s) * prev_nav:
                audit['repairs'].append('ODD_LOT_CAP_UNREPAIRABLE:' + s)
            continue
        allowed = book.buy_cap_lots(s)
        if book.L[s] > book.cur[s] and book.L[s] > allowed:
            book.L[s] = max(book.cur[s], allowed)
            repaired.append('CAP_BUY_LIMIT:' + s)
        if book.shares(s) * book.p[s] >= rules.cap(s) * prev_nav * (1 - 1e-9):
            book.L[s] = min(book.L[s], allowed)
            repaired.append('CAP_TRIM:' + s)

    # 3. Holding count.
    def count():
        return len(book.held_names())
    if count() > rules.max_positions:
        removable = sorted((s for s in names if tradable[s] and book.L[s] > 0),
                           key=lambda s: (w[s], book.cur[s] > 0, book.shares(s) * book.p[s], s))
        for s in removable:
            if count() <= rules.max_positions:
                break
            book.L[s] = 0
            repaired.append('COUNT_DROP:' + s)
    if count() < rules.min_positions:
        for s in sorted((s for s in names if tradable[s] and w[s] > 0 and book.shares(s) <= ODD_TOL),
                        key=lambda s: (-w[s], s)):
            if count() >= rules.min_positions:
                break
            if book.buy_cap_lots(s) >= 1:
                book.L[s] = 1
                repaired.append('COUNT_MIN_LOT:' + s)

    # 4. Worst-case funding (buys at the upper bound, sells at the lower bound):
    #    scale buy increments, then remove single lots from the largest buy.
    #    New entries keep one lot while the count needs them.
    def buy_floor(s):
        if book.cur[s] > 0:
            return book.cur[s]
        return 1 if count() <= rules.min_positions else 0

    if book.worst_cash() < rules.cash_min - 1e-6:
        buys = [s for s in names if book.L[s] > book.cur[s]]
        upper = policy.buy_price_buffer * (1 + rules.commission_buy)
        required = sum((book.L[s] - book.cur[s]) * lot * book.p[s] * upper for s in buys)
        available = book.worst_cash() + required - rules.cash_min
        factor = max(0., available) / required if required > 0 else 1.
        for s in buys:
            scaled = book.cur[s] + int(math.floor((book.L[s] - book.cur[s]) * factor))
            book.L[s] = max(scaled, min(book.L[s], 1 if book.cur[s] == 0 else book.cur[s]))
        repaired.append(f'FUNDING_SCALE:{factor:.6f}')
    steps = 0
    while book.worst_cash() < rules.cash_min - 1e-6 and steps < policy.max_repair_steps:
        reducible = [s for s in names if book.L[s] > book.cur[s] and book.L[s] > buy_floor(s)]
        if not reducible:
            break
        s = max(reducible, key=lambda x: ((book.L[x] - book.cur[x]) * book.p[x], x))
        book.L[s] -= 1
        steps += 1
    if steps:
        repaired.append(f'FUNDING_TRIM_LOTS:{steps}')
    if any(x.startswith('FUNDING_SCALE') for x in repaired):
        # Refill scaled-down buys toward (never beyond) their formula/cap lots.
        steps = 0
        unit = policy.buy_price_buffer * (1 + rules.commission_buy)
        while steps < policy.max_repair_steps:
            options = [s for s in names if tradable[s] and book.L[s] >= book.cur[s]
                       and book.L[s] < min(formula_lots[s], book.buy_cap_lots(s)) and book.L[s] > 0
                       and book.worst_cash() - lot * book.p[s] * unit >= rules.cash_min]
            if not options:
                break
            s = max(options, key=lambda x: ((formula_lots[x] - book.L[x]) * book.p[x], x))
            book.L[s] += 1
            steps += 1
        if steps:
            repaired.append(f'FUNDING_REFILL_LOTS:{steps}')

    # 5. Cash ceiling: add lots toward target shortfall while funding and caps allow.
    steps = 0
    while (book.best_cash_ratio() >= rules.cash_max_exclusive - policy.cash_ratio_margin
           and steps < policy.max_repair_steps):
        options = [s for s in book.held_names() if tradable[s] and book.L[s] < book.buy_cap_lots(s)
                   and book.L[s] >= book.cur[s]]
        if not options:
            break
        s = max(options, key=lambda x: (w[x] * prev_nav - book.shares(x) * book.p[x], x))
        book.L[s] += 1
        if book.worst_cash() < rules.cash_min - 1e-6:
            book.L[s] -= 1
            break
        steps += 1
    if steps:
        repaired.append(f'CASH_CEILING_TOPUP_LOTS:{steps}')

    audit['repairs'].extend(repaired)
    est = book.estimate()
    failures = _failures(book, est, rules, policy)
    orders = {s: int((book.L[s] - book.cur[s]) * lot) for s in names if tradable[s] and book.L[s] != book.cur[s]}
    # Every emitted order must round-trip a D-Plan weight through the official formula.
    dplan_weights = {}
    for s, q in sorted(orders.items()):
        try:
            dplan_weights[s] = representable_weight(held.get(s, 0.) + q, book.p[s], prev_nav, rules.cap(s), lot)
        except ValueError as error:
            failures.append(f'UNREPRESENTABLE_ORDER:{s}:{error}')
    audit.update(formula_lots={s: v for s, v in formula_lots.items() if v}, dplan_target_weight=dplan_weights,
                 estimate={k: (v if k != 'weights' else {s: round(x, 8) for s, x in v.items()}) for k, v in est.items()},
                 failures=list(failures))
    target_shares = {s: book.shares(s) for s in book.held_names()}
    if not failures:
        status = 'REPAIRED' if repaired else 'OK'
        return Plan(orders, target_shares, status, 'PLAN_' + status, audit)
    # Hold the current book when it is itself nominally valid; otherwise emit the
    # best representable repair (never an unrepresentable order).
    hold = _Book(names, book.p, cur, extra, tradable, prev_nav, cash, rules, policy)
    hold_failures = [f for f in _failures(hold, hold.estimate(), rules, policy) if f != 'BEST_CASE_CASH_RATIO_HIGH']
    audit['hold_failures'] = hold_failures
    if not hold_failures or any(f.startswith('UNREPRESENTABLE_ORDER') for f in failures):
        return Plan({}, {s: hold.shares(s) for s in hold.held_names()}, 'HOLD_FALLBACK',
                    'HOLD_FALLBACK:' + ';'.join(failures), audit)
    return Plan(orders, target_shares, 'INFEASIBLE', 'INFEASIBLE:' + ';'.join(failures), audit)
