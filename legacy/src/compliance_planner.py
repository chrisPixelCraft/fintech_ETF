"""Causal, fixed-share planner with proactive cash/count/cap headroom.

This module is injected into an isolated ``backtest_v2`` module.  It does not
change the frozen engine.  Every order uses the prior-close cross-section only,
is a whole board lot, and remains buy-only or sell-only for a session.

The price envelope is deliberately conservative: executions are stressed at
the predeclared 0.9/1.1 bounds, the whole held book is marked down for the cash
test, and each position is marked up while all others are marked down for its
weight-cap test.  Failure to find a feasible fixed-share order is explicit; the
planner does not claim a guarantee outside that envelope or when no order can
satisfy all constraints.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class _Guard:
    target: float
    ceiling: float
    lower: float
    upper: float


def _rank(rows: pd.DataFrame) -> pd.DataFrame:
    if (
        rows.index.name == "_symbol_index"
        and len(rows.index) == len(rows)
        and all(str(index) == str(symbol) for index, symbol in zip(rows.index, rows.symbol))
    ):
        return rows
    rows = rows.copy()
    rows.index = rows.symbol.astype(str)
    rows.index.name = "_symbol_index"
    return rows.sort_values(["score", "symbol"], ascending=[False, True])


def _cap(symbol: str, config: Mapping) -> float:
    return float(
        config["tsmc_max_weight"]
        if symbol.split(".")[0] == "2330"
        else config["max_weight"]
    )


def _guard(config: Mapping) -> _Guard:
    target = float(config.get("cash_guard_ratio", .16))
    headroom = float(config.get("cash_guard_headroom", .02))
    lower = float(config.get("price_lower_buffer", .9))
    upper = float(config.get("price_buffer", 1.1))
    ceiling = .25 - headroom
    if not 0 <= target < ceiling < .25:
        raise ValueError("cash_guard_ratio must be below .25 - cash_guard_headroom")
    if not 0 < lower <= 1 <= upper:
        raise ValueError("cash_guard price bounds must contain 1.0")
    return _Guard(target=target, ceiling=ceiling, lower=lower, upper=upper)


def _projected_holdings(
    orders: Mapping[str, float], holdings: Mapping[str, float]
) -> tuple[dict[str, float], list[str]]:
    projected = {str(symbol): float(quantity) for symbol, quantity in holdings.items()}
    failures: list[str] = []
    for symbol, quantity in orders.items():
        projected[str(symbol)] = projected.get(str(symbol), 0.) + float(quantity)
    for symbol, quantity in projected.items():
        if quantity < -1e-6:
            failures.append("SHORT_POSITION:" + symbol)
    projected = {
        symbol: quantity for symbol, quantity in projected.items() if quantity > 1e-6
    }
    return projected, failures


def _cash_after(
    orders: Mapping[str, float],
    cash: float,
    prices: Mapping[str, float],
    config: Mapping,
    buy_multiplier: float,
    sell_multiplier: float,
) -> float:
    result = float(cash)
    commission = float(config["commission"])
    sell_tax = float(config["sell_tax"])
    for symbol, quantity in orders.items():
        multiplier = buy_multiplier if quantity > 0 else sell_multiplier
        amount = abs(float(quantity)) * float(prices[symbol]) * multiplier
        result -= float(quantity) * float(prices[symbol]) * multiplier
        result -= amount * (commission + (sell_tax if quantity < 0 else 0.))
    return result


def _state(
    orders: Mapping[str, float],
    holdings: Mapping[str, float],
    cash: float,
    prices: Mapping[str, float],
    config: Mapping,
    buy_multiplier: float,
    sell_multiplier: float,
    mark_multipliers: Mapping[str, float],
) -> tuple[dict[str, float], float, float, dict[str, float]]:
    projected, _ = _projected_holdings(orders, holdings)
    projected_cash = _cash_after(
        orders, cash, prices, config, buy_multiplier, sell_multiplier
    )
    values = {
        symbol: quantity * float(prices[symbol]) * float(mark_multipliers[symbol])
        for symbol, quantity in projected.items()
    }
    nav = projected_cash + sum(values.values())
    weights = {symbol: value / nav for symbol, value in values.items()} if nav > 0 else {}
    return projected, projected_cash, nav, weights


def stress_violations(
    orders: Mapping[str, float],
    holdings: Mapping[str, float],
    cash: float,
    ranked: pd.DataFrame,
    config: Mapping,
    *,
    cash_ceiling: float | None = None,
    allow_mixed: bool = False,
) -> list[str]:
    """Return deterministic violations over the declared execution/mark envelope."""
    guard = _guard(config)
    ceiling = guard.ceiling if cash_ceiling is None else float(cash_ceiling)
    table = _rank(ranked)
    prices = {str(key): float(value) for key, value in table.close.items()}
    lot = int(config["lot_size"])
    failures: set[str] = set()
    sides = {int(np.sign(float(quantity))) for quantity in orders.values() if quantity}
    if len(sides) > 1 and not allow_mixed:
        failures.add("MIXED_BUY_SELL")
    for symbol, quantity in orders.items():
        if symbol not in prices:
            failures.add("MISSING_PRICE:" + str(symbol))
        if abs(float(quantity) / lot - round(float(quantity) / lot)) > 1e-10:
            failures.add("NON_BOARD_LOT:" + str(symbol))
    if failures:
        return sorted(failures)
    projected, structural = _projected_holdings(orders, holdings)
    failures.update(structural)
    for symbol in projected:
        if symbol not in prices:
            failures.add("NON_WHITELIST:" + symbol)
    count = len(projected)
    if count < int(config["min_count"]):
        failures.add("HOLDING_COUNT_MIN")
    if count > int(config["max_count"]):
        failures.add("HOLDING_COUNT_MAX")
    if failures:
        return sorted(failures)

    # Closed-form envelope.  Besides avoiding repeated DataFrame work in the
    # planner search, this makes each corner explicit and independently
    # reproducible by the audit oracle.
    cash_low = _cash_after(
        orders, cash, prices, config, guard.upper, guard.lower
    )
    if cash_low < -1e-6:
        failures.add("NEGATIVE_CASH")

    # Worst cash ratio: buys at the lower bound, sells at the upper bound, and
    # the entire remaining book is marked at the lower bound.
    cash_high = _cash_after(
        orders, cash, prices, config, guard.lower, guard.upper
    )
    base_values = {
        symbol: float(quantity) * prices[symbol]
        for symbol, quantity in projected.items()
    }
    low_book = guard.lower * sum(base_values.values())
    low_nav = cash_high + low_book
    if low_nav <= 0 or cash_high / low_nav >= ceiling - 1e-12:
        failures.add("CASH_GUARD")

    # Worst single-name concentration: the tested name rises while every other
    # holding falls, using the low-cash execution corner to avoid overstating NAV.
    concentration_base = cash_low + low_book
    spread = guard.upper - guard.lower
    for tested, base_value in base_values.items():
        value = guard.upper * base_value
        corner_nav = concentration_base + spread * base_value
        if corner_nav <= 0 or value / corner_nav > _cap(tested, config) + 1e-10:
            failures.add("WEIGHT_CAP:" + tested)
    return sorted(failures)


def _max_cash_ratio(
    orders: Mapping[str, float],
    holdings: Mapping[str, float],
    cash: float,
    prices: Mapping[str, float],
    config: Mapping,
) -> float:
    guard = _guard(config)
    projected, _ = _projected_holdings(orders, holdings)
    projected_cash = _cash_after(
        orders, cash, prices, config, guard.lower, guard.upper
    )
    nav = projected_cash + guard.lower * sum(
        float(quantity) * float(prices[symbol])
        for symbol, quantity in projected.items()
    )
    return projected_cash / nav if nav > 0 else np.inf


def _buy_plan(
    ranked: pd.DataFrame,
    holdings: Mapping[str, float],
    cash: float,
    nav: float,
    config: Mapping,
    selected: list[str],
    forced: set[str],
    *,
    future_sells: Mapping[str, float] | None = None,
    require_guard_target: bool = False,
) -> dict[str, int]:
    """Find a fixed-share buy batch; optional future sells define headroom need."""
    guard = _guard(config)
    prices = ranked.close.to_dict()
    lot = int(config["lot_size"])
    vacancies = [symbol for symbol in selected if symbol not in holdings]
    retained = [
        symbol for symbol in selected
        if symbol in holdings and symbol not in forced
    ]
    names = list(dict.fromkeys(vacancies + retained))
    if not names:
        return {}
    target_count = max(1, int(config["target_count"]))
    equal_value = float(nav) * (1 - guard.target) / target_count
    max_factor = 1.
    for symbol in names:
        if equal_value > 0:
            max_factor = max(
                max_factor,
                .80 * _cap(symbol, config) * float(nav) / equal_value,
            )
    future_sells = dict(future_sells or {})
    feasible: list[tuple[float, dict[str, int]]] = []
    # Dense, deterministic bounded search over a scalar allocation.  Quantities
    # remain whole lots; results are stable under future-row changes.
    for factor in np.linspace(.02, max_factor, 241):
        orders: dict[str, int] = {}
        for symbol in names:
            target_value = min(
                equal_value * float(factor),
                .80 * _cap(symbol, config) * float(nav),
            )
            target = int(np.floor(target_value / float(prices[symbol]) / lot)) * lot
            quantity = max(0, target - int(round(float(holdings.get(symbol, 0.)))))
            quantity = int(np.floor(quantity / lot)) * lot
            if quantity:
                orders[symbol] = quantity
        if not orders:
            continue
        current_failures = stress_violations(
            orders, holdings, cash, ranked, config, cash_ceiling=guard.ceiling
        )
        if current_failures:
            continue
        if future_sells:
            combined = {**orders}
            for symbol, quantity in future_sells.items():
                combined[symbol] = combined.get(symbol, 0) + int(quantity)
                if not combined[symbol]:
                    del combined[symbol]
            future_failures = stress_violations(
                combined, holdings, cash, ranked, config,
                cash_ceiling=guard.ceiling, allow_mixed=True,
            )
            if future_failures:
                continue
            return orders
        ratio = _max_cash_ratio(orders, holdings, cash, prices, config)
        feasible.append((ratio, orders))
        if not require_guard_target or ratio <= guard.target + 1e-12:
            return orders
    if feasible:
        # The proactive target can be mathematically incompatible with the
        # execution band.  A compliant plan below the hard ceiling still makes
        # useful progress; choose the lowest stressed cash ratio found.
        feasible.sort(key=lambda item: (item[0], sorted(item[1].items())))
        return feasible[0][1]
    return {}


def _prefunded_mixed_plan(
    ranked: pd.DataFrame,
    holdings: Mapping[str, float],
    cash: float,
    nav: float,
    config: Mapping,
    selected: list[str],
    forced: set[str],
    sells: Mapping[str, float],
) -> dict[str, int]:
    """Return a disjoint mixed correction whose buys need no sale proceeds.

    This is a last-resort compliance transition.  Buys are affordable at the
    upper execution bound from start-of-day cash alone; sells are existing
    shares; and the combined fixed-share portfolio passes every envelope
    constraint.  Thus sell proceeds never finance same-session buys.
    """
    if not sells or any(float(quantity) >= 0 for quantity in sells.values()):
        return {}
    guard = _guard(config)
    prices = ranked.close.to_dict()
    lot = int(config["lot_size"])
    base_sells = {str(symbol): int(quantity) for symbol, quantity in sells.items()}
    sell_names = set(base_sells)
    vacancies = [
        symbol for symbol in selected
        if symbol not in holdings and symbol not in sell_names
    ]
    retained = [
        symbol for symbol in selected
        if symbol in holdings and symbol not in forced and symbol not in sell_names
    ]
    names = list(dict.fromkeys(vacancies + retained))
    if not names:
        return {}
    equal_value = float(nav) * (1 - guard.target) / max(
        1, int(config["target_count"])
    )
    max_factor = max(
        [1.] + [
            .80 * _cap(symbol, config) * float(nav) / equal_value
            for symbol in names if equal_value > 0
        ]
    )
    # The minimum cap sale computed without simultaneous buys can be slightly
    # too small at the buy-high/sell-low concentration corner.  Search larger
    # existing-share reductions in increasing order rather than declaring a
    # false deadlock.  This remains bounded and deterministic.
    sell_plans: list[dict[str, int]] = []
    seen_sells: set[tuple[tuple[str, int], ...]] = set()
    for fraction in np.linspace(0., 1., 41):
        plan: dict[str, int] = {}
        for symbol, base_quantity in base_sells.items():
            held_lots = int(np.floor(float(holdings[symbol]) / lot))
            base_lots = abs(int(base_quantity)) // lot
            lots = base_lots + int(np.floor((held_lots - base_lots) * fraction))
            if lots:
                plan[symbol] = -lots * lot
        key = tuple(sorted(plan.items()))
        if key and key not in seen_sells:
            seen_sells.add(key)
            sell_plans.append(plan)

    seen: set[tuple[tuple[tuple[str, int], ...], tuple[tuple[str, int], ...]]] = set()
    for sell_plan in sell_plans:
        for factor in np.linspace(.02, max_factor, 241):
            buys: dict[str, int] = {}
            for symbol in names:
                target_value = min(
                    equal_value * float(factor),
                    .80 * _cap(symbol, config) * float(nav),
                )
                target = int(np.floor(target_value / float(prices[symbol]) / lot)) * lot
                quantity = max(0, target - int(round(float(holdings.get(symbol, 0.)))))
                quantity = int(np.floor(quantity / lot)) * lot
                if quantity:
                    buys[symbol] = quantity
            key = (tuple(sorted(sell_plan.items())), tuple(sorted(buys.items())))
            if not buys or key in seen:
                continue
            seen.add(key)
            # Upper-bound purchase cost, including commission, must fit wholly
            # in cash already available before any sell settles.
            if _cash_after(
                buys, cash, prices, config, guard.upper, guard.lower
            ) < -1e-6:
                continue
            combined = dict(sell_plan)
            combined.update(buys)  # symbol sets are disjoint by construction.
            if not stress_violations(
                combined, holdings, cash, ranked, config,
                cash_ceiling=guard.ceiling, allow_mixed=True,
            ):
                return combined
    return {}


def _weight_cap_orders(
    ranked: pd.DataFrame,
    holdings: Mapping[str, float],
    cash: float,
    config: Mapping,
) -> dict[str, int]:
    violations = [
        value.split(":", 1)[1]
        for value in stress_violations(
            {}, holdings, cash, ranked, config, cash_ceiling=1.
        )
        if value.startswith("WEIGHT_CAP:")
    ]
    if not violations:
        return {}
    lot = int(config["lot_size"])
    orders: dict[str, int] = {}
    for symbol in sorted(violations):
        maximum = int(np.floor(float(holdings[symbol]) / lot))
        lo, hi, answer = 1, maximum, None
        while lo <= hi:
            count = (lo + hi) // 2
            trial = {**orders, symbol: -count * lot}
            remaining = {
                item.split(":", 1)[1]
                for item in stress_violations(
                    trial, holdings, cash, ranked, config,
                    cash_ceiling=1., allow_mixed=False,
                )
                if item.startswith("WEIGHT_CAP:")
            }
            if symbol not in remaining:
                answer = count
                hi = count - 1
            else:
                lo = count + 1
        if answer is not None:
            orders[symbol] = -answer * lot
    return orders


def make_plan_v2(
    ranked: pd.DataFrame,
    holdings: Mapping[str, float],
    cash: float,
    nav: float,
    config: Mapping,
    buy_phase: bool = False,
    desired_symbols: list[str] | None = None,
):
    """Return fixed-share causal orders, reason, and the selected symbol list."""
    guard = _guard(config)
    ranked = _rank(ranked)
    holdings = {str(symbol): float(quantity) for symbol, quantity in holdings.items()}
    if any(symbol not in ranked.index for symbol in holdings):
        return {}, "INFEASIBLE_MISSING_HELD_QUOTE", list(holdings)
    prices = ranked.close.to_dict()
    lot = int(config["lot_size"])
    forced = {
        symbol for symbol in holdings if bool(ranked.at[symbol, "exit"])
    }
    retained = [
        symbol for symbol in ranked.index
        if symbol in holdings and (symbol not in forced or holdings[symbol] < lot)
    ]
    entrants = [
        symbol for symbol in ranked.index
        if bool(ranked.at[symbol, "entry_ok"]) and symbol not in holdings
    ]
    selected = list(retained)
    remaining_entrants = list(entrants)
    while len(selected) < int(config["target_count"]) and remaining_entrants:
        selected.append(remaining_entrants.pop(0))
    # Exit flags cannot make the 20-name floor disappear.  When too few
    # eligible replacements exist, defer the highest-ranked forced exits needed
    # to keep a physically valid book.  They may also absorb cash as an explicit
    # compliance correction; the remaining forced names are still sold first.
    protected_for_count: list[str] = []
    if len(selected) < int(config["min_count"]):
        for symbol in ranked.index:
            if symbol in forced and symbol not in selected:
                selected.append(symbol)
                protected_for_count.append(symbol)
                if len(selected) >= int(config["min_count"]):
                    break
    actionable_forced = forced.difference(protected_for_count)
    tradable_forced = {symbol for symbol in forced if holdings[symbol] >= lot}
    if not buy_phase and len(holdings) >= int(config["target_count"]) and not tradable_forced:
        for _ in range(int(config["max_replacements_per_day"])):
            eligible = [
                symbol for symbol in selected
                if symbol in holdings and holdings[symbol] >= lot
            ]
            if not eligible or not remaining_entrants:
                break
            weakest = min(eligible, key=lambda symbol: (ranked.at[symbol, "score"], symbol))
            candidate = remaining_entrants[0]
            if ranked.at[candidate, "score"] <= (
                ranked.at[weakest, "score"] + float(config["replacement_margin"])
            ):
                break
            selected.remove(weakest)
            selected.append(remaining_entrants.pop(0))
    selected = selected[: int(config["max_count"])]
    if not holdings and len(selected) < int(config["min_count"]):
        return {}, "INFEASIBLE_FEWER_THAN_MIN", selected

    cap_orders = _weight_cap_orders(ranked, holdings, cash, config)
    if cap_orders:
        failures = stress_violations(
            cap_orders, holdings, cash, ranked, config,
            cash_ceiling=guard.ceiling,
        )
        if not failures:
            return cap_orders, "SELL_THEN_WAIT_SETTLEMENT", selected
        buys = _buy_plan(
            ranked, holdings, cash, nav, config, selected, actionable_forced,
            future_sells=cap_orders,
        )
        if buys:
            return buys, "BUY_CASH_CAP_CORRECTION", selected
        mixed = _prefunded_mixed_plan(
            ranked, holdings, cash, nav, config, selected,
            actionable_forced, cap_orders,
        )
        if mixed:
            return mixed, "BUY_CASH_CAP_CORRECTION_PREFUNDED_MIXED", selected
        return {}, "INFEASIBLE_CAP_AND_CASH_GUARD:" + ";".join(failures), selected

    proposals = []
    target_value = float(nav) * (1 - float(config.get("cash_target", 0.))) / max(
        1, int(config["target_count"])
    )
    for symbol, shares in holdings.items():
        if symbol not in selected:
            quantity = -int(np.floor(shares / lot)) * lot
            priority = 0
        elif config["allocation_mode"] == "full" and set(selected) != set(holdings):
            target = int(np.floor(
                target_value
                / (float(prices[symbol]) * guard.upper * (1 + float(config["commission"])))
                / lot
            )) * lot
            quantity = min(0, int(np.trunc((target - shares) / lot)) * lot)
            priority = 1
        else:
            quantity = 0
            priority = 1
        if quantity:
            proposals.append((priority, float(ranked.at[symbol, "score"]), symbol, quantity))
    proposals.sort()
    all_sells = {symbol: int(quantity) for _, _, symbol, quantity in proposals}

    current_ratio = _max_cash_ratio({}, holdings, cash, prices, config)
    if buy_phase:
        buys = _buy_plan(
            ranked, holdings, cash, nav, config, selected, actionable_forced,
            require_guard_target=current_ratio > guard.target,
        )
        if buys:
            reason = (
                "BUY_CASH_CAP_CORRECTION"
                if any(symbol in holdings for symbol in buys)
                else "BUY_WITH_SETTLED_CASH"
            )
            return buys, reason, selected
        return {}, "WAIT_FUNDED_VACANCY:INFEASIBLE_CASH_GUARD", selected

    if all_sells:
        failures = stress_violations(
            all_sells, holdings, cash, ranked, config,
            cash_ceiling=guard.ceiling,
        )
        if not failures:
            return all_sells, "SELL_THEN_WAIT_SETTLEMENT", selected
        accepted: dict[str, int] = {}
        for _, _, symbol, quantity in proposals:
            trial = {**accepted, symbol: int(quantity)}
            if not stress_violations(
                trial, holdings, cash, ranked, config,
                cash_ceiling=guard.ceiling,
            ):
                accepted = trial
        if accepted:
            return accepted, "SELL_THEN_WAIT_SETTLEMENT", selected
        buys = _buy_plan(
            ranked, holdings, cash, nav, config, selected, actionable_forced,
            future_sells=all_sells,
        )
        if buys:
            return buys, "BUY_CASH_CAP_CORRECTION", selected
        mixed = _prefunded_mixed_plan(
            ranked, holdings, cash, nav, config, selected,
            actionable_forced, all_sells,
        )
        if mixed:
            return mixed, "BUY_CASH_CAP_CORRECTION_PREFUNDED_MIXED", selected
        # A future full exit can be impossible even though a present buy-only
        # cash correction is safe.  Make that causal progress first, then
        # re-evaluate the exit from the settled next-session state.
        if current_ratio > guard.target + 1e-12:
            buys = _buy_plan(
                ranked, holdings, cash, nav, config, selected,
                actionable_forced, require_guard_target=True,
            )
            if buys:
                return buys, "BUY_CASH_CAP_CORRECTION", selected
        return {}, "INFEASIBLE_SELL_AND_CASH_GUARD:" + ";".join(failures), selected

    if current_ratio > guard.target + 1e-12:
        buys = _buy_plan(
            ranked, holdings, cash, nav, config, selected, actionable_forced,
            require_guard_target=True,
        )
        if buys:
            return buys, "BUY_CASH_CAP_CORRECTION", selected
        return {}, "INFEASIBLE_CASH_GUARD:NO_CAPACITY", selected

    vacancies = [symbol for symbol in selected if symbol not in holdings]
    if vacancies:
        buys = _buy_plan(
            ranked, holdings, cash, nav, config, selected, actionable_forced
        )
        if buys:
            reason = (
                "BUY_CASH_CAP_CORRECTION"
                if any(symbol in holdings for symbol in buys)
                else "BUY_WITH_SETTLED_CASH"
            )
            return buys, reason, selected
        return {}, "INFEASIBLE_CASH_OR_NO_ELIGIBLE_VACANCY", selected
    if any(quantity % lot > 1e-6 for quantity in holdings.values()):
        return {}, "HOLD_WITH_ODD_ENTITLEMENTS", selected
    return {}, "HOLD", selected
