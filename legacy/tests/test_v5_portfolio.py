import unittest

import numpy as np
import pandas as pd

from src.v5_portfolio import (cap_fill, construct, regime_at, regime_table, risk_aware_weights, select_names,
                              settings)
from src.v5_strategy import Strategy, previous_rows


def cross_section(n=40, seed=0, tsmc=False):
    rng = np.random.default_rng(seed)
    symbols = [f'{1000 + i}.TW' for i in range(n)]
    if tsmc:
        symbols[0] = '2330.TW'
    scores = pd.DataFrame(dict(symbol=symbols, score=rng.normal(size=n)))
    rows = pd.DataFrame(dict(symbol=symbols, date=pd.Timestamp('2020-01-02'), feature_ready=True,
                             close=rng.uniform(20, 500, n), vol20=rng.uniform(.01, .04, n)))
    return scores, rows


def order(scores):
    return list(scores.sort_values(['score', 'symbol'], ascending=[False, True]).symbol)


class ConstructionConstraints(unittest.TestCase):
    def check(self, w, n, cash=.05):
        self.assertEqual(len(w), n)
        self.assertTrue((w >= 0).all())
        self.assertAlmostEqual(w.sum(), 1 - cash, places=12)
        for s, x in w.items():
            self.assertLessEqual(x, (.25 if s.startswith('2330') else .10) + 1e-12)

    def test_methods_respect_caps_count_and_cash(self):
        scores, rows = cross_section(tsmc=True)
        for method in ('topn_equal', 'score_bounded', 'risk_aware'):
            for n in (20, 22, 26, 30):
                w = construct(scores, rows, {}, dict(method=method, n=n))
                self.check(w, n)

    def test_topn_equal_picks_top_scores(self):
        scores, rows = cross_section()
        w = construct(scores, rows, {}, dict(n=22))
        self.assertEqual(set(w.index), set(order(scores)[:22]))
        self.assertTrue(np.allclose(w, .95 / 22))

    def test_score_bounded_monotone_in_rank(self):
        scores, rows = cross_section()
        w = construct(scores, rows, {}, dict(method='score_bounded', n=22))
        ranked = [s for s in order(scores) if s in w.index]
        self.assertTrue((np.diff(w.reindex(ranked).to_numpy()) <= 1e-15).all())

    def test_risk_aware_uses_expected_return_and_penalizes_variance(self):
        scores, rows = cross_section()
        scores['expected_return'] = .02
        rows['vol20'] = .02
        rows.loc[rows.symbol.eq(order(scores)[0]), 'vol20'] = .06
        w = construct(scores, rows, {}, dict(method='risk_aware', n=22))
        self.assertEqual(w.attrs['mu_source'], 'expected_return')
        self.assertLess(w[order(scores)[0]], w.drop(order(scores)[0]).min() + 1e-12)
        self.check(w, 22)

    def test_cap_fill_and_kkt_solver(self):
        w = cap_fill(pd.Series([10., 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]), .95, pd.Series([.1] * 11))
        self.assertAlmostEqual(w.sum(), .95)
        self.assertLessEqual(w.max(), .1 + 1e-15)
        x = risk_aware_weights(np.linspace(.05, 0, 22), np.full(22, .01), .95, np.full(22, .1), .005, 1.)
        self.assertAlmostEqual(x.sum(), .95, places=12)
        self.assertTrue((x >= .005 - 1e-12).all() and (x <= .1 + 1e-12).all())

    def test_ineligible_and_too_few(self):
        scores, rows = cross_section(n=25)
        rows.loc[:5, 'feature_ready'] = False
        w = construct(scores, rows, {}, dict(n=22))
        self.assertTrue(w.empty)
        self.assertTrue(w.attrs['reason'].startswith('INFEASIBLE'))
        scores.loc[0, 'score'] = np.nan
        with self.assertRaises(ValueError):
            construct(scores, rows, {}, dict(n=31))

    def test_invalid_settings(self):
        with self.assertRaises(ValueError):
            settings(dict(cash_target=.25))
        with self.assertRaises(ValueError):
            settings(dict(rotation='weekly'))


