"""V5 family A (momentum) causality, determinism, and sanity tests on real data."""
import hashlib
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from src.v5_features import build_features, history_at
from src import v5_momentum

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/yahoo_daily/v3_20260923/nominal_daily.parquet'
# Two names list inside the window (6415.TW 2013-12, 6446.TW 2014-03), so some
# cutoffs have symbols that exist only in the corrupted future.
LATE_LISTINGS = ('3661.TW', '6415.TW', '6446.TW')
CUTOFFS = ('2013-10-01', '2014-02-05', '2014-08-01')
_CACHE = {}


def real_daily():
    """30-symbol slice of the real nominal daily data, 2011-07..2014-12."""
    if 'daily' not in _CACHE:
        daily = pd.read_parquet(DATA)
        dates = pd.to_datetime(daily['date'])
        first = dates.groupby(daily['symbol']).min()
        early = sorted(first.index[first <= '2011-01-01'])[:27]
        keep = daily['symbol'].isin(early + list(LATE_LISTINGS))
        keep &= (dates >= '2011-07-01') & (dates <= '2014-12-31')
        _CACHE['daily'] = daily.loc[keep].reset_index(drop=True)
    return _CACHE['daily'].copy()


def real_panel():
    if 'panel' not in _CACHE:
        _CACHE['panel'] = build_features(real_daily())
    return _CACHE['panel'].copy()


def corrupt_daily_future(daily, cutoff, seed):
    """Raw-input corruption: per-symbol price/volume factors, deletions, shuffles."""
    rng = np.random.default_rng(seed)
    out = daily.copy()
    future = pd.to_datetime(out['date']) >= cutoff
    symbols = sorted(out['symbol'].unique())
    factors = dict(zip(symbols, rng.uniform(0.3, 3.0, len(symbols))))
    fac = out.loc[future, 'symbol'].map(factors).to_numpy() * rng.uniform(0.7, 1.3, int(future.sum()))
    for column in ('open', 'high', 'low', 'close', 'adj_close'):
        out.loc[future, column] = out.loc[future, column].to_numpy() * fac
    out.loc[future, 'volume'] = out.loc[future, 'volume'].to_numpy() * rng.uniform(0.1, 5.0, int(future.sum()))
    for day, idx in out.loc[future].groupby('date').groups.items():
        out.loc[idx, 'symbol'] = rng.permutation(out.loc[idx, 'symbol'].to_numpy())
    out = out.loc[~(future & (rng.random(len(out)) < 0.15))]
    return out.sample(frac=1.0, random_state=seed).reset_index(drop=True)


def session_on_or_after(panel, day):
    dates = np.sort(pd.to_datetime(panel['date']).unique())
    return pd.Timestamp(dates[np.searchsorted(dates, np.datetime64(pd.Timestamp(day)))])


def corrupt_future(panel, cutoff, seed):
    """Non-uniform corruption of every row dated >= cutoff.

    Per-symbol random multiplicative factors on all numeric columns, random
    flips of readiness flags, symbol shuffles within dates, row deletions,
    a whole-date deletion, a future-only fake symbol, and row-order shuffle.
    """
    rng = np.random.default_rng(seed)
    out = panel.copy()
    future = pd.to_datetime(out['date']) >= cutoff
    numeric = [c for c in out.columns
               if pd.api.types.is_float_dtype(out[c]) and c not in ('signal_history_segment',)]
    symbols = sorted(out['symbol'].unique())
    factors = dict(zip(symbols, rng.uniform(0.3, 3.0, len(symbols))))
    fac = out.loc[future, 'symbol'].map(factors).to_numpy()
    noise = rng.uniform(0.5, 1.5, (int(future.sum()), len(numeric)))
    out.loc[future, numeric] = out.loc[future, numeric].to_numpy() * fac[:, None] * noise
    for flag in ('feature_ready', 'signal_available'):
        flip = future & (rng.random(len(out)) < 0.2)
        out.loc[flip, flag] = ~out.loc[flip, flag].astype(bool)
    out.loc[future, 'signal_history_segment'] += rng.integers(0, 2, int(future.sum()))
    # Shuffle symbol labels within each future date.
    for day, idx in out.loc[future].groupby('date').groups.items():
        out.loc[idx, 'symbol'] = rng.permutation(out.loc[idx, 'symbol'].to_numpy())
    # Delete random rows and one whole future date.
    future_dates = np.sort(pd.to_datetime(out.loc[future, 'date']).unique())
    drop = future & ((rng.random(len(out)) < 0.15)
                     | (pd.to_datetime(out['date']) == future_dates[min(3, len(future_dates) - 1)]))
    out = out.loc[~drop]
    # A symbol that exists only in the future.
    ghost = out.loc[pd.to_datetime(out['date']) >= cutoff].drop_duplicates('date').copy()
    ghost['symbol'] = '0000.GHOST'
    ghost['feature_ready'] = True
    out = pd.concat([out, ghost], ignore_index=True)
    return out.sample(frac=1.0, random_state=seed).reset_index(drop=True)


def corrupt_from_previous_session(panel, cutoff, seed):
    dates = np.sort(pd.to_datetime(panel['date']).unique())
    previous = pd.Timestamp(dates[np.searchsorted(dates, np.datetime64(cutoff)) - 1])
    return corrupt_future(panel, previous, seed)


