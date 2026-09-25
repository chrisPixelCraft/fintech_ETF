"""Hybrid spec: gate, point-in-time features, walk-forward maturity and causality, strategy switching."""
import json
import tempfile
import unittest
from dataclasses import fields
from pathlib import Path

import numpy as np
import pandas as pd

from competition.backtest import PortfolioState
from competition.data import MarketData, load_market
from competition.rules import load_rules
from hybrid import features, gate, walkforward
from hybrid.strategy import HybridConfig, HybridStrategy, _CACHE
from research.baselines import MomentumConfig, MomentumStrategy

RULES = load_rules()
SYMBOLS = walkforward.symbols_by_ticker()


def until(market: MarketData, end) -> MarketData:
    """The market with every row after ``end`` removed."""
    keep = market.calendar <= pd.Timestamp(end)
    values = {f.name: getattr(market, f.name) for f in fields(market)}
    values.update({k: v[keep] for k, v in values.items() if isinstance(v, (pd.DataFrame, pd.Series, pd.DatetimeIndex))})
    return MarketData(**values)


def toy_revenue() -> pd.DataFrame:
    months = pd.period_range('2021-01', '2023-12', freq='M')
    rows = []
    for i, m in enumerate(months):
        for j, t in enumerate(('2330', '2317', '6488')):
            rows.append(dict(month=str(m), ticker=t, revenue=100. + i + 10 * j, prev_revenue=99. + i + 10 * j,
                             last_year_revenue=90. + 10 * j))
    return pd.DataFrame(rows)


def toy_flows(dates) -> pd.DataFrame:
    rows = [dict(date=d.date(), ticker='2330', foreign_net=1000., trust_net=-500., source='twse') for d in dates]
    rows += [dict(date=d.date(), ticker='6488', foreign_net=10., trust_net=0., source='tpex') for d in dates[5:]]
    return pd.DataFrame(rows)


class GateTest(unittest.TestCase):
    def test_prefix_property_and_definition(self):
        bench = load_market().benchmark_ret.loc['2018':]
        full = gate.panic_table(bench)
        part = gate.panic_table(bench.iloc[:700])
        pd.testing.assert_frame_equal(part, full.iloc[:700])
        row = full.dropna().iloc[400]
        t = full.index.get_loc(row.name)
        window = np.log1p(bench.iloc[t - 19:t + 1])
        self.assertAlmostEqual(row.vol20, window.std())
        self.assertAlmostEqual(row.r20, np.expm1(window.sum()))
        self.assertAlmostEqual(row.threshold, full.vol20.iloc[:t + 1].median())
        self.assertEqual(row.panic, bool(row.r20 < 0 and row.vol20 > row.threshold))
        self.assertFalse(full.panic.iloc[:gate.MIN_HISTORY + gate.WINDOW - 2].any())


class FeatureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = load_market().since('2021-01-01')
        dates = cls.market.calendar
        cls.flows = toy_flows(dates[dates >= '2022-01-03'])
        cls.panel = features.build_panel(cls.market, toy_revenue(), cls.flows, SYMBOLS)

    def test_revenue_known_from_the_11th(self):
        rev = features.revenue_features(toy_revenue(), pd.DatetimeIndex(['2023-02-10', '2023-02-11', '2023-02-13']),
                                        SYMBOLS)
        yoy = rev['rev_yoy']['2330.TW']
        dec, jan = 100. + 23 - 1, 100. + 24 - 1                      # Dec 2022 and Jan 2023 revenue
        self.assertAlmostEqual(yoy.loc['2023-02-10'], (100. + 23) / 90 - 1)   # still December
        self.assertAlmostEqual(yoy.loc['2023-02-11'], (100. + 24) / 90 - 1)   # January released on the 11th
        self.assertEqual(rev['rev_days']['2330.TW'].loc['2023-02-13'], 2.)
        self.assertTrue(dec < jan)

    def test_flow_zero_when_omitted_nan_before_start(self):
        f = features.flow_features(self.flows, self.market.volume, SYMBOLS)['foreign_5d']
        start = pd.Timestamp('2022-01-03')
        self.assertTrue(f.loc[:start - pd.Timedelta(days=1), '2317.TW'].isna().all())       # no report yet
        later = f.loc['2022-03-01':, '2317.TW']
        self.assertTrue((later == 0).all())                        # TWSE reported, 2317 omitted -> 0
        self.assertTrue((f.loc['2022-03-01':, '2330.TW'] > 0).all())

    def test_rows_do_not_depend_on_later_data(self):
        cut = pd.Timestamp('2023-06-30')
        short = features.build_panel(until(self.market, cut), toy_revenue(), self.flows, SYMBOLS)
        a = self.panel.loc[:cut, list(features.V2B) + ['ready']]
        b = short.loc[:cut, list(features.V2B) + ['ready']]
        pd.testing.assert_frame_equal(a, b)

    def test_definitions(self):
        row = self.panel.xs('2023-05-31', level='date').loc['2330.TW']
        p = np.exp(np.log1p(self.market.ret['2330.TW']).cumsum())
        t = self.market.calendar.get_loc(pd.Timestamp('2023-05-31'))
        self.assertAlmostEqual(row.r20, p.iloc[t] / p.iloc[t - 20] - 1)
        self.assertAlmostEqual(row.dd60, p.iloc[t] / p.iloc[t - 59:t + 1].max() - 1)
        self.assertEqual(set(features.V2B) - set(features.V2A), set(features.RAW_PRICE))


class WalkForwardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        market = load_market().since('2021-01-01')
        cls.market = market
        cls.panel = features.build_panel(market, toy_revenue(), toy_flows(market.calendar), SYMBOLS)
        cls.calendar = market.calendar

    def test_training_labels_end_before_the_refit(self):
        refit = pd.Timestamp('2023-03-01')
        rows, audit = walkforward.training_rows(self.panel, self.calendar, refit)
        self.assertLessEqual(pd.Timestamp(audit['latest_label_end']), pd.Timestamp(audit['cutoff']))
        self.assertLess(pd.Timestamp(audit['cutoff']), refit)
        self.assertTrue(rows.ready.all() and rows.label.notna().all())

    def test_predictions_ignore_data_after_the_cutoff(self):
        refit = pd.Timestamp('2023-03-01')
        day = self.calendar[self.calendar.get_loc(refit) + 2]
        walkforward._PANEL, walkforward._CALENDAR = self.panel, self.calendar
        base = walkforward.fit_and_predict((refit, [day], 'v2A'))
        cut = self.calendar[self.calendar.get_loc(day) - 1]
        noisy = self.market.ret.copy()
        future = noisy.index > cut
        noisy.loc[future] = noisy.loc[future] * 3 + .01            # every later return changes
        changed = features.build_panel(self.market.with_frames(ret=noisy), toy_revenue(),
                                       toy_flows(self.market.calendar), SYMBOLS)
        walkforward._PANEL = changed
        other = walkforward.fit_and_predict((refit, [day], 'v2A'))
        score = lambda out: {r['symbol']: r['score'] for r in out if r['symbol'] != '__audit__'}
        self.assertEqual(score(base), score(other))
        self.assertGreater(len(score(base)), 100)


class StrategyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = load_market().since('2011-01-01')
        table = gate.panic_table(cls.market.benchmark_ret)
        cal = cls.market.calendar
        cls.panic_day = next(cal[i + 1] for i in range(len(cal) - 1) if table.panic.iloc[i] and cal[i] > pd.Timestamp('2020-01-01'))
        cls.calm_day = next(cal[i + 1] for i in range(len(cal) - 1)
                            if not table.panic.iloc[i] and cal[i] > pd.Timestamp('2020-01-01'))

    def state(self, day):
        return PortfolioState(date=day, asof=day, day_index=0, sessions_remaining=24, holdings={}, cash=1e9, nav=1e9,
                              weights={}, episode_id='x')

    def strategy(self, predictions: Path):
        return HybridStrategy(HybridConfig.from_dict(dict(predictions=str(predictions), window=20)), RULES)

    def test_switches_expert_on_panic_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'p.parquet'
            symbols = self.market.symbols
            pd.DataFrame(dict(decision_date=self.panic_day, symbol=symbols,
                              score=np.arange(len(symbols), dtype=float))).to_parquet(path)
            _CACHE.clear()
            s = self.strategy(path)
            view = self.market.asof(self.market.calendar[self.market.position(self.calm_day) - 1])
            calm = s.decide(view, self.state(self.calm_day))
            self.assertEqual(calm, MomentumStrategy(MomentumConfig(), RULES).decide(view, self.state(self.calm_day)))
            pview = self.market.asof(self.market.calendar[self.market.position(self.panic_day) - 1])
            panic = s.decide(pview, self.state(self.panic_day))
            self.assertEqual(s.log[-1]['expert'], 'lgbm_v2')
            self.assertTrue(set(panic) <= set(symbols[-40:]))          # the highest scores are the last symbols
            with self.assertRaises(RuntimeError):
                other = self.market.calendar[self.market.position(self.panic_day) + 400]
                table = gate.panic_table(self.market.benchmark_ret)
                other = next(d for d in self.market.calendar[self.market.position(self.panic_day) + 60:]
                             if table.panic.loc[self.market.calendar[self.market.position(d) - 1]])
                s.decide(self.market.asof(self.market.calendar[self.market.position(other) - 1]), self.state(other))


if __name__ == '__main__':
    unittest.main()
