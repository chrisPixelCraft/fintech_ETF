"""AutoTS wrapper: episode validation, competition metric, frozen templates, sigma.

Hook points and pitfalls follow docs/autots_internals.md:
- synthetic contiguous business-day clock, so ``forecast_length=h`` means h
  trading days and holidays never become NaN rows (§1, §2d);
- ``validation_method='custom'``: element 0 is the full index (round 0 = last h
  rows), elements 1..n end ``validation_step`` rows earlier each (§2a);
- ``custom_metric`` = negative cross-sectional rank IC (or normalized top-k
  spread) of the h-step change, broadcast to all series (§2b);
- ``preclean=None`` (leaks), ``introduce_na=False`` (blanks tails), fixed
  ``random_seed``, explicit model lists (hash order), configurable ``n_jobs``;
- search mode curates the sklearn regressor pools (xgboost is not installed),
  caps ``WindowRegression.max_windows`` and never passes future regressors,
  so no ``regression_type='User'`` model can see the future (§1, §2c, §2g).
Sigma is calibrated from out-of-sample residuals of the chosen model on the
validation windows (AutoTS intervals are heuristic, §1 and §2f).
"""
from __future__ import annotations

import json
import random
from dataclasses import dataclass, field, fields

import numpy as np
import pandas as pd
from scipy.stats import norm, rankdata

CLOCK_START = '2000-01-03'
TEMPLATE_COLS = ['Model', 'ModelParameters', 'TransformationParameters', 'Ensemble']
MODES = ('fixed_template', 'search', 'frozen')
METRICS = ('rank_ic', 'topk_spread')
CURATED_REGRESSORS = ('Ridge', 'ElasticNet', 'LightGBM', 'HistGradientBoost')
MAX_WINDOWS_CAP = 50000
# Runtime (not source) overrides applied in search mode; recorded in run manifests.
RUNTIME_OVERRIDES = (
    f'sklearn regressor pools restricted to {list(CURATED_REGRESSORS)} (search mode)',
    f'WindowRegression.get_new_params: max_windows capped at {MAX_WINDOWS_CAP}, regression_type=None (search mode)',
)

_DIFF = {'fillna': 'ffill', 'transformations': {'0': 'DifferencedTransformer'},
         'transformation_params': {'0': {'lag': 1, 'fill': 'zero'}}}
_NONE = {'fillna': 'ffill', 'transformations': {}, 'transformation_params': {}}
_RIDGE = {'model': 'Ridge', 'model_params': {}}
# Curated default pool: every model fits 150 series in <2.5 s (docs/autots_internals.md §3).
DEFAULT_TEMPLATE = [
    dict(Model='LastValueNaive', ModelParameters={}, TransformationParameters=_NONE),
    dict(Model='AverageValueNaive', ModelParameters={'method': 'mean', 'window': 20}, TransformationParameters=_DIFF),
    dict(Model='AverageValueNaive', ModelParameters={'method': 'mean', 'window': 60}, TransformationParameters=_DIFF),
    dict(Model='SeasonalNaive', ModelParameters={'method': 'lastvalue', 'lag_1': 5, 'lag_2': None},
         TransformationParameters=_DIFF),
    dict(Model='ETS', ModelParameters={'damped_trend': True, 'trend': 'additive', 'seasonal': None,
                                       'seasonal_periods': None, 'method': None}, TransformationParameters=_NONE),
    dict(Model='ARIMA', ModelParameters={'p': 1, 'd': 1, 'q': 0, 'regression_type': None},
         TransformationParameters=_NONE),
    dict(Model='WindowRegression', ModelParameters={
        'window_size': 20, 'input_dim': 'univariate', 'output_dim': 'forecast_length', 'normalize_window': False,
        'max_windows': 5000, 'fourier_encoding_components': None, 'scale': False, 'datepart_method': None,
        'regression_type': None, 'regression_model': _RIDGE}, TransformationParameters=_DIFF),
]


