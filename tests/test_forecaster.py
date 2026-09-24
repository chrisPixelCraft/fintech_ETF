import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from autots_strategy import portfolio, scoring, targets
from autots_strategy.forecaster import (CURATED_REGRESSORS, MAX_WINDOWS_CAP, AutoTSForecaster, ForecastConfig,
                                        competition_metric, load_template, prepare_panel, save_template,
                                        template_frame)
from competition.rules import load_rules
from tests.synthetic import synthetic_market

_NONE = {'fillna': 'ffill', 'transformations': {}, 'transformation_params': {}}
_DIFF = {'fillna': 'ffill', 'transformations': {'0': 'DifferencedTransformer'},
         'transformation_params': {'0': {'lag': 1, 'fill': 'zero'}}}
FAST_TEMPLATE = (
    dict(Model='LastValueNaive', ModelParameters={}, TransformationParameters=_NONE),
    dict(Model='AverageValueNaive', ModelParameters={'method': 'mean', 'window': 20}, TransformationParameters=_DIFF),
    dict(Model='SeasonalNaive', ModelParameters={'method': 'lastvalue', 'lag_1': 5, 'lag_2': None},
         TransformationParameters=_DIFF),
)
FAST = dict(horizon=5, lookback=120, validation_windows=3, validation_step=24, template=FAST_TEMPLATE)


class MetricTest(unittest.TestCase):
    def test_rank_ic_metric(self):
        rng = np.random.default_rng(0)
        train = rng.normal(size=(30, 12))
        A = train[-1] + rng.normal(size=(5, 12)).cumsum(axis=0)
        perfect = competition_metric(A, A, train, .9)
        self.assertEqual(perfect.shape, (12,))
        self.assertAlmostEqual(perfect[0], -1.)
        flat = competition_metric(A, np.repeat(train[-1:], 5, axis=0), train, .9)
        self.assertEqual(flat[0], 0.)
        self.assertAlmostEqual(competition_metric(A, -A + 2 * train[-1], train, .9)[0], 1.)
        top = competition_metric(A, A, train, .9, kind='topk_spread', top_k=3)
        self.assertLess(top[0], 0)


class ForecasterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = synthetic_market(n_days=200, n_symbols=24)
        view = cls.market.asof(cls.market.calendar[-1])
        cls.panel = prepare_panel(targets.build_series(view, 'relative_log_price'), FAST['lookback'])

    def test_prepare_panel_masks_short_history(self):
        view = self.market.asof(self.market.calendar[-1])
        series = targets.build_series(view, 'log_price')
        series.iloc[:150, 3] = np.nan
        panel = prepare_panel(series, 120)
        self.assertEqual(panel.shape, (120, 23))
        self.assertNotIn(series.columns[3], panel.columns)
        self.assertEqual(panel.index.freqstr, 'B')
        self.assertEqual(prepare_panel(series, 500).shape[1], 0)

    def test_validation_windows_are_disjoint(self):
        f = AutoTSForecaster(ForecastConfig(**FAST))
        vi = f.validation_indexes(self.panel.index)
        self.assertTrue(vi[0].equals(self.panel.index))
        ends = [len(x) for x in vi]
        self.assertEqual(ends, [120, 96, 72])
        self.assertTrue(all(a - b >= 5 for a, b in zip(ends, ends[1:])))

    def test_select_calibrate_forecast(self):
        f = AutoTSForecaster(ForecastConfig(**FAST))
        best, info = f.select(self.panel)
        self.assertEqual(len(best), 1)
        self.assertIn(best.Model.iloc[0], {r['Model'] for r in FAST_TEMPLATE})
        self.assertEqual(info['failed'], 0)
        sigma, ics = f.calibrate(self.panel, best)
        self.assertEqual(len(ics), 3)
        self.assertTrue((sigma > 0).all() and np.isfinite(sigma).all())
        frame = f.forecast(self.panel, best, sigma)
        self.assertEqual(list(frame.index), list(self.panel.columns))
        self.assertTrue(np.isfinite(frame[['mu', 'sigma', 'z', 'confidence']].to_numpy()).all())
        self.assertTrue(((frame.confidence > 0) & (frame.confidence < 1)).all())
        again = f.forecast(self.panel, best, sigma)
        pd.testing.assert_frame_equal(frame, again)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'best.csv'
            save_template(best, path)
            frozen = AutoTSForecaster(ForecastConfig(**dict(FAST, mode='frozen', template_path=str(path))))
            loaded, info = frozen.select(self.panel)
            self.assertEqual(info['selection'], 'frozen')
            pd.testing.assert_frame_equal(frozen.forecast(self.panel, loaded, sigma), frame)
            pd.testing.assert_frame_equal(load_template(path), best.astype(load_template(path).dtypes))

    def test_log_return_series_path(self):
        view = self.market.asof(self.market.calendar[-1])
        panel = prepare_panel(targets.build_series(view, 'log_return'), 120)
        mean20 = dict(Model='AverageValueNaive', ModelParameters={'method': 'mean', 'window': 20},
                      TransformationParameters=_NONE)
        f = AutoTSForecaster(ForecastConfig(**dict(FAST, template=(mean20,))), level=False)
        best, info = f.select(panel)
        self.assertEqual(info['selection'], 'single_template')
        sigma, _ = f.calibrate(panel, best)
        frame = f.forecast(panel, best, sigma)
        np.testing.assert_allclose(frame.mu, panel.iloc[-20:].mean() * 5, atol=1e-9)

    def test_search_mode_is_bounded_and_curated(self):
        f = AutoTSForecaster(ForecastConfig(**dict(FAST, mode='search', max_generations=0,
                                                    model_list=('LastValueNaive', 'SeasonalNaive'))))
        best, info = f.select(self.panel)
        self.assertIn(info['best_model'], {'LastValueNaive', 'SeasonalNaive'})
        from autots.models import sklearn as sk
        self.assertNotIn('xgboost', sk.sklearn_model_dict)
        self.assertLessEqual(set(sk.sklearn_model_dict), set(CURATED_REGRESSORS))
        for _ in range(20):
            params = sk.WindowRegression().get_new_params()
            self.assertLessEqual(params['max_windows'], MAX_WINDOWS_CAP)
            self.assertIsNone(params['regression_type'])

    def test_config_rejects_unknown_and_unsafe(self):
        with self.assertRaises(ValueError):
            ForecastConfig.from_dict({'horizon': 5, 'bogus': 1})
        with self.assertRaises(ValueError):
            ForecastConfig(horizon=10, validation_step=5)
        self.assertEqual(template_frame(FAST_TEMPLATE).shape, (3, 4))


