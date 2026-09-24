import unittest

import numpy as np
import pandas as pd

from lgbm_strategy import dataset
from tests.synthetic import synthetic_market

H, LOOKBACK = 10, 250


class DatasetTest(unittest.TestCase):
    def setUp(self):
        self.market = synthetic_market(n_days=360, n_symbols=21)
        self.view = self.market.asof(self.market.calendar[-1])
        self.data = dataset.build_training_set(self.view, H, LOOKBACK, 'raw_return')

    def test_labels_are_matured_by_the_cutoff(self):
        dates = self.view.close.index
        last_row = max(self.data.train.date.max(), self.data.validation.date.max())
        label_end = dates[dates.get_loc(last_row) + H]
        self.assertLessEqual(label_end, self.view.date)
        self.assertEqual(pd.Timestamp(self.data.audit['latest_label_end']), self.view.date)

    def test_label_is_forward_signal_price_return(self):
        row = self.data.train.iloc[123]
        ret = self.market.ret[row.symbol]
        i = self.market.position(row.date)
        self.assertAlmostEqual(row.label, np.prod(1 + ret.iloc[i + 1:i + 1 + H]) - 1)

    def test_split_is_chronological_with_purge(self):
        train_dates = pd.DatetimeIndex(sorted(self.data.train.date.unique()))
        val_dates = pd.DatetimeIndex(sorted(self.data.validation.date.unique()))
        cal = self.view.close.index
        gap = cal.get_loc(val_dates[0]) - cal.get_loc(train_dates[-1])
        self.assertGreater(gap, H)                        # no training label reaches a validation date
        self.assertEqual(len(val_dates), round(LOOKBACK * dataset.VALIDATION_SHARE))
        self.assertEqual(self.data.audit['n_dates'], LOOKBACK - H)   # purged dates are in neither set

    def test_audit_fields(self):
        audit = self.data.audit
        for key in ('train_start', 'train_end', 'validation_start', 'validation_end', 'latest_label_end',
                    'n_dates', 'n_rows', 'n_symbols'):
            self.assertIn(key, audit)
        self.assertEqual(audit['n_rows'], len(self.data.train) + len(self.data.validation))
        self.assertEqual(audit['n_symbols'], 21)

    def test_rows_are_ready_and_finite(self):
        for frame in (self.data.train, self.data.validation):
            self.assertTrue(np.isfinite(frame.drop(columns=['date', 'symbol']).to_numpy(float)).all())

    def test_lookback_keeps_latest_dates(self):
        cal = self.view.close.index
        self.assertEqual(pd.Timestamp(self.data.audit['validation_end']), cal[-1 - H])
        self.assertEqual(pd.Timestamp(self.data.audit['train_start']), cal[-H - LOOKBACK])

    def test_relative_alpha_rows_and_audit(self):
        alpha = dataset.build_training_set(self.view, H, LOOKBACK, 'relative_alpha')
        for key in ('train_start', 'validation_end', 'latest_label_end', 'n_rows', 'n_symbols'):
            self.assertEqual(alpha.audit[key], self.data.audit[key])      # same rows, only the label differs
        audit = alpha.audit
        self.assertEqual((audit['target_mode'], self.data.audit['target_mode']), ('relative_alpha', 'raw_return'))
        self.assertLess(audit['alpha_cross_section_mean_error'], 1e-12)
        self.assertLess(abs(audit['alpha_target_mean']), 1e-12)
        self.assertAlmostEqual(audit['raw_target_std'], self.data.audit['raw_target_std'])
        raw = self.data.train.set_index(['date', 'symbol']).label
        shifted = alpha.train.set_index(['date', 'symbol']).label
        market = (raw - shifted).groupby(level='date')
        self.assertLess(market.std().max(), 1e-12)                # one market return per origin date
        self.assertGreater(market.mean().abs().max(), 0)

    def test_unknown_target_mode_raises(self):
        with self.assertRaises(ValueError):
            dataset.build_training_set(self.view, H, LOOKBACK, 'excess')

    def test_insufficient_history_raises(self):
        view = self.market.asof(self.market.calendar[120])
        with self.assertRaises(dataset.InsufficientHistoryError):
            dataset.build_training_set(view, H, LOOKBACK, 'raw_return')


if __name__ == '__main__':
    unittest.main()
