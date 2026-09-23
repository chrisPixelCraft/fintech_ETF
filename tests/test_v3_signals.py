"""Focused correctness tests for the fixed v3 weak-market intervention."""
from dataclasses import asdict
import unittest

import numpy as np
import pandas as pd

from src.v3_signals import WeakMarketParameters, WeakMarketSignals


def parameters(**overrides):
    result = asdict(WeakMarketParameters())
    result.update(overrides)
    return result


def fixture(sessions=230, stocks=5, seed=40):
    """Deterministic stochastic returns, followed by a sustained weak regime."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range('2024-01-02', periods=sessions)
    market = rng.normal(.0002, .005, sessions)
    market[-55:] -= .004
    rows = []
    for j, symbol in enumerate(['0050.TW'] + [f'S{i:02d}.TW' for i in range(stocks)]):
        values = market if j == 0 else -.0001 + j * .3 * market + rng.normal(j * .00003, .001 + j * .0005, sessions)
        prices = 100 * np.cumprod(1 + values)
        rows.append(pd.DataFrame(dict(date=dates, symbol=symbol, open=prices, high=prices,
            low=prices, close=prices, volume=10000., dividend=0., split=1.)))
    return pd.concat(rows, ignore_index=True)


def ranked(day, symbols=None):
    symbols = symbols or ['S00.TW', 'S01.TW', 'S02.TW', 'S03.TW', 'S04.TW']
    n = len(symbols)
    return pd.DataFrame(dict(symbol=symbols, date=pd.Timestamp(day), score=np.linspace(.8, .2, n),
        entry_ok=[i % 2 == 0 for i in range(n)], exit=[i % 3 == 1 for i in range(n)],
        close=np.arange(n, dtype=float) + 100)).set_index('symbol', drop=False)


class V3SignalsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daily = fixture()
        cls.params = parameters(minimum_common_valid=3)
        cls.signals = WeakMarketSignals(cls.daily, cls.params)
        cls.day = cls.daily.date.max()

    def test_explicit_contract_and_input_guards(self):
        for invalid in ({}, {k:v for k,v in self.params.items() if k != 'ddof'},
                        {**self.params, 'rank_ties':'first'}, {**self.params, 'ddof':0},
                        {**self.params, 'beta_window':1}, {**self.params, 'current_pair_required':False}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                WeakMarketSignals(self.daily, invalid)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            WeakMarketSignals(pd.concat([self.daily, self.daily.iloc[[0]]]), self.params)
        with self.assertRaisesRegex(ValueError, 'benchmark'):
            WeakMarketSignals(self.daily[self.daily.symbol.ne('0050.TW')], self.params)
        broken = self.daily.copy(); broken.loc[0, 'close'] = np.nan
        with self.assertRaisesRegex(ValueError, 'Nonfinite'):
            WeakMarketSignals(broken, self.params)

    def test_signed_beta_and_sample_volatility_match_direct_math(self):
        features = self.signals.features.query("symbol == 'S00.TW'").set_index('date')
        pair = features[['stock_return', 'market_return']].dropna().tail(60)
        x, y = pair.market_return.to_numpy(), pair.stock_return.to_numpy()
        self.assertAlmostEqual(features.loc[self.day, 'beta'], np.cov(x, y, ddof=1)[0, 1] / np.var(x, ddof=1), places=12)
        self.assertAlmostEqual(features.loc[self.day, 'volatility'], np.std(y, ddof=1), places=14)
        self.assertEqual(features.loc[self.day, 'beta_obs_count'], 60)
        self.assertEqual(features.loc[self.day, 'beta_span_sessions'], 60)

    def test_lagged_ols_and_residual_momentum_match_independent_lstsq(self):
        f = self.signals.features.query("symbol == 'S02.TW'").set_index('date')
        pairs = f[['stock_return', 'market_return']].dropna()
        residuals = []
        coefficients = []
        for i in range(120, len(pairs)):
            history = pairs.iloc[i-120:i]
            design = np.c_[np.ones(120), history.market_return.to_numpy()]
            alpha, beta = np.linalg.lstsq(design, history.stock_return.to_numpy(), rcond=None)[0]
            row = pairs.iloc[i]
            residuals.append(row.stock_return-alpha-beta*row.market_return)
            coefficients.append((alpha, beta))
        result = f.loc[self.day]
        self.assertAlmostEqual(result.ols_alpha_lagged, coefficients[-1][0], places=13)
        self.assertAlmostEqual(result.ols_beta_lagged, coefficients[-1][1], places=12)
        self.assertAlmostEqual(result.one_step_residual, residuals[-1], places=13)
        expected = np.sum(residuals[-20:]) / (np.sqrt(20) * np.std(residuals[-20:], ddof=1))
        self.assertAlmostEqual(result.residual_momentum, expected, places=10)
        self.assertGreater(abs(result.residual_sum), 1e-6)
        self.assertLess(result.ols_window_end, self.day)
        self.assertEqual(result.ols_obs_count, 120)
        self.assertEqual(result.residual_obs_count, 20)

    def test_current_return_never_enters_current_fitted_coefficients(self):
        changed = self.daily.copy()
        current = changed.symbol.eq('S02.TW') & changed.date.eq(self.day)
        changed.loc[current, ['open', 'high', 'low', 'close']] *= 1.06
        fresh = WeakMarketSignals(changed, self.params)
        before = self.signals.features.query("symbol == 'S02.TW'").iloc[-1]
        after = fresh.features.query("symbol == 'S02.TW'").iloc[-1]
        self.assertEqual(before.ols_alpha_lagged, after.ols_alpha_lagged)
        self.assertEqual(before.ols_beta_lagged, after.ols_beta_lagged)
        self.assertNotEqual(before.one_step_residual, after.one_step_residual)

    def test_split_and_dividend_causal_price_equivalence(self):
        changed = self.daily.copy()
        for symbol in ['0050.TW', 'S02.TW']:
            indices = changed.index[changed.symbol.eq(symbol)].to_numpy()
            original = changed.loc[indices, 'close'].to_numpy(float)
            raw = original.copy()
            split = np.ones(len(indices)); dividend = np.zeros(len(indices))
            split[70] = 4.; dividend[110] = .65
            for i in range(1, len(raw)):
                raw[i] = ((original[i]/original[i-1])*raw[i-1]-dividend[i])/split[i]
            for column in ['open', 'high', 'low', 'close']:
                changed.loc[indices, column] = raw
            changed.loc[indices, 'split'] = split
            changed.loc[indices, 'dividend'] = dividend
        adjusted = WeakMarketSignals(changed, self.params)
        for symbol in ['0050.TW', 'S02.TW']:
            a = self.signals.features.query('symbol == @symbol').reset_index(drop=True)
            b = adjusted.features.query('symbol == @symbol').reset_index(drop=True)
            np.testing.assert_allclose(a.total_return_price, b.total_return_price, rtol=1e-12, atol=1e-12)
            np.testing.assert_allclose(a.stock_return, b.stock_return, rtol=1e-10, atol=1e-13, equal_nan=True)
        np.testing.assert_array_equal(self.signals.market_state.regime, adjusted.market_state.regime)

    def test_benchmark_suspension_has_no_zero_or_multiday_regression_observation(self):
        dates = sorted(self.daily.date.unique())
        suspended = dates[180:185]
        shortened = self.daily[~(self.daily.symbol.eq('0050.TW') & self.daily.date.isin(suspended))].copy()
        obj = WeakMarketSignals(shortened, self.params)
        market = obj.market_state.set_index('date')
        f = obj.features.query("symbol == 'S00.TW'").set_index('date')
        for date in [*suspended, dates[185]]:
            self.assertTrue(pd.isna(market.loc[date, 'market_return']))
            self.assertFalse(f.loc[date, 'current_pair_valid'])
            self.assertFalse(f.loc[date, 'common_valid'])
            self.assertTrue(pd.isna(f.loc[date, 'beta']))
        self.assertEqual(market.loc[suspended[0], 'regime'], 'UNKNOWN')
        self.assertTrue(f.loc[dates[186], 'current_pair_valid'])
        self.assertEqual(f.loc[dates[186], 'beta_obs_count'], 60)
        self.assertEqual(f.loc[dates[186], 'beta_span_sessions'], 66)
        # The first resumed fit still ends BEFORE the suspension. Its lag-age
        # exposes the gap; one day later the training span crosses that gap.
        self.assertEqual(f.loc[dates[186], 'ols_latest_age_sessions'], 7)
        self.assertGreater(f.loc[dates[187], 'ols_span_sessions'], 120)
        # A 20-market-session endpoint return may cover a suspension internally;
        # it is a cumulative return, not a fabricated daily regression pair.
        observed = market.total_return_price
        expected = observed.loc[dates[186]]/observed.loc[dates[166]]-1
        self.assertAlmostEqual(market.loc[dates[186], 'regime_return'], expected)

    def test_zero_volume_stock_quote_is_invalid_and_resume_needs_previous_quote(self):
        changed = self.daily.copy()
        dates = sorted(changed.date.unique())
        changed.loc[changed.symbol.eq('S00.TW') & changed.date.eq(dates[-2]), 'volume'] = 0.
        obj = WeakMarketSignals(changed, self.params)
        f = obj.features.query("symbol == 'S00.TW'").set_index('date')
        self.assertFalse(f.loc[dates[-2], 'quote_present'])
        self.assertFalse(f.loc[dates[-1], 'current_pair_valid'])
        result, meta = obj.callback('B')(self.day, ranked(self.day))
        self.assertEqual(meta['status'], 'WEAK_MARKET_RERANKED')
        self.assertEqual(result.loc['S00.TW', 'score'], 0.)
        self.assertTrue(result.loc['S00.TW', 'entry_ok'])
        self.assertIn('CONSECUTIVE', result.loc['S00.TW', 'v3_factor_reason'])

    def test_all_ranked_common_mask_and_d_percentile_of_composite(self):
        original = ranked(self.day)
        factors = self.signals.features[self.signals.features.date.eq(self.day)].set_index('symbol').loc[original.index]
        self.assertTrue(factors.common_valid.all())
        b = .5*((-factors.beta).rank(pct=True, method='average')+(-factors.volatility).rank(pct=True, method='average'))
        c = factors.residual_momentum.rank(pct=True, method='average')
        d = .5*(b.rank(pct=True, method='average')+c)
        self.assertFalse(original.entry_ok.all())
        for version, expected in [('B',b),('C',c),('D',d)]:
            result, meta = self.signals.callback(version)(self.day, original)
            self.assertEqual(meta['common_valid_count'], len(original))
            pd.testing.assert_series_equal(result.score, expected.rename('score'), check_names=False)
            pd.testing.assert_frame_equal(result[original.columns.difference(['score'])], original[original.columns.difference(['score'])])
        pd.testing.assert_series_equal(original.score, ranked(self.day).score)

    def test_normal_unknown_and_small_crosssection_fall_back_whole_day(self):
        original = ranked(self.day)
        # A always stays the baseline even when market is weak and features ready.
        result, meta = self.signals.callback('A')(self.day, original)
        pd.testing.assert_frame_equal(result[original.columns], original)
        self.assertEqual(meta['status'], 'BASELINE_A')
        obj = WeakMarketSignals(self.daily, parameters(minimum_common_valid=6))
        for version in 'BCD':
            result, meta = obj.callback(version)(self.day, original)
            pd.testing.assert_series_equal(result.score, original.score)
            self.assertEqual(meta['status'], 'BASELINE_FALLBACK_INSUFFICIENT_COMMON_VALID')
        unknown_day = self.daily.date.min()
        result, meta = self.signals.callback('C')(unknown_day, ranked(unknown_day))
        self.assertEqual(meta['status'], 'BASELINE_FALLBACK_MARKET_UNKNOWN')
        pd.testing.assert_series_equal(result.score, ranked(unknown_day).score)
        normal_days = self.signals.market_state.query("regime == 'NORMAL'").date
        self.assertFalse(normal_days.empty)
        normal = normal_days.iloc[-1]
        result, meta = self.signals.callback('D')(normal, ranked(normal))
        self.assertEqual(meta['status'], 'NORMAL_MARKET_BASELINE')
        pd.testing.assert_series_equal(result.score, ranked(normal).score)

    def test_ties_use_average_and_signed_negative_beta_is_preferred(self):
        altered = self.daily.copy()
        prototype = altered[altered.symbol.eq('S00.TW')].copy()
        keep = altered[~altered.symbol.isin(['S01.TW','S02.TW'])]
        altered = pd.concat([keep, prototype.assign(symbol='S01.TW'), prototype.assign(symbol='S02.TW')], ignore_index=True)
        obj = WeakMarketSignals(altered, self.params)
        original = ranked(self.day, ['S00.TW','S01.TW','S02.TW'])
        for version in 'BCD':
            out, _ = obj.callback(version)(self.day, original)
            np.testing.assert_allclose(out.score, 2/3, atol=1e-12)
        # Negative beta must remain signed, not abs(beta) or clipped at zero.
        indices = self.daily.index[self.daily.symbol.eq('S00.TW')]
        market = self.daily[self.daily.symbol.eq('0050.TW')].close.to_numpy()
        rets = market[1:]/market[:-1]-1
        anti = np.r_[100.,100.*np.cumprod(1-rets)]
        changed = self.daily.copy()
        for column in ('open','high','low','close'):changed.loc[indices,column] = anti
        obj = WeakMarketSignals(changed, self.params)
        row = obj.features.query("symbol == 'S00.TW'").iloc[-1]
        self.assertAlmostEqual(row.beta, -1., places=12)

    def test_constant_market_and_zero_residual_variance_are_not_certified_valid(self):
        changed = self.daily.copy()
        for symbol in changed.symbol.unique():
            mask = changed.symbol.eq(symbol)
            values = 100.*np.cumprod(np.repeat(.997, mask.sum()))
            for column in ('open','high','low','close'):changed.loc[mask,column] = values
        obj = WeakMarketSignals(changed, self.params)
        last = obj.features[obj.features.date.eq(self.day)]
        self.assertFalse(last.common_valid.any())
        _, meta = obj.callback('B')(self.day, ranked(self.day))
        self.assertEqual(meta['status'], 'BASELINE_FALLBACK_INSUFFICIENT_COMMON_VALID')
        # The benchmark's exact beta-one regression has near-zero residual std.
        benchmark = self.signals.features.query("symbol == '0050.TW'").iloc[-1]
        self.assertEqual(benchmark.invalid_reason, 'RESIDUAL_STD_TOO_SMALL')

    def test_physical_prefix_and_future_price_action_perturbations(self):
        cutoff = sorted(self.daily.date.unique())[190]
        prefix = WeakMarketSignals(self.daily[self.daily.date.le(cutoff)], self.params)
        expected = self.signals.features[self.signals.features.date.le(cutoff)].reset_index(drop=True)
        pd.testing.assert_frame_equal(prefix.features, expected, check_exact=True)
        pd.testing.assert_frame_equal(prefix.market_state, self.signals.market_state.query('date <= @cutoff').reset_index(drop=True), check_exact=True)
        changed = self.daily.copy(); future = changed.date.gt(cutoff)
        changed.loc[future,['open','high','low','close']] *= 3.
        changed.loc[future,'split'] = 1.1
        changed.loc[future,'dividend'] = 1.
        modified = WeakMarketSignals(changed, self.params)
        pd.testing.assert_frame_equal(modified.features[modified.features.date.le(cutoff)].reset_index(drop=True), expected, check_exact=True)
        for version in 'ABCD':
            a, am = prefix.callback(version)(cutoff, ranked(cutoff))
            b, bm = self.signals.callback(version)(cutoff, ranked(cutoff))
            pd.testing.assert_frame_equal(a,b,check_exact=True); self.assertEqual(am,bm)

    def test_export_copies_and_callback_guards(self):
        exported = self.signals.features
        exported.loc[:, 'beta'] = 999.
        self.assertFalse(self.signals.features.beta.eq(999.).any())
        market = self.signals.market_state
        market.loc[:, 'regime'] = 'WRONG'
        self.assertFalse(self.signals.market_state.regime.eq('WRONG').any())
        with self.assertRaisesRegex(ValueError,'version'):
            self.signals.callback('E')
        with self.assertRaisesRegex(ValueError,'unique'):
            self.signals.callback('B')(self.day,pd.concat([ranked(self.day),ranked(self.day).iloc[:1]]))
        with self.assertRaisesRegex(ValueError,'observation date'):
            self.signals.callback('B')(self.day,ranked(self.day-pd.Timedelta(days=1)))
        empty = ranked(self.day).iloc[:0]
        out, meta = self.signals.callback('D')(self.day,empty)
        self.assertTrue(out.empty)
        self.assertEqual(meta['common_valid_count'],0)


if __name__ == '__main__':
    unittest.main()
