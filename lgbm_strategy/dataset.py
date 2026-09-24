"""Training rows (date, symbol, features) -> matured forward return, with a chronological split.

Decision day D sees the view through D-1. A row dated t is usable only when its
label window t+1..t+h has closed by D-1, so ``latest_label_end <= view.date`` is
checked and any violation raises. The window keeps the latest ``lookback``
matured dates; the latest ``VALIDATION_SHARE`` of them validate (early
stopping), and the ``horizon`` dates just before validation are dropped so no
training label overlaps a validation date.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from autots_strategy.targets import forward_log_return
from competition.data import AsOfView
from lgbm_strategy.features import COLUMNS, build_features

VALIDATION_SHARE = .2
MIN_TRAIN_DATES = 100


class LabelLeakError(RuntimeError):
    """A training label ends after the information cutoff."""


class InsufficientHistoryError(ValueError):
    """Too few matured dates to train and validate."""


@dataclass(frozen=True)
class TrainingSet:
    train: pd.DataFrame          # date, symbol, features..., label
    validation: pd.DataFrame
    audit: dict


def forward_return(ret: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Label at t: P(t+h) / P(t) - 1 on the action-neutral price; NaN unless all h returns are observed."""
    observed = ret.notna().rolling(horizon).sum().shift(-horizon) == horizon
    return np.expm1(forward_log_return(ret, horizon)).where(observed)


def split_dates(dates: pd.DatetimeIndex, horizon: int) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """Chronological (train, validation) dates with a ``horizon``-date purge between them."""
    n_val = int(round(len(dates) * VALIDATION_SHARE))
    train, validation = dates[:max(len(dates) - n_val - horizon, 0)], dates[len(dates) - n_val:]
    if len(train) < MIN_TRAIN_DATES or len(validation) == 0:
        raise InsufficientHistoryError(f'{len(train)} train / {len(validation)} validation dates '
                                       f'(need >= {MIN_TRAIN_DATES} / 1)')
    return train, validation


def labelled_rows(view: AsOfView, dates: pd.DatetimeIndex, horizon: int) -> pd.DataFrame:
    """Feature-ready rows on ``dates`` with a finite label."""
    table = build_features(view, dates)
    label = forward_return(view.ret, horizon).reindex(index=dates, columns=sorted(view.ret.columns))
    table['label'] = label.stack(future_stack=True).to_numpy()
    keep = table.feature_ready & np.isfinite(table.label)
    return table.loc[keep, ['date', 'symbol', *COLUMNS, 'label']].reset_index(drop=True)


def build_training_set(view: AsOfView, horizon: int, lookback: int) -> TrainingSet:
    """Matured rows of the latest ``lookback`` dates, split train / validation, plus an audit record."""
    dates = view.close.index
    matured = dates[:max(len(dates) - horizon, 0)]           # label end t+h <= view.date
    window = matured[-lookback:]
    if len(window) == 0:
        raise InsufficientHistoryError('No matured label dates')
    latest_label_end = dates[dates.get_loc(window[-1]) + horizon]
    if latest_label_end > view.date:
        raise LabelLeakError(f'Label ends {latest_label_end.date()} after the cutoff {view.date.date()}')
    train_dates, validation_dates = split_dates(window, horizon)
    rows = labelled_rows(view, window, horizon)
    train, validation = rows[rows.date.isin(train_dates)], rows[rows.date.isin(validation_dates)]
    if train.empty or validation.empty:
        raise InsufficientHistoryError(f'{len(train)} train / {len(validation)} validation rows')
    audit = dict(train_start=str(train_dates[0].date()), train_end=str(train_dates[-1].date()),
                 validation_start=str(validation_dates[0].date()), validation_end=str(validation_dates[-1].date()),
                 latest_label_end=str(latest_label_end.date()), n_dates=len(train_dates) + len(validation_dates),
                 n_rows=len(train) + len(validation), n_train_rows=len(train), n_validation_rows=len(validation),
                 n_symbols=int(rows.symbol.nunique()))
    return TrainingSet(train.reset_index(drop=True), validation.reset_index(drop=True), audit)
