"""Future-modification tests for the LightGBM path: features, training set, predictions and weights for
decision D must not change when only data after D-1 is multiplied, shuffled, deleted or replaced."""
import unittest

import numpy as np
import pandas as pd

from competition import backtest
from competition.backtest import PortfolioState
from competition.episodes import Episode
from competition.rules import load_rules
from lgbm_strategy.dataset import build_training_set
from lgbm_strategy.features import build_features
from lgbm_strategy.strategy import LightGBMStrategy, LightGBMStrategyConfig, predict_scores
from lgbm_strategy.targets import TARGET_MODES
from tests.synthetic import synthetic_market
from tests.test_causality import Recorder, corrupt_after

RULES = load_rules()
CONFIG = dict(horizon=10, lookback=250, refit_every=2,
              portfolio=dict(n_holdings=22, keep_rank=24, rebalance_threshold=.02, freeze_last_days=1))
PRICES = ('open', 'high', 'low', 'close', 'volume', 'ret', 'official_vwap')


def modified(market, cutoff, how: str):
    """Copy of ``market`` whose rows after ``cutoff`` are changed by ``how``."""
    future = market.calendar > cutoff
    rng = np.random.default_rng(3)
    if how == 'replace':
        return corrupt_after(market, cutoff)
    frames = {}
    for name in (*PRICES, 'valid'):
        frame = getattr(market, name).copy()
        block = frame.loc[future]
        if how == 'multiply' and name != 'valid':
            block = block * 1.7
        elif how == 'shuffle':
            block = pd.DataFrame(block.to_numpy()[rng.permutation(len(block))], index=block.index,
                                 columns=block.columns)
        elif how == 'delete':
            block = block & False if name == 'valid' else block * np.nan
        frame.loc[future] = block
        frames[name] = frame
    return market.with_frames(**frames)


def fresh_state(day):
    return PortfolioState(date=day, asof=day, day_index=0, sessions_remaining=24, holdings={}, cash=1e9, nav=1e9,
                          weights={}, episode_id='x')


class LightGBMCausalityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = synthetic_market(n_days=360, n_symbols=26, official_from=330)
        cls.cutoff = cls.market.calendar[340]            # D-1
        cls.day = cls.market.calendar[341]               # D

    def decision(self, market, target_mode='raw_return'):
        view = market.asof(self.cutoff)
        strategy = LightGBMStrategy(LightGBMStrategyConfig.from_dict(dict(CONFIG, target_mode=target_mode)), RULES)
        weights = strategy.decide(view, fresh_state(self.day))
        return dict(features=build_features(view), training=build_training_set(view, 10, 250, target_mode),
                    prediction=predict_scores(strategy.fitted, view), weights=weights)

    def test_future_modifications_change_nothing_at_d(self):
        for mode in TARGET_MODES:
            base = self.decision(self.market, mode)
            self.assertGreaterEqual(len(base['weights']), 20)
            for how in ('multiply', 'shuffle', 'delete', 'replace'):
                with self.subTest(target_mode=mode, how=how):
                    self.assert_same(base, self.decision(modified(self.market, self.cutoff, how), mode))

    def test_future_execution_prices_do_not_move_decision_d(self):
        """Fill prices after D-1 feed only unmatured execution labels (spec §25)."""
        future = self.market.calendar > self.cutoff
        frames = {name: getattr(self.market, name).copy() for name in ('official_vwap', 'high', 'low')}
        for frame in frames.values():
            frame.loc[future] = frame.loc[future] * 1.3
        base = self.decision(self.market, 'execution_alpha')
        self.assert_same(base, self.decision(self.market.with_frames(**frames), 'execution_alpha'))

    def test_alpha_labels_differ_from_raw(self):
        raw, alpha = self.decision(self.market)['training'], self.decision(self.market, 'relative_alpha')['training']
        pd.testing.assert_frame_equal(raw.train.drop(columns='label'), alpha.train.drop(columns='label'))
        self.assertFalse(np.allclose(raw.train.label, alpha.train.label))

    def assert_same(self, base, other):
        """Features, training rows (labels included), audit, predictions and weights are identical."""
        pd.testing.assert_frame_equal(base['features'], other['features'])
        pd.testing.assert_frame_equal(base['training'].train, other['training'].train)
        pd.testing.assert_frame_equal(base['training'].validation, other['training'].validation)
        self.assertEqual(base['training'].audit, other['training'].audit)
        pd.testing.assert_series_equal(base['prediction'], other['prediction'])
        self.assertEqual(base['weights'], other['weights'])

    def test_label_maturity(self):
        audit = self.decision(self.market)['training'].audit
        self.assertLessEqual(pd.Timestamp(audit['latest_label_end']), self.cutoff)

    def test_episode_decisions_identical_until_the_corruption(self):
        sessions = tuple(self.market.calendar[330:337])
        episode = Episode('causality', 'dev', self.market.calendar[329], sessions)
        k = 4

        def run(market, mode):
            config = LightGBMStrategyConfig.from_dict(dict(CONFIG, target_mode=mode))
            strategy = Recorder(LightGBMStrategy(config, RULES))
            return backtest.run_episode(market, episode, strategy, RULES), strategy.calls

        for mode in TARGET_MODES:
            with self.subTest(target_mode=mode):
                base, base_calls = run(self.market, mode)
                bad, bad_calls = run(corrupt_after(self.market, sessions[k - 1]), mode)
                for i in range(k + 1):
                    self.assertLess(base_calls[i][0], base_calls[i][2])
                    self.assertEqual(base_calls[i][3], bad_calls[i][3], f'decision for day {i} changed')
                self.assertFalse(np.allclose(base['ledger'].nav.iloc[k:], bad['ledger'].nav.iloc[k:]))
                self.assertEqual(backtest.verify_episode(self.market, episode, base, RULES), [])


if __name__ == '__main__':
    unittest.main()
