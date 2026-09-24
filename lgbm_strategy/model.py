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
        refit_on_all: bool = False) -> FittedModel:
    """Fit on ``train``, early-stop on the validation Pearson correlation, keep the best iteration.

    ``refit_on_all``: afterwards refit ``best_iteration`` trees on train + validation, so the latest
    (validation) rows also train the model that predicts; the metadata keeps the early-stopping scores.
    """
    regressor = lightgbm.LGBMRegressor(**PARAMS)
    regressor.fit(train[list(features)], train[label], eval_X=(validation[list(features)],),
                  eval_y=(validation[label],), eval_metric=pearson_metric,
                  callbacks=[lightgbm.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)])
    best = int(regressor.best_iteration_ or PARAMS['n_estimators'])
    validation_prediction = predict_array(regressor, validation[list(features)])
    metadata = dict(best_iteration=best, validation_pearson=float(regressor.best_score_['valid_0']['pearson']),
                    train_pearson=pearson(train[label].to_numpy(), predict_array(regressor, train[list(features)])),
                    prediction_std=float(np.std(validation_prediction)), refit_on_all=refit_on_all)
    if refit_on_all:
        both = pd.concat([train, validation])
        regressor = lightgbm.LGBMRegressor(**dict(PARAMS, n_estimators=best))
        regressor.fit(both[list(features)], both[label])
        metadata['refit_rows'] = len(both)
    return FittedModel(regressor, tuple(features), metadata)


def predict_array(regressor: lightgbm.LGBMRegressor, x: pd.DataFrame) -> np.ndarray:
    return regressor.predict(x, num_iteration=regressor.best_iteration_ or None)


def predict(model: FittedModel, table: pd.DataFrame) -> pd.Series:
    """Predicted forward return indexed like ``table``."""
    return pd.Series(predict_array(model.regressor, table[list(model.features)]), index=table.index)
