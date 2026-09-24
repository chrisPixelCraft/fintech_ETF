import unittest
import numpy as np
import pandas as pd
from src.v4_direct_optimizer import fit_direct


class DirectTests(unittest.TestCase):
    def test_bounded_deterministic_development_fit(self):
        calls = []
        def objective(c):
            calls.append(c.copy())
            return c[1] - c[0] * .2
        a = fit_direct(('R5','R20'), objective, max_candidates=7, seed=9)
        b = fit_direct(('R5','R20'), objective, max_candidates=7, seed=9)
        self.assertEqual(len(calls), 14)
        self.assertEqual(a, b)
        self.assertGreater(a.coefficients[1], a.coefficients[0])
        score = a.score(pd.DataFrame({'R5':[3,1,2], 'R20':[1,2,3]}))
        self.assertGreater(score.iloc[2], score.iloc[0])

    def test_validation_fit_forbidden(self):
        with self.assertRaisesRegex(ValueError, 'development'):
            fit_direct(('R5',), lambda c: 1., split='validation')

    def test_nonfinite_evidence_rejected(self):
        with self.assertRaisesRegex(ValueError, 'invalid utility'):
            fit_direct(('R5',), lambda c: np.nan)
