import unittest

import numpy as np

from autots_strategy import portfolio
from competition.backtest import PortfolioState
from competition.rules import ROOT, load_rules
from lgbm_strategy import model
from lgbm_strategy.strategy import LightGBMStrategy, LightGBMStrategyConfig, predict_scores
from lgbm_strategy.targets import TARGET_MODES
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
        view = self.market.asof(self.market.calendar[340])
        for mode in TARGET_MODES:
            with self.subTest(target_mode=mode):
                make = lambda: LightGBMStrategy(LightGBMStrategyConfig.from_dict(dict(CONFIG, target_mode=mode)), RULES)
                (s1, w1), (s2, w2) = decide(self.market, strategy=make()), decide(self.market, strategy=make())
                np.testing.assert_array_equal(predict_scores(s1.fitted, view), predict_scores(s2.fitted, view))
                self.assertEqual(w1, w2)
                self.assertEqual(s1.log, s2.log)                   # includes the target audit
        self.assertEqual(model.PARAMS['n_jobs'], 1)

    def test_alpha_mode_changes_only_the_label(self):
        raw, _ = decide(self.market)
        alpha, _ = decide(self.market, strategy=LightGBMStrategy(
            LightGBMStrategyConfig.from_dict(dict(CONFIG, target_mode='relative_alpha')), RULES))
        a, b = raw.log[0], alpha.log[0]
        self.assertEqual((a['target_mode'], b['target_mode']), ('raw_return', 'relative_alpha'))
        self.assertEqual((a['n_rows'], a['train_start']), (b['n_rows'], b['train_start']))
        self.assertLess(b['alpha_cross_section_mean_error'], 1e-12)

    def test_turnover_diagnostics(self):
        strategy, weights = decide(self.market)
        decide(self.market, 1, weights, strategy)
        first, second = strategy.log
        self.assertIsNone(first['top25_overlap'])
        self.assertEqual((first['entries'], first['exits']), (25, 0))
        self.assertAlmostEqual(first['one_way_turnover'], .44)   # 0.88 invested from cash
        self.assertTrue(0 <= second['top25_overlap'] <= 1 and 0 <= second['top35_overlap'] <= 1)
        order = portfolio.ranked(predict_scores(strategy.fitted, self.market.asof(self.market.calendar[341])))
        previous = portfolio.ranked(predict_scores(strategy.fitted, self.market.asof(self.market.calendar[340])))
        self.assertEqual(second['top25_overlap'], len(set(order[:25]) & set(previous[:25])) / 25)

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
        with self.assertRaises(ValueError):
            LightGBMStrategyConfig.from_dict(dict(target_mode='excess'))
        alpha = run_experiment.build_strategy(
            run_experiment.load_config(ROOT / 'research/configs/baseline_lgbm_alpha.json'), RULES)
        self.assertEqual(alpha.c.target_mode, 'relative_alpha')
        config = run_experiment.load_config(ROOT / 'research/configs/baseline_lgbm_jpx2.json')
        strategy = run_experiment.build_strategy(config, RULES)
        self.assertIsInstance(strategy, LightGBMStrategy)
        self.assertEqual((strategy.c.horizon, strategy.c.lookback, strategy.c.refit_every), (10, 750, 5))

    def test_pearson_metric(self):
        self.assertEqual(model.pearson_metric(np.array([1., 2, 3]), np.array([2., 4, 6])), ('pearson', 1., True))
        self.assertEqual(model.pearson(np.array([1., 2, 3]), np.ones(3)), 0.)


if __name__ == '__main__':
    unittest.main()
