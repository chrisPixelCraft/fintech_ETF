"""Walk-forward predictions may use only labels matured at each historic origin."""
import unittest
import numpy as np
import pandas as pd
from src.v4_features import build_features
from src.v4_forecast import WalkForwardForecaster
from tests.test_v4_features import fixture


class ForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daily, cls.dates = fixture()
        cls.features = build_features(cls.daily)

    def test_future_perturbation_preserves_forecast_and_weights(self):
        day = self.dates[310]
        base = WalkForwardForecaster(self.features).predict(day, 20)
        changed = self.daily.copy()
        changed.loc[changed.date.ge(day), ['open', 'high', 'low', 'close', 'volume']] *= 3
        other = WalkForwardForecaster(build_features(changed)).predict(day, 20)
        pd.testing.assert_frame_equal(base, other, check_exact=True)
        self.assertEqual(base.attrs, other.attrs)
        self.assertLess(pd.Timestamp(base.attrs['latest_label_end']), day)

    def test_historical_oos_fit_excludes_own_future_target(self):
        a = WalkForwardForecaster(self.features)
        b = WalkForwardForecaster(self.features)
        origin, horizon = 260, 20
        original = a._forecast(origin, horizon)
        b.price[origin+1:] *= 4
        changed = b._forecast(origin, horizon)
        np.testing.assert_array_equal(original, changed)

    def test_all_required_horizons_intervals_and_weight_bounds(self):
        model = WalkForwardForecaster(self.features)
        for horizon in (1, 5, 10, 20, 4, 24):
            out = model.predict(self.dates[320], horizon)
            self.assertEqual(len(out), 25)
            self.assertTrue(np.isfinite(out.to_numpy()).all())
            self.assertTrue(out.confidence.between(0, 1).all())
            self.assertTrue(out.lower_bound.le(out.upper_bound).all())
            weights = list(out.attrs['expert_weights'].values())
            self.assertAlmostEqual(sum(weights), 1.)
            self.assertGreaterEqual(min(weights), .0625)
            self.assertLessEqual(max(weights), .5625)
            pd.testing.assert_frame_equal(out, model.predict(self.dates[320], horizon), check_exact=True)

    def test_no_observations_before_first_date(self):
        self.assertTrue(WalkForwardForecaster(self.features).predict(self.dates[0]).empty)
        with self.assertRaises(ValueError):
            WalkForwardForecaster(self.features).predict(self.dates[320], 0)
