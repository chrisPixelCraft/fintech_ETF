"""Small adversarial checks for the frozen tuning/execution causality boundary."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.backtest_v2 import run_v2


ROOT = Path(__file__).resolve().parents[1]


def v2_panel(symbols=26, sessions=215):
    dates = pd.bdate_range("2024-01-02", periods=sessions)
    frames = []
    for j in range(symbols):
        x = np.arange(sessions, dtype=float)
        close = (50 + j) * 1.001**x * (1 + .002 * np.sin(x / 7 + j))
        volume = 1_000_000 + 1_000 * j + 100 * np.sin(x)
        frames.append(pd.DataFrame(dict(
            date=dates, symbol=f"{2000 + j}.TW", open=close * .999,
            high=close * 1.01, low=close * .99, close=close, volume=volume,
            turnover=close * volume, execution_volume=volume,
            dividend=0., split=1.,
        )))
    daily = pd.concat(frames, ignore_index=True)
    universe = pd.DataFrame(dict(
        symbol=sorted(daily.symbol.unique()), known_at="2023-01-01T00:00:00Z"))
    config = json.loads((ROOT / "config/strategy_v2.json").read_text())
    config.update(start=str(dates[205].date()), end=str(dates[208].date()),
                  target_count=20, use_4h=False, match_4h_coverage=False)
    return daily, universe, config, dates


class FrozenTuningCredibilityAudit(unittest.TestCase):
    def test_execution_day_vwap_poison_changes_fill_price_not_fixed_order_shares(self):
        daily, universe, config, dates = v2_panel()
        execution_day = dates[205]
        poisoned = daily.copy()
        poisoned.loc[poisoned.date.eq(execution_day), "turnover"] *= 1.08

        baseline = run_v2(daily, universe, copy.deepcopy(config))
        altered = run_v2(poisoned, universe, copy.deepcopy(config))
        signal_day = str(dates[204].date())
        trade_day = str(execution_day.date())
        left_orders = baseline["orders"].query("signal_date == @signal_day")
        right_orders = altered["orders"].query("signal_date == @signal_day")
        self.assertGreater(len(left_orders), 0, "Fixture must produce actual prior-close orders")
        pd.testing.assert_frame_equal(left_orders.reset_index(drop=True),
                                      right_orders.reset_index(drop=True), check_exact=True)
        left_fills = baseline["trades"].query("date == @trade_day")
        right_fills = altered["trades"].query("date == @trade_day")
        pd.testing.assert_frame_equal(
            left_fills[["signal_date", "symbol", "shares"]].reset_index(drop=True),
            right_fills[["signal_date", "symbol", "shares"]].reset_index(drop=True),
            check_exact=True)
        self.assertTrue(np.allclose(right_fills.price, left_fills.price * 1.08))

    def test_execution_day_split_changes_old_inventory_not_pending_fixed_shares(self):
        dates = pd.bdate_range("2026-01-02", periods=3)
        daily = pd.DataFrame(dict(
            date=dates, symbol="2330.TW", open=[100., 100., 50.],
            high=[100., 100., 50.], low=[100., 100., 50.], close=[100., 100., 50.],
            volume=1_000_000., turnover=[100e6, 100e6, 50e6],
            execution_volume=1_000_000., dividend=0., split=[1., 1., 2.]))
        universe = pd.DataFrame(dict(symbol=["2330.TW"], known_at=["2025-01-01T00:00:00Z"]))
        config = json.loads((ROOT / "config/strategy_v2.json").read_text())
        config.update(start=str(dates[1].date()), end=str(dates[2].date()),
                      min_count=1, max_count=1, target_count=1, max_weight=1.,
                      tsmc_max_weight=1., use_4h=False, match_4h_coverage=False)
        scripted = {
            str(dates[0].date()): ({"2330.TW": 1000}, "BUY_FIXTURE", ["2330.TW"]),
            str(dates[1].date()): ({"2330.TW": -1000}, "SELL_FIXTURE", ["2330.TW"]),
        }

        def planner(ranked, holdings, cash, nav, cfg, buy_phase=False, desired_symbols=None):
            return copy.deepcopy(scripted.get(str(pd.Timestamp(ranked.date.iloc[0]).date()),
                                                ({}, "HOLD_FIXTURE", list(holdings))))

        with patch("src.backtest_v2.make_plan_v2", side_effect=planner):
            result = run_v2(daily, universe, config)
        self.assertEqual(result["trades"].shares.tolist(), [1000, -1000])
        final = result["holdings"].query("date == @dates[-1].strftime('%Y-%m-%d')")
        self.assertEqual(float(final.iloc[0].shares), 1000.)


if __name__ == "__main__":
    unittest.main()
