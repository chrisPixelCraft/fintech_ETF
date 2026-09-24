"""Causality, determinism and sanity tests for the V5 Family B ensemble."""
import functools
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from src.v5_features import build_features
from src import v5_ensemble

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/yahoo_daily/v3_20260923/nominal_daily.parquet'
CUTOFFS = ('2013-03-15', '2013-11-04', '2014-06-20')


@functools.lru_cache(maxsize=1)
def _raw_daily():
    daily = pd.read_parquet(DATA)
    symbols = sorted(daily.symbol.unique())[:30]
    daily = daily.loc[daily.symbol.isin(symbols) & daily.date.between('2010-01-01', '2014-12-31')]
    return daily.reset_index(drop=True)


def raw_daily():
    """30 real symbols, 2010-2014 (feature warm-up in 2010, scores 2011+)."""
    return _raw_daily().copy(deep=True)


@functools.lru_cache(maxsize=1)
def real_panel():
    return build_features(raw_daily())


def decision_dates(panel, start='2012-01-01'):
    dates = pd.DatetimeIndex(sorted(panel.date.unique()))
    return list(dates[dates >= start])


def corrupt_from(panel, cutoff, seed):
    """Non-uniform damage to every row dated >= cutoff: per-symbol random factors on all
    float columns, flipped readiness, symbol shuffles within dates, row deletions."""
    rng = np.random.default_rng(seed)
    out = panel.copy(deep=True)
    future = out.date >= pd.Timestamp(cutoff)
    floats = [c for c in out.columns if out[c].dtype.kind == 'f']
    symbols = np.sort(out.symbol.unique())
    factors = pd.Series(rng.uniform(.3, 3., len(symbols)), index=symbols)
    scale = out.loc[future, 'symbol'].map(factors).to_numpy()
    noise = rng.uniform(.8, 1.25, (future.sum(), len(floats)))
    out.loc[future, floats] = out.loc[future, floats].to_numpy() * scale[:, None] * noise
    if 'feature_ready' in out:
        out.loc[future, 'feature_ready'] = rng.random(future.sum()) < .5
    for _, idx in out.loc[future].groupby('date').groups.items():
        out.loc[idx, 'symbol'] = rng.permutation(out.loc[idx, 'symbol'].to_numpy())
    drop = out.index[future][rng.random(future.sum()) < .15]
    return out.drop(index=drop).sample(frac=1., random_state=seed).reset_index(drop=True)


def prior(frame, cutoff):
    return frame.loc[frame.decision_date <= pd.Timestamp(cutoff)].reset_index(drop=True)


def previous_session(panel, cutoff):
    dates = pd.DatetimeIndex(sorted(panel.date.unique()))
    return dates[dates < pd.Timestamp(cutoff)][-1]


