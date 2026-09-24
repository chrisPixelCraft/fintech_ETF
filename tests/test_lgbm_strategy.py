import unittest

import numpy as np

from competition.backtest import PortfolioState
from competition.rules import ROOT, load_rules
from lgbm_strategy import model
from lgbm_strategy.strategy import LightGBMStrategy, LightGBMStrategyConfig, predict_scores
from research import run_experiment
from tests.synthetic import synthetic_market

RULES = load_rules()
CONFIG = dict(horizon=10, lookback=250, refit_every=5)


def decide(market, day_index=0, weights=None, strategy=None):
    strategy = strategy or LightGBMStrategy(LightGBMStrategyConfig.from_dict(CONFIG), RULES)
    day = market.calendar[341 + day_index]
    state = PortfolioState(date=day, asof=market.calendar[340 + day_index], day_index=day_index,
                           sessions_remaining=24 - day_index, holdings={}, cash=1e9, nav=1e9,
                           weights=weights or {}, episode_id='x')
    return strategy, strategy.decide(market.asof(state.asof), state)


class LightGBMStrategyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = synthetic_market(n_days=380, n_symbols=32)

    def test_weights_follow_the_portfolio_layer(self):
        strategy, weights = decide(self.market)
        self.assertEqual(len(weights), 25)
        self.assertAlmostEqual(sum(weights.values()), .88)
        scores = predict_scores(strategy.fitted, self.market.asof(self.market.calendar[340]))
        top = sorted(scores.index, key=lambda s: (-scores[s], s))[:25]
        self.assertEqual(sorted(weights), sorted(top))
        entry = strategy.log[0]
        self.assertTrue(entry['refit'])
        self.assertGreaterEqual(entry['best_iteration'], 1)

    def test_deterministic(self):
        (s1, w1), (s2, w2) = decide(self.market), decide(self.market)
        view = self.market.asof(self.market.calendar[340])
        np.testing.assert_array_equal(predict_scores(s1.fitted, view), predict_scores(s2.fitted, view))
        self.assertEqual(w1, w2)
        self.assertEqual(model.PARAMS['n_jobs'], 1)

    def test_refits_on_schedule_and_reuses_between(self):
        strategy, weights = decide(self.market)
        first = strategy.fitted
        decide(self.market, 1, weights, strategy)
        self.assertIs(strategy.fitted, first)
        decide(self.market, 5, weights, strategy)
        self.assertIsNot(strategy.fitted, first)
        self.assertEqual([bool(e.get('refit')) for e in strategy.log], [True, False, True])

    def test_config(self):
        with self.assertRaises(ValueError):
            LightGBMStrategyConfig.from_dict(dict(horizon=10, learning_rate=.1))
        with self.assertRaises(ValueError):
            LightGBMStrategyConfig.from_dict(dict(refit_every=0))
        config = run_experiment.load_config(ROOT / 'research/configs/baseline_lgbm_jpx2.json')
        strategy = run_experiment.build_strategy(config, RULES)
        self.assertIsInstance(strategy, LightGBMStrategy)
        self.assertEqual((strategy.c.horizon, strategy.c.lookback, strategy.c.refit_every), (10, 750, 5))

    def test_pearson_metric(self):
        self.assertEqual(model.pearson_metric(np.array([1., 2, 3]), np.array([2., 4, 6])), ('pearson', 1., True))
        self.assertEqual(model.pearson(np.array([1., 2, 3]), np.ones(3)), 0.)


if __name__ == '__main__':
    unittest.main()
