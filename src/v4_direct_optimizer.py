"""Fit a bounded linear allocation score to development portfolio utility."""
from dataclasses import dataclass
import numpy as np
import pandas as pd

DEFAULT_FEATURES = ('R5', 'R20', 'R60', 'price_ema20', 'volume_ratio', 'vol20')


@dataclass(frozen=True)
class DirectModel:
    feature_names: tuple
    coefficients: tuple
    seed: int
    evaluations: tuple
    fit_split: str = 'development'

    def score(self, features):
        """Cross-sectional ranks require only the supplied decision-time frame."""
        missing = set(self.feature_names) - set(features.columns)
        if missing:
            raise ValueError(f'Missing direct features: {sorted(missing)}')
        values = features.loc[:, list(self.feature_names)].replace([np.inf, -np.inf], np.nan)
        normalized = values.rank(pct=True).fillna(.5) - .5
        return (normalized @ np.asarray(self.coefficients)).rename('direct_score')

    def to_dict(self):
        return dict(feature_names=list(self.feature_names), coefficients=list(self.coefficients),
                    seed=self.seed, evaluations=list(self.evaluations), fit_split=self.fit_split)


def fit_direct(feature_names=DEFAULT_FEATURES, evaluator=None, *, split='development',
               max_candidates=12, seed=0, regularization=.01):
    """Evaluate coefficients against a caller's DEVELOPMENT portfolio episodes.

    The evaluator receives a coefficient ndarray and must return a finite mean
    portfolio utility. No individual stock labels or validation scores are fit.
    Candidate budget and seed are fixed before evaluating any held-out period.
    """
    if split != 'development':
        raise ValueError('Direct coefficients may only be fit on development')
    names = tuple(feature_names)
    if not names or len(set(names)) != len(names) or not callable(evaluator):
        raise ValueError('Unique feature names and a portfolio evaluator are required')
    if not 1 <= max_candidates <= 256 or regularization < 0:
        raise ValueError('Invalid bounded fit budget or regularization')
    rng = np.random.default_rng(seed)
    candidates = [np.ones(len(names)) / len(names)]
    for i in range(len(names)):
        x = np.zeros(len(names)); x[i] = 1.
        candidates.append(x)
    while len(candidates) < max_candidates:
        x = rng.uniform(-1, 1, len(names))
        candidates.append(x / max(np.abs(x).sum(), 1e-12))
    records = []
    winner, best = None, -np.inf
    for i, coefficients in enumerate(candidates[:max_candidates]):
        raw = float(evaluator(coefficients.copy()))
        if not np.isfinite(raw):
            raise ValueError('Development portfolio evaluator returned invalid utility')
        objective = raw - regularization * float(coefficients @ coefficients)
        records.append(dict(candidate=i, portfolio_utility=raw, regularized_utility=objective,
                            coefficients=coefficients.tolist()))
        if objective > best:
            winner, best = coefficients.copy(), objective
    return DirectModel(names, tuple(float(x) for x in winner), int(seed), tuple(records))
