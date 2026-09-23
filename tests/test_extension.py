"""Independent checks for extending frozen v1 across 2025 and 2026."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.backtest import compute_features, run_backtest, save_result
from src.benchmarks import run_buy_hold
from scripts.audit_extension import audit_source_alignment, audit_strategy


ROOT = Path(__file__).resolve().parents[1]


def config(**overrides):
    result = json.loads((ROOT / "config/strategy_v1.json").read_text())
    result.update(start="2025-01-01", end="2026-09-21")
    result.update(overrides)
    return result


def prices(dates, closes, symbol="A000.TW", dividends=None, splits=None):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "date": pd.to_datetime(dates), "symbol": symbol,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": 1_000_000., "turnover": closes * 1_000_000.,
        "dividend": 0. if dividends is None else dividends,
        "split": 1. if splits is None else splits,
    })


def scripted_planner(orders):
    def plan(rows, holdings, cash, nav, settings):
        date = pd.Timestamp(rows.date.iloc[0]).strftime("%Y-%m-%d")
        return copy.deepcopy(orders.get(date, {})), "EXTENSION_ACCOUNTING_FIXTURE", pd.DataFrame()
    return plan


class ExtensionCausality(unittest.TestCase):
    def test_source_gate_rejects_zero_daily_volume_with_intraday_trading(self):
        daily = prices(["2025-03-10"], [100])
        daily["date"] = "2025-03-10"
        hourly = daily[["date", "symbol", "volume"]].copy()
        daily["volume"] = 0.
        universe = pd.DataFrame({"symbol": ["A000.TW"]})
        with self.assertRaisesRegex(AssertionError, "SOURCE_ZERO_VOLUME_CONTRADICTION"):
            audit_source_alignment(daily, hourly, universe, config())

    def test_initial_universe_must_be_known_before_first_2025_signal(self):
        data = prices(["2024-12-31", "2025-01-02", "2025-01-03"], [100, 101, 102])
        settings = config(end="2025-01-03", use_4h=False)
        known = pd.DataFrame({"symbol": ["A000.TW"], "known_at": ["2024-12-31T19:30:00+08:00"]})
        with patch("src.backtest.make_plan", side_effect=scripted_planner({})):
            result = run_backtest(data, known, settings)
        self.assertEqual(result["equity"].date.iloc[0], "2025-01-02")
        for timestamp in ["2024-12-31T19:30:01+08:00", "2025-12-31T19:30:00+08:00"]:
            with self.subTest(timestamp=timestamp):
                late = known.assign(known_at=timestamp)
                with self.assertRaisesRegex(ValueError, "LOOKAHEAD_UNIVERSE"):
                    run_backtest(data, late, settings)

    def test_daily_warmup_counts_actual_prior_observations(self):
        dates = pd.bdate_range("2024-01-02", "2025-01-03")
        data = prices(dates, 100 * np.exp(np.arange(len(dates)) * .001))
        features = compute_features(data, config(use_4h=False))
        self.assertFalse(bool(features.iloc[198].ready))
        self.assertTrue(bool(features.iloc[199].ready))
        self.assertTrue(np.isfinite(features.iloc[199].ema200))
        initial = features[features.date == pd.Timestamp("2024-12-31")].iloc[0]
        self.assertTrue(bool(initial.ready))
        # A late listing is not made ready merely because calendar history exists.
        short = compute_features(data.iloc[-199:], config(use_4h=False))
        self.assertFalse(bool(short.iloc[-1].ready))

    def test_four_hour_warmup_requires_50_observed_bars(self):
        dates = pd.bdate_range("2024-01-02", "2025-01-03")
        data = prices(dates, 100 * np.exp(np.arange(len(dates)) * .001))
        prior = data[data.date <= "2024-12-31"].tail(49)
        bars = pd.concat([prior, data[data.date >= "2025-01-02"]], ignore_index=True)
        bars = bars[["date", "symbol", "open", "high", "low", "close", "volume"]]
        features = compute_features(data, config(), bars).set_index("date")
        self.assertFalse(bool(features.loc["2024-12-31", "four_hour_ready"]))
        self.assertTrue(bool(features.loc["2025-01-02", "four_hour_ready"]))
        self.assertFalse(bool(features.loc["2024-10-01", "four_hour_available"]))

    def test_future_2025_and_2026_events_do_not_rewrite_prior_features(self):
        dates = pd.bdate_range("2024-01-02", "2026-09-21")
        t = np.arange(len(dates))
        data = prices(dates, 100 * np.exp(.0005 * t + .004 * np.sin(t / 9)))
        bars = data[["date", "symbol", "open", "high", "low", "close", "volume"]].copy()
        original = compute_features(data, config(), bars)
        for cutoff in ["2025-06-30", "2025-12-31", "2026-06-30"]:
            with self.subTest(cutoff=cutoff):
                changed, changed_bars = data.copy(), bars.copy()
                for frame in (changed, changed_bars):
                    frame.loc[frame.date > cutoff, ["open", "high", "low", "close"]] *= .1
                    frame.loc[frame.date > cutoff, "volume"] *= 10
                first_future = changed.index[changed.date > cutoff][0]
                changed.loc[first_future, "split"] = 10.
                changed.loc[first_future, "dividend"] = 25.
                altered = compute_features(changed, config(), changed_bars)
                pd.testing.assert_frame_equal(
                    original[original.date <= cutoff].reset_index(drop=True),
                    altered[altered.date <= cutoff].reset_index(drop=True),
                )


class ExtensionAccounting(unittest.TestCase):
    def test_cash_capital_reduction_conserves_old_share_entitlement(self):
        adjusted_price = (100. - .5) / .95
        daily = prices(["2025-06-09", "2025-06-10", "2025-06-23", "2025-06-24"],
                       [100., 100., adjusted_price, adjusted_price],
                       dividends=[0., 0., .5, 0.], splits=[1., 1., .95, 1.])
        settings = config(start="2025-06-10", end="2025-06-24", initial_cash=1_000_000., slippage_bps=0., use_4h=False)
        universe = pd.DataFrame({"symbol": ["A000.TW"], "known_at": ["2024-12-31"]})
        orders = {"2025-06-09": {"A000.TW": 1000}}
        features = compute_features(daily, settings)
        self.assertTrue(np.allclose(features.signal_price, 100.))
        with patch("src.backtest.make_plan", side_effect=scripted_planner(orders)):
            result = run_backtest(daily, universe, settings)
        equity = result["equity"].set_index("date")
        holdings = result["holdings"].set_index("date")
        fee = 100_000 * settings["commission"]
        self.assertAlmostEqual(float(holdings.loc["2025-06-23", "shares"]), 950.)
        self.assertAlmostEqual(float(equity.loc["2025-06-23", "dividend_receivable"]), 500.)
        self.assertAlmostEqual(float(equity.loc["2025-06-23", "cash"]), 900_000 - fee)
        self.assertTrue(np.allclose(equity.economic_nav, 1_000_000 - fee))
        self.assertAlmostEqual(float(equity.loc["2025-06-24", "cash"]), 900_500 - fee)
        self.assertEqual(len(result["trades"]), 1, "A capital reduction is not an executed stock trade")

    def test_independent_ledger_detects_untraded_share_creation(self):
        data = prices(
            ["2024-12-31", "2025-01-02", "2025-12-31", "2026-01-02", "2026-09-21"],
            [100, 100, 45, 45, 45], dividends=[0, 0, 10, 0, 0], splits=[1, 1, 2, 1, 1],
        )
        universe = pd.DataFrame({"symbol": ["A000.TW"], "known_at": ["2024-12-31T19:00:00+08:00"]})
        settings = config(initial_cash=1_000_000., slippage_bps=0., use_4h=False)
        orders = {"2024-12-31": {"A000.TW": 1000}}
        with patch("src.backtest.make_plan", side_effect=scripted_planner(orders)):
            result = run_backtest(data, universe, settings)
        data["date"] = data.date.dt.strftime("%Y-%m-%d")
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            save_result(result, output)
            self.assertEqual(audit_strategy(output, data, universe, settings, {})["status"], "PASS")
            holdings = pd.read_csv(output / "holdings.csv")
            holdings.loc[holdings.date == "2026-01-02", "shares"] += 1.
            holdings.to_csv(output / "holdings.csv", index=False)
            with self.assertRaisesRegex(AssertionError, "reconstructed shares"):
                audit_strategy(output, data, universe, settings, {})

    def test_year_boundary_preserves_positions_cash_and_dividend_receivable(self):
        data = prices(
            ["2024-12-31", "2025-01-02", "2025-12-31", "2026-01-02", "2026-09-21"],
            [100, 100, 90, 95, 95], dividends=[0, 0, 10, 0, 0],
        )
        universe = pd.DataFrame({"symbol": ["A000.TW"], "known_at": ["2024-12-31T19:00:00+08:00"]})
        settings = config(initial_cash=1_000_000., slippage_bps=0., use_4h=False)
        orders = {"2024-12-31": {"A000.TW": 1000}}
        with patch("src.backtest.make_plan", side_effect=scripted_planner(orders)):
            result = run_backtest(data, universe, settings)
        eq = result["equity"].set_index("date")
        fee = 100_000 * settings["commission"]
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(set(result["holdings"].shares), {1000.})
        for day in ["2025-12-31", "2026-01-02"]:
            self.assertAlmostEqual(float(eq.loc[day, "cash"]), 900_000 - fee)
            self.assertAlmostEqual(float(eq.loc[day, "dividend_receivable"]), 10_000.)
        self.assertAlmostEqual(float(eq.loc["2026-09-21", "cash"]), 910_000 - fee)
        self.assertEqual(float(eq.iloc[-1].dividend_receivable), 0.)
        self.assertAlmostEqual(float(eq.iloc[-1]["nav"]), float(eq.iloc[-1].economic_nav))
        # Segment denominators carry the previous year-end economic wealth.
        wealth_2025 = float(eq.loc["2025-12-31", "economic_nav"])
        wealth_final = float(eq.iloc[-1].economic_nav)
        ret_2025 = wealth_2025 / settings["initial_cash"] - 1
        ret_2026 = wealth_final / wealth_2025 - 1
        self.assertAlmostEqual((1 + ret_2025) * (1 + ret_2026) - 1, result["metrics"]["total_return"])
        self.assertAlmostEqual(ret_2025, -fee / settings["initial_cash"])

    def test_0050_split_preserves_wealth_without_trade_or_fee(self):
        # Issuer confirms four new units per old unit on 2025-06-18.
        data = prices(
            ["2024-12-31", "2025-01-02", "2025-06-10", "2025-06-18", "2026-09-21"],
            [200, 200, 200, 50, 50], symbol="0050.TW", splits=[1, 1, 1, 4, 1],
        )
        settings = config(initial_cash=1_000_000., slippage_bps=0.)
        result = run_buy_hold(data, ["0050.TW"], settings)
        # 85% allocation rounds to 4,000 old units, becoming 16,000 new units.
        initial_cost = 4000 * 200 * settings["commission"]
        self.assertTrue(np.allclose(result["equity"].nav, 1_000_000 - initial_cost))
        self.assertAlmostEqual(float(result["equity"].costs.sum()), initial_cost)
        self.assertTrue((result["equity"].costs.iloc[1:] == 0).all())

    def test_0050_suspension_keeps_common_market_calendar(self):
        suspended = ["2025-06-11", "2025-06-12", "2025-06-13", "2025-06-16", "2025-06-17"]
        etf = prices(["2025-06-09", "2025-06-10", "2025-06-18"], [200, 200, 50],
                     symbol="0050.TW", splits=[1, 1, 4])
        market_days = ["2025-06-09", "2025-06-10", *suspended, "2025-06-18"]
        market = prices(market_days, [100] * len(market_days), symbol="2330.TW")
        settings = config(start="2025-06-10", end="2025-06-18", initial_cash=1_000_000., slippage_bps=0.)
        result = run_buy_hold(pd.concat([etf, market], ignore_index=True), ["0050.TW"], settings)
        eq = result["equity"].set_index("date")
        self.assertEqual(list(eq.index), market_days[1:])
        self.assertTrue((eq.loc[suspended, "stale_count"] == 1).all())
        self.assertTrue((eq.loc[suspended, "costs"] == 0).all())
        self.assertTrue(np.allclose(eq.nav, eq.nav.iloc[0]))


if __name__ == "__main__":
    unittest.main()
