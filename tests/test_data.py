import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from competition.data import load_market
from tests.synthetic import synthetic_market


class DataFloorTest(unittest.TestCase):
    def test_since_drops_earlier_rows_only(self):
        market = synthetic_market(n_days=60, n_symbols=21, official_from=40)
        start = market.calendar[20]
        cut = market.since(start)
        self.assertEqual(cut.calendar[0], start)
        self.assertEqual(len(cut.calendar), 40)
        for name in ('open', 'close', 'ret', 'valid', 'official_vwap', 'split', 'dividend', 'quality_flag'):
            pd.testing.assert_frame_equal(getattr(cut, name), getattr(market, name).loc[start:])
        pd.testing.assert_series_equal(cut.benchmark_ret, market.benchmark_ret.loc[start:])
        self.assertEqual(cut.asof(market.calendar[30]).close.index[0], start)
        with self.assertRaises(KeyError):
            cut.asof(market.calendar[10])

    def test_run_experiment_applies_the_floor(self):
        from research import run_experiment
        from research.run_experiment import ROOT
        base = json.loads((ROOT / 'research/configs/baselines/momentum_20d.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / 'bad.json'
            bad.write_text(json.dumps(dict(base, data={'begin': '2019-01-01'})))
            with self.assertRaises(ValueError):
                run_experiment.load_config(bad)
            good = Path(tmp) / 'good.json'
            good.write_text(json.dumps(dict(base, data={'start': '2020-01-01'},
                                            episodes={'start': '2021-01-01', 'offsets': ['month_start']})))
            with contextlib.redirect_stdout(io.StringIO()):
                manifest = run_experiment.main(['--config', str(good), '--split', 'dev', '--episodes', '1',
                                                '--out', str(Path(tmp) / 'run'),
                                                '--registry', str(Path(tmp) / 'registry.csv')])
            self.assertEqual((manifest['status'], manifest['data']['data_start']), ('COMPLETE', '2020-01-01'))
            self.assertEqual(run_experiment.market_for(dict(data={'start': '2020-01-01'})).calendar[0],
                             pd.Timestamp('2020-01-02'))


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