@dataclass(frozen=True)
class ForecastConfig:
    horizon: int = 5
    lookback: int = 240                     # rows fed to AutoTS; names need full history over it
    mode: str = 'fixed_template'            # fixed_template | search | frozen
    template: tuple = tuple(DEFAULT_TEMPLATE)  # rows {Model, ModelParameters, TransformationParameters}
    template_path: str | None = None        # frozen mode: CSV exported by save_template
    model_list: tuple = ('LastValueNaive', 'AverageValueNaive', 'SeasonalNaive', 'ETS', 'WindowRegression',
                         'MultivariateRegression', 'SectionalMotif')   # search mode pool
    transformer_list: dict = field(default_factory=lambda: {
        'DifferencedTransformer': 1, None: .5, 'ClipOutliers': .3, 'StandardScaler': .3,
        'AlignLastValue': .3, 'Detrend': .2})
    transformer_max_depth: int = 2
    max_generations: int = 1                # search mode
    generation_timeout: float | None = 10.  # minutes, search mode
    validation_windows: int = 4             # total windows incl. round 0
    validation_step: int = 24               # rows between window ends (>= horizon: disjoint tests)
    metric: str = 'rank_ic'
    top_k: int = 25
    metric_weighting: dict = field(default_factory=lambda: {
        'custom_weighting': 2, 'mae_weighting': 1, 'oda_weighting': 1, 'smape_weighting': 0,
        'rmse_weighting': 0, 'spl_weighting': 0, 'made_weighting': 0, 'containment_weighting': 0,
        'contour_weighting': 0, 'runtime_weighting': 0, 'wasserstein_weighting': 0})
    sigma_prior_windows: float = 4.         # shrink per-name residual variance to the cross-sectional median
    prediction_interval: float = .9
    n_jobs: int = 1
    random_seed: int = 2026

    def __post_init__(self):
        if self.mode not in MODES or self.metric not in METRICS:
            raise ValueError(f'Bad mode/metric {self.mode}/{self.metric}')
        if self.mode == 'frozen' and not self.template_path:
            raise ValueError('frozen mode needs template_path')
        if self.validation_step < self.horizon:
            raise ValueError('validation_step < horizon would overlap test windows')
        if self.lookback <= self.horizon + self.validation_windows * self.validation_step:
            raise ValueError('lookback too short for the validation windows')

    @classmethod
    def from_dict(cls, raw: dict) -> 'ForecastConfig':
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f'Unknown forecaster keys {sorted(unknown)}')
        raw = dict(raw)
        for key in ('template', 'model_list'):
            if key in raw:
                raw[key] = tuple(raw[key])
        if 'transformer_list' in raw:  # JSON cannot hold a None key; "None" means no transform
            raw['transformer_list'] = {None if k == 'None' else k: v for k, v in raw['transformer_list'].items()}
        return cls(**raw)


def template_frame(rows) -> pd.DataFrame:
    return pd.DataFrame([{'Model': r['Model'], 'ModelParameters': json.dumps(r['ModelParameters']),
                          'TransformationParameters': json.dumps(r['TransformationParameters']), 'Ensemble': 0}
                         for r in rows], columns=TEMPLATE_COLS)


def save_template(template: pd.DataFrame, path) -> None:
    template[TEMPLATE_COLS].iloc[:1].to_csv(path, index=False)


def load_template(path) -> pd.DataFrame:
    return pd.read_csv(path)[TEMPLATE_COLS].iloc[:1]


def prepare_panel(series: pd.DataFrame, lookback: int) -> pd.DataFrame:
    """Last ``lookback`` rows on a synthetic trading-day clock; names without full history are masked."""
    window = series.iloc[-lookback:]
    if len(window) < lookback:
        return window.iloc[:, :0]
    window = window.loc[:, window.notna().all()]
    return window.set_axis(pd.bdate_range(CLOCK_START, periods=len(window), freq='B'), axis=0)


