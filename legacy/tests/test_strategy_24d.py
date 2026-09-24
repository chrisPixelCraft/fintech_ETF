"""Indicator parity, causality, and failure containment for the daily adapter."""
import unittest

import numpy as np
import pandas as pd

from src.strategy_24d import build_config, build_features, run_episode
from src.tuning_features import FeatureCache
from src.tuning_a_deep import NumericFeatureCache


def fixture(count=25, sessions=260):
    dates = pd.bdate_range('2009-01-01', periods=sessions)
    rows = []
    for s in range(count):
        returns = .001 + .0002 * np.sin(np.arange(sessions) / 3 + s / 9)
        prices = (60 + s) * np.cumprod(1 + returns)
        for i, date in enumerate(dates):
            price = prices[i]
            rows.append(dict(date=date, symbol=f'{1000+s}.TW', open=price,
                             close=price, high=price*1.01, low=price*.99,
                             volume=1_000_000., dividend=0., split=1.))
    daily = pd.DataFrame(rows)
    universe = pd.DataFrame(dict(symbol=sorted(daily.symbol.unique()), known_at='2026-07-31'))
    return daily, universe, dates[220:244]


class DailyStrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daily, cls.universe, cls.dates = fixture()
        cls.config = build_config()
        cls.features = build_features(cls.daily, cls.config)

    def test_frozen_numeric_indicator_parity(self):
        reference = NumericFeatureCache(FeatureCache(self.daily)).frame(self.config)
        cols = ['signal_price', 'return20', 'return50', 'ema20', 'ema50', 'ema100',
                'ema200', 'macd_hist', 'volume_ratio', 'volatility_ratio',
                'return1', 'ready', 'trend', 'long_trend']
        pd.testing.assert_frame_equal(self.features[cols], reference[cols], atol=1e-11, rtol=1e-10)
        self.assertEqual(self.config['score_weights'], dict(return20=.44, return50=.11,
                         volume=.18, macd=.09, trend=.09, long_trend=.09))
        self.assertFalse(self.config['use_4h'])
        self.assertFalse(self.config['match_4h_coverage'])

    def test_prefix_and_future_mutation_invariance(self):
        cutoff = self.dates[3]
        prefix = build_features(self.daily[self.daily.date <= cutoff], self.config)
        pd.testing.assert_frame_equal(prefix, self.features[self.features.date <= cutoff].reset_index(drop=True))
        changed = self.daily.copy()
        changed.loc[changed.date > cutoff, ['open', 'high', 'low', 'close', 'volume']] *= 2
        mutated = build_features(changed, self.config)
        pd.testing.assert_frame_equal(prefix, mutated[mutated.date <= cutoff].reset_index(drop=True))

    def test_reset_episode_costs_and_lots(self):
        result = run_episode(self.daily, self.universe, self.config, self.dates, self.features)
        self.assertEqual(result['metrics']['observed_sessions'], 24, result['metrics'])
        self.assertTrue(result['metrics']['measured_pass'], result['metrics'])
        trades = result['trades']
        self.assertFalse(trades.empty)
        np.testing.assert_allclose(trades.fee, trades.notional*.001425)
        np.testing.assert_allclose(trades.tax, trades.notional*.003*(trades.shares < 0))
        self.assertTrue((trades.shares % 1000 == 0).all())
        self.assertTrue((pd.to_datetime(trades.signal_date) < pd.to_datetime(trades.date)).all())
        self.assertEqual(result['metrics']['active_share_status'], 'ACTIVE_SHARE_NOT_VERIFIED')
        self.assertAlmostEqual(result['metrics']['episode_return'], result['equity'].economic_nav.iloc[-1]/1e9-1)

    def test_same_day_open_shock_cannot_change_submitted_orders(self):
        base = run_episode(self.daily, self.universe, self.config, self.dates, self.features)
        changed = self.daily.copy()
        changed.loc[changed.date == self.dates[0], 'open'] *= 2
        shocked = run_episode(changed, self.universe, self.config, self.dates)
        signal = str((self.dates[0]-pd.offsets.BDay()).date())
        pd.testing.assert_frame_equal(base['orders'].query('signal_date == @signal').reset_index(drop=True),
                                      shocked['orders'].query('signal_date == @signal').reset_index(drop=True))
        self.assertTrue(shocked['compliance_daily'].iloc[0].rolled_back)
        self.assertTrue(shocked['trades'][shocked['trades'].date == str(self.dates[0].date())].empty)

    def test_missing_open_expires_and_failure_is_retained(self):
        changed = self.daily.copy()
        changed.loc[changed.date == self.dates[0], 'open'] = np.nan
        result = run_episode(changed, self.universe, self.config, self.dates)
        self.assertGreater(result['metrics']['unfilled_orders'], 0)
        self.assertIn('FAIL_MISSING_DATA', result['metrics']['failure_reasons'])
        self.assertFalse(result['metrics']['measured_pass'])
        self.assertTrue(result['trades'][result['trades'].date == str(self.dates[0].date())].empty)

    def test_missing_held_close_is_flagged_and_carried(self):
        changed = self.daily.copy()
        changed.loc[changed.date == self.dates[2], 'close'] = np.nan
        result = run_episode(changed, self.universe, self.config, self.dates)
        self.assertIn('FAIL_MISSING_DATA', result['metrics']['failure_reasons'])
        self.assertGreater(result['metrics']['stale_held_price_days'], 0)
        self.assertTrue(np.isfinite(result['equity'].nav).all())

    def test_insufficient_warmup_is_failed_not_dropped(self):
        dates = pd.DatetimeIndex(sorted(self.daily.date.unique()))[1:25]
        result = run_episode(self.daily, self.universe, self.config, dates, self.features)
        self.assertFalse(result['metrics']['measured_pass'])
        self.assertEqual(result['metrics']['requested_sessions'], 24)
        self.assertFalse(result['metrics']['complete_period'])

    def test_current_volume_does_not_gate_open_fill(self):
        changed = self.daily.copy()
        changed.loc[changed.date == self.dates[0], 'volume'] = np.nan
        result = run_episode(changed, self.universe, self.config, self.dates)
        trades = result['trades'][result['trades'].date == str(self.dates[0].date())]
        self.assertFalse(trades.empty)
        self.assertTrue(trades.volume_participation.isna().all())
        self.assertIsNone(result['metrics']['max_daily_volume_participation'])

    def test_disqualified_partial_return_never_called_24day_return(self):
        daily, universe, dates = fixture(count=10)
        result = run_episode(daily, universe, self.config, dates)
        self.assertTrue(result['metrics']['disqualified'])
        self.assertIsNone(result['metrics']['episode_return'])
        self.assertIsNotNone(result['metrics']['forensic_partial_return'])
        self.assertEqual(result['metrics']['observed_sessions'], 3)
        self.assertEqual(result['metrics']['requested_sessions'], 24)

    def test_missing_prior_session_remains_explicit_failed_episode(self):
        dates = pd.DatetimeIndex(sorted(self.daily.date.unique()))[:24]
        result = run_episode(self.daily, self.universe, self.config, dates, self.features)
        self.assertEqual(result['metrics']['episode_status'], 'FAIL_MISSING_DATA')
        self.assertIsNone(result['metrics']['episode_return'])

    def test_nominal_split_and_dividend_indicator_parity(self):
        changed = self.daily.copy()
        event = self.dates[3]
        changed.loc[(changed.symbol == '1000.TW') & (changed.date >= event), ['open','high','low','close']] /= 2
        changed.loc[(changed.symbol == '1000.TW') & (changed.date == event), ['split','dividend']] = [2, 1]
        actual = build_features(changed, self.config)
        reference = NumericFeatureCache(FeatureCache(changed)).frame(self.config)
        np.testing.assert_allclose(actual.signal_price, reference.signal_price, rtol=1e-12)


