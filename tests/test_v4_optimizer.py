import unittest
import numpy as np
import pandas as pd
from src.v4_portfolio_optimizer import optimize_portfolio, portfolio_utility


class OptimizerTests(unittest.TestCase):
    def setUp(self):
        self.symbols = [f'{i:04d}.TW' for i in range(30)]
        self.mu = pd.Series(np.linspace(.01, .15, 30), index=self.symbols)
        self.prices = pd.Series(100., index=self.symbols)

    def test_fee_cash_lot_and_cap_feasibility(self):
        for method in ('equal', 'score', 'continuous', 'de'):
            plan = optimize_portfolio(self.mu, self.prices, 1e9, method=method,
                                      de_maxiter=1, de_popsize=2)
            self.assertEqual(plan.status, 'PASS_MEASURED')
            self.assertEqual((plan.target_shares > 0).sum(), 22)
            self.assertTrue((plan.orders % 1000 == 0).all())
            notional = (plan.target_shares * self.prices).sum()
            self.assertAlmostEqual(plan.estimated_cash, 1e9 - notional * 1.001425, places=5)
            self.assertGreaterEqual(plan.estimated_cash, 0)
            self.assertLess(plan.estimated_cash / (1e9 - notional * .001425), .25)
            self.assertTrue((plan.target_shares * self.prices.reindex(plan.target_shares.index) / (1e9 - notional * .001425) <= .1).all())
            reconstructed = np.floor(plan.target_weights * 1e9 / self.prices / 1000) * 1000
            pd.testing.assert_series_equal(reconstructed.dropna().astype('int64'), plan.target_shares, check_names=False)

    def test_deterministic_de(self):
        a = optimize_portfolio(self.mu, self.prices, 1e9, method='de', seed=7, de_maxiter=1)
        b = optimize_portfolio(self.mu, self.prices, 1e9, method='de', seed=7, de_maxiter=1)
        pd.testing.assert_series_equal(a.target_shares, b.target_shares)

    def test_zero_rotation_preserves_incumbents(self):
        old = pd.Series(400000, index=self.symbols[:22])
        plan = optimize_portfolio(self.mu, self.prices, 1e9, old, max_replacements=0)
        pd.testing.assert_series_equal(plan.target_shares, old.astype('int64'), check_names=False)
        self.assertTrue(plan.orders.empty)

    def test_replacement_requires_cost_and_margin(self):
        old = pd.Series(400000, index=self.symbols[:22])
        mu = pd.Series(.02, index=self.symbols)
        mu.iloc[-1] = .024
        plan = optimize_portfolio(mu, self.prices, 1e9, old, replacement_margin=0)
        self.assertNotIn(self.symbols[-1], plan.target_shares.index)
        mu.iloc[-1] = .10
        plan = optimize_portfolio(mu, self.prices, 1e9, old, replacement_margin=0)
        self.assertIn(self.symbols[-1], plan.target_shares.index)
        self.assertEqual((plan.orders < 0).sum(), 1)

    def test_fees_are_nonvacuous_in_utility(self):
        self.assertAlmostEqual(portfolio_utility(np.array([.8]), np.array([0.]),
            np.zeros((1, 1)), np.array([0.])), -.8*.001425)
        self.assertAlmostEqual(portfolio_utility(np.array([0.]), np.array([0.]),
            np.zeros((1, 1)), np.array([.8])), -.8*.004425)

    def test_missing_current_prices_raise(self):
        old = pd.Series({self.symbols[0]:1000})
        with self.assertRaises(ValueError):
            optimize_portfolio(self.mu, self.prices.drop(self.symbols[0]), 1e9, old)

    def test_receivables_not_spendable(self):
        plan = optimize_portfolio(self.mu, self.prices, 1e9, current_cash=100000)
        self.assertEqual(plan.status, 'NO_VALID_PLAN')

    def test_special_cap_and_forced_repair(self):
        symbols = ['2330.TW'] + self.symbols[:21]
        old = pd.Series(350000, index=symbols)
        old.loc['2330.TW'] = 2600000
        mu = self.mu.reindex(symbols).fillna(.4)
        prices = self.prices.reindex(symbols).fillna(100.)
        plan = optimize_portfolio(mu, prices, 1e9, old, current_cash=5000000,
                                  max_replacements=0)
        fees = -(plan.orders.clip(upper=0) * prices).sum() * .004425
        self.assertLessEqual(plan.target_shares['2330.TW'] * 100 / (1e9-fees), .25)
        self.assertEqual(plan.status, 'PASS_MEASURED')
        self.assertTrue((plan.orders <= 0).all())

    def test_negative_holdings_and_penalties_rejected(self):
        with self.assertRaises(ValueError):
            optimize_portfolio(self.mu, self.prices, 1e9, pd.Series({self.symbols[0]:-1000}))
        with self.assertRaises(ValueError):
            optimize_portfolio(self.mu, self.prices, 1e9, replacement_margin=-1)

    def test_cash_neighborhood_changes_deployment_beyond_buffer(self):
        low=optimize_portfolio(self.mu,self.prices,1e9,cash_target=.05)
        high=optimize_portfolio(self.mu,self.prices,1e9,cash_target=.12)
        self.assertGreater(low.target_shares.sum(),high.target_shares.sum())
        self.assertGreater(high.estimated_cash,low.estimated_cash)
        self.assertEqual(low.status,high.status)