def _change(block: np.ndarray, last: np.ndarray, level: bool) -> np.ndarray:
    return block[-1] - last if level else block.sum(axis=0)


def _rank_ic(x: np.ndarray, y: np.ndarray) -> float:
    ok = np.isfinite(x) & np.isfinite(y)
    if ok.sum() < 3 or np.ptp(x[ok]) == 0 or np.ptp(y[ok]) == 0:
        return 0.
    return float(np.corrcoef(rankdata(x[ok]), rankdata(y[ok]))[0, 1])


def competition_metric(A, F, df_train, prediction_interval, *, level=True, kind='rank_ic', top_k=25):
    """Lower is better. Negative cross-sectional rank IC (or top-k spread / cross-sectional std)."""
    A, F, train = np.asarray(A, float), np.asarray(F, float), np.asarray(df_train, float)
    last = train[-1]
    fa, ff = _change(A, last, level), _change(F, last, level)
    if kind == 'rank_ic':
        value = _rank_ic(ff, fa)
    else:
        ok = np.isfinite(fa) & np.isfinite(ff)
        spread = np.nanstd(fa[ok])
        top = np.argsort(-ff[ok], kind='stable')[:top_k]
        value = float((fa[ok][top].mean() - fa[ok].mean()) / spread) if spread > 0 and np.ptp(ff[ok]) > 0 else 0.
    return np.full(A.shape[1], -value)


class _Metric:
    """Picklable custom_metric bound to the series kind."""

    def __init__(self, level, kind, top_k):
        self.level, self.kind, self.top_k = level, kind, top_k

    def __call__(self, A, F, df_train, prediction_interval):
        return competition_metric(A, F, df_train, prediction_interval, level=self.level, kind=self.kind,
                                  top_k=self.top_k)


_CURATED = False


def _curate_search_space():
    """Runtime overrides for random/genetic search (see RUNTIME_OVERRIDES)."""
    global _CURATED
    if _CURATED:
        return
    from autots.models import sklearn as sk
    for name in ('sklearn_model_dict', 'multivariate_model_dict', 'univariate_model_dict', 'rolling_regression_dict',
                 'no_shared_model_dict', 'datepart_model_dict', 'gradient_boosting'):
        pool = getattr(sk, name)
        keep = {k: v for k, v in pool.items() if k in CURATED_REGRESSORS} or {'Ridge': 1.}
        pool.clear()
        pool.update(keep)
    original = sk.WindowRegression.get_new_params

    def get_new_params(self, method='random'):
        params = original(self, method)
        params['regression_type'] = None
        if params.get('max_windows') is None or params['max_windows'] > MAX_WINDOWS_CAP:
            params['max_windows'] = MAX_WINDOWS_CAP
        return params
    sk.WindowRegression.get_new_params = get_new_params
    _CURATED = True


