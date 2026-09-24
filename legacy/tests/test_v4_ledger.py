"""Independent accounting identities, lot constraints, and rollback tests."""
import unittest
import numpy as np
import pandas as pd
from src.strategy_24d import build_config
from src.v4_baseline import run_episode
from tests.test_strategy_24d import fixture
from tests.test_v4_episode import official_fixture


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.daily, self.universe, self.dates = fixture()
        self.config = build_config()

    def run_ledger(self, daily=None, table=None):
        daily = self.daily if daily is None else daily
        return run_episode(daily, self.universe, self.config, self.dates,
                           official_fixture(daily) if table is None else table)

    def test_fees_lots_no_shorts_no_day_trades_cash_and_nav(self):
        result = self.run_ledger()
        trades, eq, held, orders = (result[k] for k in ['trades','equity','holdings','orders'])
        np.testing.assert_allclose(trades.fee, trades.notional * .001425)
        np.testing.assert_allclose(trades.tax, trades.notional * .003 * trades.shares.lt(0))
        self.assertTrue(orders.shares.mod(1000).eq(0).all())
        self.assertTrue(held.shares.ge(0).all())
        self.assertFalse(trades.duplicated(['date','symbol']).any())
        self.assertTrue(eq.cash.ge(0).all())
        flow = (trades.shares * trades.price + trades.fee + trades.tax).groupby(trades.date).sum()
        expected_cash = 1e9 - flow.reindex(eq.date, fill_value=0).cumsum()
        np.testing.assert_allclose(eq.cash, expected_cash, atol=1e-6)
        market = (held.shares * held.close).groupby(held.date).sum().reindex(eq.date, fill_value=0)
        np.testing.assert_allclose(eq.nav, eq.cash.to_numpy() + market.to_numpy() + eq.terminal_dividend_credit.to_numpy())
        target = np.floor(orders.target_weight * orders.signal_nav / orders.sizing_price / 1000)*1000
        np.testing.assert_allclose(target, orders.target_shares)
        self.assertTrue(eq.holdings.between(20,30).all())
        self.assertTrue(held.weight.le(.1 + 1e-10).all())

    def test_excess_execution_cost_rolls_back_fixed_orders(self):
        table = official_fixture(self.daily)
        mask = table.date.eq(self.dates[0])
        table.loc[mask, 'trading_value'] *= 2
        table.loc[mask, 'average_execution_price'] *= 2
        result = self.run_ledger(table=table)
        self.assertTrue(result['compliance_daily'].iloc[0].rolled_back)
        self.assertEqual(result['equity'].cash.iloc[0], 1e9)
        self.assertEqual(result['equity'].costs.iloc[0], 0)
        self.assertGreater(len(result['rejected_trades']), 0)
        self.assertGreater(result['metrics']['cash_violation_days'], 0)

    def test_dividend_credit_and_split_entitlements(self):
        base = self.run_ledger()
        symbol = base['holdings'].symbol.iloc[0]
        day = self.dates[3]
        daily = self.daily.copy()
        mask = daily.symbol.eq(symbol)
        daily.loc[mask & daily.date.ge(day), ['open','high','low','close']] /= 2
        daily.loc[mask & daily.date.eq(day), ['split','dividend']] = [2, 1]
        result = self.run_ledger(daily=daily)
        before = result['holdings'].loc[result['holdings'].symbol.eq(symbol) & pd.to_datetime(result['holdings'].date).lt(day)]
        quantity = before.shares.iloc[-1]
        eq = result['equity']
        self.assertAlmostEqual(eq.terminal_dividend_credit.sum(), quantity)
        self.assertAlmostEqual(eq.dividend_receivable.iloc[3], quantity)
        # Receivables do not enter investable cash before the terminal credit.
        np.testing.assert_allclose(eq.economic_nav, eq.cash + (result['holdings'].shares*result['holdings'].close).groupby(result['holdings'].date).sum().reindex(eq.date).to_numpy() + eq.dividend_receivable + eq.terminal_dividend_credit)

    def test_sell_commission_and_tax_are_debited(self):
        from src.strategy_24d import build_features
        from src.v4_ledger import run_ledger
        daily = self.daily.copy()
        daily[['open','close','high','low']] = 100.
        features = build_features(daily, self.config)
        prior = daily.loc[daily.date.lt(self.dates[0]), 'date'].max()
        calendar = list(self.dates.insert(0, prior))
        window = features.loc[features.date.isin(calendar)]
        calls = []
        def planner(ranked, holdings, cash, nav, config, *args):
            calls.append(1)
            if len(calls) == 1:
                return {f'{1000+i}.TW': 400000 for i in range(20)}, 'TEST_BUY', []
            if len(calls) == 2:
                return {'1000.TW': -400000, '1020.TW': 400000}, 'TEST_ROTATE', []
            return {}, 'TEST_HOLD', []
        prices = {(d, s): 100. for d in calendar for s in window.symbol.unique()}
        volumes = {key: 10000000. for key in prices}
        config = dict(self.config, start=str(self.dates[0].date()), end=str(self.dates[-1].date()))
        result = run_ledger(window, self.universe, config, calendar, planner, prices, volumes, 'official_average')
        sells = result['trades'].loc[result['trades'].shares.lt(0)]
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells.fee.iloc[0], 57000.)
        self.assertEqual(sells.tax.iloc[0], 120000.)
        self.assertEqual(result['equity'].cash.iloc[1], 198626000.)
        self.assertEqual(result['equity'].costs.iloc[1], 234000.)
