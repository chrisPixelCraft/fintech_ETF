"""Causal, factorized features for v2 tuning without changing the frozen engine.

The engine's historical column names are interface slots: ``return20`` and
``return50`` contain the selected short/long return horizons; ``ema20`` and
``ema50`` contain the selected EMA pair.  Actual horizons live in the trial's
``feature_presets`` configuration and ``feature_spec`` metadata.

Only DAILY return/EMA/MACD parameters vary.  The 4H calculation remains the
frozen 20 EMA, 12/26/9 MACD and 50 observed-bar readiness rule.  ``strict`` versus
``coverage_only`` changes the entry gate, not intraday observations/readiness.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
from pathlib import Path
from typing import Mapping
import uuid

import numpy as np
import pandas as pd

from src.backtest import compute_features, validate_daily


RETURN_PRESETS = {'fast': (10, 30), 'base': (20, 50), 'slow': (30, 90)}
EMA_PRESETS = {'fast': (10, 30), 'base': (20, 50), 'slow': (30, 90)}
MACD_PRESETS = {'fast': (8, 21, 5), 'base': (12, 26, 9), 'slow': (19, 39, 9)}
BASE_PRESETS = {'returns': 'base', 'ema': 'base', 'macd': 'base'}


def feature_spec(config: Mapping) -> dict:
    """Validate the small feature search space and return explicit horizons."""
    chosen = {**BASE_PRESETS, **config.get('feature_presets', {})}
    if set(chosen) != set(BASE_PRESETS):
        raise ValueError('feature_presets accepts returns, ema and macd only')
    for key, table in [('returns', RETURN_PRESETS), ('ema', EMA_PRESETS), ('macd', MACD_PRESETS)]:
        if chosen[key] not in table:
            raise ValueError(f'Unknown {key} preset: {chosen[key]}')
    mode = config.get('four_hour_mode')
    if mode is None:
        if config.get('use_4h', True):
            mode = 'strict'
        elif config.get('match_4h_coverage', False):
            mode = 'coverage_only'
        else:
            raise ValueError('Tuning requires strict or coverage_only 4H, not absent coverage')
    if mode not in {'strict', 'coverage_only'}:
        raise ValueError('four_hour_mode must be strict or coverage_only')
    warmup = config.get('warmup_sessions', 200)
    if isinstance(warmup, bool) or int(warmup) != warmup or warmup < 1:
        raise ValueError('warmup_sessions must be a positive integer')
    return dict(presets=chosen, return_lookbacks=list(RETURN_PRESETS[chosen['returns']]),
                ema_spans=list(EMA_PRESETS[chosen['ema']]), macd_spans=list(MACD_PRESETS[chosen['macd']]),
                long_ema_spans=[100, 200], four_hour_mode=mode,
                four_hour_ema=20, four_hour_macd=[12, 26, 9], four_hour_ready_bars=50,
                warmup_sessions=int(warmup))


def normalized_config(config: Mapping) -> dict:
    """Copy a trial config and express the requested 4H mode in engine flags."""
    out = copy.deepcopy(dict(config))
    spec = feature_spec(out)
    out['feature_presets'] = spec['presets']
    out['four_hour_mode'] = spec['four_hour_mode']
    out['use_4h'] = spec['four_hour_mode'] == 'strict'
    out['match_4h_coverage'] = spec['four_hour_mode'] == 'coverage_only'
    out['feature_spec'] = spec
    return out


def _readonly(values) -> np.ndarray:
    result = np.asarray(values).copy()
    result.flags.writeable = False
    return result


def _keys(frame):
    return pd.MultiIndex.from_arrays([pd.DatetimeIndex(pd.to_datetime(frame.date)).normalize(), frame.symbol.astype(str)])


def _row_hash(frame, columns):
    """Hashes are a guard against stale cache reuse, never a market-data claim."""
    return pd.util.hash_pandas_object(frame[columns], index=False).to_numpy(np.uint64)


class FeatureCache:
    """Factorize causal indicators once; return independent frames per trial.

    Build one cache per worker process and market snapshot.  Cached NumPy
    indicator arrays are read-only; returned DataFrames are deep copies.  The
    input panel can include benchmarks: engine calls are checked against the
    corresponding universe subset.  A shorter prefix is safe, but removing
    observations inside a symbol's history or changing its values is rejected.
    """

    def __init__(self, daily: pd.DataFrame, four_hour: pd.DataFrame | None = None):
        self._daily = validate_daily(daily).sort_values(['date', 'symbol']).reset_index(drop=True)
        self._columns = list(self._daily.columns)
        self._index = _keys(self._daily)
        self._input_hashes = _readonly(_row_hash(self._daily, self._columns))
        self._ordinal = _readonly(self._daily.groupby('symbol', sort=False).cumcount().to_numpy() + 1)
        self._four_hour = None if four_hour is None else four_hour.copy(deep=True)
        if self._four_hour is not None:
            self._four_hour['date'] = pd.to_datetime(self._four_hour.date).dt.normalize()
            self._four_hour['symbol'] = self._four_hour.symbol.astype(str)
            if self._four_hour.duplicated(['date', 'symbol']).any():
                raise ValueError('Duplicate 4H bars')
        self._baseline = compute_features(self._daily, {'warmup_sessions': 200}, self._four_hour)
        if not _keys(self._baseline).equals(self._index):
            raise AssertionError('Baseline feature/input ordering mismatch')
        size = len(self._baseline)
        self._returns = {n: np.full(size, np.nan) for n in {x for p in RETURN_PRESETS.values() for x in p}}
        self._emas = {n: np.full(size, np.nan) for n in {x for p in EMA_PRESETS.values() for x in p}}
        self._macd = {name: np.full(size, np.nan) for name in MACD_PRESETS}
        for _, rows in self._baseline.groupby('symbol', sort=True):
            # Per-symbol rows are chronological, exactly as in compute_features.
            positions = rows.index.to_numpy()
            price = rows.signal_price.reset_index(drop=True)
            for n in self._returns:
                self._returns[n][positions] = price.pct_change(n, fill_method=None).to_numpy()
            for n in self._emas:
                self._emas[n][positions] = price.ewm(span=n, adjust=False, min_periods=n).mean().to_numpy()
            for name, (fast, slow, signal) in MACD_PRESETS.items():
                macd = price.ewm(span=fast, adjust=False).mean() - price.ewm(span=slow, adjust=False).mean()
                self._macd[name][positions] = (macd - macd.ewm(span=signal, adjust=False).mean()).to_numpy()
        self._returns = {n: _readonly(x) for n, x in self._returns.items()}
        self._emas = {n: _readonly(x) for n, x in self._emas.items()}
        self._macd = {n: _readonly(x) for n, x in self._macd.items()}
        self.fingerprint = hashlib.sha256(self._input_hashes.tobytes()).hexdigest()
        self.four_hour_fingerprint = (None if self._four_hour is None else
                                     hashlib.sha256(_row_hash(self._four_hour, list(self._four_hour)).tobytes()).hexdigest())

    @property
    def factorized_bytes(self) -> int:
        return sum(x.nbytes for table in [self._returns, self._emas, self._macd] for x in table.values())

    def _positions(self, daily: pd.DataFrame | None, four_hour: pd.DataFrame | None = None) -> np.ndarray:
        if daily is None:
            return np.arange(len(self._baseline))
        given = daily.copy()
        given['date'] = pd.to_datetime(given.date).dt.normalize()
        given['symbol'] = given.symbol.astype(str)
        if set(given.columns) != set(self._columns):
            raise ValueError('CACHE_INPUT_MISMATCH: daily schema changed')
        pos = self._index.get_indexer(_keys(given))
        if (pos < 0).any() or len(np.unique(pos)) != len(pos):
            raise ValueError('CACHE_INPUT_MISMATCH: missing or duplicate keys')
        if not np.array_equal(_row_hash(given, self._columns), self._input_hashes[pos]):
            raise ValueError('CACHE_INPUT_MISMATCH: daily values changed')
        # A prefix retains every earlier observation for every requested symbol.
        for symbol, group in given.groupby('symbol', sort=False):
            selected = pos[given.symbol.to_numpy() == symbol]
            ordinals = np.sort(self._ordinal[selected])
            if not np.array_equal(ordinals, np.arange(1, len(group) + 1)):
                raise ValueError('CACHE_INPUT_MISMATCH: only complete history prefixes are reusable')
        if self._four_hour is None or not len(self._four_hour):
            if four_hour is not None and len(four_hour):
                raise ValueError('CACHE_INPUT_MISMATCH: new 4H data')
        else:
            if four_hour is None:
                raise ValueError('CACHE_INPUT_MISMATCH: omitted 4H source')
            bars = four_hour.copy()
            bars['date'] = pd.to_datetime(bars.date).dt.normalize()
            bars['symbol'] = bars.symbol.astype(str)
            requested = _keys(given)
            expected = self._four_hour[_keys(self._four_hour).isin(requested)].sort_values(['date', 'symbol']).reset_index(drop=True)
            observed = bars[_keys(bars).isin(requested)].sort_values(['date', 'symbol']).reset_index(drop=True)
            if set(expected.columns) != set(observed.columns) or not expected.equals(observed[expected.columns]):
                raise ValueError('CACHE_INPUT_MISMATCH: 4H source changed')
        return np.sort(pos)

    def frame(self, config: Mapping, daily: pd.DataFrame | None = None,
              four_hour: pd.DataFrame | None = None) -> pd.DataFrame:
        """Return a causal trial frame; optional source inputs must match cache."""
        spec = feature_spec(config)
        pos = self._positions(daily, four_hour)
        result = self._baseline.iloc[pos].copy(deep=True)
        short, long = spec['return_lookbacks']
        result['return20'] = self._returns[short][pos]
        result['return50'] = self._returns[long][pos]
        fast, slow = spec['ema_spans']
        result['ema20'] = self._emas[fast][pos]
        result['ema50'] = self._emas[slow][pos]
        result['macd_hist'] = self._macd[spec['presets']['macd']][pos]
        result['trend'] = ((result.signal_price > result.ema20) & (result.ema20 > result.ema50)).astype(float)
        result['ready'] = self._ordinal[pos] >= spec['warmup_sessions']
        return result.reset_index(drop=True)

    def frame_for_schedule(self, schedule: Mapping, base_config: Mapping | None = None,
                           daily: pd.DataFrame | None = None,
                           four_hour: pd.DataFrame | None = None) -> pd.DataFrame:
        """Apply configs forward from effective SIGNAL dates in one feature panel.

        This composes indicators only.  The caller owns dated selection evidence
        and the engine's corresponding daily weights/planning config updates.
        Schedule keys must be distinct timezone-naive dates; no backward fill.
        """
        base = dict(base_config or {})
        result = self.frame(base, daily, four_hour)
        parsed = [(pd.Timestamp(day), dict(cfg)) for day, cfg in schedule.items()]
        if any(day.tzinfo is not None or day != day.normalize() for day, _ in parsed):
            raise ValueError('Schedule keys must be timezone-naive signal dates')
        parsed.sort(key=lambda item: item[0])
        if len({day for day, _ in parsed}) != len(parsed):
            raise ValueError('Duplicate normalized schedule date')
        pos = self._positions(daily, four_hour)
        for j, (day, config) in enumerate(parsed):
            next_day = parsed[j + 1][0] if j + 1 < len(parsed) else pd.Timestamp.max
            mask = result.date.ge(day) & result.date.lt(next_day)
            if not mask.any():
                continue
            settings = {**base, **config}
            spec = feature_spec(settings)
            selected = pos[mask.to_numpy()]
            short, long = spec['return_lookbacks']
            fast, slow = spec['ema_spans']
            result.loc[mask, 'return20'] = self._returns[short][selected]
            result.loc[mask, 'return50'] = self._returns[long][selected]
            result.loc[mask, 'ema20'] = self._emas[fast][selected]
            result.loc[mask, 'ema50'] = self._emas[slow][selected]
            result.loc[mask, 'macd_hist'] = self._macd[spec['presets']['macd']][selected]
            result.loc[mask, 'trend'] = ((result.loc[mask, 'signal_price'] > result.loc[mask, 'ema20'])
                                       & (result.loc[mask, 'ema20'] > result.loc[mask, 'ema50'])).astype(float)
            result.loc[mask, 'ready'] = self._ordinal[selected] >= spec['warmup_sessions']
        return result


class IsolatedEngine:
    """Private engine module; safe alongside other adapters without global patching.

    ``module`` is deliberately exposed for the study runner's optional scheduled
    score wrapper.  Do not share the same adapter between concurrent runs: use
    one per worker/process.  Shared FeatureCache state is never mutated.
    """

    def __init__(self, cache: FeatureCache, engine_path: str | Path | None = None):
        path = Path(engine_path) if engine_path else Path(__file__).with_name('backtest_v2.py')
        spec = importlib.util.spec_from_file_location('_tuning_engine_' + uuid.uuid4().hex, path)
        if spec is None or spec.loader is None:
            raise ValueError('Cannot load isolated engine')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.cache = cache
        self.module.compute_features = self._features

    def _features(self, daily, config, four_hour=None):
        return self.cache.frame(config, daily, four_hour)

    def run_v2(self, daily, universe, config, four_hour=None, signal_transform=None):
        return self.module.run_v2(daily, universe, normalized_config(config), four_hour,
                                  signal_transform=signal_transform)
