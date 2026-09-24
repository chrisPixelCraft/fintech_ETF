"""Stage-2 adapters reuse sealed accounting and preserve causal order submission."""
import unittest
import tempfile
from pathlib import Path
import numpy as np
import pandas as pd
from src import v4_baseline, v4_ledger
from src.strategy_24d import build_config
from src.v4_features import build_features
from src.v4_forecast import WalkForwardForecaster
from src.v4_stage2_episode import run_episode, _bind, _identity_score
from tests.test_v4_features import fixture
from tests.test_v4_episode import official_fixture


class Stage2EpisodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daily, cls.dates = fixture()
        cls.features = build_features(cls.daily)
        cls.dates = cls.dates[300:324]
        cls.universe = pd.DataFrame({'symbol': sorted(cls.daily.symbol.unique()), 'known_at': '2000-01-01'})
        cls.official = official_fixture(cls.daily, multiplier=1.003)
        cls.official['close'] *= 1.002
        cls.config = build_config()
        cls.strategy = dict(id='TEST_V4', family='momentum', target_count=22, max_replacements=0, cash_target=.1)
        cls.base = cls.replay()

    @classmethod
    def replay(cls, daily=None, official=None, features=None, strategy=None, forecaster=None):
        return run_episode(cls.daily if daily is None else daily, cls.universe, cls.config, cls.dates,
            cls.official if official is None else official, cls.features if features is None else features,
            cls.strategy if strategy is None else strategy, forecaster)

    def test_exact_code_reuse_and_global_isolation(self):
        original = v4_ledger.run_ledger.__globals__['_score']
        clone = _bind(v4_ledger.run_ledger, _score=_identity_score)
        self.assertIs(clone.__code__, v4_ledger.run_ledger.__code__)
        self.assertIsNot(clone.__globals__, v4_ledger.run_ledger.__globals__)
        self.assertIs(v4_ledger.run_ledger.__globals__['_score'], original)
        self.assertIsNot(original, _identity_score)
        self.assertIs(v4_baseline.run_episode.__globals__['run_ledger'], v4_ledger.run_ledger)

    def test_official_sizing_formula_and_average_fills(self):
        result = self.base
        self.assertTrue(result['metrics']['complete_period'])
        self.assertGreater(len(result['trades']), 0)
        orders = result['orders']
        np.testing.assert_allclose(np.floor(orders.target_weight * orders.signal_nav / orders.sizing_price / 1000) * 1000, orders.target_shares)
        official = self.official.set_index(['date', 'symbol'])
        for trade in result['trades'].itertuples():
            row = official.loc[(pd.Timestamp(trade.date), trade.symbol)]
            self.assertAlmostEqual(trade.price, row.trading_value / row.volume)
        for order in orders.itertuples():
            self.assertAlmostEqual(order.sizing_price, official.loc[(pd.Timestamp(order.signal_date), order.symbol), 'close'])
        self.assertTrue(orders.shares.mod(1000).eq(0).all())

    def test_same_day_and_future_mutation_preserves_prior_orders(self):
        cutoff = self.dates[5]
        daily, official = self.daily.copy(), self.official.copy()
        daily.loc[daily.date.ge(cutoff), ['open', 'high', 'low', 'close', 'volume']] *= 4
        official.loc[official.date.ge(cutoff), ['open', 'high', 'low', 'close', 'trading_value']] *= 4
        changed = self.replay(daily=daily, official=official, features=build_features(daily))
        for name, field in [('orders','signal_date'), ('equity','date'), ('trades','date')]:
            a, b = self.base[name], changed[name]
            pd.testing.assert_frame_equal(a.loc[pd.to_datetime(a[field]).lt(cutoff)].reset_index(drop=True),
                                          b.loc[pd.to_datetime(b[field]).lt(cutoff)].reset_index(drop=True), check_exact=True)

    def test_reproducible_outputs(self):
        repeated = self.replay()
        for key in ['orders', 'trades', 'equity', 'holdings', 'predictions', 'compliance_daily']:
            pd.testing.assert_frame_equal(self.base[key], repeated[key], check_exact=True)

    def test_missing_execution_does_not_fallback_to_open(self):
        official = self.official.copy()
        official.loc[official.date.eq(self.dates[0]), 'trading_value'] = np.nan
        result = self.replay(official=official)
        self.assertTrue(result['trades'].loc[pd.to_datetime(result['trades'].date).eq(self.dates[0])].empty)
        self.assertGreater(result['metrics']['missing_execution_price_days'], 0)
        self.assertEqual(result['metrics']['canonical_status'], 'BLOCK_CANONICAL_V4')

    def test_direct_future_mutation_preserves_submitted_lot_plan(self):
        strategy = dict(self.strategy, family='direct', coefficients=[1/6] * 6, regime=True)
        baseline = self.replay(strategy=strategy)
        cutoff = self.dates[5]
        daily, official = self.daily.copy(), self.official.copy()
        daily.loc[daily.date.ge(cutoff), ['open', 'high', 'low', 'close', 'volume']] *= 3
        official.loc[official.date.ge(cutoff), ['open', 'high', 'low', 'close', 'trading_value']] *= 3
        changed = self.replay(strategy=strategy, daily=daily, official=official, features=build_features(daily))
        self.assertGreater(len(baseline['trades']), 0)
        a, b = baseline['orders'], changed['orders']
        pd.testing.assert_frame_equal(a.loc[pd.to_datetime(a.signal_date).lt(cutoff)].reset_index(drop=True),
                                      b.loc[pd.to_datetime(b.signal_date).lt(cutoff)].reset_index(drop=True), check_exact=True)

    def test_adaptive_integration_has_mature_model_provenance(self):
        strategy = dict(self.strategy, family='adaptive', confidence=True, horizon=10)
        result = self.replay(strategy=strategy, forecaster=WalkForwardForecaster(self.features))
        self.assertTrue(result['metrics']['complete_period'])
        prediction = result['predictions']
        self.assertGreater(len(prediction), 0)
        self.assertTrue((pd.to_datetime(prediction.model_train_end) <= pd.to_datetime(prediction.observed_date)).all())
        self.assertTrue((pd.to_datetime(prediction.observed_date) < pd.to_datetime(prediction.decision_date)).all())


