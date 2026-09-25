"""Momentum-v2 spec: signal definitions, point-in-time panels, causality, Mom20 equivalence."""
import dataclasses
import unittest

import numpy as np
import pandas as pd

from competition.backtest import PortfolioState
from competition.rules import load_rules
from hybrid.walkforward import symbols_by_ticker
from momv2 import signals
from momv2.signals import SignalConfig, rank, residual_momentum, shares_panel, turnover
from momv2.strategy import Momv2Config, Momv2Strategy
from research.baselines import MomentumConfig, MomentumStrategy, _eligible
from tests.test_hybrid import until
from competition.data import load_market

RULES = load_rules()


class SignalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = load_market().since('2014-01-01')
        cls.date = cls.market.calendar[cls.market.calendar >= '2018-06-01'][0]
        cls.view = cls.market.asof(cls.date)
        cls.names = _eligible(cls.view, 20)

    def test_rank_missing_is_neutral(self):
        r = rank(pd.Series({'a': 1., 'b': np.nan, 'c': 3., 'd': np.inf}), ['a', 'b', 'c', 'd', 'e'])
        self.assertEqual(r['a'], .5)
        self.assertEqual(r['c'], 1.)
        self.assertEqual(r['b'], .5)
        self.assertEqual(r['d'], .5)
        self.assertEqual(r['e'], .5)

    def test_residual_matches_explicit_regression(self):
        got = residual_momentum(self.view, self.names, 25, 120)
        lm_all = np.log1p(self.view.benchmark_ret.iloc[-120:])
        for name in self.names[:5]:
            lr = np.log1p(self.view.ret[name].iloc[-120:])
            ok = lr.notna() & lm_all.notna()
            x, y = lr[ok], lm_all[ok]
            beta = np.cov(x, y, bias=True)[0, 1] / np.var(y)
            tail = ok.iloc[-25:]
            expected = (lr.iloc[-25:][tail] - beta * lm_all.iloc[-25:][tail]).sum()
            self.assertAlmostEqual(got[name], expected, places=10)

    def test_residual_needs_eighty_percent(self):
        name = self.names[0]
        ret = self.view.ret.copy()
        ret.iloc[-20:-14, ret.columns.get_loc(name)] = np.nan                 # 6 of 20 missing
        view = dataclasses.replace(self.view, ret=ret)
        self.assertTrue(np.isnan(residual_momentum(view, self.names, 20, 60)[name]))

    def test_market_returns_leave_one_out(self):
        rows, name = 60, self.names[0]
        lr_all = np.log1p(self.view.ret.iloc[-rows:])
        ew = signals.market_returns(self.view, self.names, rows, 'ew')
        expected = lr_all.drop(columns=name).mean(axis=1)
        np.testing.assert_allclose(ew[name].to_numpy(), expected.to_numpy())
        groups = signals.industries()
        semis = [s for s in groups.index[groups == '半導體業'] if s in lr_all.columns]
        inside = [s for s in self.names if s in semis][0]
        ind = signals.market_returns(self.view, self.names, rows, 'industry')
        np.testing.assert_allclose(ind[inside].to_numpy(), lr_all[semis].drop(columns=inside).mean(axis=1).to_numpy())
        small = [s for s in self.names if (groups == groups.get(s)).sum() < signals.INDUSTRY_MIN]
        self.assertTrue(small)
        np.testing.assert_allclose(ind[small[0]].to_numpy(), ew[small[0]].to_numpy())

    def test_beta_shrink_and_benchmarks(self):
        name = self.names[2]
        for bench in ('ew', 'industry'):
            lm = signals.market_returns(self.view, self.names, 60, bench)[name]
            lr = np.log1p(self.view.ret[name].iloc[-60:])
            ok = lr.notna() & lm.notna()
            beta = np.cov(lr[ok], lm[ok], bias=True)[0, 1] / np.var(lm[ok])
            for shrink in (0., .5):
                b = (1 - shrink) * beta + shrink
                tail = ok.iloc[-25:]
                expected = (lr.iloc[-25:][tail] - b * lm.iloc[-25:][tail]).sum()
                got = residual_momentum(self.view, self.names, 25, 60, bench, shrink)[name]
                self.assertAlmostEqual(got, expected, places=10)

    def test_fixed_residuals_ignore_the_future(self):
        cut = until(self.market, self.date)
        noisy = cut.with_frames(ret=pd.concat([cut.ret, self.market.ret.loc[self.date:].iloc[1:] * 3]))
        for bench, shrink in (('ew', 0.), ('industry', 0.), ('0050', .5)):
            config = SignalConfig(kind='h1', resid_window=25, beta_window=60, benchmark=bench, beta_shrink=shrink)
            a, _ = signals.score(self.market.asof(self.date), config)
            b, _ = signals.score(noisy.asof(self.date), config)
            pd.testing.assert_series_equal(a, b)

    def test_turnover_uses_valid_days_and_shares(self):
        shares = pd.Series(1e6, index=self.names)
        got = turnover(self.view, self.names, 20, shares)
        name = self.names[3]
        vol = self.view.volume[name].iloc[-20:].where(self.view.valid[name].iloc[-20:])
        self.assertAlmostEqual(got[name], vol.mean() / 1e6)

    def test_shares_panel_snapshot_and_multipliers(self):
        dates = pd.DatetimeIndex(['2020-01-02', '2020-01-03', '2020-01-06', '2020-02-03', '2020-02-04'])
        split = pd.DataFrame(1., index=dates, columns=['2330.TW', '2317.TW'])
        split.loc['2020-01-06', '2330.TW'] = 1.1                 # 10% stock dividend after the snapshot
        split.loc['2020-02-04', '2317.TW'] = .5                  # capital reduction
        snaps = pd.DataFrame(dict(date=['2020-01-02', '2020-01-02', '2020-02-03'],
                                  ticker=['2330', '2317', '2330'], shares=[100., 50., 115.]))
        panel = shares_panel(snaps, split, symbols_by_ticker())
        self.assertEqual(panel.loc['2020-01-03', '2330.TW'], 100.)
        self.assertAlmostEqual(panel.loc['2020-01-06', '2330.TW'], 110.)
        self.assertAlmostEqual(panel.loc['2020-02-03', '2330.TW'], 115.)      # new snapshot replaces
        self.assertAlmostEqual(panel.loc['2020-02-04', '2317.TW'], 25.)
        # causality: dropping every row after t leaves row t unchanged
        for t in dates:
            part = shares_panel(snaps[pd.to_datetime(snaps.date) <= t], split.loc[:t], symbols_by_ticker())
            pd.testing.assert_series_equal(part.loc[t], panel.loc[t])

    def test_revenue_rows_ignore_later_releases(self):
        from competition.data import load_calendar
        from competition.rules import ROOT
        from hybrid.features import revenue_features
        revenue = pd.read_csv(ROOT / 'data/hybrid/revenue.csv', dtype={'ticker': str})
        calendar = load_calendar()
        full = revenue_features(revenue, calendar, symbols_by_ticker())
        release = (pd.PeriodIndex(revenue.month, freq='M') + 1).to_timestamp() + pd.Timedelta(days=10)
        for t in (pd.Timestamp('2016-03-10'), pd.Timestamp('2016-03-11'), pd.Timestamp('2023-08-14')):
            t = calendar[calendar >= t][0]
            part = revenue_features(revenue[release <= t], calendar, symbols_by_ticker())
            for name in signals.REVENUE_SCORES['yoy_acc_record']:
                pd.testing.assert_series_equal(part[name].loc[t].reindex(full[name].columns), full[name].loc[t])

    def test_scores_ignore_the_future(self):
        """Scrambling every price row after the view date changes nothing."""
        config = SignalConfig(kind='composite', resid_window=40, beta_window=120, turnover_window=25,
                              revenue_score='yoy_acc_record')
        base, _ = signals.score(self.market.asof(self.date), config)
        cut = until(self.market, self.date)
        noisy = cut.with_frames(**{f: pd.concat([getattr(cut, f), getattr(self.market, f).loc[self.date:].iloc[1:] * 3]
                                                  ) for f in ('ret', 'volume', 'close')})
        again, _ = signals.score(noisy.asof(self.date), config)
        pd.testing.assert_series_equal(base, again)

    def test_composite_is_mean_of_component_ranks(self):
        full = SignalConfig(kind='composite')
        got, _ = signals.score(self.view, full)
        parts = [signals.score(self.view, SignalConfig(kind='mom20'))[0],
                 rank(residual_momentum(self.view, self.names, 20, 60), self.names),
                 rank(-turnover(self.view, self.names, 20, signals._row('shares', self.date)), self.names),
                 signals.revenue_score(self.date, self.names, 'yoy_acc')]
        pd.testing.assert_series_equal(got, sum(parts) / 4, check_names=False)

    def test_h2_h3_blend_with_mom20(self):
        h3, _ = signals.score(self.view, SignalConfig(kind='h3', revenue_score='yoy'))
        mom = signals.score(self.view, SignalConfig(kind='mom20'))[0]
        rev = rank(signals._row('rev_yoy', self.date), self.names)
        pd.testing.assert_series_equal(h3, (mom + rev) / 2, check_names=False)


class StrategyTest(unittest.TestCase):
    def test_mom20_kind_matches_production_momentum(self):
        market = load_market().since('2014-01-01')
        state = PortfolioState(date=market.calendar[-30], asof=market.calendar[-31], day_index=0, sessions_remaining=24,
                               holdings={}, cash=1e9, nav=1e9, weights={}, episode_id='test')
        for date in market.calendar[market.calendar >= '2016-01-01'][::250]:
            view = market.asof(date)
            mine = Momv2Strategy(Momv2Config.from_dict(dict(kind='mom20')), RULES).decide(view, state)
            prod = MomentumStrategy(MomentumConfig(), RULES).decide(view, state)
            self.assertEqual(mine, prod)

    def test_config_rejects_unknown_keys_and_bad_windows(self):
        with self.assertRaises(ValueError):
            Momv2Config.from_dict(dict(kind='h1', lookback=5))
        with self.assertRaises(ValueError):
            Momv2Config.from_dict(dict(kind='h1', resid_window=60, beta_window=40))
        with self.assertRaises(ValueError):
            Momv2Config.from_dict(dict(kind='h4'))


if __name__ == '__main__':
    unittest.main()
