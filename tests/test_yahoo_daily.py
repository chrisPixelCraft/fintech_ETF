"""Yahoo snapshot integrity and nominal-share accounting contract tests."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from scripts.download_yahoo_daily import request_args, write_json
from src.yahoo_daily import (UNIVERSE, broad_observation_calendar, calendar_amendment,
                             load_daily, load_metadata, nominal_daily,
                             official_symbols, sha256, validate_frame)


def raw_frame():
    return pd.DataFrame({
        'Open': [50., 50., 52.], 'High': [51., 51., 53.],
        'Low': [49., 49., 51.], 'Close': [50., 50., 52.],
        'Adj Close': [45., 46., 48.], 'Volume': [1000., 2000., 2200.],
        'Dividends': [0., 2., 0.], 'Stock Splits': [0., 2., 0.],
        'Repaired?': [False, True, False],
    }, index=pd.date_range('2020-01-01', periods=3, tz='Asia/Taipei', name='Date'))


class YahooDailyContractTests(unittest.TestCase):
    def test_required_request_and_current_whitelist(self):
        request = request_args('2026-09-24')
        self.assertEqual(request['start'], '2009-01-01')
        self.assertEqual(request['interval'], '1d')
        self.assertFalse(request['auto_adjust'])
        self.assertTrue(request['actions'])
        self.assertTrue(request['repair'])
        self.assertTrue(request['keepna'])
        symbols = official_symbols()
        self.assertEqual(len(symbols), 150)
        self.assertIn('3718.TWO', symbols)
        self.assertNotIn('5371.TWO', symbols)

    def test_nominal_share_split_and_dividend_no_double_adjustment(self):
        raw = raw_frame()
        untouched = raw.copy(deep=True)
        daily = nominal_daily(raw, 'TEST.TW')
        self.assertEqual(daily.future_split_factor.tolist(), [2., 1., 1.])
        self.assertEqual(daily.close.tolist(), [100., 50., 52.])
        self.assertEqual(daily.volume.tolist(), [500., 2000., 2200.])
        self.assertEqual(daily.split.tolist(), [1., 2., 1.])
        self.assertEqual(daily.dividend.tolist(), [0., 4., 0.])
        # 1,000 old shares -> 2,000 new shares, same market value, +4,000 dividend.
        self.assertAlmostEqual(1000 * daily.close.iloc[0], 1000 * daily.split.iloc[1] * daily.close.iloc[1])
        self.assertAlmostEqual(daily.action_neutral_return.iloc[1], .04)
        self.assertEqual(daily.adj_close.tolist(), raw['Adj Close'].tolist())
        pd.testing.assert_frame_equal(raw, untouched)

    def test_appending_future_split_does_not_change_past_nominal_inputs(self):
        original = raw_frame()
        expected = nominal_daily(original, 'TEST.TW')
        revised = original.copy()
        revised[['Open', 'High', 'Low', 'Close', 'Adj Close', 'Dividends']] /= 3
        revised['Volume'] *= 3
        new = revised.iloc[[-1]].copy()
        new.index = pd.DatetimeIndex([pd.Timestamp('2020-01-04', tz='Asia/Taipei')], name='Date')
        new['Stock Splits'] = 3.
        revised = pd.concat([revised, new])
        actual = nominal_daily(revised, 'TEST.TW').iloc[:3]
        for column in ['open', 'high', 'low', 'close', 'volume', 'dividend', 'split']:
            np.testing.assert_allclose(actual[column], expected[column], rtol=1e-12)

    def test_reverse_split_and_invalid_source_preserved(self):
        raw = raw_frame()
        raw['Stock Splits'] = [0., .5, 0.]
        raw.loc[raw.index[2], 'Open'] = 0.
        daily = nominal_daily(raw, 'TEST.TW')
        self.assertEqual(daily.close.tolist(), [25., 50., 52.])
        self.assertEqual(daily.open.iloc[2], 0.)
        self.assertFalse(daily.valid_price.iloc[2])
        self.assertEqual(len(daily), len(raw))

    def test_duplicate_dates_fail_instead_of_deduplication(self):
        raw = raw_frame()
        raw = pd.concat([raw, raw.iloc[[-1]]])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            nominal_daily(raw, 'TEST.TW')

    def test_gaps_are_distinguished_from_leading_coverage(self):
        raw = raw_frame().iloc[[0, 2]]
        validation = validate_frame(raw, 'TEST.TW', ['2019-12-31', '2020-01-01', '2020-01-02', '2020-01-03', '2020-01-06'])
        self.assertEqual(validation['leading_missing_dates'], ['2019-12-31'])
        self.assertEqual(validation['internal_missing_dates'], ['2020-01-02'])
        self.assertEqual(validation['trailing_missing_dates'], ['2020-01-06'])

    def test_raw_and_derived_hashes_checked_and_benchmark_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            raw_frame().to_parquet(folder / 'raw.parquet')
            daily = pd.concat([nominal_daily(raw_frame(), '2330.TW'), nominal_daily(raw_frame(), '0050.TW')], ignore_index=True)
            daily.to_parquet(folder / 'nominal_daily.parquet', index=False)
            metadata = {'universe_sha256': sha256(UNIVERSE), 'artifact_sha256': {
                p.name: sha256(p) for p in folder.glob('*.parquet')},
                'derived_file': 'nominal_daily.parquet', 'calendar': ['2020-01-01']}
            write_json(folder / 'metadata.json', metadata)
            calendar_amendment(folder, write=True)
            loaded = load_daily(folder)
            self.assertEqual(set(loaded.symbol), {'2330.TW'})
            self.assertEqual(set(load_daily(folder, include_benchmark=True).symbol), {'2330.TW', '0050.TW'})
            (folder / 'raw.parquet').write_bytes(b'corruption')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                load_metadata(folder)

    def test_metadata_is_immutable(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'state.json'
            write_json(path, {'value': 1})
            write_json(path, {'value': 1})
            with self.assertRaisesRegex(ValueError, 'Immutable'):
                write_json(path, {'value': 2})
            self.assertEqual(json.loads(path.read_text()), {'value': 1})

    def test_etf_suspension_does_not_remove_broad_market_sessions(self):
        records = []
        symbols = official_symbols()[:20]
        for date, count in [('2024-01-15', 20), ('2025-06-11', 20),
                            ('2025-06-12', 19), ('2025-06-14', 20)]:
            for symbol in symbols[:count]:
                records.append(dict(date=date, symbol=symbol, open=10., high=11.,
                                    low=9., close=10., volume=1000., valid_price=True))
        # A zero-volume twentieth name must not turn an isolated date into a session.
        records.append(dict(date='2025-06-12', symbol=symbols[-1], open=10., high=11.,
                            low=9., close=10., volume=0., valid_price=True))
        calendar, counts = broad_observation_calendar(pd.DataFrame(records), ['2025-06-10'])
        self.assertEqual(calendar, ['2024-01-15', '2025-06-10', '2025-06-11'])
        self.assertEqual(counts['2025-06-11'], 20)


if __name__ == '__main__':
    unittest.main()
