"""Lazy causal OOS expert evaluation with fully matured return labels.

Only requested dates/horizons are predicted. Ridge fits and OOS forecasts are
cached at fixed 20-session anchors; fitting uses weekly-spaced historical rows.
All labels end by the fitting cutoff, including during OOS validation.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from src.v4_alpha import expert_scores, EXPERT_NAMES

MODEL_FEATURES = ('R3', 'R10', 'R20', 'R60', 'price_ema20', 'volume_ratio', 'vol20', 'drawdown20')


class WalkForwardForecaster:
    def __init__(self, panel, lookback=252, validation_origins=12, ridge=1., weighting='inverse_error'):
        if weighting not in ('inverse_error', 'rank_ic', 'equal'):
            raise ValueError('Unknown weighting rule')
        if lookback < 20 or validation_origins < 1 or ridge <= 0:
            raise ValueError('Invalid bounded forecasting configuration')
        self.lookback, self.validation_origins, self.ridge, self.weighting = lookback, validation_origins, ridge, weighting
        needed = list(dict.fromkeys(['date', 'symbol', 'signal_price', 'signal_history_segment',
                                     'feature_ready', *MODEL_FEATURES, 'R5', 'R30',
                                     'ema_slope20']))
        self.panel = panel.loc[:, needed].copy(deep=True)
        self.dates = pd.DatetimeIndex(sorted(pd.to_datetime(panel.date).unique()))
        self.symbols = sorted(panel.symbol.unique())
        self.n = len(self.dates)
        self._models, self._forecasts, self._labels, self._predictions = {}, {}, {}, {}
        indexed = self.panel.set_index(['date', 'symbol'])
        axis = pd.MultiIndex.from_product([self.dates, self.symbols], names=['date', 'symbol'])
        aligned = indexed.reindex(axis)
        shape = (self.n, len(self.symbols))
        self.price = aligned.signal_price.to_numpy(float).reshape(shape)
        self.segment = aligned.signal_history_segment.to_numpy(float).reshape(shape)
        self.ready = aligned.feature_ready.fillna(False).to_numpy(bool).reshape(shape)
        self.x = np.stack([aligned[k].groupby(level='date').rank(pct=True).to_numpy(float).reshape(shape) - .5
                           for k in MODEL_FEATURES], axis=2)
        # Expert computation is cheap and purely row-local; models remain lazy.
        scores = expert_scores(aligned.reset_index())
        self.experts = scores.to_numpy(float).reshape((*shape, 7))

    def _target(self, horizon):
        if horizon not in self._labels:
            out = np.full_like(self.price, np.nan)
            if horizon < self.n:
                with np.errstate(invalid='ignore', divide='ignore'):
                    out[:-horizon] = self.price[horizon:] / self.price[:-horizon] - 1
                # No target may cross an observed action-quality reset.
                out[:-horizon][self.segment[horizon:] != self.segment[:-horizon]] = np.nan
            self._labels[horizon] = out
        return self._labels[horizon]

    def _model(self, cutoff, horizon):
        anchor = (cutoff // 20) * 20
        key = (anchor, horizon)
        if key not in self._models:
            stop = anchor - horizon
            starts = np.arange(max(0, stop - self.lookback + 1), stop + 1, 5, dtype=int)
            if len(starts):
                x = self.x[starts].reshape(-1, len(MODEL_FEATURES))
                y = self._target(horizon)[starts].ravel()
                good = np.isfinite(x).all(axis=1) & np.isfinite(y) & self.ready[starts].ravel()
                x, y = x[good], y[good]
            else:
                x, y = np.empty((0, len(MODEL_FEATURES))), np.empty(0)
            if len(y) < 30:
                coeff = np.zeros(len(MODEL_FEATURES) + 1)
            else:
                design = np.column_stack([np.ones(len(x)), x])
                penalty = np.eye(design.shape[1]) * self.ridge
                penalty[0, 0] = 0
                coeff = np.linalg.solve(design.T @ design + penalty, design.T @ y)
            self._models[key] = coeff
        return self._models[key]

    def _forecast(self, origin, horizon):
        key = (origin, horizon)
        if key not in self._forecasts:
            heuristic = self.experts[origin] * horizon
            design = np.column_stack([np.ones(len(self.symbols)), self.x[origin]])
            ridge = design @ self._model(origin, horizon)
            result = np.column_stack([heuristic, ridge]).clip(-.05 * horizon, .05 * horizon)
            result[~self.ready[origin]] = np.nan
            self._forecasts[key] = result
        return self._forecasts[key]

    def predict(self, decision_date, horizon=5):
        if not isinstance(horizon, (int, np.integer)) or not 1 <= horizon <= 24:
            raise ValueError('horizon must be an integer in [1,24]')
        cutoff = int(self.dates.searchsorted(pd.Timestamp(decision_date), side='left')) - 1
        if cutoff < 0:
            return pd.DataFrame(columns=['expected_return', 'lower_bound', 'upper_bound', 'uncertainty', 'confidence'], index=pd.Index([], name='symbol'))
        key = (cutoff, horizon)
        if key in self._predictions:
            return self._predictions[key].copy()
        latest = cutoff - horizon
        anchor = (latest // 20) * 20
        origins = [i for i in range(anchor - 20 * (self.validation_origins - 1), anchor + 1, 20) if i >= 0]
        errors, ics, residuals = [], [], []
        for origin in origins:
            predicted, actual = self._forecast(origin, horizon), self._target(horizon)[origin]
            good = np.isfinite(predicted).all(axis=1) & np.isfinite(actual)
            if good.sum() < 20:
                continue
            residual = actual[good, None] - predicted[good]
            errors.append(np.mean(residual ** 2, axis=0))
            ranked = pd.DataFrame(predicted[good]).rank().to_numpy()
            yrank = pd.Series(actual[good]).rank().to_numpy()
            centered = ranked - ranked.mean(axis=0)
            cy = yrank - yrank.mean()
            denominator = np.sqrt((centered ** 2).sum(axis=0) * (cy ** 2).sum())
            ic = np.divide((centered * cy[:, None]).sum(axis=0), denominator, out=np.zeros(8), where=denominator > 0)
            ics.append(ic)
            residuals.append(residual)
        weights = np.ones(8) / 8
        if errors and self.weighting != 'equal':
            decay = .9 ** np.arange(len(errors) - 1, -1, -1)
            if self.weighting == 'inverse_error':
                quality = 1 / np.sqrt(np.average(errors, axis=0, weights=decay) + 1e-8)
            else:
                quality = np.maximum(np.average(ics, axis=0, weights=decay), 0) + .01
            # Uniform shrinkage guarantees deterministic nonzero bounded weights.
            weights = .5 / 8 + .5 * quality / quality.sum()
        prediction = self._forecast(cutoff, horizon)
        expected = prediction @ weights
        dispersion = np.sqrt(((prediction - expected[:, None]) ** 2) @ weights)
        agreement = np.abs(np.sign(prediction) @ weights)
        if residuals:
            pooled = np.concatenate(residuals) @ weights
            low, high = np.quantile(pooled, [.1, .9])
            residual_scale = float(np.std(pooled))
        else:
            low, high, residual_scale = -.02 * np.sqrt(horizon), .02 * np.sqrt(horizon), .02 * np.sqrt(horizon)
        uncertainty = np.sqrt(dispersion ** 2 + residual_scale ** 2)
        confidence = agreement / (1 + uncertainty / (np.abs(expected) + .005))
        out = pd.DataFrame(dict(expected_return=expected, lower_bound=expected + low - dispersion,
                                upper_bound=expected + high + dispersion, uncertainty=uncertainty,
                                confidence=confidence.clip(0, 1), sign_agreement=agreement,
                                horizon=horizon), index=pd.Index(self.symbols, name='symbol'))
        out = out.loc[self.ready[cutoff] & np.isfinite(expected)]
        out.attrs.update(expert_weights=dict(zip(EXPERT_NAMES, weights.tolist())),
                         observed_date=str(self.dates[cutoff].date()),
                         model_train_end=str(self.dates[(cutoff // 20) * 20].date()),
                         latest_label_end=str(self.dates[max(origins) + horizon].date()) if origins else None,
                         validation_origins=len(errors), model_identity='V4_CAUSAL_7_EXPERTS_RIDGE_ANCHOR20')
        self._predictions[key] = out
        return out.copy()
