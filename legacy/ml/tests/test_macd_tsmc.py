"""MACD Momentum and TSMC Core specs: formulas, causality, rerank structure, weights, trim rule."""
import json
import unittest

import numpy as np
import pandas as pd

from competition.backtest import PortfolioState
from competition.data import load_market
from competition.rules import ROOT, load_rules
from lgbm_strategy.features import signal_price
from momv2 import macd as M
from momv2 import tsmc as T
from research.baselines import MomentumConfig, momentum_score
from tests.test_hybrid import until

RULES = load_rules()
PORTFOLIO = json.loads((ROOT / 'production/strategy.json').read_text())['params']['portfolio']


def state(date, weights=None, remaining=24, day=5):
    weights = weights or {}
    return PortfolioState(date=date, asof=date, day_index=day, sessions_remaining=remaining, holdings={},
                          cash=1e9 * (1 - sum(weights.values())), nav=1e9, weights=weights, episode_id='t')


class MacdTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = load_market().since(M.DATA_START)
        cls.frames = M.normalized_macd(cls.market)
        cls.date = cls.market.calendar[cls.market.calendar >= '2019-03-01'][0]

    def test_formula_by_hand(self):
        p = signal_price(self.market.ret)['2330.TW'].where(self.market.close['2330.TW'].notna()).loc[:self.date]
        for name, (s, l) in M.SCALES.items():
            q = (p.ewm(alpha=1 / s, adjust=False).mean() - p.ewm(alpha=1 / l, adjust=False).mean()) / \
                p.rolling(63, min_periods=51).std()
            expected = q.iloc[-1] / q.iloc[-252:].std()
            self.assertAlmostEqual(self.frames[name].loc[self.date, '2330.TW'], expected, places=10)

    def test_ignores_the_future(self):
        for t in (self.date, self.market.calendar[self.market.calendar >= '2025-06-02'][0]):
            part = M.normalized_macd(until(self.market, t))
            for name in M.SCALES:
                pd.testing.assert_series_equal(part[name].loc[t], self.frames[name].loc[t], check_names=False)

    def test_rerank_structure(self):
        view = self.market.asof(self.date)
        score, top = M.rerank(view, 'combined', self.frames)
        mom = momentum_score(view, MomentumConfig())
        order = sorted(score.index, key=lambda s: -score[s])
        self.assertEqual(set(order[:40]), set(top))
        self.assertEqual(order[40:], sorted(order[40:], key=lambda s: -mom[s]))
        comb = M.macd_score(self.date, top, 'combined', self.frames)
        parts = [M.macd_score(self.date, top, f'scale:{s}', self.frames) for s in M.SCALES]
        pd.testing.assert_series_equal(comb, sum(parts) / 3)


class TsmcTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = load_market().since('2014-01-01')
        cls.date = cls.market.calendar[cls.market.calendar >= '2021-05-03'][0]
        cls.view = cls.market.asof(cls.date)

    def decide(self, variant, st):
        return T.TsmcStrategy(T.TsmcConfig.from_dict(dict(variant=variant, portfolio=PORTFOLIO)), RULES).decide(self.view, st)

    def test_formal_weights(self):
        w = self.decide('formal', state(self.date))
        self.assertEqual(len(w), 25)
        self.assertAlmostEqual(w[T.TSMC], .225)
        self.assertAlmostEqual(sum(w.values()), .95, places=9)
        self.assertLessEqual(max(v for s, v in w.items() if s != T.TSMC), .09 + 1e-12)
        mom = momentum_score(self.view, MomentumConfig()).drop(T.TSMC)
        others = [s for s in w if s != T.TSMC]
        self.assertEqual(set(others), set(mom.sort_values(ascending=False).index[:24]))

    def test_blend_formula(self):
        mom = momentum_score(self.view, MomentumConfig())
        names = list(mom.sort_values(ascending=False).index[:5])
        s = mom[names]
        shifted = s - s.min() + (s.max() - s.min()) / 5
        vol = np.log1p(self.view.ret[names].iloc[-60:]).std()
        expected = .85 * shifted / shifted.sum() + .15 * (1 / vol) / (1 / vol).sum()
        pd.testing.assert_series_equal(T.blend_preference(s, self.view), expected, check_names=False)

    def test_core_equal_and_blend_only(self):
        w = self.decide('core_equal', state(self.date))
        others = [v for s, v in w.items() if s != T.TSMC]
        self.assertEqual(len(others), 24)
        np.testing.assert_allclose(others, .725 / 24)
        b = self.decide('blend_only', state(self.date))
        self.assertEqual(len(b), 25)
        self.assertAlmostEqual(sum(b.values()), .95, places=9)
        self.assertLessEqual(b.get(T.TSMC, 0.), .25 * .9 + 1e-12)

    def test_trim_overrides_freeze_and_turnover_gate(self):
        book = self.decide('formal', state(self.date))
        over = {**{s: v * (.95 - .26) / (.95 - .225) for s, v in book.items() if s != T.TSMC}, T.TSMC: .26}
        frozen = self.decide('formal', state(self.date, over, remaining=2))
        self.assertAlmostEqual(frozen[T.TSMC], .225)
        self.assertEqual({s: v for s, v in frozen.items() if s != T.TSMC},
                         {s: v for s, v in over.items() if s != T.TSMC})
        calm = {**book, T.TSMC: .235}
        self.assertEqual(self.decide('formal', state(self.date, calm)), calm)     # below 24%: no trim


if __name__ == '__main__':
    unittest.main()
