"""Future-corruption test on the full AutoTSStrategy decision path.

Everything dated after D-1 is overwritten with noise; every decision up to and
including day D must be bit-identical, while the corrupted run's later ledger
must differ (the corruption is real).
"""
import unittest

import numpy as np

from autots_strategy.strategy import AutoTSStrategy, AutoTSStrategyConfig
from competition import backtest
from competition.episodes import Episode
from competition.rules import load_rules
from tests.synthetic import synthetic_market
from tests.test_forecaster import FAST

RULES = load_rules()
CONFIG = dict(target=dict(series='relative_log_price', market='ew'),
              forecaster=dict(FAST, template=list(FAST['template'])), score='z',
              portfolio=dict(n_holdings=22, keep_rank=24, rebalance_threshold=0.02, freeze_last_days=1),
              refit_every=3, predict_every=1)


class Recorder:
    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def decide(self, view, state):
        weights = self.inner.decide(view, state)
        self.calls.append((view.date, view.close.index[-1], state.date, dict(weights)))
        return weights


def corrupt_after(market, cutoff, seed=1):
    rng = np.random.default_rng(seed)
    future = market.calendar > cutoff
    frames = {}
    for name in ('open', 'high', 'low', 'close', 'volume', 'ret', 'official_vwap'):
        frame = getattr(market, name).copy()
        frame.loc[future] = frame.loc[future] * rng.uniform(.5, 1.5, frame.loc[future].shape)
        frames[name] = frame
    valid = market.valid.copy()
    valid.loc[future] = rng.uniform(size=valid.loc[future].shape) > .3
    bench = market.benchmark_ret.copy()
    bench[future] = rng.normal(size=future.sum())
    return market.with_frames(valid=valid, benchmark_ret=bench, **frames)


class CausalityTest(unittest.TestCase):
    def run_strategy(self, market, episode):
        strategy = Recorder(AutoTSStrategy(AutoTSStrategyConfig.from_dict(CONFIG), RULES))
        return backtest.run_episode(market, episode, strategy, RULES), strategy.calls

    def test_future_corruption_leaves_decisions_bit_identical(self):
        market = synthetic_market(n_days=230, n_symbols=26, official_from=210)
        sessions = tuple(market.calendar[200:207])
        episode = Episode('causality', 'dev', market.calendar[199], sessions)
        k = 4                                    # corrupt everything after the close before day k
        base, base_calls = self.run_strategy(market, episode)
        bad, bad_calls = self.run_strategy(corrupt_after(market, sessions[k - 1]), episode)
        for i in range(k + 1):
            view_date, last_row, day, weights = base_calls[i]
            self.assertLess(view_date, day)
            self.assertEqual(last_row, view_date)
            self.assertEqual(weights, bad_calls[i][3], f'decision for day {i} changed')
        self.assertTrue(len(base_calls[0][3]) >= 20)
        self.assertFalse(np.allclose(base['ledger'].nav.iloc[k:], bad['ledger'].nav.iloc[k:]))
        np.testing.assert_array_equal(base['ledger'].nav.iloc[:k], bad['ledger'].nav.iloc[:k])
        self.assertEqual(backtest.verify_episode(market, episode, base, RULES), [])

    def test_asof_view_has_no_future_rows(self):
        market = synthetic_market(n_days=50, n_symbols=21)
        date = market.calendar[30]
        view = market.asof(date)
        for frame in (view.close, view.ret, view.valid, view.volume, view.open):
            self.assertEqual(frame.index[-1], date)
        self.assertEqual(view.benchmark_ret.index[-1], date)
        with self.assertRaises(KeyError):
            market.asof('2015-01-03')                # a Saturday is not a session


if __name__ == '__main__':
    unittest.main()
