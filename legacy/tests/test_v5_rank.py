"""Causality, determinism and sanity tests for the V5 Family C rank classifier."""
import unittest

import numpy as np
import pandas as pd

from src import v5_rank
from test_v5_ensemble import CausalityMixin


class RankTests(CausalityMixin, unittest.TestCase):
    module = v5_rank
    configs = (dict(horizon=10, train_window=250, score_mode='p_agree'),
               dict(horizon=20, train_window=750, score_mode='p'))

    def test_sanity(self):
        for out in self.base.values():
            self.assertFalse(out.isna().any().any())
            self.assertTrue(out.p_topk.between(0, 1).all())
            self.assertTrue(out.agreement.isin([0, 1, 2, 3]).all())
            topn = out.attrs['config']['agree_topn']
            per_date = out.groupby('decision_date').agreement.sum()
            names = out.groupby('decision_date').size()
            np.testing.assert_array_equal(per_date, 3 * np.minimum(names, topn))
        agree = self.base[0]
        np.testing.assert_array_equal(agree.score, agree.p_topk * agree.agreement / 3)
        np.testing.assert_array_equal(self.base[1].score, self.base[1].p_topk)
        self.assertEqual(agree.attrs['model_identity'], v5_rank.MODEL_IDENTITY)

    def test_irls_matches_known_solution(self):
        rng = np.random.default_rng(0)
        x = np.column_stack([np.ones(4000), rng.normal(size=(4000, 2))])
        y = (rng.random(4000) < 1 / (1 + np.exp(-(x @ [-.5, 1., -2.])))).astype(float)
        beta = v5_rank.logistic_irls(x, y, l2=1e-6)
        np.testing.assert_allclose(beta, [-.5, 1., -2.], atol=.15)
        p = 1 / (1 + np.exp(-(x @ beta)))
        np.testing.assert_allclose(x.T @ (y - p), 1e-6 * np.r_[0, beta[1:]], atol=1e-6)


if __name__ == '__main__':
    unittest.main()
