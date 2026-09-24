"""Training rows (date, symbol, features) -> matured target (lgbm_strategy/targets.py), split chronologically.

Decision day D sees the view through D-1. A row dated t is usable only when its
label has closed by D-1 (label end t + span, span = h, or 1 + h for
execution_alpha), so ``latest_label_end <= view.date`` is checked, a target
with a finite label past the matured window raises, and neither is ever
silently dropped. The window keeps the latest ``lookback`` matured origins; the
latest ``VALIDATION_SHARE`` of them validate (early stopping), and the ``span``
origins just before validation are purged so no training label overlaps a
validation date.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from competition.data import AsOfView
from lgbm_strategy.features import COLUMNS, build_features
from lgbm_strategy.targets import RawReturnTarget, target_for

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


def split_dates(dates: pd.DatetimeIndex, span: int) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    """Chronological (train, validation) dates with a ``span``-date purge (the label span) between them."""
    n_val = int(round(len(dates) * VALIDATION_SHARE))
    train, validation = dates[:max(len(dates) - n_val - span, 0)], dates[len(dates) - n_val:]
    if len(train) < MIN_TRAIN_DATES or len(validation) == 0:
        raise InsufficientHistoryError(f'{len(train)} train / {len(validation)} validation dates '
                                       f'(need >= {MIN_TRAIN_DATES} / 1)')
    return train, validation


def labelled_rows(view: AsOfView, dates: pd.DatetimeIndex, target: pd.DataFrame) -> pd.DataFrame:
    """Feature-ready rows on ``dates`` with a finite label from the wide ``target``."""
    table = build_features(view, dates)
    label = target.reindex(index=dates, columns=sorted(view.ret.columns))
    table['label'] = label.stack(future_stack=True).to_numpy()
    keep = table.feature_ready & np.isfinite(table.label)
    return table.loc[keep, ['date', 'symbol', *COLUMNS, 'label']].reset_index(drop=True)


def target_audit(target: RawReturnTarget, view: AsOfView, origins: pd.DatetimeIndex, label: pd.DataFrame,
                 horizon: int) -> dict:
    """Universe-wide statistics of the window's labels (before feature filtering) and their price sources."""
    window = label.reindex(origins)
    count = window.notna().sum(axis=1)
    audit = dict(cross_section_count=float(count[count > 0].mean()) if count.any() else 0.,
                 **target.price_sources(view, origins, label, horizon))
    if target.alpha:
        date_mean = window.mean(axis=1).dropna()
        audit['alpha_cross_section_mean_error'] = float(date_mean.abs().max()) if len(date_mean) else np.nan
    return audit


def matured_window(view: AsOfView, span: int, lookback: int) -> tuple[pd.DatetimeIndex, pd.Timestamp]:
    """Latest ``lookback`` origins whose label end (origin + ``span`` sessions) is <= view.date."""
    dates = view.close.index
    window = dates[:max(len(dates) - span, 0)][-lookback:]
    if len(window) == 0:
        raise InsufficientHistoryError('No matured label dates')
    latest_label_end = dates[dates.get_loc(window[-1]) + span]
    if latest_label_end > view.date:
        raise LabelLeakError(f'Label ends {latest_label_end.date()} after the cutoff {view.date.date()}')
    return window, latest_label_end


def check_unmatured_empty(label: pd.DataFrame, window: pd.DatetimeIndex):
    """Fail if the target has a label after the matured window: its declared span would be too short."""
    later = label.loc[label.index > window[-1]]
    if later.notna().to_numpy().any():
        raise LabelLeakError(f'Finite labels after the last matured origin {window[-1].date()}')


def build_training_set(view: AsOfView, horizon: int, lookback: int, target_mode: str) -> TrainingSet:
    """Matured rows of the latest ``lookback`` origins labelled by ``target_mode``, split train / validation
    (purged by the label span), plus an audit record."""
    target = target_for(target_mode)
    span = target.label_span(horizon)
    window, latest_label_end = matured_window(view, span, lookback)
    label = target.build(view, horizon)
    check_unmatured_empty(label, window)
    train_dates, validation_dates = split_dates(window, span)
    rows = labelled_rows(view, window, label)
    train, validation = rows[rows.date.isin(train_dates)], rows[rows.date.isin(validation_dates)]
    if train.empty or validation.empty:
        raise InsufficientHistoryError(f'{len(train)} train / {len(validation)} validation rows')
    used = pd.concat([train, validation])
    dates = view.close.index
    audit = dict(target_mode=target_mode, train_start=str(train_dates[0].date()), train_end=str(train_dates[-1].date()),
                 validation_start=str(validation_dates[0].date()), validation_end=str(validation_dates[-1].date()),
                 label_start=str(dates[dates.get_loc(window[0]) + 1].date()),
                 latest_label_end=str(latest_label_end.date()), n_dates=len(train_dates) + len(validation_dates),
                 n_rows=len(used), n_train_rows=len(train), n_validation_rows=len(validation),
                 n_symbols=int(used.symbol.nunique()), target_mean=float(used.label.mean()),
                 target_std=float(used.label.std()), **target_audit(target, view, window, label, horizon))
    return TrainingSet(train.reset_index(drop=True), validation.reset_index(drop=True), audit)