class AutoTSForecaster:
    def __init__(self, config: ForecastConfig, level: bool = True):
        self.c, self.level = config, level

    def _model_forecast(self, panel: pd.DataFrame, template: pd.DataFrame):
        from autots import model_forecast
        row = template.iloc[0]
        return model_forecast(model_name=row.Model, model_param_dict=json.loads(row.ModelParameters),
                              model_transform_dict=json.loads(row.TransformationParameters), df_train=panel,
                              forecast_length=self.c.horizon, frequency='B',
                              prediction_interval=self.c.prediction_interval, random_seed=self.c.random_seed,
                              n_jobs=self.c.n_jobs, verbose=0)

    def validation_indexes(self, index: pd.DatetimeIndex) -> list:
        n = len(index)
        return [index] + [index[:n - k * self.c.validation_step] for k in range(1, self.c.validation_windows)]

    def select(self, panel: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
        """Choose one model on the episode validation windows (AutoTS model selection)."""
        c = self.c
        if c.mode == 'frozen':
            return load_template(c.template_path), dict(selection='frozen', template_path=c.template_path)
        candidates = template_frame(c.template)
        if c.mode == 'fixed_template' and len(candidates) == 1:
            return candidates, dict(selection='single_template')
        from autots import AutoTS
        if c.mode == 'search':
            _curate_search_space()
        random.seed(c.random_seed)
        np.random.seed(c.random_seed)
        model = AutoTS(
            forecast_length=c.horizon, frequency='B', prediction_interval=c.prediction_interval,
            max_generations=0 if c.mode == 'fixed_template' else c.max_generations, ensemble=None,
            initial_template='General+Random' if c.mode == 'search' else 'Random',
            random_seed=c.random_seed, metric_weighting=dict(c.metric_weighting),
            model_list=sorted(set(candidates.Model)) if c.mode == 'fixed_template' else list(c.model_list),
            transformer_list=dict(c.transformer_list), transformer_max_depth=c.transformer_max_depth,
            num_validations=c.validation_windows - 1, validation_method='custom', models_to_validate=.99,
            preclean=None, introduce_na=False, drop_most_recent=0, no_negatives=False,
            generation_timeout=c.generation_timeout if c.mode == 'search' else None,
            custom_metric=_Metric(self.level, c.metric, c.top_k), verbose=-1, n_jobs=c.n_jobs)
        if c.mode == 'fixed_template':
            model.import_template(candidates, method='only', enforce_model_list=False)
        model.fit(panel, validation_indexes=self.validation_indexes(panel.index))
        results = model.results()
        best = model.best_model[TEMPLATE_COLS].reset_index(drop=True)
        info = dict(selection=c.mode, best_model=str(best.Model.iloc[0]), evaluated=int(len(results)),
                    failed=int(results.Exceptions.notna().sum()))
        return best, info

    def calibrate(self, panel: pd.DataFrame, template: pd.DataFrame) -> tuple[pd.Series, list]:
        """Per-name sigma of the h-step change from out-of-sample residuals on the validation windows."""
        c, n = self.c, len(panel)
        residuals, ics = [], []
        for k in range(c.validation_windows):
            end = n - k * c.validation_step
            train, actual = panel.iloc[:end - c.horizon], panel.iloc[end - c.horizon:end]
            pred = self._model_forecast(train, template).forecast[panel.columns].to_numpy()
            last = train.iloc[-1].to_numpy()
            fa, ff = _change(actual.to_numpy(), last, self.level), _change(pred, last, self.level)
            residuals.append(fa - ff)
            ics.append(_rank_ic(ff, fa))
        mse = np.mean(np.square(residuals), axis=0)
        prior = float(np.median(mse))
        m = len(residuals)
        sigma = np.sqrt((m * mse + c.sigma_prior_windows * prior) / (m + c.sigma_prior_windows))
        return pd.Series(sigma, index=panel.columns), ics

    def forecast(self, panel: pd.DataFrame, template: pd.DataFrame, sigma: pd.Series) -> pd.DataFrame:
        pred = self._model_forecast(panel, template)
        last = panel.iloc[-1]
        h = self.c.horizon
        if self.level:
            mu = pred.forecast.iloc[h - 1] - last
            lower, upper = pred.lower_forecast.iloc[h - 1] - last, pred.upper_forecast.iloc[h - 1] - last
        else:
            mu = pred.forecast.iloc[:h].sum()
            lower, upper = pred.lower_forecast.iloc[:h].sum(), pred.upper_forecast.iloc[:h].sum()
        frame = pd.DataFrame(dict(mu=mu, sigma=sigma.reindex(mu.index), lower=lower, upper=upper))
        frame['z'] = frame.mu / frame.sigma
        frame['confidence'] = norm.cdf(frame.z)   # P(h-day (excess) log return > 0) under N(mu, sigma)
        return frame
