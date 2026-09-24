"""Unified strategy interface and decision-time isolation tests."""
import unittest
import numpy as np
import pandas as pd
from src.v4_features import build_features
from src.v4_forecast import WalkForwardForecaster
from src.v4_strategy import V4Strategy
from tests.test_v4_features import fixture


class StrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daily, cls.dates = fixture()
        cls.panel = build_features(cls.daily)
        cls.forecaster = WalkForwardForecaster(cls.panel)
        cls.state = dict(holdings={}, cash=1e9, nav=1e9)

    def test_three_families_share_output_lots_and_prior_date(self):
        for family in ('momentum', 'adaptive', 'direct'):
            config = dict(family=family, target_count=22, coefficients=[1/6]*6, horizon='remaining')
            out = V4Strategy(config, self.forecaster).generate_target(self.dates[310], self.panel, self.state, dict(remaining_sessions=10))
            self.assertEqual(out.attrs['status'], 'PASS_MEASURED')
            self.assertEqual(len(out), 22)
            self.assertTrue(out.target_shares.mod(1000).eq(0).all())
            self.assertTrue(out.family.eq(family).all())
            self.assertEqual(out.attrs['observed_date'], str(self.dates[309].date()))
            self.assertEqual(out.attrs['horizon'], 10)
            self.assertTrue(out.prior_regime.isin(['risk_on', 'neutral', 'risk_off']).all())
            self.assertTrue(np.isfinite(out[['market_volatility', 'median_R20', 'breadth', 'above_EMA20']].to_numpy()).all())

    def test_future_mutation_does_not_change_target(self):
        day = self.dates[310]
        strategy = V4Strategy(dict(family='momentum', confidence=True, regime=True), self.forecaster)
        base = strategy.generate_target(day, self.panel, self.state, {})
        future = self.panel.copy()
        future.loc[future.date.ge(day), ['R5', 'R20', 'R60', 'close', 'volume']] *= 100
        changed = strategy.generate_target(day, future, self.state, {})
        pd.testing.assert_frame_equal(base, changed, check_exact=True)

    def test_missing_history_and_missing_held_price_fail_closed(self):
        strategy = V4Strategy(dict(family='momentum'))
        self.assertEqual(strategy.generate_target(self.dates[0], self.panel, self.state, {}).attrs['status'], 'NO_VALID_PLAN')
        state = dict(self.state, holdings={'MISSING.TW': 1000})
        output = strategy.generate_target(self.dates[310], self.panel, state, {})
        self.assertTrue(output.empty)
        self.assertEqual(output.attrs['status'], 'NO_VALID_PLAN')

    def test_remaining_horizon_boolean_uses_episode_state(self):
        strategy = V4Strategy(dict(family='adaptive', horizon=5, remaining_horizon=True), self.forecaster)
        out = strategy.generate_target(self.dates[310], self.panel, self.state, dict(remaining_sessions=4))
        self.assertEqual(out.attrs['horizon'], 4)

    def test_no_implicit_unfitted_direct_or_missing_forecaster(self):
        with self.assertRaises(ValueError):
            V4Strategy(dict(family='direct'))
        with self.assertRaises(ValueError):
            V4Strategy(dict(family='adaptive'))
