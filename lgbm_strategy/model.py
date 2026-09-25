"""One global LightGBM regression with Pearson early stopping (JPX #2 settings).

Only the settings JPX #2 published are fixed here; every other LightGBM
parameter stays at the library default. No competition knowledge lives here.
"""
from __future__ import annotations

from dataclasses import dataclass

import lightgbm
import numpy as np
import pandas as pd

PARAMS = dict(objective='regression', boosting_type='gbdt', learning_rate=.005, n_estimators=3000, n_jobs=1,
              random_state=2026, verbosity=-1, force_col_wise=True,
              metric='None')          # only the Pearson metric below drives early stopping
EARLY_STOPPING_ROUNDS = 300
BASE_LEARNING_RATE = PARAMS['learning_rate']


def stopping_schedule(learning_rate: float) -> tuple[int, int]:
    """(tree cap, early-stopping patience): the JPX2 (3000, 300) at the base rate or above, scaled by
    base / rate below it, so a smaller rate is not stopped before it can travel as far."""
    scale = max(1., BASE_LEARNING_RATE / learning_rate)
    return int(round(PARAMS['n_estimators'] * scale)), int(round(EARLY_STOPPING_ROUNDS * scale))


def pearson(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Pearson correlation; 0 when either side is constant (e.g. before the first split)."""
    if np.std(y_true) == 0 or np.std(y_pred) == 0:
        return 0.
    return float(np.corrcoef(y_true, y_pred)[0, 1])


def pearson_metric(y_true: np.ndarray, y_pred: np.ndarray) -> tuple[str, float, bool]:
    """LightGBM sklearn-API eval metric (higher is better)."""
    return 'pearson', pearson(y_true, y_pred), True


@dataclass(frozen=True)
class FittedModel:
    regressor: lightgbm.LGBMRegressor
    features: tuple[str, ...]
    metadata: dict


def fit(train: pd.DataFrame, validation: pd.DataFrame, features: tuple[str, ...], label: str = 'label',
        refit_on_all: bool = False, learning_rate: float = BASE_LEARNING_RATE) -> FittedModel:
    """Fit on ``train``, early-stop on the validation Pearson correlation, keep the best iteration.

    ``refit_on_all``: afterwards refit ``best_iteration`` trees on train + validation, so the latest
    (validation) rows also train the model that predicts; the metadata keeps the early-stopping scores.
    """
    cap, patience = stopping_schedule(learning_rate)
    params = dict(PARAMS, learning_rate=learning_rate, n_estimators=cap)
    regressor = lightgbm.LGBMRegressor(**params)
    regressor.fit(train[list(features)], train[label], eval_X=(validation[list(features)],),
                  eval_y=(validation[label],), eval_metric=pearson_metric,
                  callbacks=[lightgbm.early_stopping(patience, verbose=False)])
    best = int(regressor.best_iteration_ or cap)
    validation_prediction = predict_array(regressor, validation[list(features)])
    metadata = dict(best_iteration=best, validation_pearson=float(regressor.best_score_['valid_0']['pearson']),
                    train_pearson=pearson(train[label].to_numpy(), predict_array(regressor, train[list(features)])),
                    prediction_std=float(np.std(validation_prediction)), refit_on_all=refit_on_all,
                    learning_rate=learning_rate)
    if refit_on_all:
        both = pd.concat([train, validation])
        regressor = lightgbm.LGBMRegressor(**dict(params, n_estimators=best))
        regressor.fit(both[list(features)], both[label])
        metadata['refit_rows'] = len(both)
    return FittedModel(regressor, tuple(features), metadata)


def fit_fixed(rows: pd.DataFrame, features: tuple[str, ...], n_trees: int, label: str = 'label',
              learning_rate: float = BASE_LEARNING_RATE) -> FittedModel:
    """Fit exactly ``n_trees`` trees on all ``rows`` (no validation split, no early stopping)."""
    regressor = lightgbm.LGBMRegressor(**dict(PARAMS, learning_rate=learning_rate, n_estimators=n_trees))
    regressor.fit(rows[list(features)], rows[label])
    metadata = dict(best_iteration=n_trees, validation_pearson=None, fixed_trees=n_trees, refit_rows=len(rows),
                    learning_rate=learning_rate,
                    train_pearson=pearson(rows[label].to_numpy(), predict_array(regressor, rows[list(features)])))
    return FittedModel(regressor, tuple(features), metadata)


def predict_array(regressor: lightgbm.LGBMRegressor, x: pd.DataFrame) -> np.ndarray:
    return regressor.predict(x, num_iteration=regressor.best_iteration_ or None)


def predict(model: FittedModel, table: pd.DataFrame) -> pd.Series:
    """Predicted forward return indexed like ``table``."""
    return pd.Series(predict_array(model.regressor, table[list(model.features)]), index=table.index)
