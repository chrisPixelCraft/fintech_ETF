"""Independent-review regressions for canonical price provenance."""
import unittest

from src.strategy_24d import build_config
from src.v4_baseline import run_episode
from tests.test_strategy_24d import fixture
from tests.test_v4_episode import official_fixture


class CanonicalCloseProvenanceTests(unittest.TestCase):
    def test_wrong_market_prior_close_cannot_be_used_for_sizing(self):
        daily, universe, dates = fixture()
        official = official_fixture(daily)
        # These are .TW symbols. A TPEx price must not become an official
        # sizing close even when all execution-day TWSE fills remain valid.
        official.loc[official.date.lt(dates[0]), 'source'] = 'TPEX_OFFICIAL'
        result = run_episode(daily, universe, build_config(), dates,
                             execution_data=official, sizing_price_mode='official_close')
        self.assertFalse(result['metrics']['canonical_sizing_available'])
        self.assertEqual(result['metrics']['canonical_status'], 'BLOCK_CANONICAL_V4')
        self.assertEqual(result['metrics']['compliance_status'], 'BLOCK_MISSING_OFFICIAL_CLOSE')
        prior = daily.loc[daily.date.lt(dates[0]), 'date'].max()
        orders = result['orders']
        self.assertTrue(orders.loc[orders.signal_date.eq(str(prior.date()))].empty)


if __name__ == '__main__':
    unittest.main()
