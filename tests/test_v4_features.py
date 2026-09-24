"""Causal feature and corporate-action invariants for shared V4 observations."""
import unittest
import numpy as np
import pandas as pd
from src.v4_features import build_features, history_at
from src.v4_regime import classify_regime


def fixture():
    dates = pd.bdate_range('2010-01-01', periods=340)
    frames = []
    for i in range(25):
        t = np.arange(len(dates))
        close = 100 * np.exp(.0002 * (i - 10) * t + .025 * np.sin(t / (5 + i / 4)))
        frames.append(pd.DataFrame(dict(date=dates, symbol=f'{1000+i}.TW', open=close, high=close*1.01,
                                       low=close*.99, close=close, volume=1e6+i*1000+t*100,
                                       split=1., dividend=0.)))
    return pd.concat(frames, ignore_index=True), dates


class FeaturesTests(unittest.TestCase):
    def test_features_are_prefix_invariant_and_decision_strict(self):
        daily, dates = fixture()
        full = build_features(daily)
        prefix = build_features(daily.loc[daily.date.le(dates[270])])
        pd.testing.assert_frame_equal(full.loc[full.date.le(dates[270])].reset_index(drop=True), prefix)
        selected = history_at(full, dates[270])
        self.assertTrue(selected.date.eq(dates[269]).all())
        self.assertTrue(selected.feature_ready.all())
        self.assertIn(classify_regime(selected)['regime'], ('risk_on', 'neutral', 'risk_off'))

    def test_split_neutral_return_and_quarantined_reset(self):
        daily, dates = fixture()
        base = build_features(daily)
        changed = daily.copy()
        mask = changed.symbol.eq('1000.TW') & changed.date.ge(dates[230])
        changed.loc[mask, ['open', 'high', 'low', 'close']] /= 2
        changed.loc[mask, 'volume'] *= 2
        changed.loc[mask & changed.date.eq(dates[230]), 'split'] = 2
        output = build_features(changed)
        np.testing.assert_allclose(base.R20, output.R20, equal_nan=True)
        changed['quality_flags'] = ''
        changed.loc[mask & changed.date.eq(dates[240]), 'quality_flags'] = 'ACTION_NEUTRAL_RETURN_GT_30PCT'
        output = build_features(changed)
        row = output.loc[output.symbol.eq('1000.TW') & output.date.eq(dates[240])].iloc[0]
        self.assertFalse(row.feature_ready)
        self.assertFalse(row.signal_available)
