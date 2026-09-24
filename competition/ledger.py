"""Strategy-free daily settlement ledger.

Semantics ported from legacy/src/v4_ledger.py run_ledger, with rule_check from
legacy/src/backtest.py and warning_reasons/cap_state from
legacy/src/double_check_ledger.py (functions copied, not imported):

- orders are fixed at D-1; only the fill reads day-t prices;
- corporate actions apply before fills (splits scale shares, cash dividends
  accrue to a receivable credited only to the terminal NAV);
- sells fill before buys; a missing price leaves the order unfilled;
- fees and tax use official rates on the fill notional, paid from cash;
- NAV = sum(shares x close) + cash (stale mark if today's close is missing);
- a settlement violation rolls back the day's trades (keeping corporate
  entitlements) and adds one warning; caps drifted by price alone get a
  5-session grace, caps caused by trading warn immediately.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from competition.execution import OFFICIAL
from competition.rules import CompetitionRules


@dataclass(frozen=True)
class Book:
    holdings: dict = field(default_factory=dict)   # symbol -> shares
    cash: float = 0.
    receivable: float = 0.                        # accrued cash dividends (terminal credit)
    marks: dict = field(default_factory=dict)      # symbol -> last valid close
    cap_ages: dict = field(default_factory=dict)
    warnings: int = 0

    def nav(self) -> float:
        return self.cash + sum(q * self.marks[s] for s, q in self.holdings.items())


def rule_check(holdings, cash, prices, rules: CompetitionRules, whitelist) -> dict:
    nav = cash + sum(q * prices[s] for s, q in holdings.items())
    violations = []
    if not rules.min_positions <= len(holdings) <= rules.max_positions:
        violations.append('HOLDING_COUNT')
    if cash < rules.cash_min - 1e-6:
        violations.append('NEGATIVE_CASH')
    if nav <= 0 or cash / nav >= rules.cash_max_exclusive:
        violations.append('CASH_GE_25_PERCENT')
    for s, q in holdings.items():
        if s not in whitelist:
            violations.append('NON_WHITELIST:' + s)
        if nav > 0 and q * prices[s] / nav > rules.cap(s) + 1e-10:
            violations.append('WEIGHT_CAP:' + s)
    return dict(nav=nav, cash_ratio=cash / nav if nav > 0 else np.inf, count=len(holdings), violations=violations)


def warning_reasons(checks: dict, active: list, overdue: list) -> list[str]:
    """Passive weights have a grace period; other breaches do not."""
    return sorted({v for v in checks['violations'] if not v.startswith('WEIGHT_CAP:')}
                  | {'ACTIVE_CAP:' + s for s in active} | {'OVERDUE_CAP:' + s for s in overdue})


def cap_state(holdings, prices, nav, previous_ages, bought, rules: CompetitionRules,
              baseline_holdings=None, baseline_cash=None):
    """Separate price-only drift from transaction-created concentration (no-trade counterfactual)."""
    ages, active, passive = {}, [], []
    baseline_nav = (baseline_cash + sum(q * prices[s] for s, q in baseline_holdings.items())
                    if baseline_holdings is not None else None)
    for s, q in holdings.items():
        if nav <= 0 or q * prices[s] / nav > rules.cap(s) + 1e-10:
            ages[s] = previous_ages.get(s, 0) + 1
            caused = (baseline_nav is not None and baseline_nav > 0
                      and baseline_holdings.get(s, 0.) * prices[s] / baseline_nav <= rules.cap(s) + 1e-10)
            (active if s in bought or caused else passive).append(s)
    return ages, active, passive


def _finite(x) -> bool:
    return x is not None and np.isfinite(x) and x > 0


def settle(book: Book, date, orders: dict, fill_price: pd.Series, fill_source: pd.Series,
           close: pd.Series, split: pd.Series, dividend: pd.Series, sizing_close: dict,
           rules: CompetitionRules, whitelist, quality_flag: pd.Series | None = None,
           price_band: tuple[float, float] = (.9, 1.1)) -> tuple[Book, dict, list, list]:
    """Settle trade date ``date``. Returns (book, day record, accepted trades, issues)."""
    day = str(pd.Timestamp(date).date())
    prev_nav = book.nav()
    holdings, marks, issues = dict(book.holdings), dict(book.marks), []
    receivable, cash = book.receivable, book.cash
    for s, q in list(holdings.items()):
        receivable += q * float(dividend.get(s, 0.))
        ratio = float(split.get(s, 1.))
        holdings[s] = q * ratio
        if not _finite(close.get(s)):
            marks[s] /= ratio
    base_holdings, base_cash = dict(holdings), cash

    trades, fees, taxes, notional_total = [], 0., 0., 0.
    for s, q in sorted(orders.items(), key=lambda kv: (kv[1] > 0, kv[0])):
        price = fill_price.get(s, np.nan)
        if not _finite(price):
            issues.append(dict(date=day, symbol=s, issue='UNFILLED_NO_PRICE'))
            continue
        if holdings.get(s, 0.) + q < -1e-6:
            issues.append(dict(date=day, symbol=s, issue='UNFILLED_INSUFFICIENT_SHARES'))
            continue
        notional = abs(q) * price
        fee = notional * (rules.commission_buy if q > 0 else rules.commission_sell)
        tax = notional * rules.tax_sell if q < 0 else 0.
        cash -= q * price + fee + tax
        fees, taxes, notional_total = fees + fee, taxes + tax, notional_total + notional
        holdings[s] = holdings.get(s, 0.) + q
        if holdings[s] < 1e-6:
            del holdings[s]
        bound = sizing_close[s]
        if not bound * price_band[0] - 1e-6 <= price <= bound * price_band[1] + 1e-6:
            issues.append(dict(date=day, symbol=s, issue='FILL_OUTSIDE_PRICE_ENVELOPE'))
        trades.append(dict(date=day, symbol=s, shares=q, price=price, source=str(fill_source.get(s, '')),
                           notional=notional, fee=fee, tax=tax))

    marks.update({t['symbol']: t['price'] for t in trades if t['symbol'] not in marks})
    marks.update({s: float(v) for s, v in close.items() if _finite(v)})
    stale = sorted(s for s in holdings if not _finite(close.get(s)))
    checks = rule_check(holdings, cash, marks, rules, whitelist)
    bought = {t['symbol'] for t in trades if t['shares'] > 0}
    ages, active, passive = cap_state(holdings, marks, checks['nav'], book.cap_ages, bought, rules,
                                      base_holdings, base_cash)
    overdue = [s for s in passive if ages[s] > rules.passive_grace_days]
    reasons = warning_reasons(checks, active, overdue)
    rejected = []
    if reasons:
        rejected = [dict(t, rejection=';'.join(reasons)) for t in trades]
        trades, holdings, cash = [], base_holdings, base_cash
        fees = taxes = notional_total = 0.
        checks = rule_check(holdings, cash, marks, rules, whitelist)
        ages, _, _ = cap_state(holdings, marks, checks['nav'], book.cap_ages, set(), rules)
    nav = checks['nav']
    sources = pd.Series([t['source'] for t in trades], dtype=object)
    flagged = [s for s in holdings if quality_flag is not None and bool(quality_flag.get(s, False))]
    record = dict(date=day, nav=nav, cash=cash, cash_ratio=checks['cash_ratio'], holdings=len(holdings),
                  receivable=receivable, fees=fees, taxes=taxes, costs=fees + taxes, traded_notional=notional_total,
                  turnover=notional_total / prev_nav if prev_nav > 0 else 0., fills=len(trades),
                  fills_official=int((sources == OFFICIAL).sum()), fills_proxy=int(sources.str.startswith('proxy').sum()),
                  unfilled=sum(i['issue'].startswith('UNFILLED') for i in issues),
                  envelope_breaches=sum(i['issue'] == 'FILL_OUTSIDE_PRICE_ENVELOPE' for i in issues),
                  violations=';'.join(checks['violations']), warning=bool(reasons), warning_reasons=';'.join(reasons),
                  rejected_fills=len(rejected), cumulative_warnings=book.warnings + bool(reasons),
                  passive_caps=';'.join(s for s in passive if not reasons), stale_marks=';'.join(stale),
                  quality_flagged_holdings=';'.join(flagged))
    new = replace(book, holdings=holdings, cash=cash, receivable=receivable, marks=marks, cap_ages=ages,
                  warnings=book.warnings + bool(reasons))
    return new, record, trades, issues + [dict(date=day, symbol=r['symbol'], issue='REJECTED_FILL') for r in rejected]