class PortfolioTest(unittest.TestCase):
    rules = load_rules()

    def test_weights_respect_caps_and_count(self):
        scores = pd.Series(np.arange(40, dtype=float), index=[f'S{i:03d}.TW' for i in range(40)])
        cfg = portfolio.PortfolioConfig(n_holdings=25, weighting='score')
        w = portfolio.target_weights(scores, {}, 24, self.rules, cfg)
        self.assertEqual(len(w), 25)
        self.assertAlmostEqual(sum(w.values()), cfg.invested)
        self.assertLessEqual(max(w.values()), .1 * cfg.cap_scale + 1e-12)
        self.assertIn('S039.TW', w)

    def test_hold_buffer_and_turnover_gate(self):
        idx = [f'S{i:03d}.TW' for i in range(40)]
        scores = pd.Series(np.arange(40, dtype=float)[::-1], index=idx)
        cfg = portfolio.PortfolioConfig(n_holdings=25, keep_rank=35)
        current = {s: .88 / 25 for s in idx[5:30]}          # ranks 5..29: all within keep_rank
        self.assertEqual(portfolio.target_weights(scores, current, 20, self.rules, cfg), current)
        self.assertEqual(portfolio.target_weights(scores, current, 2, self.rules, cfg), current)   # frozen tail
        eager = portfolio.PortfolioConfig(n_holdings=25, keep_rank=25, rebalance_threshold=0.)
        w = portfolio.target_weights(scores, current, 20, self.rules, eager)
        self.assertEqual(sorted(w), sorted(idx[:25]))

    def test_score_forms(self):
        frame = pd.DataFrame(dict(mu=[.01, .02, -.01], z=[1., .5, -1.]), index=list('abc'))
        self.assertEqual(scoring.score(frame, 'mu').idxmax(), 'b')
        self.assertEqual(scoring.score(frame, 'z').idxmax(), 'a')
        self.assertEqual(scoring.score(frame, 'rank_blend').idxmin(), 'c')


if __name__ == '__main__':
    unittest.main()


class TargetsTest(unittest.TestCase):
    def test_series_and_labels(self):
        m = synthetic_market(n_days=60, n_symbols=21)
        view = m.asof(m.calendar[-1])
        level = targets.build_series(view, 'log_price')
        np.testing.assert_allclose(level.diff().iloc[2:], np.log1p(view.ret).iloc[2:])
        rel = targets.build_series(view, 'relative_log_price', 'ew')
        np.testing.assert_allclose(rel.diff().iloc[2:].mean(axis=1), 0, atol=1e-12)
        fwd = targets.forward_log_return(m.ret, 5)
        self.assertAlmostEqual(fwd.iloc[10, 0], np.log1p(m.ret.iloc[11:16, 0]).sum())
        fill = targets.fill_aligned_return(m.close, m.open, 3)
        self.assertAlmostEqual(fill.iloc[10, 0], m.close.iloc[12, 0] / m.open.iloc[10, 0] - 1)
        rank = targets.cross_sectional_rank(targets.excess(fwd))
        self.assertTrue(((rank.dropna() > 0) & (rank.dropna() <= 1)).all().all())
