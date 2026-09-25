"""Focused cash/count/cap tests for the injectable causal v2 planner."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from src.compliance_planner import make_plan_v2, stress_violations


def settings(**changes):
    config = dict(
        target_count=25,
        min_count=20,
        max_count=30,
        max_weight=.10,
        tsmc_max_weight=.25,
        lot_size=1000,
        commission=.001425,
        sell_tax=.003,
        price_buffer=1.1,
        price_lower_buffer=.9,
        cash_target=0.,
        replacement_margin=.1,
        max_replacements_per_day=0,
        allocation_mode="local",
        cash_guard_ratio=.16,
        cash_guard_headroom=.02,
    )
    config.update(changes)
    return config


def ranked_frame(count=35, price=100.):
    symbols = [f"{1000 + i}.TW" for i in range(count)]
    return pd.DataFrame(dict(
        symbol=symbols,
        close=float(price),
        score=np.linspace(1., 0., count),
        entry_ok=True,
        exit=False,
    ))


def apply_at_prior_close(orders, holdings, cash, ranked, config):
    prices = ranked.set_index("symbol").close.to_dict()
    result = dict(holdings)
    for symbol, quantity in orders.items():
        price = prices[symbol]
        cash -= quantity * price
        cash -= abs(quantity) * price * (
            config["commission"] + (config["sell_tax"] if quantity < 0 else 0.)
        )
        result[symbol] = result.get(symbol, 0.) + quantity
        if result[symbol] <= 1e-6:
            del result[symbol]
    nav = cash + sum(quantity * prices[symbol] for symbol, quantity in result.items())
    return result, cash, nav


class CompliancePlannerTests(unittest.TestCase):
    def test_high_cash_topups_retained_even_when_entry_gate_is_false(self):
        ranked = ranked_frame()
        holdings = {symbol: 30_000. for symbol in ranked.symbol.iloc[:25]}
        ranked.loc[ranked.symbol.isin(holdings), "entry_ok"] = False
        cash, nav = 25_000_000., 100_000_000.
        orders, reason, _ = make_plan_v2(ranked, holdings, cash, nav, settings())
        self.assertTrue(orders)
        self.assertTrue(all(quantity > 0 and quantity % 1000 == 0 for quantity in orders.values()))
        self.assertTrue(set(orders).issubset(holdings))
        self.assertEqual(reason, "BUY_CASH_CAP_CORRECTION")
        self.assertEqual(stress_violations(orders, holdings, cash, ranked, settings()), [])

    def test_predicted_sell_cash_breach_builds_buy_only_headroom_then_sells(self):
        ranked = ranked_frame()
        held = ranked.symbol.iloc[:25].tolist()
        holdings = {symbol: 30_000. for symbol in held}
        # Large enough that the forced sale breaks the cash envelope, while
        # still below the independent single-name concentration corner.
        holdings[held[0]] = 70_000.
        ranked.loc[ranked.symbol.eq(held[0]), "exit"] = True
        ranked.loc[ranked.symbol.eq(held[0]), "entry_ok"] = False
        cash = 15_000_000.
        nav = cash + sum(holdings.values()) * 100.
        config = settings()
        first, reason, _ = make_plan_v2(ranked, holdings, cash, nav, config)
        self.assertTrue(first)
        self.assertTrue(all(quantity > 0 for quantity in first.values()))
        self.assertEqual(reason, "BUY_CASH_CAP_CORRECTION")
        self.assertEqual(stress_violations(first, holdings, cash, ranked, config), [])
        updated, settled_cash, updated_nav = apply_at_prior_close(first, holdings, cash, ranked, config)
        second, second_reason, _ = make_plan_v2(ranked, updated, settled_cash, updated_nav, config)
        self.assertTrue(second)
        self.assertTrue(all(quantity < 0 for quantity in second.values()))
        self.assertEqual(second_reason, "SELL_THEN_WAIT_SETTLEMENT")
        self.assertEqual(stress_violations(second, updated, settled_cash, ranked, config), [])

    def test_forced_exit_at_min_count_buys_vacancy_before_selling(self):
        ranked = ranked_frame()
        held = ranked.symbol.iloc[:20].tolist()
        holdings = {symbol: 40_000. for symbol in held}
        ranked.loc[ranked.symbol.eq(held[0]), "exit"] = True
        ranked.loc[ranked.symbol.eq(held[0]), "entry_ok"] = False
        cash, nav = 10_000_000., 90_000_000.
        config = settings(cash_guard_ratio=.20)
        orders, reason, selected = make_plan_v2(ranked, holdings, cash, nav, config)
        self.assertTrue(orders)
        self.assertTrue(all(quantity > 0 for quantity in orders.values()))
        self.assertTrue(any(symbol not in holdings for symbol in orders))
        self.assertIn(reason, {"BUY_WITH_SETTLED_CASH", "BUY_CASH_CAP_CORRECTION"})
        self.assertGreaterEqual(len(selected), config["min_count"])
        self.assertEqual(stress_violations(orders, holdings, cash, ranked, config), [])

    def test_all_exit_flags_defer_only_names_needed_for_count_and_prefund_exit(self):
        ranked = ranked_frame()
        held = ranked.symbol.iloc[:25].tolist()
        holdings = {symbol: 30_000. for symbol in held}
        ranked.loc[ranked.symbol.isin(held), "exit"] = True
        ranked["entry_ok"] = False
        cash = 21_000_000.
        nav = cash + sum(holdings.values()) * 100.
        config = settings(cash_guard_ratio=.12)

        orders, reason, selected = make_plan_v2(
            ranked, holdings, cash, nav, config
        )

        self.assertEqual(len(selected), config["min_count"])
        self.assertEqual(selected, held[: config["min_count"]])
        self.assertTrue(orders)
        self.assertTrue(all(quantity > 0 for quantity in orders.values()))
        self.assertTrue(set(orders).issubset(selected))
        self.assertEqual(reason, "BUY_CASH_CAP_CORRECTION")
        self.assertEqual(stress_violations(orders, holdings, cash, ranked, config), [])

    def test_prefunded_mixed_cap_repair_uses_no_sale_proceeds_or_roundtrip(self):
        ranked = ranked_frame()
        held = ranked.symbol.iloc[:25].tolist()
        holdings = {symbol: 25_000. for symbol in held}
        holdings[held[0]] = 100_000.
        cash = 20_000_000.
        nav = cash + sum(holdings.values()) * 100.
        config = settings(cash_guard_ratio=.12)

        orders, reason, _ = make_plan_v2(
            ranked, holdings, cash, nav, config
        )

        buys = {symbol: quantity for symbol, quantity in orders.items() if quantity > 0}
        sells = {symbol: quantity for symbol, quantity in orders.items() if quantity < 0}
        self.assertTrue(buys)
        self.assertTrue(sells)
        self.assertEqual(set(buys) & set(sells), set())
        self.assertEqual(reason, "BUY_CASH_CAP_CORRECTION_PREFUNDED_MIXED")
        prices = ranked.set_index("symbol").close.to_dict()
        prefunded_cost = sum(
            quantity * prices[symbol] * config["price_buffer"]
            * (1 + config["commission"])
            for symbol, quantity in buys.items()
        )
        self.assertLessEqual(prefunded_cost, cash + 1e-6)
        self.assertTrue(all(-quantity <= holdings[symbol] for symbol, quantity in sells.items()))
        self.assertEqual(
            stress_violations(
                orders, holdings, cash, ranked, config, allow_mixed=True
            ),
            [],
        )

    def test_no_capacity_returns_explicit_infeasible_without_fake_guarantee(self):
        ranked = ranked_frame()
        holdings = {symbol: 35_000. for symbol in ranked.symbol.iloc[:20]}
        ranked["entry_ok"] = False
        cash, nav = 30_000_000., 100_000_000.
        config = settings(max_weight=.037)
        orders, reason, _ = make_plan_v2(ranked, holdings, cash, nav, config)
        self.assertEqual(orders, {})
        self.assertTrue(reason.startswith("INFEASIBLE_"), reason)
        self.assertIn("CASH_GUARD", reason)

    def test_normal_local_book_does_not_rebalance_retained_shares(self):
        ranked = ranked_frame()
        holdings = {symbol: 36_000. for symbol in ranked.symbol.iloc[:25]}
        cash, nav = 10_000_000., 100_000_000.
        orders, reason, _ = make_plan_v2(ranked, holdings, cash, nav, settings())
        self.assertEqual(orders, {})
        self.assertEqual(reason, "HOLD")

    def test_invalid_guard_configuration_rejected(self):
        ranked = ranked_frame()
        with self.assertRaisesRegex(ValueError, "cash_guard"):
            make_plan_v2(ranked, {}, 1., 1., settings(cash_guard_ratio=.235))


if __name__ == "__main__":
    unittest.main()
