"""Independent accounting and causality checks; no downloaded market data required."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.backtest import aggregate_four_hour, causal_prices, compute_features, make_plan, run_backtest


ROOT = Path(__file__).resolve().parents[1]


def frozen_config(**overrides):
    config = json.loads((ROOT / "tests/fixtures/strategy_v1.json").read_text())
    config.update(overrides)
    return config


def trend_market(count=26, sessions=275):
    dates = pd.bdate_range("2025-01-02", periods=sessions)
    t = np.arange(sessions, dtype=float)
    frames = []
    for i in range(count):
        close = (40 + 3 * i) * np.exp(
            .001 * t + .000003 * t**2 + .002 * np.sin(.43 * t + .13 * i)
        )
        frames.append(pd.DataFrame({
            "date": dates, "symbol": f"A{i:03d}.TW", "open": close * .999,
            "high": close * 1.01, "low": close * .99, "close": close,
            "volume": 2_000_000. + 10_000 * np.sin(t / 7 + i),
            "dividend": 0., "split": 1.,
        }))
    daily = pd.concat(frames, ignore_index=True)
    daily["turnover"] = daily.close * daily.volume
    bars = daily[["date", "symbol", "open", "high", "low", "close", "volume"]].copy()
    bars["close"] *= .9995
    universe = pd.DataFrame({
        "symbol": sorted(daily.symbol.unique()),
        "known_at": "2025-01-01T00:00:00+08:00",
    })
    config = frozen_config(start=str(dates[220].date()), end=str(dates[min(265, sessions - 1)].date()))
    return daily, bars, universe, config, dates


def accounting_market(closes, splits=None, dividends=None):
    dates = pd.bdate_range("2026-01-01", periods=len(closes))
    closes = np.asarray(closes, dtype=float)
    daily = pd.DataFrame({
        "date": dates, "symbol": "A000.TW", "open": closes,
        "high": closes, "low": closes, "close": closes,
        "volume": 1_000_000.,
        "split": splits if splits is not None else 1.,
        "dividend": dividends if dividends is not None else 0.,
    })
    universe = pd.DataFrame({"symbol": ["A000.TW"], "known_at": ["2025-12-01"]})
    config = frozen_config(
        start=str(dates[1].date()), end=str(dates[-1].date()),
        initial_cash=1_000_000., use_4h=False, slippage_bps=0.,
    )
    return daily, universe, config, dates


def scripted_planner(orders_by_date):
    """Replace selection only, leaving the production execution ledger intact."""
    def plan(rows, holdings, cash, nav, config):
        date = pd.Timestamp(rows.date.iloc[0]).strftime("%Y-%m-%d")
        return copy.deepcopy(orders_by_date.get(date, {})), "ACCOUNTING_FIXTURE", pd.DataFrame()
    return plan


class IndependentCausalityAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daily, cls.bars, cls.universe, cls.config, cls.dates = trend_market()

    def test_append_future_rows_preserves_features_signals_and_orders(self):
        cutoff = self.dates[244]
        prefix = self.daily[self.daily.date <= cutoff]
        prefix_bars = self.bars[self.bars.date <= cutoff]
        short_config = dict(self.config, end=str(cutoff.date()))
        early = run_backtest(prefix, self.universe, short_config, prefix_bars)
        full = run_backtest(self.daily, self.universe, self.config, self.bars)
        self.assertGreater(len(early["orders"]), 0, "The fixture must actually place orders")
        for name, date_col in [("signals", "date"), ("orders", "signal_date")]:
            expected = full[name][pd.to_datetime(full[name][date_col]) <= cutoff].reset_index(drop=True)
            pd.testing.assert_frame_equal(early[name].reset_index(drop=True), expected)
        a = compute_features(prefix, self.config, prefix_bars)
        b = compute_features(self.daily, self.config, self.bars)
        b = b[b.date <= cutoff].reset_index(drop=True)
        pd.testing.assert_frame_equal(a.reset_index(drop=True), b)

    def test_future_price_and_4h_shocks_preserve_prior_signals_and_orders(self):
        cutoff = self.dates[244]
        changed = self.daily.copy()
        changed_bars = self.bars.copy()
        for frame in (changed, changed_bars):
            mask = frame.date > cutoff
            frame.loc[mask, ["open", "high", "low", "close"]] *= 7.
            frame.loc[mask, "volume"] *= 19.
        changed.loc[changed.date > cutoff, "turnover"] *= 7. * 19.
        a = run_backtest(self.daily, self.universe, self.config, self.bars)
        b = run_backtest(changed, self.universe, self.config, changed_bars)
        for name, date_col in [("signals", "date"), ("orders", "signal_date")]:
            left = a[name][pd.to_datetime(a[name][date_col]) <= cutoff].reset_index(drop=True)
            right = b[name][pd.to_datetime(b[name][date_col]) <= cutoff].reset_index(drop=True)
            pd.testing.assert_frame_equal(left, right)

    def test_late_announced_universe_cannot_enter_initial_signal(self):
        universe = self.universe.copy()
        universe["known_at"] = "2026-09-18T10:02:56+08:00"
        with self.assertRaisesRegex(ValueError, "LOOKAHEAD_UNIVERSE"):
            run_backtest(self.daily, universe, self.config, self.bars)

    def test_execution_day_prices_do_not_resize_previous_orders(self):
        first_day = pd.Timestamp(self.config["start"])
        for model in ("next_open", "vwap"):
            with self.subTest(model=model):
                config = dict(self.config, execution=model)
                changed = self.daily.copy()
                mask = changed.date == first_day
                changed.loc[mask, "open"] *= 1.08
                changed.loc[mask, "high"] = changed.loc[mask, "open"] * 1.01
                changed.loc[mask, "turnover"] *= 1.08
                a = run_backtest(self.daily, self.universe, config, self.bars)
                b = run_backtest(changed, self.universe, config, self.bars)
                left = a["trades"][a["trades"].date == config["start"]]
                right = b["trades"][b["trades"].date == config["start"]]
                self.assertGreater(len(left), 0)
                pd.testing.assert_frame_equal(
                    left[["symbol", "shares", "signal_date"]].reset_index(drop=True),
                    right[["symbol", "shares", "signal_date"]].reset_index(drop=True),
                )
                self.assertFalse(np.allclose(left.price, right.price))

    def test_4h_session_is_four_complete_hours_without_1330_residual(self):
        rows = []
        for day in ["2026-01-02", "2026-01-05"]:
            for hour in [9, 10, 11, 12, 13]:
                if day == "2026-01-05" and hour == 11:
                    continue
                rows.append({
                    "symbol": "A000.TW", "timestamp": f"{day}T{hour:02d}:00:00+08:00",
                    "open": 100 + hour, "high": 102 + hour, "low": 99 + hour,
                    "close": 101 + hour, "volume": 100 * hour,
                })
        bars = aggregate_four_hour(pd.DataFrame(rows))
        self.assertEqual(len(bars), 1, "Incomplete sessions must not become full 4H bars")
        self.assertEqual(float(bars.iloc[0].close), 113.)
        self.assertEqual(float(bars.iloc[0].volume), 4200.)

    def test_4h_missing_split_day_does_not_erase_corporate_action(self):
        daily, bars, _, config, dates = trend_market(count=1, sessions=260)
        action_day = dates[230]
        bars = bars[bars.date != action_day].copy()
        unadjusted = daily.copy()
        split_bars = bars.copy()
        for frame in (unadjusted, split_bars):
            frame.loc[frame.date >= action_day, ["open", "high", "low", "close"]] /= 2.
            frame.loc[frame.date >= action_day, "volume"] *= 2.
        unadjusted.loc[unadjusted.date == action_day, "split"] = 2.
        a = compute_features(daily, config, bars)
        b = compute_features(unadjusted, config, split_bars)
        pd.testing.assert_series_equal(
            a.four_hour_ok, b.four_hour_ok,
            obj="Economically equivalent split paths must have identical 4H decisions",
        )


class IndependentAccountingAudit(unittest.TestCase):
    def test_pending_buy_quantity_is_not_scaled_by_execution_day_split(self):
        daily, universe, config, dates = accounting_market(
            [100, 50, 50], splits=[1, 2, 1]
        )
        orders = {str(dates[0].date()): {"A000.TW": 1000}}
        with patch("src.backtest.make_plan", side_effect=scripted_planner(orders)):
            result = run_backtest(daily, universe, config)
        self.assertEqual(float(result["trades"].iloc[0].shares), 1000.)
        self.assertEqual(float(result["holdings"].iloc[0].shares), 1000.)

    def test_pending_sell_keeps_fixed_quantity_after_existing_holding_splits(self):
        daily, universe, config, dates = accounting_market(
            [100, 100, 50], splits=[1, 1, 2]
        )
        orders = {str(dates[0].date()): {"A000.TW": 1000},
                  str(dates[1].date()): {"A000.TW": -1000}}
        with patch("src.backtest.make_plan", side_effect=scripted_planner(orders)):
            result = run_backtest(daily, universe, config)
        self.assertEqual(float(result["trades"].iloc[-1].shares), -1000.)
        self.assertEqual(float(result["holdings"].iloc[-1].shares), 1000.)

    def test_planner_does_not_trade_odd_bonus_shares(self):
        daily, bars, _, config, dates = trend_market(count=1, sessions=260)
        config.update(min_count=1, max_count=1, target_count=1, use_4h=False, max_weight=1.)
        features = compute_features(daily, config, bars)
        rows = features[features.date == dates[-1]]
        symbol = "A000.TW"
        price = float(rows.iloc[0].close)
        holdings = {symbol: 1100.5}
        nav = 5_000_000.
        cash = nav - holdings[symbol] * price
        orders, reason, _ = make_plan(rows, holdings, cash, nav, config)
        self.assertEqual(reason, "REBALANCE")
        self.assertGreater(len(orders), 0)
        for qty in orders.values():
            self.assertAlmostEqual(qty / 1000, round(qty / 1000))
        remaining = holdings[symbol] + orders.get(symbol, 0)
        self.assertAlmostEqual(remaining % 1000, 100.5)

    def test_pretrade_rejects_31_names_when_six_odd_residuals_cannot_be_sold(self):
        daily, bars, _, config, dates = trend_market(count=32, sessions=260)
        config.update(use_4h=False)
        features = compute_features(daily, config, bars)
        rows = features[features.date == dates[-1]].copy()
        residuals = [f"A{i:03d}.TW" for i in range(6)]
        retained = [f"A{i:03d}.TW" for i in range(6, 30)]
        # Six exited names retain unsellable odd shares; 24 regular names survive.
        mask = rows.symbol.isin(residuals)
        rows.loc[mask, "signal_price"] = rows.loc[mask, "ema50"] * .5
        holdings = {**{s: 100.5 for s in residuals}, **{s: 1000. for s in retained}}
        self.assertEqual(len(holdings), 30)
        prices = rows.set_index("symbol").close.to_dict()
        nav = 1_000_000_000.
        cash = nav - sum(q * prices[s] for s, q in holdings.items())
        orders, reason, _ = make_plan(rows, holdings, cash, nav, config)
        self.assertEqual(orders, {}, "A 25-name target plus six residuals would create 31 names")
        self.assertTrue(reason.startswith("INFEASIBLE_PRETRADE"), reason)

    def test_same_day_cash_and_stock_dividend_preserves_signal_value(self):
        # Official cash is per OLD share; 10 cash and 10% stock at prior price 100.
        daily, _, _, _ = accounting_market(
            [100, 100, (100 - 10) / 1.1],
            splits=[1, 1, 1.1], dividends=[0, 0, 10],
        )
        total_return_index, _ = causal_prices(daily)
        np.testing.assert_allclose(total_return_index, [100., 100., 100.], atol=1e-10)

    def test_same_day_cash_and_stock_dividend_pays_only_old_shares(self):
        daily, universe, config, dates = accounting_market(
            [100, 100, (100 - 10) / 1.1],
            splits=[1, 1, 1.1], dividends=[0, 0, 10],
        )
        orders = {str(dates[0].date()): {"A000.TW": 1000}}
        with patch("src.backtest.make_plan", side_effect=scripted_planner(orders)):
            result = run_backtest(daily, universe, config)
        expected_cost = 100_000 * .001425
        self.assertAlmostEqual(result["metrics"]["final_nav"], 1_000_000 - expected_cost)
        self.assertAlmostEqual(float(result["holdings"].iloc[-1].shares), 1100.)
        # Cash purchases cost 100,000 plus fees; entitlement is 10,000, not 11,000.
        self.assertAlmostEqual(float(result["equity"].iloc[-1].cash), 910_000 - expected_cost)

    def test_constant_prices_round_trip_loses_exact_fees_and_sell_tax(self):
        daily, universe, config, dates = accounting_market([100, 100, 100])
        orders = {str(dates[0].date()): {"A000.TW": 1000},
                  str(dates[1].date()): {"A000.TW": -1000}}
        with patch("src.backtest.make_plan", side_effect=scripted_planner(orders)):
            result = run_backtest(daily, universe, config)
        expected_cost = 100_000 * (.001425 * 2 + .003)
        self.assertAlmostEqual(result["metrics"]["transaction_costs"], expected_cost)
        self.assertAlmostEqual(result["metrics"]["final_nav"], 1_000_000 - expected_cost)
        self.assertAlmostEqual(result["trades"].tax.sum(), 300.)

    def test_split_and_deferred_dividend_conserve_wealth_except_costs(self):
        daily, universe, config, dates = accounting_market(
            [100, 100, 50, 48, 48], splits=[1, 1, 2, 1, 1], dividends=[0, 0, 0, 2, 0]
        )
        orders = {str(dates[0].date()): {"A000.TW": 1000},
                  str(dates[3].date()): {"A000.TW": -2000}}
        with patch("src.backtest.make_plan", side_effect=scripted_planner(orders)):
            result = run_backtest(daily, universe, config)
        expected_cost = 100_000 * .001425 + 96_000 * (.001425 + .003)
        self.assertAlmostEqual(result["metrics"]["final_nav"], 1_000_000 - expected_cost)
        split_holding = result["holdings"][result["holdings"].date == str(dates[2].date())]
        self.assertEqual(float(split_holding.iloc[0].shares), 2000.)
        ex_date = result["equity"][result["equity"].date == str(dates[3].date())].iloc[0]
        self.assertAlmostEqual(ex_date.dividend_receivable, 4000.)
        self.assertAlmostEqual(ex_date.economic_nav, 1_000_000 - 142.5)

    def test_terminal_dividend_is_transferred_once_and_keeps_ledger_identity(self):
        daily, universe, config, dates = accounting_market(
            [100, 100, 90], dividends=[0, 0, 10]
        )
        orders = {str(dates[0].date()): {"A000.TW": 1000}}
        with patch("src.backtest.make_plan", side_effect=scripted_planner(orders)):
            result = run_backtest(daily, universe, config)
        equity = result["equity"]
        np.testing.assert_allclose(equity.economic_nav, equity.nav + equity.dividend_receivable)
        self.assertEqual(float(equity.iloc[-1].dividend_receivable), 0.)

    def test_terminal_distribution_rechecks_cash_limit_and_holding_weights(self):
        daily, universe, config, dates = accounting_market(
            [100, 100, 90], dividends=[0, 0, 10]
        )
        config.update(initial_cash=10_000., commission=0., min_count=1, max_count=1, max_weight=1.)
        orders = {str(dates[0].date()): {"A000.TW": 80}}
        with patch("src.backtest.make_plan", side_effect=scripted_planner(orders)):
            result = run_backtest(daily, universe, config)
        final = result["equity"].iloc[-1]
        self.assertAlmostEqual(final.nav, 10_000.)
        self.assertAlmostEqual(final.cash_ratio, .28)
        self.assertIn("CASH_GE_25_PERCENT", final.violations)
        final_holding = result["holdings"].iloc[-1]
        self.assertAlmostEqual(final_holding.weight, .72)


if __name__ == "__main__":
    unittest.main()
