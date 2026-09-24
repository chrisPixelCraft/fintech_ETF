import unittest

import numpy as np
import pandas as pd

from competition import backtest, execution
from competition.episodes import Episode
from competition.ledger import Book, settle
from competition.rules import load_rules
from tests.synthetic import synthetic_market

RULES = load_rules()
SYMS = [f'S{i:03d}.TW' for i in range(25)]
DAY = '2020-01-02'


def series(value, syms=SYMS):
    return pd.Series(value, index=syms, dtype=object if isinstance(value, str) else float)


def book_with(shares=350000., price=100., cash=None):
    holdings = {s: shares for s in SYMS}
    cash = 1e9 - 25 * shares * price if cash is None else cash
    return Book(holdings=holdings, cash=cash, marks={s: price for s in SYMS})


def run(book, orders, fill=101., close=100., split=1., dividend=0., sizing=100.):
    src = series('official_vwap')
    return settle(book, DAY, orders, series(fill), src, series(close), series(split), series(dividend),
                  {s: sizing for s in orders}, RULES, set(SYMS))


class LedgerTest(unittest.TestCase):
    def test_fees_tax_and_nav(self):
        book = book_with()
        new, rec, trades, issues = run(book, {SYMS[0]: -10000, SYMS[1]: 5000}, fill=101., close=102.)
        sell, buy = 10000 * 101., 5000 * 101.
        cash = book.cash + sell * (1 - .001425 - .003) - buy * (1 + .001425)
        self.assertAlmostEqual(new.cash, cash, places=4)
        self.assertAlmostEqual(rec['fees'], (sell + buy) * .001425, places=4)
        self.assertAlmostEqual(rec['taxes'], sell * .003, places=4)
        self.assertAlmostEqual(rec['nav'], cash + sum(q * 102. for q in new.holdings.values()), places=4)
        self.assertEqual(new.holdings[SYMS[0]], 340000.)
        self.assertEqual([t['symbol'] for t in trades], [SYMS[0], SYMS[1]])   # sells fill first
        self.assertFalse(rec['warning'])
        self.assertEqual(rec['fills_official'], 2)

    def test_violation_rolls_back_the_day(self):
        book = book_with()
        orders = {s: -350000 for s in SYMS[:10]}          # 15 names left and >25% cash
        new, rec, trades, issues = run(book, orders)
        self.assertTrue(rec['warning'])
        self.assertIn('HOLDING_COUNT', rec['warning_reasons'])
        self.assertEqual(new.holdings, book.holdings)
        self.assertEqual(new.cash, book.cash)
        self.assertEqual((trades, new.warnings), ([], 1))
        self.assertEqual(rec['rejected_fills'], 10)

    def test_corporate_actions_before_fills(self):
        book = book_with()
        new, rec, _, _ = run(book, {}, close=50., split=2., dividend=1.5)
        self.assertEqual(new.holdings[SYMS[0]], 700000.)
        self.assertAlmostEqual(new.receivable, 25 * 350000 * 1.5)
        self.assertAlmostEqual(rec['nav'], book.cash + 25 * 700000 * 50.)

    def test_missing_price_leaves_order_unfilled(self):
        book = book_with()
        src = series('missing')
        fill = series(101.)
        fill[SYMS[0]] = np.nan
        new, rec, trades, issues = settle(book, DAY, {SYMS[0]: 1000}, fill, src, series(100.), series(1.),
                                          series(0.), {SYMS[0]: 100.}, RULES, set(SYMS))
        self.assertEqual((rec['unfilled'], trades), (1, []))
        self.assertEqual(new.holdings, book.holdings)

    def test_passive_cap_drift_grace(self):
        book = book_with()
        close = series(100.)
        close[SYMS[0]] = 400.                             # price-only drift above 10%
        warned = []
        for day in range(7):
            book, rec, _, _ = settle(book, DAY, {}, series(np.nan), series(''), close, series(1.),
                                     series(0.), {}, RULES, set(SYMS))
            warned.append(rec['warning'])
        self.assertEqual(warned, [False] * 5 + [True, True])

    def test_active_cap_breach_warns_immediately(self):
        book = book_with()
        _, rec, _, _ = run(book, {SYMS[0]: 700000}, fill=100., close=100.)
        self.assertTrue(rec['warning'])
        self.assertIn('ACTIVE_CAP:' + SYMS[0], rec['warning_reasons'])


class ExecutionTest(unittest.TestCase):
    def setUp(self):
        self.m = synthetic_market(n_days=40, n_symbols=25, official_from=20)

    def test_source_selection(self):
        syms = self.m.symbols[:3]
        early, late = self.m.calendar[5], self.m.calendar[30]
        p, s = execution.fill_prices(self.m, early, syms, 'auto', 'hlc3')
        self.assertTrue((s == 'proxy_hlc3').all())
        expected = ((self.m.high + self.m.low + self.m.close) / 3).loc[early, syms]
        np.testing.assert_allclose(p.to_numpy(), expected.to_numpy())
        p, s = execution.fill_prices(self.m, late, syms, 'auto')
        self.assertTrue((s == 'official_vwap').all())
        np.testing.assert_allclose(p.to_numpy(), self.m.official_vwap.loc[late, syms].to_numpy())
        p, s = execution.fill_prices(self.m, late, syms, 'proxy', 'open')
        np.testing.assert_allclose(p.to_numpy(), self.m.open.loc[late, syms].to_numpy())
        p, s = execution.fill_prices(self.m, early, syms, 'official')
        self.assertTrue(p.isna().all() and (s == 'missing').all())

    def test_calibration_prefers_hlc3_on_hlc3_like_vwap(self):
        table = execution.calibrate(self.m, start=self.m.calendar[20])
        self.assertEqual(table.median_abs.idxmin(), 'hlc3')


class EqualWeight:
    """Test strategy: equal weight over the first 25 names each day."""

    def decide(self, view, state):
        return {s: .88 / 25 for s in view.symbols[:25]}


class BacktestVerifyTest(unittest.TestCase):
    def test_independent_recompute_matches(self):
        m = synthetic_market(n_days=60, n_symbols=30, official_from=45)
        episode = Episode('synthetic', 'dev', m.calendar[29], tuple(m.calendar[30:54]))
        result = backtest.run_episode(m, episode, EqualWeight(), RULES)
        self.assertEqual(backtest.verify_episode(m, episode, result, RULES), [])
        s = result['summary']
        self.assertTrue(s['complete'])
        self.assertEqual(s['warning_days'], 0)
        self.assertEqual(set(s['fill_sources']), {'proxy_hlc3', 'official_vwap'})
        self.assertAlmostEqual(s['terminal_nav'], result['ledger'].nav.iloc[-1])
        tampered = dict(result, trades=result['trades'].assign(fee=result['trades'].fee * 1.01))
        self.assertTrue(any('fee/tax' in p for p in backtest.verify_episode(m, episode, tampered, RULES)))


if __name__ == '__main__':
    unittest.main()