class FeatureCacheTests(unittest.TestCase):
    def test_cache_rebuild_hash_binding_and_corruption_rejection(self):
        from scripts.v4_stage2_run import ensure_feature_cache
        daily, _ = fixture()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            data = output / 'daily.parquet'
            daily.to_parquet(data)
            study = {'daily': str(data)}
            # Unproven old cache is rebuilt rather than silently trusted.
            (output / 'features.pkl').write_bytes(b'unproven stale cache')
            path = ensure_feature_cache(output, study)
            self.assertTrue((output / 'features.manifest.json').exists())
            self.assertEqual(len(pd.read_pickle(path)), len(daily))
            self.assertEqual(ensure_feature_cache(output, study), path)
            with path.open('ab') as handle:
                handle.write(b'corruption')
            with self.assertRaisesRegex(ValueError, 'payload hash mismatch'):
                ensure_feature_cache(output, study)

    def test_standalone_development_rejects_changed_inputs(self):
        from scripts.v4_stage2_run import bind_development_inputs
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            source = output / 'raw.csv'
            source.write_text('original input')
            source.with_suffix('.manifest.json').write_text('{}')
            study = {key: str(source) for key in ('daily', 'calendar', 'universe', 'execution_data', 'episode_registry')}
            original = bind_development_inputs(output, source, study)
            self.assertEqual(bind_development_inputs(output, source, study), original)
            source.write_text('changed input')
            with self.assertRaisesRegex(ValueError, 'development inputs changed'):
                bind_development_inputs(output, source, study)