def frame_hash(frame):
    return hashlib.sha256(pd.util.hash_pandas_object(frame, index=True).to_numpy().tobytes()).hexdigest()


class CausalityMixin:
    """Shared causality checks; subclasses define module, configs, decision_dates."""

    def restricted(self, frame, cutoff):
        return frame.loc[frame['decision_date'] <= cutoff].reset_index(drop=True)

    def check_causality(self):
        for config in self.configs:
            base = self.module.score_table(self.panel, self.decision_dates, config)
            for i, day in enumerate(CUTOFFS):
                cutoff = session_on_or_after(self.panel, day)
                with self.subTest(config=config, cutoff=cutoff):
                    expected = self.restricted(base, cutoff)
                    self.assertGreater(expected['score'].notna().sum(), 100)
                    # Spec form: truncated panel and truncated decision dates.
                    truncated = self.module.score_table(
                        self.panel.loc[pd.to_datetime(self.panel['date']) < cutoff],
                        [d for d in self.decision_dates if d <= cutoff], config)
                    pd.testing.assert_frame_equal(self.restricted(truncated, cutoff), expected, check_exact=True)
                    # Adversarial future corruption.
                    corrupted = self.module.score_table(
                        corrupt_future(self.panel, cutoff, seed=100 + i), self.decision_dates, config)
                    pd.testing.assert_frame_equal(self.restricted(corrupted, cutoff), expected, check_exact=True)
                    # Positive control: corrupting the D-1 session must change the row for the cutoff.
                    control = self.module.score_table(
                        corrupt_from_previous_session(self.panel, cutoff, seed=200 + i),
                        self.decision_dates, config)
                    got = self.restricted(control, cutoff)
                    with self.assertRaises(AssertionError):
                        pd.testing.assert_frame_equal(got, expected, check_exact=True)
                    last_new = got.loc[got.decision_date.eq(cutoff)]
                    last_old = expected.loc[expected.decision_date.eq(cutoff)]
                    self.assertFalse(last_new.reset_index(drop=True).equals(last_old.reset_index(drop=True)))

    def check_raw_daily_causality(self):
        """End to end: corrupt raw daily rows, rebuild features, then score."""
        config = self.configs[0]
        base = self.module.score_table(self.panel, self.decision_dates, config)
        for i, day in enumerate(CUTOFFS[1:]):
            cutoff = session_on_or_after(self.panel, day)
            with self.subTest(cutoff=cutoff):
                rebuilt = build_features(corrupt_daily_future(real_daily(), cutoff, seed=300 + i))
                got = self.module.score_table(rebuilt, self.decision_dates, config)
                pd.testing.assert_frame_equal(self.restricted(got, cutoff),
                                              self.restricted(base, cutoff), check_exact=True)

    def check_determinism(self):
        for config in self.configs:
            first = self.module.score_table(self.panel, self.decision_dates, config)
            shuffled = self.panel.sample(frac=1.0, random_state=7)
            second = self.module.score_table(shuffled, self.decision_dates, config)
            self.assertEqual(frame_hash(first), frame_hash(second))
            self.assertEqual(first.attrs, second.attrs)


class MomentumTests(CausalityMixin, unittest.TestCase):
    module = v5_momentum
    configs = [{'blend': 'short', 'trend_filter': False},
               {'blend': 'long', 'trend_filter': True}]

    @classmethod
    def setUpClass(cls):
        cls.panel = real_panel()
        dates = pd.DatetimeIndex(np.sort(pd.to_datetime(cls.panel['date']).unique()))
        cls.decision_dates = list(dates[dates >= '2012-06-01'])

    def test_causality_future_corruption(self):
        self.check_causality()

    def test_raw_daily_corruption_through_build_features(self):
        self.check_raw_daily_causality()

    def test_determinism(self):
        self.check_determinism()

    def test_sanity_matches_history_at(self):
        for config in self.configs:
            out = v5_momentum.score_table(self.panel, self.decision_dates, config)
            self.assertEqual(list(out.columns), ['decision_date', 'symbol', 'score'])
            self.assertEqual(out.attrs['model']['family'], 'A_momentum')
            for day in self.decision_dates[::97]:
                prev = history_at(self.panel, day)
                ready = prev.loc[prev.feature_ready].sort_values('symbol').reset_index(drop=True)
                rows = out.loc[out.decision_date.eq(day)].reset_index(drop=True)
                self.assertEqual(rows.symbol.tolist(), ready.symbol.tolist())
                cols = v5_momentum.BLENDS[config['blend']]
                manual = ready[list(cols)].rank(pct=True).mean(axis=1)
                if config['trend_filter']:
                    manual = manual.where(ready.price_ema20 > 0)
                    self.assertTrue(rows.score.isna().equals(ready.price_ema20.le(0)))
                np.testing.assert_allclose(rows.score, manual, rtol=0, atol=1e-12)
                finite = rows.score.dropna()
                self.assertTrue(((finite > 0) & (finite <= 1)).all())

    def test_rejects_unknown_key(self):
        with self.assertRaises(ValueError):
            v5_momentum.score_table(self.panel, self.decision_dates[:2], {'blnd': 'short'})


if __name__ == '__main__':
    unittest.main()
