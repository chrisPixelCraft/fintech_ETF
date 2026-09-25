"""V5 D-1 order planner: official D-Plan lot formula plus rule repair.

Everything here uses only D-1 information (previous official close, previous
NAV, settled holdings/cash). Execution-day prices are unknown; a symmetric
price envelope (buys at up to +10%, sells at down to -10%, the TWSE/TPEx daily
limit since 2015-06) guards cash and caps.

Official sizing formula (D-Plan guide ⑥; competition_rules.json execution):
    target_shares = floor(target_weight * prev_nav / prev_close / 1000) * 1000
    order_shares  = target_shares - current_shares      (multiple of 1000)

Corporate-action odd lots (spec §5)
-----------------------------------
Sources: 比賽辦法 p.8 (十) "交易單位僅限整股交易，不進行零股交易" (orders are board lots
only); (十二) the organizer adjusts share quantities for ex-rights/splits "依市場
實際情況" and the result enters NAV; NAV = Σ 張數×1000×收盤價 + 現金 (p.7), so an
odd entitlement is valued. competition_rules.json lists
`fractional_corporate_action_entitlements` as UNRESOLVED ("handling of resulting
odd lots and rounding omitted"; must be labelled an assumption). The documents
therefore do not say how the official formula applies to a non-multiple
holding. Least-assumption rule adopted (default ``odd_lot_mode='hold'``):

* A holding whose share count is not a whole number of lots is a *legacy
  position*: it is kept unchanged (no order is ever computed from an odd
  holding, so no odd-lot order can arise), it counts toward NAV, the 20–30
  holding count and its cap, and every other name keeps trading normally.
  This matches the frozen V3 policy (`official_review_policy.odd_holdings =
  HOLD_UNTIL_OFFICIAL_FORMULA_CLARIFIED`).
* Every plan touching such a holding is tagged ``ASSUMPTION_ODD_LOT`` in its audit.

``odd_lot_mode='lots_around'`` (trade whole lots around the kept remainder,
new holding = formula lots + remainder) is implemented for study but is NOT
accepted by the sealed ledger: src/v4_ledger.py builds its order audit with
``representable_weight(holdings.get(s, 0.) + q, ...)`` which raises
``DPLAN_ODD_TARGET_UNREPRESENTABLE`` for any order on an odd-held name; the
exception aborts run_ledger and v4_baseline.run_episode returns FAIL_ROUND_LOT.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np
import pandas as pd

from src.official_deep_tuning import representable_weight

ASSUMPTION_ODD_LOT = 'ASSUMPTION_ODD_LOT'
ODD_TOL = 1e-6

DEFAULTS = dict(min_count=20, max_count=30, max_weight=.10, tsmc_max_weight=.25,
                commission=.001425, sell_tax=.003, lot_size=1000, cash_max=.25,
                cash_ratio_margin=.005, min_cash=0., buy_price_buffer=1.10,
                sell_price_buffer=.90, odd_lot_mode='hold', rebalance_band=.005,
                max_repair_steps=20000)


@dataclass
class Plan:
    orders: dict            # symbol -> signed shares, multiple of lot_size
    target_shares: dict     # symbol -> intended post-trade shares (incl. legacy odd)
    status: str             # OK | REPAIRED | HOLD_FALLBACK | INFEASIBLE
    reason: str
    audit: dict = field(default_factory=dict)


def settings(config=None):
    config = dict(config or {})
    c = {k: config.get(k, v) for k, v in DEFAULTS.items()}
    # Accept the frozen base-config spellings.
    c['buy_price_buffer'] = float(config.get('buy_price_buffer', config.get('price_buffer', c['buy_price_buffer'])))
    c['sell_price_buffer'] = float(config.get('sell_price_buffer', config.get('price_lower_buffer', c['sell_price_buffer'])))
    if c['odd_lot_mode'] not in ('hold', 'lots_around'):
        raise ValueError('odd_lot_mode must be hold or lots_around')
    if not 0 < c['sell_price_buffer'] <= 1 <= c['buy_price_buffer']:
        raise ValueError('Invalid price envelope')
    if not 0 < c['cash_max'] <= .25:
        raise ValueError('cash_max may not exceed the official 25% limit')
    return c


def cap_of(symbol, c):
    return c['tsmc_max_weight'] if str(symbol).split('.')[0] == '2330' else c['max_weight']


def is_whole_lot(quantity, lot=1000):
    return abs(float(quantity) - round(float(quantity) / lot) * lot) <= ODD_TOL


class _Book:
    """Per-name lots (int) plus fixed extra shares; D-1 prices only."""

    def __init__(self, names, price, cur, extra, tradable, weight, prev_nav, cash, c):
        self.names, self.p, self.cur, self.extra = names, price, cur, extra
        self.tradable, self.w, self.nav0, self.cash0, self.c = tradable, weight, prev_nav, cash, c
        self.lot = c['lot_size']
        self.L = dict(cur)

    def shares(self, s):
        return self.L[s] * self.lot + self.extra[s]

    def held_names(self):
        return [s for s in self.names if self.shares(s) > ODD_TOL]

    def flows(self, buy_mult=1., sell_mult=1.):
        c, cash, fees = self.c, self.cash0, 0.
        for s in self.names:
            delta = (self.L[s] - self.cur[s]) * self.lot
            if delta > 0:
                amount = delta * self.p[s] * buy_mult
                cash -= amount * (1 + c['commission'])
                fees += amount * c['commission']
            elif delta < 0:
                amount = -delta * self.p[s] * sell_mult
                cash += amount * (1 - c['commission'] - c['sell_tax'])
                fees += amount * (c['commission'] + c['sell_tax'])
        return cash, fees

    def value(self):
        return sum(self.shares(s) * self.p[s] for s in self.names)

    def worst_cash(self):
        return self.flows(self.c['buy_price_buffer'], self.c['sell_price_buffer'])[0]

    def best_cash_ratio(self):
        cash = self.flows(self.c['sell_price_buffer'], self.c['buy_price_buffer'])[0]
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
        """Largest lots whose value, at the upper price bound, stays within cap of prev NAV."""
        limit = cap_of(s, self.c) * self.nav0 / (self.p[s] * self.c['buy_price_buffer']) - self.extra[s]
        return max(0, int(math.floor(limit / self.lot + 1e-12)))

    def strict_cap_ok(self, s):
        return self.shares(s) * self.p[s] < cap_of(s, self.c) * self.nav0 * (1 - 1e-9)


def _failures(book, est, c):
    failures = []
    if est['cash'] < c['min_cash'] - 1e-6:
        failures.append('NEGATIVE_CASH_ESTIMATE')
    if est['worst_cash'] < c['min_cash'] - 1e-6:
        failures.append('WORST_CASE_NEGATIVE_CASH')
    if not est['cash_ratio'] < c['cash_max']:
        failures.append('CASH_GE_25_PERCENT')
    if not est['best_case_cash_ratio'] < c['cash_max'] - c['cash_ratio_margin']:
        failures.append('BEST_CASE_CASH_RATIO_HIGH')
    if not c['min_count'] <= est['count'] <= c['max_count']:
        failures.append('HOLDING_COUNT')
    for s, weight in est['weights'].items():
        if weight > cap_of(s, c) + 1e-10:
            failures.append('WEIGHT_CAP:' + s)
    for s in book.names:
        if book.L[s] != book.cur[s] and not book.strict_cap_ok(s):
            failures.append('ORDER_AT_OR_ABOVE_CAP:' + s)
    return failures


def plan(target_weight, prev_close, prev_nav, holdings, cash, config=None) -> Plan:
    """Turn D-1 target weights into whole-lot orders that satisfy the hard rules.

    target_weight: Series/dict symbol -> weight (absent = 0 = exit if tradable).
    prev_close: official D-1 close per symbol (must cover every held name).
    prev_nav, holdings (symbol -> shares, may be odd), cash: D-1 settled book.
    """
    c = settings(config)
    lot = c['lot_size']
    audit = dict(assumptions=[], repairs=[], dropped_targets=[], odd_lot=[], odd_lot_mode=c['odd_lot_mode'])
    prev_nav, cash = float(prev_nav), float(cash)
    tw = pd.Series(target_weight, dtype=float) if not isinstance(target_weight, pd.Series) else target_weight.astype(float)
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
    for s in wanted:
        if not good_price(s):
            audit['dropped_targets'].append(s + ':MISSING_CLOSE')
    names = sorted(set(held) | {s for s in wanted if good_price(s)})
    cur, extra, tradable = {}, {}, {}
    for s in names:
        q = held.get(s, 0.)
        if is_whole_lot(q, lot):
            cur[s], extra[s], tradable[s] = int(round(q / lot)), 0., True
        else:
            if c['odd_lot_mode'] == 'hold':
                cur[s], extra[s], tradable[s] = 0, q, False
            else:
                base = int(math.floor(q / lot))
                cur[s], extra[s], tradable[s] = base, q - base * lot, True
            audit['odd_lot'].append(dict(symbol=s, shares=q, remainder=float(q % lot),
                                         target_weight=float(tw.get(s, 0.)), action=('HELD_UNCHANGED' if c['odd_lot_mode'] == 'hold' else 'LOTS_AROUND_REMAINDER')))
    if audit['odd_lot']:
        audit['assumptions'].append(ASSUMPTION_ODD_LOT)
    w = {s: float(tw.get(s, 0.)) for s in names}
    book = _Book(names, {s: float(prices[s]) for s in names}, cur, extra, tradable, w, prev_nav, cash, c)

    # 1. Official formula for every tradable name.
    for s in names:
        if tradable[s]:
            book.L[s] = int(math.floor(w[s] * prev_nav / book.p[s] / lot + 1e-12))
    formula_lots = dict(book.L)
    repaired = []
    # No-trade band: a held name whose formula change is below the band keeps
    # its shares (its declared D-Plan weight is then the weight of those shares).
    banded = [s for s in names if tradable[s] and book.cur[s] > 0 and book.L[s] > 0 and book.L[s] != book.cur[s]
              and abs(book.L[s] - book.cur[s]) * lot * book.p[s] < c['rebalance_band'] * prev_nav]
    for s in banded:
        book.L[s] = book.cur[s]
    if banded:
        audit['banded'] = banded

    # 2. Caps: buys sized at the upper price bound; orders strictly below cap.
    for s in names:
        if not tradable[s]:
            if book.shares(s) * book.p[s] >= cap_of(s, c) * prev_nav:
                audit['repairs'].append('ODD_LOT_CAP_UNREPAIRABLE:' + s)
            continue
        allowed = book.buy_cap_lots(s)
        if book.L[s] > book.cur[s] and book.L[s] > allowed:
            book.L[s] = max(book.cur[s], allowed)
            repaired.append('CAP_BUY_LIMIT:' + s)
        if book.shares(s) * book.p[s] >= cap_of(s, c) * prev_nav * (1 - 1e-9):
            book.L[s] = min(book.L[s], allowed)
            repaired.append('CAP_TRIM:' + s)

    # 3. Holding count.
    def count():
        return len(book.held_names())
    if count() > c['max_count']:
        removable = sorted((s for s in names if tradable[s] and book.L[s] > 0),
                           key=lambda s: (w[s], book.cur[s] > 0, book.shares(s) * book.p[s], s))
        for s in removable:
            if count() <= c['max_count']:
                break
            if book.extra[s] > ODD_TOL:
                continue  # lots_around: the remainder keeps the name alive
            book.L[s] = 0
            repaired.append('COUNT_DROP:' + s)
    if count() < c['min_count']:
        for s in sorted((s for s in names if tradable[s] and w[s] > 0 and book.shares(s) <= ODD_TOL),
                        key=lambda s: (-w[s], s)):
            if count() >= c['min_count']:
                break
            if book.buy_cap_lots(s) >= 1:
                book.L[s] = 1
                repaired.append('COUNT_MIN_LOT:' + s)

    # 4. Worst-case funding (buys at the upper bound, sells at the lower bound):
    #    scale buy increments proportionally, then remove single lots from the
    #    largest buy. New entries keep one lot while the count needs them.
    def buy_floor(s):
        if book.cur[s] > 0:
            return book.cur[s]
        return 1 if count() <= c['min_count'] else 0

    if book.worst_cash() < c['min_cash'] - 1e-6:
        buys = [s for s in names if book.L[s] > book.cur[s]]
        upper = c['buy_price_buffer'] * (1 + c['commission'])
        required = sum((book.L[s] - book.cur[s]) * lot * book.p[s] * upper for s in buys)
        available = book.worst_cash() + required - c['min_cash']
        factor = max(0., available) / required if required > 0 else 1.
        for s in buys:
            scaled = book.cur[s] + int(math.floor((book.L[s] - book.cur[s]) * factor))
            book.L[s] = max(scaled, min(book.L[s], 1 if book.cur[s] == 0 else book.cur[s]))
        repaired.append(f'FUNDING_SCALE:{factor:.6f}')
    steps = 0
    while book.worst_cash() < c['min_cash'] - 1e-6 and steps < c['max_repair_steps']:
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
        while steps < c['max_repair_steps']:
            options = [s for s in names if tradable[s] and book.L[s] >= book.cur[s]
                       and book.L[s] < min(formula_lots[s], book.buy_cap_lots(s)) and book.L[s] > 0]
            options = [s for s in options if book.worst_cash() - lot * book.p[s] * c['buy_price_buffer']
                       * (1 + c['commission']) >= c['min_cash']]
            if not options:
                break
            s = max(options, key=lambda x: ((formula_lots[x] - book.L[x]) * book.p[x], x))
            book.L[s] += 1
            steps += 1
        if steps:
            repaired.append(f'FUNDING_REFILL_LOTS:{steps}')

    # 5. Cash ceiling: add lots toward target shortfall while funding and caps allow.
    steps = 0
    while book.best_cash_ratio() >= c['cash_max'] - c['cash_ratio_margin'] and steps < c['max_repair_steps']:
        options = [s for s in book.held_names() if tradable[s] and book.L[s] < book.buy_cap_lots(s)
                   and book.L[s] >= book.cur[s]]
        if not options:
            break
        s = max(options, key=lambda x: (w[x] * prev_nav - book.shares(x) * book.p[x], x))
        book.L[s] += 1
        if book.worst_cash() < c['min_cash'] - 1e-6:
            book.L[s] -= 1
            break
        steps += 1
    if steps:
        repaired.append(f'CASH_CEILING_TOPUP_LOTS:{steps}')

    audit['repairs'].extend(repaired)
    est = book.estimate()
    failures = _failures(book, est, c)
    orders = {s: int((book.L[s] - book.cur[s]) * lot) for s in names if tradable[s] and book.L[s] != book.cur[s]}
    # Ledger-safety: every emitted order must round-trip the sealed D-Plan weight.
    dplan_weights = {}
    for s, q in sorted(orders.items()):
        try:
            dplan_weights[s] = representable_weight(held.get(s, 0.) + q, book.p[s], prev_nav, cap_of(s, c))
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
    hold = _Book(names, book.p, cur, extra, tradable, w, prev_nav, cash, c)
    hold_failures = _failures(hold, hold.estimate(), c)
    hold_failures = [f for f in hold_failures if f != 'BEST_CASE_CASH_RATIO_HIGH']
    audit['hold_failures'] = hold_failures
    if not hold_failures or any(f.startswith('UNREPRESENTABLE_ORDER') for f in failures):
        return Plan({}, {s: hold.shares(s) for s in hold.held_names()}, 'HOLD_FALLBACK',
                    'HOLD_FALLBACK:' + ';'.join(failures), audit)
    return Plan(orders, target_shares, 'INFEASIBLE', 'INFEASIBLE:' + ';'.join(failures), audit)