class CausalityMixin:
    module = None
    configs = ()

    @classmethod
    def setUpClass(cls):
        cls.panel = real_panel()
        cls.dates = decision_dates(cls.panel)
        cls.base = {i: cls.module.score_table(cls.panel, cls.dates, cfg) for i, cfg in enumerate(cls.configs)}

    def test_future_corruption_leaves_past_bit_identical(self):
        for i, cfg in enumerate(self.configs):
            for j, cutoff in enumerate(CUTOFFS):
                with self.subTest(config=i, cutoff=cutoff):
                    base = prior(self.base[i], cutoff)
                    self.assertGreater(base.decision_date.nunique(), 50)
                    self.assertEqual(base.decision_date.max(), pd.Timestamp(cutoff))
                    damaged = self.module.score_table(corrupt_from(self.panel, cutoff, 100 + j), self.dates, cfg)
                    assert_frame_equal(prior(damaged, cutoff), base, check_exact=True)
                    trimmed = self.panel.loc[self.panel.date < pd.Timestamp(cutoff)]
                    past = [d for d in self.dates if d <= pd.Timestamp(cutoff)]
                    assert_frame_equal(self.module.score_table(trimmed, past, cfg), base, check_exact=True)

    def test_raw_daily_corruption_through_build_features(self):
        cutoff = pd.Timestamp(CUTOFFS[1])
        daily = raw_daily()
        rng = np.random.default_rng(300)
        future = pd.to_datetime(daily.date) >= cutoff
        factor = daily.loc[future, 'symbol'].map(dict(zip(sorted(daily.symbol.unique()),
                                                          rng.uniform(.3, 3., 30)))).to_numpy()
        for col in ('open', 'high', 'low', 'close', 'adj_close', 'volume'):
            daily.loc[future, col] = daily.loc[future, col] * factor
        daily = daily.drop(index=daily.index[future][rng.random(future.sum()) < .1]).reset_index(drop=True)
        damaged = self.module.score_table(build_features(daily), self.dates, self.configs[0])
        assert_frame_equal(prior(damaged, cutoff), prior(self.base[0], cutoff), check_exact=True)

    def test_positive_control_corrupting_observed_day_changes_output(self):
        cfg = self.configs[0]
        for j, cutoff in enumerate(CUTOFFS):
            with self.subTest(cutoff=cutoff):
                start = previous_session(self.panel, cutoff)
                damaged = self.module.score_table(corrupt_from(self.panel, start, 200 + j), self.dates, cfg)
                left = damaged.loc[damaged.decision_date == pd.Timestamp(cutoff)].reset_index(drop=True)
                right = self.base[0].loc[self.base[0].decision_date == pd.Timestamp(cutoff)].reset_index(drop=True)
                self.assertFalse(left.equals(right))
                # Everything strictly before the corrupted observation still matches.
                assert_frame_equal(prior(damaged, start), prior(self.base[0], start), check_exact=True)

    def test_deterministic(self):
        again = self.module.score_table(self.panel.sample(frac=1., random_state=7), self.dates, self.configs[0])
        assert_frame_equal(again, self.base[0], check_exact=True)
        self.assertEqual(pd.util.hash_pandas_object(again).sum(), pd.util.hash_pandas_object(self.base[0]).sum())

    def test_only_feature_ready_names_at_previous_row(self):
        out = self.base[0]
        ready = self.panel.loc[self.panel.feature_ready.astype(bool), ['date', 'symbol']]
        sessions = pd.DatetimeIndex(sorted(self.panel.date.unique()))
        for d in out.decision_date.unique()[::25]:
            prev = sessions[sessions < d][-1]
            names = set(out.loc[out.decision_date == d, 'symbol'])
            self.assertTrue(names <= set(ready.loc[ready.date == prev, 'symbol']))

    def test_label_maturity(self):
        for i, out in self.base.items():
            audit = pd.DataFrame(out.attrs['audit'])
            self.assertEqual(set(audit.decision_date), set(out.decision_date))
            self.assertTrue((audit.latest_label_end <= audit.anchor_date).all())
            self.assertTrue((audit.anchor_date <= audit.observed_date).all())
            self.assertTrue((audit.observed_date < audit.decision_date).all())


class EnsembleTests(CausalityMixin, unittest.TestCase):
    module = v5_ensemble
    configs = (dict(horizon=10, window=60, score_mode='point_conf'),
               dict(horizon=20, window=250, score_mode='point'))

    def test_sanity(self):
        for out in self.base.values():
            self.assertFalse(out.isna().any().any())
            self.assertTrue((out.lower <= out.expected_return).all())
            self.assertTrue((out.expected_return <= out.upper).all())
            self.assertTrue(out.confidence.between(0, 1).all())
            q25, q75 = out.confidence.quantile([.25, .75])
            self.assertGreater(q75 - q25, .1)
            weights = pd.DataFrame([a['weights'] for a in out.attrs['audit']])
            self.assertEqual(list(weights.columns), list(v5_ensemble.EXPERT_NAMES))
            self.assertTrue(((weights >= 0) & (weights <= .5 + 1e-12)).all().all())
            np.testing.assert_allclose(weights.sum(axis=1), 1., atol=1e-12)
        conf = self.base[0]
        np.testing.assert_array_equal(conf.score, conf.expected_return * conf.confidence)
        np.testing.assert_array_equal(self.base[1].score, self.base[1].expected_return)
        self.assertEqual(self.base[0].attrs['model_identity'], v5_ensemble.MODEL_IDENTITY)

    def test_bounded_weights(self):
        for ic in ([.1, 0, 0, 0, 0, 0], [.3, .2, .01, -1, 0, 0], [0] * 6, [-.1] * 6, [.05, .04, 0, 0, 0, 0]):
            w = v5_ensemble.bounded_weights(np.array(ic, float))
            self.assertTrue(((w >= 0) & (w <= .5 + 1e-12)).all())
            self.assertAlmostEqual(w.sum(), 1., places=12)

    def test_remaining_horizon_ablation(self):
        dates = self.dates[300:306]
        hmap = {d: 24 - i for i, d in enumerate(dates)}
        out = v5_ensemble.score_table(self.panel, dates, dict(horizon='remaining'), horizon_by_date=hmap)
        self.assertEqual(dict(out.groupby('decision_date').horizon.first()), hmap)
        single = v5_ensemble.score_table(self.panel, [dates[2]], dict(horizon=22))
        assert_frame_equal(out.loc[out.decision_date == dates[2]].reset_index(drop=True), single, check_exact=True)
        with self.assertRaises(ValueError):
            v5_ensemble.score_table(self.panel, dates, dict(horizon='remaining'))


if __name__ == '__main__':
    unittest.main()