class SignalQualityLineageTests(unittest.TestCase):
    def test_severe_discontinuity_resets_full_warmup_without_backdating(self):
        daily, _, _ = fixture(count=1, sessions=470)
        config = build_config()
        daily['quality_flags'] = ''
        daily['valid_for_research'] = True
        event = daily.date.iloc[225]
        before = build_features(daily.loc[daily.date < event], config)
        daily.loc[daily.date >= event, ['open', 'high', 'low', 'close']] *= 15.
        daily.loc[daily.date.eq(event), 'quality_flags'] = 'ACTION_NEUTRAL_RETURN_GT_30PCT'
        daily.loc[daily.date.eq(event), 'valid_for_research'] = False
        full = build_features(daily, config)
        pd.testing.assert_frame_equal(before, full.loc[full.date < event].reset_index(drop=True), check_exact=True)
        after = full.loc[full.date > event].reset_index(drop=True)
        self.assertFalse(after.ready.iloc[:199].any())
        self.assertTrue(after.ready.iloc[199])
        self.assertTrue(pd.isna(after.return1.iloc[0]))
        self.assertLess(abs(after.return20.iloc[10]), .1)
        self.assertEqual(after.signal_history_valid_sessions.iloc[199], 200)

    def test_future_invalid_row_cannot_select_another_past_arithmetic_branch(self):
        daily, _, dates = fixture(count=2)
        for j, day in enumerate(sorted(daily.date.unique())[10:220:13]):
            daily.loc[daily.date.eq(day), 'dividend'] = .13 + j*.17
        daily.loc[daily.date.eq(dates[0]), 'split'] = 1.1
        daily['valid_for_research'] = True
        cutoff = dates[3]
        prefix = build_features(daily.loc[daily.date <= cutoff], build_config())
        daily.loc[daily.date.eq(dates[-1]), 'valid_for_research'] = False
        full = build_features(daily, build_config())
        pd.testing.assert_frame_equal(prefix, full.loc[full.date <= cutoff].reset_index(drop=True), check_exact=True)

    def test_ordinary_missing_quote_preserves_known_split_and_dividend_rights(self):
        daily, _, _ = fixture(count=1, sessions=240)
        daily.loc[210, ['open', 'high', 'low', 'close']] = np.nan
        daily.loc[210, ['split', 'dividend']] = [2., 1.25]
        daily.loc[211:, ['open', 'high', 'low', 'close']] /= 2.
        features = build_features(daily, build_config())
        expected = (2*daily.close.iloc[211]+1.25)/daily.close.iloc[209]-1
        self.assertAlmostEqual(features.return1.iloc[211], expected)
        self.assertTrue(features.ready.iloc[211])
        self.assertFalse(features.signal_history_reset.any())


if __name__ == '__main__':
    unittest.main()