class AllocationWorkTests(unittest.TestCase):
    def test_fixed_incumbents_skip_solvers_but_preserve_cap_repair(self):
        from unittest.mock import patch
        symbols = [f'{i:04d}.TW' for i in range(20)]
        prices = pd.Series(100., index=symbols)
        expected = pd.Series(np.linspace(.01, .08, 20), index=symbols)
        old = pd.Series(400000, index=symbols)
        old.iloc[0] = 1300000  # Forced cap repair must remain active.
        options = dict(current_shares=old, current_cash=110000000., target_count=20, max_replacements=0)
        reference = optimize_portfolio(expected, prices, 1e9, method='score', **options)
        self.assertLess(reference.target_shares.iloc[0], old.iloc[0])
        for method, solver in [('continuous', 'minimize'), ('de', 'differential_evolution')]:
            with patch('src.v4_portfolio_optimizer.' + solver, side_effect=AssertionError('dead solve')):
                result = optimize_portfolio(expected, prices, 1e9, method=method, **options)
            for name in ('target_weights', 'target_shares', 'orders'):
                pd.testing.assert_series_equal(getattr(result, name), getattr(reference, name), check_exact=True)
            self.assertEqual((result.estimated_cash, result.status, result.utility),
                             (reference.estimated_cash, reference.status, reference.utility))

    def test_deployment_and_explicit_rebalance_still_invoke_solver(self):
        from unittest.mock import patch
        symbols = [f'{i:04d}.TW' for i in range(20)]
        prices = pd.Series(100., index=symbols)
        expected = pd.Series(np.linspace(.01, .08, 20), index=symbols)
        for method, solver in [('continuous', 'minimize'), ('de', 'differential_evolution')]:
            for old, rebalance in [(None, False), (pd.Series(400000, index=symbols), True)]:
                with patch('src.v4_portfolio_optimizer.' + solver, side_effect=RuntimeError('solver required')):
                    with self.assertRaisesRegex(RuntimeError, 'solver required'):
                        optimize_portfolio(expected, prices, 1e9, old, method=method,
                                           target_count=20, max_replacements=0, rebalance=rebalance)

    def test_csv_roundtrip_preserves_official_target_formula(self):
        import io
        rng = np.random.default_rng(21)
        for _ in range(30):
            symbols = [f'{i:04d}.TW' for i in range(30)]
            prices = pd.Series(rng.uniform(7, 1800, 30), index=symbols)
            expected = pd.Series(rng.normal(0, .04, 30), index=symbols)
            nav = float(rng.uniform(8e8, 1.2e9))
            plan = optimize_portfolio(expected, prices, nav, method='score')
            table = pd.DataFrame(dict(weight=plan.target_weights, shares=plan.target_shares,
                                      price=prices.reindex(plan.target_shares.index), nav=nav))
            restored = pd.read_csv(io.StringIO(table.to_csv(index=False)))
            recovered = np.floor(restored.weight * restored.nav / restored.price / 1000) * 1000
            np.testing.assert_array_equal(recovered, restored.shares)
            self.assertTrue(restored.weight.le(.1).all())