class Rotation(unittest.TestCase):
    def setUp(self):
        self.c = settings(dict(n=4, min_names=1, max_names=30))
        self.ranked = [f'S{i:02d}' for i in range(20)]

    def test_initial_deployment_is_top_n(self):
        chosen, info = select_names(self.ranked, [], self.c)
        self.assertEqual(chosen, self.ranked[:4])

    def test_never_keeps_holdings_until_ineligible(self):
        c = dict(self.c, rotation='never')
        held = ['S15', 'S16', 'S17', 'S18']
        chosen, info = select_names(self.ranked, held, c)
        self.assertEqual(sorted(chosen), held)
        chosen, info = select_names([s for s in self.ranked if s != 'S18'], held, c)
        self.assertEqual(info['forced_drops'], ['S18'])
        self.assertIn('S00', chosen)
        self.assertEqual(len(chosen), 4)

    def test_buffer_holds_within_2n_and_replaces_dropouts_with_best(self):
        held = ['S00', 'S07', 'S08', 'S12']  # ranks 1, 8, 9, 13; 2N = 8
        chosen, info = select_names(self.ranked, held, self.c)
        self.assertEqual(info['kept'], ['S00', 'S07'])
        self.assertEqual(sorted(info['voluntary_drops']), ['S08', 'S12'])
        self.assertEqual(chosen, ['S00', 'S01', 'S02', 'S07'])

    def test_risk_off_blocks_voluntary_rotation(self):
        held = ['S00', 'S07', 'S08', 'S12']
        chosen, info = select_names(self.ranked, held, self.c, rotate=False)
        self.assertEqual(sorted(chosen), held)
        self.assertEqual(info['voluntary_drops'], [])

    def test_unscored_holding_kept_by_never_dropped_by_buffer(self):
        scores, rows = cross_section()
        ranked = order(scores)
        held = {s: 1000. for s in ranked[:22]}
        scores.loc[scores.symbol.eq(ranked[0]), 'score'] = np.nan      # e.g. trend filter fails
        w = construct(scores, rows, dict(holdings=held), dict(n=22, rotation='never'))
        self.assertIn(ranked[0], w.index)
        self.assertEqual(w.attrs['unscored_kept'], [ranked[0]])
        w = construct(scores, rows, dict(holdings=held), dict(n=22, rotation='buffer_2N'))
        self.assertNotIn(ranked[0], w.index)
        rows.loc[rows.symbol.eq(ranked[1]), 'feature_ready'] = False     # forced even under never
        w = construct(scores, rows, dict(holdings=held), dict(n=22, rotation='never'))
        self.assertNotIn(ranked[1], w.index)
        self.assertEqual(w.attrs['forced_drops'], [ranked[1]])
        self.assertEqual(len(w), 22)

    def test_cap_headroom(self):
        scores, rows = cross_section(tsmc=True)
        scores['expected_return'] = np.linspace(.2, 0, len(scores))
        w = construct(scores, rows, {}, dict(method='risk_aware', n=20))
        self.assertLessEqual(w.drop('2330.TW', errors='ignore').max(), .09 + 1e-12)

    def test_construct_rotation_from_share_holdings(self):
        scores, rows = cross_section()
        ranked = order(scores)
        held = {s: 1000. for s in ranked[18:40]}  # ranks 19..40 with N=22 -> 2N = 44 keeps all
        w = construct(scores, rows, dict(holdings=held), dict(n=22))
        self.assertEqual(set(w.index), set(held))
        held = {s: 1000. for s in ranked[-22:]}   # ranks 19..40 again for n=40 universe
        w = construct(scores, rows, dict(holdings=held), dict(n=22, rotation='buffer_2N'))
        self.assertTrue(set(w.index) <= set(ranked[:44]))


def synthetic_panel(days=120, names=30, seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range('2019-01-01', periods=days)
    rows = []
    for d in dates:
        r = rng.normal(0, .02, names)
        for i in range(names):
            rows.append(dict(date=d, symbol=f'{1100 + i}.TW', feature_ready=True, R1=r[i],
                             price_ema20=rng.normal(), close=100., vol20=.02))
    return pd.DataFrame(rows)


class Regime(unittest.TestCase):
    def test_prefix_invariance_under_future_corruption(self):
        panel = synthetic_panel()
        full = regime_table(panel)
        cut = pd.Timestamp(panel.date.unique()[90])
        corrupted = panel.copy()
        future = corrupted.date >= cut
        rng = np.random.default_rng(7)
        corrupted.loc[future, 'R1'] *= rng.uniform(-5, 5, future.sum())
        corrupted.loc[future, 'price_ema20'] = rng.permutation(corrupted.loc[future, 'price_ema20'].to_numpy())
        corrupted = corrupted.drop(corrupted.index[future][::3])
        other = regime_table(corrupted)
        past = full.index < cut
        pd.testing.assert_frame_equal(full.loc[past], other.loc[other.index < cut])
        self.assertEqual(regime_at(full, cut), regime_at(other, cut))

    def test_regime_at_reads_strictly_before(self):
        panel = synthetic_panel()
        table = regime_table(panel)
        t = table.index[70]
        self.assertEqual(regime_at(table, t)['observed_date'], str(table.index[69].date()))
        self.assertIsNone(regime_at(table, table.index[0])['observed_date'])

    def test_risk_off_raises_cash_and_freezes_rotation(self):
        scores, rows = cross_section()
        ranked = order(scores)
        held = {s: 1000. for s in ranked[-22:]}
        state = dict(holdings=held, regime=dict(risk_off=True))
        w = construct(scores, rows, state, dict(n=22, regime=True))
        self.assertAlmostEqual(w.sum(), .90)
        self.assertEqual(set(w.index), set(held))
        w = construct(scores, rows, state, dict(n=22, regime=False))
        self.assertAlmostEqual(w.sum(), .95)


class StrategyLookup(unittest.TestCase):
    def test_generate_weights_uses_prior_rows_and_decision_scores(self):
        panel = synthetic_panel(days=30)
        dates = sorted(panel.date.unique())
        t = pd.Timestamp(dates[20])
        scores = pd.DataFrame(dict(decision_date=t, symbol=panel.symbol.unique(),
                                   score=np.arange(30, dtype=float)))
        strategy = Strategy(dict(id='A01', family='A', construction=dict(n=22)), scores)
        out = strategy.generate_weights(t, panel, dict(holdings={}), {})
        self.assertEqual(len(out), 22)
        self.assertEqual(out.attrs['observed_date'], str(pd.Timestamp(dates[19]).date()))
        self.assertEqual(previous_rows(panel, t).date.iloc[0], dates[19])
        log = strategy.predictions()
        self.assertTrue((pd.to_datetime(log.observed_date) < pd.to_datetime(log.decision_date)).all())
        missing = strategy.generate_weights(pd.Timestamp(dates[21]), panel, dict(holdings={}), {})
        self.assertTrue(missing.empty)
        self.assertIn('MISSING_SCORES', missing.attrs['reason'])

    def test_regime_candidate_requires_table(self):
        with self.assertRaises(ValueError):
            Strategy(dict(id='x', family='A', construction=dict(regime=True)), pd.DataFrame(
                columns=['decision_date', 'symbol', 'score']))


if __name__ == '__main__':
    unittest.main()
