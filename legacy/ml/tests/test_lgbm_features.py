import unittest

import numpy as np
import pandas as pd

from lgbm_strategy import features
from tests.synthetic import synthetic_market


class FeatureValueTest(unittest.TestCase):
    def setUp(self):
        self.market = synthetic_market(n_days=120, n_symbols=21)
        self.view = self.market.asof(self.market.calendar[-1])
        self.frames = features.feature_frames(self.view)

    def test_values_match_definitions(self):
        s, t = 'S003.TW', self.view.close.index[-1]
        ret = self.view.ret[s]
        price = (1 + ret.fillna(0.)).cumprod()           # row 0 is NaN in the synthetic panel
        log_ret = np.log1p(ret)
        for h in features.WINDOWS:
            self.assertAlmostEqual(self.frames[f'return_{h}'].at[t, s], price.iloc[-1] / price.iloc[-1 - h] - 1)
            self.assertAlmostEqual(self.frames[f'volatility_{h}'].at[t, s], log_ret.iloc[-h:].std())
            self.assertAlmostEqual(self.frames[f'ma_gap_{h}'].at[t, s], price.iloc[-1] / price.iloc[-h:].mean())
        for name in features.RAW:
            self.assertEqual(self.frames[name].at[t, s], getattr(self.view, name).at[t, s])

    def test_table_has_contract_columns_sorted(self):
        dates = self.view.close.index[-3:]
        table = features.build_features(self.view, dates)
        self.assertEqual(list(table.columns), ['date', 'symbol', *features.COLUMNS, 'feature_ready'])
        self.assertEqual(len(table), 3 * 21)
        self.assertTrue(table.equals(table.sort_values(['date', 'symbol']).reset_index(drop=True)))
        self.assertTrue(table.feature_ready.all())

    def test_rolling_is_per_symbol(self):
        other = self.market.ret.copy()
        other['S010.TW'] *= 3                             # another symbol's history changes
        frames = features.feature_frames(self.market.with_frames(ret=other).asof(self.view.date))
        for name in features.COLUMNS:
            pd.testing.assert_series_equal(frames[name]['S003.TW'], self.frames[name]['S003.TW'])
        self.assertFalse(np.allclose(frames['volatility_20']['S010.TW'].iloc[-1],
                                     self.frames['volatility_20']['S010.TW'].iloc[-1]))

    def test_missing_return_blocks_volatility_without_backfill(self):
        ret, valid = self.market.ret.copy(), self.market.valid.copy()
        gap = self.market.calendar[-10]
        ret.loc[gap, 'S003.TW'] = np.nan
        valid.loc[gap, 'S003.TW'] = False
        view = self.market.with_frames(ret=ret, valid=valid).asof(self.view.date)
        frames = features.feature_frames(view)
        vol = frames['volatility_20']['S003.TW']
        self.assertTrue(vol.iloc[-10:].isna().all())      # the gap sits in every window through the cutoff
        self.assertTrue(np.isfinite(vol.iloc[-11]))       # the day before the gap is untouched
        table = features.build_features(view, pd.DatetimeIndex([gap]))
        self.assertFalse(table.set_index('symbol').at['S003.TW', 'feature_ready'])

    def test_min_observed_share_keeps_rows_across_a_gap(self):
        ret = self.market.ret.copy()
        gap = self.market.calendar[-10]
        ret.loc[gap, 'S003.TW'] = np.nan
        view = self.market.with_frames(ret=ret).asof(self.view.date)
        relaxed = features.feature_frames(view, features.FeatureConfig(min_observed_share=.8))
        vol = relaxed['volatility_20']['S003.TW']
        self.assertTrue(vol.iloc[-10:].notna().all())                  # 19 of 20 observed >= 16 needed
        self.assertAlmostEqual(vol.iloc[-1], np.log1p(ret['S003.TW'].iloc[-20:]).std())   # std skips the gap
        strict = features.feature_frames(view)
        self.assertTrue(strict['volatility_20']['S003.TW'].iloc[-10:].isna().all())
        self.assertEqual(features.FeatureConfig(.8).min_periods(60), 48)
        with self.assertRaises(ValueError):
            features.FeatureConfig(0.)

    def test_short_history_is_not_ready(self):
        view = self.market.asof(self.market.calendar[40])  # 41 rows < 60-session windows
        table = features.build_features(view, view.close.index[-1:])
        self.assertTrue(table.ma_gap_60.isna().all() and table.volatility_60.isna().all())
        self.assertFalse(table.feature_ready.any())
        self.assertTrue(table.return_20.notna().all())

    def test_dates_after_cutoff_are_refused(self):
        view = self.market.asof(self.market.calendar[80])
        with self.assertRaises(ValueError):
            features.build_features(view, self.market.calendar[80:82])


if __name__ == '__main__':
    unittest.main()
