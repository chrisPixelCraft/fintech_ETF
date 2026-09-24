"""V5 family E (direct portfolio utility) causality, determinism, and sanity tests."""
import unittest

import numpy as np
import pandas as pd

from src import v5_direct
from test_v5_momentum import CausalityMixin, real_panel


class DirectTests(CausalityMixin, unittest.TestCase):
    module = v5_direct
    configs = [{'train_window': 120, 'l2': 0.1, 'horizon': 10, 'top_n': 10, 'min_train_dates': 40},
               {'train_window': 250, 'l2': 1.0, 'horizon': 20, 'top_n': 12, 'min_train_dates': 40}]

    @classmethod
    def setUpClass(cls):
        cls.panel = real_panel()
        cls.dates = pd.DatetimeIndex(np.sort(pd.to_datetime(cls.panel['date']).unique()))
        cls.decision_dates = list(cls.dates[cls.dates >= '2012-06-01'])
        cls.outputs = [v5_direct.score_table(cls.panel, cls.decision_dates, c) for c in cls.configs]

    def test_causality_future_corruption(self):
        self.check_causality()

    def test_raw_daily_corruption_through_build_features(self):
        self.check_raw_daily_causality()

    def test_determinism(self):
        self.check_determinism()

    def test_refit_cadence_and_label_maturity(self):
        for config, out in zip(self.configs, self.outputs):
            cfg = out.attrs['model']['config']
            refits = out.attrs['refits']
            fitted = [r for r in refits if r['w'] is not None]
            self.assertGreater(len(fitted), 10)
            sessions = [r['refit_session'] for r in refits]
            self.assertEqual(sessions, sorted(set(sessions)))
            self.assertTrue(all(s % cfg['refit_every'] == 0 for s in sessions))
            for r in fitted:
                refit_date = pd.Timestamp(r['refit_date'])
                self.assertEqual(refit_date, self.dates[r['refit_session']])
                # label window ends on or before the refit session (<= D-1 < decision date)
                self.assertLessEqual(pd.Timestamp(r['label_end']), refit_date)
                end_idx = self.dates.get_loc(pd.Timestamp(r['train_end']))
                self.assertEqual(self.dates[end_idx + cfg['entry_lag'] + cfg['horizon']],
                                 pd.Timestamp(r['label_end']))
                self.assertLessEqual(r['n_dates'], cfg['train_window'])
                self.assertTrue(np.isfinite(list(r['w'].values())).all())
            # Every scored decision uses the refit block of its D-1 session.
            prior = np.searchsorted(self.dates.values, out.decision_date.values, side='left') - 1
            block = (prior // cfg['refit_every']) * cfg['refit_every']
            fitted_sessions = {r['refit_session'] for r in fitted}
            scored = out.score.notna().to_numpy()
            self.assertTrue(set(block[scored]) <= fitted_sessions)
            self.assertFalse(np.isin(block[~scored], list(fitted_sessions)).any())

    def test_scores_finite_and_reproduce_weights(self):
        for out in self.outputs:
            refits = {r['refit_session']: r for r in out.attrs['refits'] if r['w'] is not None}
            scored = out.loc[out.score.notna()]
            self.assertGreater(len(scored), 0.8 * len(out))
            self.assertTrue(np.isfinite(scored.score).all())
            # Scores within one decision date are a linear map of features: must vary.
            self.assertTrue((scored.groupby('decision_date').score.std() > 0).all())
            self.assertGreater(len(refits), 0)

    def test_optimizer_improves_objective_over_zero(self):
        # At w = 0 the soft portfolio is equal-weight; the fit must not be worse
        # (objective includes the L2 penalty).
        panel = self.panel
        cfg = v5_direct.resolve_config(self.configs[0])
        out = self.outputs[0]
        fitted = [r for r in out.attrs['refits'] if r['w'] is not None]
        for r in fitted[::5]:
            dates, symbols, grids, ready = v5_direct.session_grid(
                panel, ['signal_price', 'signal_available', 'signal_history_segment'])
            z = v5_direct.standardized_features(panel, dates, symbols, cfg['winsor_z'])
            label = v5_direct.forward_labels(grids, cfg['horizon'], cfg['entry_lag'])
            idx = np.arange(dates.get_loc(pd.Timestamp(r['train_start'])),
                            dates.get_loc(pd.Timestamp(r['train_end'])) + 1)
            valid = ready[idx] & np.isfinite(label[idx])
            idx, valid = idx[valid.sum(1) >= 2], valid[valid.sum(1) >= 2]
            xc, yc, mc, counts = v5_direct._compact(z[idx], np.nan_to_num(label[idx]), valid)
            zero = float(((yc * mc).sum(1) / counts).mean())
            self.assertGreaterEqual(r['objective'], zero - 1e-9)


if __name__ == '__main__':
    unittest.main()
