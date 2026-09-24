"""Adversarial temporal isolation tests for the fixed V3 Stage 1 adapter."""
import unittest

import numpy as np
import pandas as pd

from src.strategy_24d import build_config
from src.v4_baseline import run_episode
from test_strategy_24d import fixture


def official_fixture(daily):
    frame = daily[['date', 'symbol', 'open', 'high', 'low', 'close', 'volume']].copy()
    frame['trading_value'] = frame.close * frame.volume
    frame['average_execution_price'] = frame.trading_value / frame.volume
    frame['source'] = 'TWSE_OFFICIAL'
    frame['quality_flags'] = ''
    frame['official_execution_available'] = True
    return frame


class V4CausalityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daily, cls.universe, cls.dates = fixture()
        cls.config = build_config()
        cls.official = official_fixture(cls.daily)
        cls.base = run_episode(cls.daily, cls.universe, cls.config, cls.dates,
                               execution_data=cls.official, execution_mode='official_average')

    def run_changed(self, daily=None, official=None):
        return run_episode(self.daily if daily is None else daily, self.universe,
                           self.config, self.dates,
                           execution_data=self.official if official is None else official,
                           execution_mode='official_average')

    def assert_prior_orders_equal(self, changed, cutoff):
        for table, key in [('orders', 'signal_date'), ('snapshots', 'date')]:
            left, right = self.base[table], changed[table]
            left = left[pd.to_datetime(left[key]) < cutoff].reset_index(drop=True)
            right = right[pd.to_datetime(right[key]) < cutoff].reset_index(drop=True)
            pd.testing.assert_frame_equal(left, right, check_exact=True)
        self.assertFalse(self.base['orders'].empty)

    def test_same_day_official_price_and_volume_do_not_resize_order(self):
        official = self.official.copy()
        mask = official.date.eq(self.dates[0])
        official.loc[mask, 'volume'] *= 3
        official.loc[mask, 'trading_value'] *= 6
        official.loc[mask, 'average_execution_price'] *= 2
        changed = self.run_changed(official=official)
        self.assert_prior_orders_equal(changed, self.dates[0])
        self.assertTrue(changed['compliance_daily'].iloc[0].rolled_back)

    def test_same_day_ohlcv_and_actions_do_not_change_submitted_plan(self):
        daily = self.daily.copy()
        mask = daily.date.ge(self.dates[0])
        daily.loc[mask, ['open', 'high', 'low', 'close', 'volume']] *= 7
        daily.loc[mask, ['dividend', 'split']] = [2., 1.5]
        changed = self.run_changed(daily=daily)
        self.assert_prior_orders_equal(changed, self.dates[0])

    def test_future_signal_and_execution_perturbation_preserves_past(self):
        cutoff = self.dates[8]
        daily = self.daily.copy()
        daily.loc[daily.date.ge(cutoff), ['open', 'high', 'low', 'close', 'volume']] *= 4
        official = self.official.copy()
        official.loc[official.date.ge(cutoff), ['open', 'high', 'low', 'close',
                     'trading_value', 'average_execution_price']] *= 4
        changed = self.run_changed(daily=daily, official=official)
        self.assert_prior_orders_equal(changed, cutoff)
        for table in ['equity', 'trades', 'holdings', 'compliance_daily']:
            left, right = self.base[table], changed[table]
            pd.testing.assert_frame_equal(
                left[pd.to_datetime(left.date) < cutoff].reset_index(drop=True),
                right[pd.to_datetime(right.date) < cutoff].reset_index(drop=True),
                check_exact=True)

    def test_official_close_future_and_same_day_cannot_change_earlier_plans(self):
        baseline = run_episode(self.daily, self.universe, self.config, self.dates,
                               execution_data=self.official, sizing_price_mode='official_close')
        cutoff = self.dates[5]
        official = self.official.copy()
        official.loc[official.date.ge(cutoff), ['open', 'high', 'low', 'close',
                     'trading_value', 'average_execution_price']] *= 3
        changed = run_episode(self.daily, self.universe, self.config, self.dates,
                              execution_data=official, sizing_price_mode='official_close')
        for table, key in [('orders', 'signal_date'), ('snapshots', 'date'),
                           ('equity', 'date'), ('trades', 'date')]:
            left, right = baseline[table], changed[table]
            pd.testing.assert_frame_equal(
                left[pd.to_datetime(left[key]) < cutoff].reset_index(drop=True),
                right[pd.to_datetime(right[key]) < cutoff].reset_index(drop=True),
                check_exact=True)
        self.assertFalse(baseline['orders'].empty)

    def test_missing_same_day_official_data_does_not_select_different_plan(self):
        official = self.official.loc[self.official.date.ne(self.dates[0])].copy()
        changed = self.run_changed(official=official)
        self.assert_prior_orders_equal(changed, self.dates[0])
        self.assertTrue(changed['trades'].loc[
            pd.to_datetime(changed['trades'].date).eq(self.dates[0])].empty)


if __name__ == '__main__':
    unittest.main()
