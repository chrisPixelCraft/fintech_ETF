import unittest

import numpy as np

from competition.data import load_market


class RealDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = load_market()

    def test_universe_and_rename(self):
        m = self.m
        self.assertEqual(len(m.symbols), 150)
        self.assertNotIn('0050.TW', m.symbols)
        self.assertNotIn('5371.TWO', m.symbols)
        first = m.close['3718.TWO'].first_valid_index()
        self.assertEqual(str(first.date()), '2024-01-02')      # 5371 history backfilled
        self.assertTrue(np.isfinite(m.close.loc['2026-09-03', '3718.TWO']))

    def test_official_prices_only_where_published(self):
        v = self.m.official_vwap
        self.assertEqual(int(v.loc[:'2023-12-31'].notna().sum().sum()), 0)
        self.assertGreater(v.loc['2025-01-01':'2025-12-31'].notna().mean().mean(), .9)
        ratio = (v / self.m.close).stack()
        self.assertLess(float((ratio - 1).abs().median()), .01)

    def test_flagged_rows_carry_no_return(self):
        m = self.m
        self.assertFalse(m.ret.where(m.quality_flag).notna().any().any())
        self.assertEqual(m.split.isna().sum().sum() + m.dividend.isna().sum().sum(), 0)


if __name__ == '__main__':
    unittest.main()
