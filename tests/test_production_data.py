"""Production data layer and the daily entry point (no network: fixtures are trimmed real responses)."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from competition.data import load_market
from competition.rules import load_rules
from production import active_share, cold_start, etf_holdings, market_data, run_daily
from production.state import SettledBook, symbol_to_ticker

FIXTURES = Path(__file__).parent / 'fixtures'
UNIVERSE = set(pd.read_csv(Path(__file__).parents[1] / 'data/reference/universe_competition_20260731.csv').yahoo_symbol)


class OfficialQuotesTest(unittest.TestCase):
    def test_twse_and_tpex_payloads(self):
        twse = market_data.parse_official_day(json.loads((FIXTURES / 'TWSE_20260924.json').read_text()), 'TWSE',
                                              '2026-09-24', UNIVERSE)
        tpex = market_data.parse_official_day(json.loads((FIXTURES / 'TPEx_20260924.json').read_text()), 'TPEx',
                                              '2026-09-24', UNIVERSE)
        self.assertIn('2330.TW', set(twse.symbol))
        self.assertTrue(set(tpex.symbol) <= {s for s in UNIVERSE if s.endswith('.TWO')} and len(tpex))
        row = twse.set_index('symbol').loc['2330.TW']
        self.assertAlmostEqual(row.execution_vwap, row.trading_value / row.volume)
        self.assertTrue(row.low <= row.execution_vwap <= row.high)
        self.assertNotIn('0050.TW', set(twse.symbol))                      # not in the 150-name universe

    def test_wrong_date_is_rejected(self):
        payload = json.loads((FIXTURES / 'TWSE_20260924.json').read_text())
        with self.assertRaises(ValueError):
            market_data.parse_official_day(payload, 'TWSE', '2026-09-23', UNIVERSE)


class EtfTopTenTest(unittest.TestCase):
    def test_moneydj_page(self):
        frame = etf_holdings.parse_top10((FIXTURES / 'moneydj_00981A.html').read_text(), '00981A', 'url')
        self.assertEqual(len(frame), 10)
        self.assertEqual(frame.as_of.unique().tolist(), ['2026-09-24'])
        self.assertEqual(frame.ticker.iloc[0], '2330')
        self.assertTrue(frame.weight.between(0, .25).all() and .3 < frame.weight.sum() < 1)
        self.assertFalse(frame.name.str.endswith('*').any())
        with tempfile.TemporaryDirectory() as tmp:
            frame.to_csv(Path(tmp) / 'top.csv', index=False)
            loaded = active_share.load_etf_holdings(Path(tmp) / 'top.csv')['00981A']
        self.assertAlmostEqual(sum(loaded.weights.values()), frame.weight.sum())

    def test_page_without_table_raises(self):
        with self.assertRaises(ValueError):
            etf_holdings.parse_top10('<html>資料日期：2026/09/24</html>', '00981A', 'url')


class MarketDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = load_market()
        cls.prev = cls.market.calendar[-1]
        close = cls.market.close.loc[cls.prev]
        cls.official = pd.DataFrame(dict(symbol=close.index, close=close.to_numpy(), open=close.to_numpy(),
                                         high=close.to_numpy(), low=close.to_numpy(), volume=1e6,
                                         execution_vwap=close.to_numpy()))

    def test_cross_check(self):
        self.assertEqual(market_data.cross_check(self.market, self.prev, self.official)['problems'], [])
        off = self.official.copy()
        off.loc[:6, 'close'] *= 1.02
        report = market_data.cross_check(self.market, self.prev, off)
        self.assertEqual(report['problems'], ['CLOSE_DISAGREEMENT:7'])
        self.assertEqual(market_data.cross_check(self.market, self.prev, off.iloc[:0])['problems'],
                         ['OFFICIAL_T-1_MISSING'])

    def test_exchange_close_is_the_sizing_price(self):
        off = self.official.copy()
        off.loc[0, 'close'] = 123.
        patched = market_data.with_exchange_close(self.market, self.prev, off)
        self.assertEqual(patched.close.loc[self.prev, off.symbol[0]], 123.)
        pd.testing.assert_frame_equal(patched.ret, self.market.ret)       # returns are untouched

    def test_official_row_backup(self):
        nxt = self.prev + pd.offsets.BDay()
        grown = market_data.append_official_row(self.market, nxt, self.official)
        self.assertEqual(grown.calendar[-1], nxt)
        self.assertTrue(grown.ret.loc[nxt].isna().all())                     # action-neutral return unknown
        np.testing.assert_allclose(grown.close.loc[nxt].dropna().to_numpy(),
                                   self.official.set_index('symbol').close.reindex(grown.symbols).dropna().to_numpy())
        self.assertIs(market_data.append_official_row(grown, nxt, self.official), grown)


class ColdStartTest(unittest.TestCase):
    def test_choose_repairs_active_share(self):
        market = load_market()
        as_of = market.calendar[-1]
        plain, _ = cold_start.choose(market, as_of, None)
        etf = active_share.EtfTop10('00980A', str(as_of.date()),
                                    {symbol_to_ticker(s): .06 for s in plain[:10]})
        names, dropped = cold_start.choose(market, as_of, {'00980A': etf})
        self.assertEqual(len(names), 25)
        self.assertTrue(dropped)
        weights = {symbol_to_ticker(s): .95 / 25 for s in names}
        self.assertGreaterEqual(active_share.active_share(weights, etf), active_share.INTERNAL_MIN)

    def test_frozen_list_is_valid(self):
        frozen = json.loads(cold_start.PATH.read_text())
        self.assertEqual(len(frozen['symbols']), 25)
        self.assertEqual((frozen['check']['mode'], frozen['check']['active_share'], frozen['check']['errors']),
                         ('COLD_START_FALLBACK', 'PASS', []))


class RunDailyOfflineTest(unittest.TestCase):
    def test_two_consecutive_days(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = run_daily.run('2026-09-01', offline=True, runs_dir=tmp)
            second = run_daily.run('2026-09-02', offline=True, runs_dir=tmp)
            for audit in (first, second):
                self.assertEqual((audit['status'], audit['mode'], audit['validation_errors']),
                                 (run_daily.READY, 'NORMAL', []))
            self.assertEqual(first['orders'], 25)
            book = SettledBook.load(Path(tmp) / 'state' / 'book.json')
            self.assertEqual(book.date, '2026-09-01')                       # day 1 settled before day 2
            self.assertEqual(len(book.holdings), 25)
            self.assertTrue((Path(tmp) / '2026-09-02' / 'audit.md').exists())
            self.assertEqual((Path(tmp) / '2026-09-02' / 'status.txt').read_text().strip(), run_daily.READY)

    def test_unreadable_holdings_fall_back_and_still_submit(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = run_daily.run('2026-09-01', holdings=str(Path(tmp) / 'missing.json'), offline=True,
                                  runs_dir=tmp)
            self.assertEqual((audit['status'], audit['mode']), (run_daily.READY, 'COLD_START_FALLBACK'))
            self.assertTrue(any('OFFICIAL_HOLDINGS_UNREADABLE' in r for r in audit['reasons']))


if __name__ == '__main__':
    unittest.main()
