"""The Stage 1 treatment changes fills, never frozen V3 strategy parameters."""
import unittest
import numpy as np
import pandas as pd
from src import strategy_24d
from src.v4_baseline import run_episode
from tests.test_strategy_24d import fixture


def official_fixture(daily, multiplier=1.):
    table = daily[['date', 'symbol', 'open', 'high', 'low', 'close', 'volume']].copy()
    table['trading_value'] = table.open * multiplier * table.volume
    table['average_execution_price'] = table.trading_value / table.volume
    table['source'] = 'TWSE_OFFICIAL'
    table['quality_flags'] = ''
    table['official_execution_available'] = True
    return table


class EpisodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daily, cls.universe, cls.dates = fixture()
        cls.config = strategy_24d.build_config()
        cls.features = strategy_24d.build_features(cls.daily, cls.config)

    def run_mode(self, mode, table=None, daily=None):
        return run_episode(self.daily if daily is None else daily, self.universe, self.config,
                           self.dates, execution_data=table, execution_mode=mode,
                           features=self.features if daily is None else None)

    def test_open_reproduces_frozen_v3_exactly(self):
        reference = strategy_24d.run_episode(self.daily, self.universe, self.config, self.dates, self.features)
        actual = self.run_mode('open_proxy')
        for key in ['equity', 'trades', 'orders', 'holdings', 'signals', 'warnings', 'snapshots', 'compliance_daily', 'rejected_trades', 'plan_audit']:
            pd.testing.assert_frame_equal(reference[key], actual[key])
        for key, value in reference['metrics'].items():
            self.assertEqual(value, actual['metrics'][key], key)

    def test_equal_official_prices_equal_proxy_ledger(self):
        official = self.run_mode('official_average', official_fixture(self.daily))
        proxy = self.run_mode('open_proxy')
        for key in ['equity', 'trades', 'orders', 'holdings', 'compliance_daily']:
            pd.testing.assert_frame_equal(official[key], proxy[key], rtol=1e-12, atol=1e-6)
        self.assertTrue(official['metrics']['canonical_execution_available'])
        self.assertEqual(official['metrics']['canonical_status'], 'BLOCK_CANONICAL_V4')
        self.assertEqual(proxy['metrics']['canonical_status'], 'BLOCK_CANONICAL_V4')

    def test_missing_official_never_falls_back(self):
        result = self.run_mode('official_average')
        self.assertTrue(result['trades'].empty)
        self.assertGreater(result['metrics']['missing_execution_price_days'], 0)
        self.assertEqual(result['metrics']['compliance_status'], 'BLOCK_MISSING_OFFICIAL_EXECUTION')
        self.assertIsNone(result['metrics']['episode_return'])
        self.assertEqual(result['metrics']['requested_sessions'], 24)

    def test_official_price_changes_fills_not_first_plan(self):
        base = self.run_mode('official_average', official_fixture(self.daily))
        changed = self.run_mode('official_average', official_fixture(self.daily, 1.005))
        date = base['orders'].signal_date.min()
        pd.testing.assert_frame_equal(base['orders'].query('signal_date == @date'), changed['orders'].query('signal_date == @date'))
        self.assertNotEqual(base['metrics']['episode_return'], changed['metrics']['episode_return'])

    def test_partial_failure_is_retained(self):
        daily, universe, dates = fixture(count=10)
        result = run_episode(daily, universe, self.config, dates, official_fixture(daily))
        self.assertEqual(result['metrics']['requested_sessions'], 24)
        self.assertEqual(result['metrics']['observed_sessions'], 3)
        self.assertFalse(result['metrics']['measured_pass'])
        self.assertIsNone(result['metrics']['episode_return'])
        self.assertIsNotNone(result['metrics']['forensic_partial_return'])

    def test_canonical_sizing_uses_official_prior_close(self):
        table = official_fixture(self.daily)
        table['close'] *= 1.002
        result = run_episode(self.daily, self.universe, self.config, self.dates,
                             table, features=self.features, sizing_price_mode='official_close')
        first = result['orders'].signal_date.min()
        orders = result['orders'].loc[result['orders'].signal_date.eq(first)]
        self.assertFalse(orders.empty)
        quotes = table.loc[table.date.eq(pd.Timestamp(first))].set_index('symbol').close
        np.testing.assert_allclose(orders.sizing_price, orders.symbol.map(quotes))
        self.assertTrue(result['metrics']['canonical_sizing_available'])
        self.assertEqual(result['metrics']['canonical_status'], 'AVAILABLE')
        # The official close overlay does not recompute V3 indicators.
        signals = result['signals'].loc[result['signals'].date.eq(first)].set_index('symbol')
        expected = self.features.loc[self.features.date.eq(pd.Timestamp(first))].set_index('symbol')
        np.testing.assert_allclose(signals.signal_price.sort_index(), expected.signal_price.sort_index())

    def test_canonical_missing_prior_close_has_no_yahoo_fallback(self):
        table = official_fixture(self.daily)
        prior = self.features.loc[self.features.date.lt(self.dates[0]), 'date'].max()
        table.loc[table.date.eq(prior), 'close'] = np.nan
        result = run_episode(self.daily, self.universe, self.config, self.dates,
                             table, features=self.features, sizing_price_mode='official_close')
        self.assertTrue(result['orders'].loc[result['orders'].signal_date.eq(str(prior.date()))].empty)
        self.assertEqual(result['metrics']['compliance_status'], 'BLOCK_MISSING_OFFICIAL_CLOSE')
        self.assertFalse(result['metrics']['canonical_sizing_available'])
