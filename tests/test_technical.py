"""Technical Momentum spec: indicator formulas, adjusted bars, causality, rerank structure."""
import unittest

import numpy as np
import pandas as pd

from competition.backtest import PortfolioState
from competition.data import load_market
from competition.rules import load_rules
from momv2 import technical as T
from research.baselines import MomentumConfig, _eligible, momentum_score
from tests.test_hybrid import until

RULES = load_rules()


class IndicatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = load_market().since(T.DATA_START)
        cls.frames = T.indicators(cls.market)
        cls.bars = T.adjusted_bars(cls.market)
        cls.date = cls.market.calendar[cls.market.calendar >= '2019-03-01'][0]
        cls.sym = '2330.TW'
        cls.c = cls.bars['close'][cls.sym].loc[:cls.date]
        cls.h = cls.bars['high'][cls.sym].loc[:cls.date]
        cls.l = cls.bars['low'][cls.sym].loc[:cls.date]
        cls.v = cls.bars['volume'][cls.sym].loc[:cls.date]

    def got(self, code):
        return self.frames[code].loc[self.date, self.sym]

    def test_adjusted_close_follows_action_neutral_return(self):
        c = self.bars['close'][self.sym]
        r = self.market.ret[self.sym]
        ok = r.notna() & c.shift().notna()
        np.testing.assert_allclose((c / c.shift() - 1)[ok], r[ok], rtol=1e-9)
        ratio = self.bars['high'][self.sym] / self.market.high[self.sym]
        np.testing.assert_allclose(ratio.dropna(), (c / self.market.close[self.sym]).reindex(ratio.dropna().index))

    def test_macd_rsi_by_hand(self):
        c = self.c
        e12 = c.ewm(span=12, adjust=False).mean(); e26 = c.ewm(span=26, adjust=False).mean()
        macd = e12 - e26; hist = macd - macd.ewm(span=9, adjust=False).mean()
        self.assertAlmostEqual(self.got('macd_hist'), hist.iloc[-1] / c.iloc[-1], places=10)
        self.assertAlmostEqual(self.got('macd_slope'), (hist.iloc[-1] - hist.iloc[-6]) / c.iloc[-1], places=10)
        d = c.diff()
        g, lo = 0., 0.
        gains, losses = d.clip(lower=0).to_numpy(), (-d).clip(lower=0).to_numpy()
        first = True
        for x, y in zip(gains[1:], losses[1:]):
            if first:
                g, lo, first = x, y, False
            else:
                g, lo = g + (x - g) / 14, lo + (y - lo) / 14
        self.assertAlmostEqual(self.got('rsi14'), 100 - 100 / (1 + g / lo), places=8)

    def test_oscillators_by_hand(self):
        c, h, l, v = self.c, self.h, self.l, self.v
        n = len(c)
        k = [(c.iloc[i] - l.iloc[i - 13:i + 1].min()) / (h.iloc[i - 13:i + 1].max() - l.iloc[i - 13:i + 1].min())
             for i in (n - 3, n - 2, n - 1)]
        self.assertAlmostEqual(self.got('stoch_k'), np.mean(k), places=10)
        w = c.iloc[-20:]
        self.assertAlmostEqual(self.got('pct_b'), (c.iloc[-1] - (w.mean() - 2 * w.std(ddof=0))) / (4 * w.std(ddof=0)),
                               places=10)
        self.assertAlmostEqual(self.got('bandwidth'), 4 * w.std(ddof=0) / w.mean(), places=10)
        tp = (h + l + c) / 3
        flow = (tp * v).iloc[-14:]
        dirn = np.sign(tp.diff()).iloc[-14:]
        pos, neg = flow[dirn > 0].sum(), flow[dirn < 0].sum()
        self.assertAlmostEqual(self.got('mfi14'), 100 - 100 / (1 + pos / neg), places=8)
        self.assertAlmostEqual(self.got('channel20'), (c.iloc[-1] - l.iloc[-20:].min()) /
                               (h.iloc[-20:].max() - l.iloc[-20:].min()), places=10)
        self.assertAlmostEqual(self.got('dist_high60'), c.iloc[-1] / h.iloc[-60:].max() - 1, places=10)
        clv = ((c - l) - (h - c)) / (h - l)
        self.assertAlmostEqual(self.got('clv5'), clv.iloc[-5:].mean(), places=10)

    def test_volume_and_volatility_by_hand(self):
        c, v = self.c, self.v
        lr = np.log(c / c.shift())
        self.assertAlmostEqual(self.got('vol_ratio'), lr.iloc[-10:].std() / lr.iloc[-60:].std(), places=10)
        self.assertAlmostEqual(self.got('abn_volume'), v.iloc[-5:].mean() / v.iloc[-60:].mean(), places=10)
        obv = (np.sign(c.diff()) * v).fillna(0).cumsum()
        self.assertAlmostEqual(self.got('obv_slope'), (obv.iloc[-1] - obv.iloc[-21]) / (20 * v.iloc[-20:].mean()),
                               places=10)
        dlogv = np.log(v).diff()
        self.assertAlmostEqual(self.got('pv_corr'), lr.iloc[-20:].corr(dlogv.iloc[-20:]), places=10)
        roc = c / c.shift(10) - 1
        self.assertAlmostEqual(self.got('roc_accel'), roc.iloc[-1] - roc.iloc[-11], places=10)

    def test_indicators_ignore_the_future(self):
        for t in (self.date, self.market.calendar[self.market.calendar >= '2025-06-02'][0]):
            part = T.indicators(until(self.market, t))
            for code in T.INDICATORS:
                pd.testing.assert_series_equal(part[code].loc[t], self.frames[code].loc[t], check_names=False,
                                               obj=code)


class RerankTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = load_market().since(T.DATA_START)
        cls.frames = T.indicators(cls.market)
        cls.date = cls.market.calendar[cls.market.calendar >= '2021-05-03'][0]
        cls.view = cls.market.asof(cls.date)

    def test_top40_first_rest_in_mom20_order(self):
        score, top = T.rerank(self.view, 'composite', self.frames)
        mom = momentum_score(self.view, MomentumConfig())
        order = sorted(score.index, key=lambda s: -score[s])
        self.assertEqual(set(order[:40]), set(top))
        rest = order[40:]
        self.assertEqual(rest, sorted(rest, key=lambda s: -mom[s]))
        self.assertEqual(set(score.index), set(_eligible(self.view, 20)))

    def test_bad_direction_prefers_low_rsi(self):
        _, top = T.rerank(self.view, 'ind:rsi14', self.frames)
        tech = T.technical_score(self.date, top, 'ind:rsi14', self.frames)
        rsi = self.frames['rsi14'].loc[self.date].reindex(top)
        self.assertEqual(tech.idxmax(), rsi.idxmin())
        self.assertEqual(tech.idxmin(), rsi.idxmax())

    def test_composite_is_mean_of_family_reranks(self):
        _, top = T.rerank(self.view, 'composite', self.frames)
        comp = T.technical_score(self.date, top, 'composite', self.frames)
        fam = [T.technical_score(self.date, top, f'family:{f}', self.frames) for f in T.FAMILIES]
        pd.testing.assert_series_equal(comp, sum(fam) / 4)
        for f in fam:
            self.assertAlmostEqual(f.max(), 1.)                     # every family re-ranked to 1/n..1

    def test_holdings_stay_25_and_keep_rank_applies(self):
        score, top = T.rerank(self.view, 'composite', self.frames)
        state = PortfolioState(date=self.date, asof=self.date, day_index=0, sessions_remaining=24, holdings={},
                               cash=1e9, nav=1e9, weights={}, episode_id='t')
        w = T.TechStrategy(T.TechConfig(), RULES).decide(self.view, state)
        self.assertEqual(len(w), 25)
        order = sorted(score.index, key=lambda s: (-score[s], s))
        held = {s: .95 / 25 for s in order[30:35] + order[:20]}     # 5 held names ranked 31-35 are kept
        state2 = PortfolioState(date=self.date, asof=self.date, day_index=5, sessions_remaining=19, holdings={},
                                cash=0., nav=1e9, weights=held, episode_id='t')
        w2 = T.TechStrategy(T.TechConfig(), RULES).decide(self.view, state2)
        self.assertTrue(set(order[30:35]) <= set(w2) or w2 == held)
        self.assertLessEqual(len(w2), 25)


if __name__ == '__main__':
    unittest.main()
