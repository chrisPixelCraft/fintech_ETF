import unittest

import numpy as np
import pandas as pd

from competition import portfolio
from competition.backtest import PortfolioState
from competition.rules import load_rules
from research.baselines import MomentumConfig, MomentumStrategy, _eligible, momentum_score
from tests.synthetic import synthetic_market

RULES = load_rules()


class MomentumSignalTest(unittest.TestCase):
    def setUp(self):
        self.market = synthetic_market(n_days=120, n_symbols=30)
        self.view = self.market.asof(self.market.calendar[-1])

    def test_default_is_the_plain_20_session_sum(self):
        names = _eligible(self.view, 20)
        before = np.log1p(self.view.ret[names].iloc[-20:]).sum()        # the Mom20 formula before this change
        pd.testing.assert_series_equal(momentum_score(self.view, MomentumConfig()), before)

    def test_default_weights_unchanged(self):
        state = PortfolioState(date=self.market.calendar[-1], asof=self.view.date, day_index=0, sessions_remaining=24,
                               holdings={}, cash=1e9, nav=1e9, weights={}, episode_id='x')
        names = _eligible(self.view, 20)
        expected = portfolio.target_weights(np.log1p(self.view.ret[names].iloc[-20:]).sum(), {}, 24, RULES,
                                            portfolio.PortfolioConfig())
        self.assertEqual(MomentumStrategy(MomentumConfig(), RULES).decide(self.view, state), expected)

    def test_skip_drops_the_latest_sessions(self):
        score = momentum_score(self.view, MomentumConfig(window=20, skip=3))
        log_ret = np.log1p(self.view.ret[score.index])
        pd.testing.assert_series_equal(score, log_ret.iloc[-23:-3].sum())

    def test_blend_is_mean_percentile_rank(self):
        score = momentum_score(self.view, MomentumConfig(window=20, extra_windows=(10,)))
        log_ret = np.log1p(self.view.ret[score.index])
        expected = (log_ret.iloc[-20:].sum().rank(pct=True) + log_ret.iloc[-10:].sum().rank(pct=True)) / 2
        pd.testing.assert_series_equal(score, expected)
        self.assertTrue(((score > 0) & (score <= 1)).all())

    def test_risk_adjusted_divides_by_volatility(self):
        score = momentum_score(self.view, MomentumConfig(risk_adjusted=True))
        rows = np.log1p(self.view.ret[score.index]).iloc[-20:]
        pd.testing.assert_series_equal(score, rows.sum() / rows.std())

    def test_quality_factors_and_history(self):
        for kind in ('near_high', 'up_ratio', 'volume_trend'):
            score = momentum_score(self.view, MomentumConfig(quality=kind))
            self.assertEqual(len(score), len(_eligible(self.view, MomentumConfig(quality=kind).history)))
            self.assertTrue(score.between(0, 1).all())
        self.assertEqual(MomentumConfig(quality='near_high').history, 60)
        self.assertEqual(MomentumConfig(extra_windows=(60,), skip=5).history, 65)

    def test_config_validation(self):
        for raw in (dict(window=1), dict(skip=-1), dict(quality='value'), dict(lookback=20)):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                MomentumConfig.from_dict(raw)
        c = MomentumConfig.from_dict(dict(window=25, skip=1, extra_windows=[60], risk_adjusted=True, quality='up_ratio'))
        self.assertEqual((c.window, c.skip, c.extra_windows, c.risk_adjusted, c.quality), (25, 1, (60,), True, 'up_ratio'))


if __name__ == '__main__':
    unittest.main()
