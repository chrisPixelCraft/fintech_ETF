"""Causal weak-market rank experiments; this module never certifies compliance.

Returns require consecutive MARKET-session quotes in both the stock and 0050.
Risk/model windows count valid paired observations, not padded calendar days.
No missing price, return or factor is forward-filled. A common B/C/D factor
mask keeps their cross-sectional coverage identical without changing A's gates.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, fields
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from src.backtest import causal_prices, validate_daily


@dataclass(frozen=True)
class WeakMarketParameters:
    benchmark_symbol: str = '0050.TW'
    regime_ema_span: int = 60
    regime_return_sessions: int = 20
    beta_window: int = 60
    volatility_window: int = 60
    ols_window: int = 120
    residual_window: int = 20
    minimum_common_valid: int = 20
    return_kind: str = 'simple_total_return'
    window_basis: str = 'valid_paired_observations'
    rank_ties: str = 'average'
    missing_policy: str = 'zero_score_then_day_baseline_if_insufficient'
    require_positive_volume: bool = True
    current_pair_required: bool = True
    variance_epsilon: float = 1e-16
    ddof: int = 1
    residual_std_floor: float = 1e-12

    @classmethod
    def from_mapping(cls, parameters: Mapping[str, Any] | 'WeakMarketParameters') -> 'WeakMarketParameters':
        if isinstance(parameters, cls):
            result = parameters
        else:
            names = {f.name for f in fields(cls)}
            if set(parameters) != names:
                raise ValueError(f'v3 parameters must be explicit: missing={sorted(names-set(parameters))}; '
                                 f'unknown={sorted(set(parameters)-names)}')
            result = cls(**parameters)
        for name in ('regime_ema_span', 'regime_return_sessions', 'beta_window', 'volatility_window',
                     'ols_window', 'residual_window', 'minimum_common_valid'):
            value = getattr(result, name)
            minimum = 1 if name in ('regime_return_sessions', 'minimum_common_valid') else 2
            if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value < minimum:
                raise ValueError(f'{name} must be an integer >= {minimum}')
        fixed = dict(benchmark_symbol='0050.TW', return_kind='simple_total_return',
                     window_basis='valid_paired_observations', rank_ties='average',
                     missing_policy='zero_score_then_day_baseline_if_insufficient',
                     require_positive_volume=True, current_pair_required=True, ddof=1)
        for name, expected in fixed.items():
            if getattr(result, name) != expected:
                raise ValueError(f'Unsupported v3 contract: {name}={getattr(result,name)!r}')
        if isinstance(result.ddof, bool) or not isinstance(result.ddof, (int, np.integer)):
            raise ValueError('ddof must be integer 1')
        for name in ('require_positive_volume', 'current_pair_required'):
            if not isinstance(getattr(result, name), (bool, np.bool_)):
                raise ValueError(f'{name} must be an explicit boolean')
        for name in ('variance_epsilon', 'residual_std_floor'):
            value = getattr(result, name)
            if isinstance(value, bool) or not np.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be positive and finite')
        return result


def _day(value: Any) -> pd.Timestamp:
    result = pd.Timestamp(value)
    if pd.isna(result):
        raise ValueError('Missing v3 signal date')
    if result.tzinfo is not None:
        result = result.tz_convert('Asia/Taipei').tz_localize(None)
    return result.normalize()


def _window_details(index: pd.DatetimeIndex, calendar: pd.DatetimeIndex,
                    window: int, prefix: str, lag: bool = False) -> pd.DataFrame:
    """Observation metadata, including the market-session span across gaps."""
    n = len(index)
    stop = np.arange(n) + (0 if lag else 1)
    start = np.maximum(stop - window, 0)
    count = stop - start
    starts, ends = np.full(n, np.datetime64('NaT'), dtype='datetime64[ns]'), np.full(n, np.datetime64('NaT'), dtype='datetime64[ns]')
    valid = count > 0
    dates = index.to_numpy(dtype='datetime64[ns]')
    starts[valid], ends[valid] = dates[start[valid]], dates[stop[valid] - 1]
    start_pos, end_pos = calendar.get_indexer(starts), calendar.get_indexer(ends)
    current_pos = calendar.get_indexer(index)
    return pd.DataFrame({prefix + '_obs_count': count,
                         prefix + '_window_start': pd.to_datetime(starts),
                         prefix + '_window_end': pd.to_datetime(ends),
                         prefix + '_span_sessions': np.where(valid, end_pos-start_pos+1, 0),
                         prefix + '_latest_age_sessions': np.where(valid, current_pos-end_pos, np.nan)}, index=index)


class WeakMarketSignals:
    """Fixed weak-market features and `(day, ranked) -> (ranked, meta)` callbacks.

    `day` is the completed signal session supplied by the existing engine; it
    schedules orders for the NEXT session. Do not shift this callback again.
    Export properties return copies so external reporting cannot mutate caches.
    """

    def __init__(self, daily: pd.DataFrame,
                 parameters: Mapping[str, Any] | WeakMarketParameters):
        self.parameters = WeakMarketParameters.from_mapping(parameters)
        if not isinstance(daily, pd.DataFrame) or daily.empty:
            raise ValueError('v3 requires nonempty canonical daily observations')
        if 'date' not in daily or 'symbol' not in daily or daily.symbol.isna().any():
            raise ValueError('v3 requires nonmissing date/symbol identities')
        source = daily.copy(deep=True)
        source['date'] = source.date.map(_day)
        source = validate_daily(source)
        benchmark = self.parameters.benchmark_symbol
        if benchmark not in set(source.symbol):
            raise ValueError('v3 benchmark 0050.TW is missing')
        self.sessions = pd.DatetimeIndex(sorted(source.date.unique()))
        self._prices, self._quotes = {}, {}
        for symbol, rows in source.groupby('symbol', sort=True):
            rows = rows.sort_values('date').reset_index(drop=True)
            price, _ = causal_prices(rows)
            if not np.isfinite(price.to_numpy(float)).all() or (price <= 0).any():
                raise ValueError('Nonfinite/nonpositive causal total-return price: ' + str(symbol))
            observed = pd.Series(price.to_numpy(float), index=pd.DatetimeIndex(rows.date))
            traded = pd.Series(rows.volume.to_numpy(float) > 0, index=pd.DatetimeIndex(rows.date))
            # Numeric stale quotes on suspension days do not become observations.
            self._prices[str(symbol)] = observed.where(traded).reindex(self.sessions)
            self._quotes[str(symbol)] = traded.reindex(self.sessions, fill_value=False)
        self._market_state = self._build_market_state()
        self._state_index = self._market_state.set_index('date')
        market_return = self._prices[benchmark].pct_change(fill_method=None)
        frames = [self._build_stock(symbol, price, market_return)
                  for symbol, price in self._prices.items()]
        self._features = pd.concat(frames, ignore_index=True).sort_values(['date', 'symbol']).reset_index(drop=True)
        self._feature_index = self._features.set_index(['date', 'symbol'])

    @property
    def features(self) -> pd.DataFrame:
        return self._features.copy(deep=True)

    @property
    def market_state(self) -> pd.DataFrame:
        return self._market_state.copy(deep=True)

    def _build_market_state(self) -> pd.DataFrame:
        p = self.parameters
        price = self._prices[p.benchmark_symbol]
        # EMA advances only at a genuinely traded quote. Missing sessions remain
        # UNKNOWN, while old observations retain their original event adjustment.
        ema = price.dropna().ewm(span=p.regime_ema_span, adjust=False,
                                  min_periods=p.regime_ema_span).mean().reindex(self.sessions)
        horizon_return = price / price.shift(p.regime_return_sessions) - 1
        observed = self._quotes[p.benchmark_symbol]
        known = observed & np.isfinite(ema) & np.isfinite(horizon_return)
        weak = known & (price < ema) & (horizon_return < 0)
        reason = np.where(~observed, 'BENCHMARK_QUOTE_MISSING_OR_ZERO_VOLUME',
                          np.where(~np.isfinite(ema), 'BENCHMARK_EMA_WARMUP',
                                   np.where(~np.isfinite(horizon_return), 'BENCHMARK_RETURN_ENDPOINT_MISSING', '')))
        return pd.DataFrame(dict(date=self.sessions, benchmark_symbol=p.benchmark_symbol,
            total_return_price=price.to_numpy(float), regime_ema=ema.to_numpy(float),
            regime_return=horizon_return.to_numpy(float),
            market_return=price.pct_change(fill_method=None).to_numpy(float),
            quote_present=observed.to_numpy(bool), quote_observation_count=observed.cumsum().to_numpy(int),
            regime_known=known.to_numpy(bool), weak_market=weak.to_numpy(bool),
            regime=np.where(~known, 'UNKNOWN', np.where(weak, 'WEAK', 'NORMAL')),
            regime_reason=reason,
            available_through=pd.Series(self.sessions).where(observed.to_numpy(bool)).to_numpy()))

    def _build_stock(self, symbol: str, price: pd.Series, market_return: pd.Series) -> pd.DataFrame:
        p = self.parameters
        stock_return = price.pct_change(fill_method=None)
        paired = pd.DataFrame(dict(stock_return=stock_return, market_return=market_return)).dropna()
        paired = paired[np.isfinite(paired.stock_return) & np.isfinite(paired.market_return)]
        x, y = paired.market_return, paired.stock_return
        pair_count = len(paired)
        beta_var = x.rolling(p.beta_window, min_periods=p.beta_window).var(ddof=p.ddof)
        paired['beta_covariance'] = y.rolling(p.beta_window, min_periods=p.beta_window).cov(x, ddof=p.ddof)
        paired['beta_market_variance'] = beta_var
        paired['beta'] = (paired.beta_covariance / beta_var).where(beta_var > p.variance_epsilon)
        paired['volatility'] = y.rolling(p.volatility_window, min_periods=p.volatility_window).std(ddof=p.ddof)
        # Shift before fitting: today's stock return cannot enter today's model.
        old_x, old_y = x.shift(1), y.shift(1)
        x_mean = old_x.rolling(p.ols_window, min_periods=p.ols_window).mean()
        y_mean = old_y.rolling(p.ols_window, min_periods=p.ols_window).mean()
        ols_var = old_x.rolling(p.ols_window, min_periods=p.ols_window).var(ddof=p.ddof)
        ols_cov = old_y.rolling(p.ols_window, min_periods=p.ols_window).cov(old_x, ddof=p.ddof)
        paired['ols_market_variance'] = ols_var
        paired['ols_beta_lagged'] = (ols_cov / ols_var).where(ols_var > p.variance_epsilon)
        paired['ols_alpha_lagged'] = y_mean - paired.ols_beta_lagged * x_mean
        paired['one_step_residual'] = y - paired.ols_alpha_lagged - paired.ols_beta_lagged * x
        for prefix, size, lag in [('beta', p.beta_window, False), ('volatility', p.volatility_window, False),
                                  ('ols', p.ols_window, True)]:
            paired = paired.join(_window_details(paired.index, self.sessions, size, prefix, lag))
        residuals = paired.one_step_residual.dropna()
        residual_frame = _window_details(residuals.index, self.sessions, p.residual_window, 'residual')
        residual_frame['residual_sum'] = residuals.rolling(p.residual_window, min_periods=p.residual_window).sum()
        residual_frame['residual_std'] = residuals.rolling(p.residual_window, min_periods=p.residual_window).std(ddof=p.ddof)
        residual_frame['residual_momentum'] = (residual_frame.residual_sum /
            (np.sqrt(p.residual_window) * residual_frame.residual_std)).where(residual_frame.residual_std > p.residual_std_floor)
        paired = paired.join(residual_frame)
        paired['paired_observation_number'] = np.arange(pair_count) + 1
        output = paired.reindex(self.sessions)
        output['stock_return'] = stock_return
        output['market_return'] = market_return
        output['date'] = self.sessions
        output['symbol'] = symbol
        output['total_return_price'] = price
        output['quote_present'] = self._quotes[symbol]
        output['current_pair_valid'] = np.isfinite(stock_return) & np.isfinite(market_return)
        for prefix in ('beta', 'volatility', 'ols', 'residual'):
            output[prefix + '_obs_count'] = output[prefix + '_obs_count'].fillna(0).astype(int)
            output[prefix + '_span_sessions'] = output[prefix + '_span_sessions'].fillna(0).astype(int)
        output['paired_observation_number'] = output.paired_observation_number.fillna(0).astype(int)
        output['beta_valid'] = output.current_pair_valid & np.isfinite(output.beta) & output.beta_obs_count.eq(p.beta_window)
        output['volatility_valid'] = output.current_pair_valid & np.isfinite(output.volatility) & output.volatility_obs_count.eq(p.volatility_window)
        output['residual_valid'] = output.current_pair_valid & np.isfinite(output.residual_momentum) & output.residual_obs_count.eq(p.residual_window)
        output['common_valid'] = output.beta_valid & output.volatility_valid & output.residual_valid
        reason = np.full(len(output), '', dtype=object)
        # Ordered reasons are mutually exclusive first blockers, not claims that
        # all other factor checks would pass. Raw fields expose all diagnostics.
        checks = [
            (~output.quote_present, 'STOCK_QUOTE_MISSING_OR_ZERO_VOLUME'),
            (~np.isfinite(stock_return), 'STOCK_CONSECUTIVE_RETURN_MISSING'),
            (~np.isfinite(market_return), 'MARKET_CONSECUTIVE_RETURN_MISSING'),
            (output.beta_obs_count.lt(p.beta_window), 'BETA_WARMUP'),
            (~np.isfinite(output.beta), 'BETA_MARKET_VARIANCE_TOO_SMALL'),
            (output.volatility_obs_count.lt(p.volatility_window), 'VOLATILITY_WARMUP'),
            (~np.isfinite(output.volatility), 'VOLATILITY_NONFINITE'),
            (output.ols_obs_count.lt(p.ols_window), 'OLS_WARMUP'),
            (~np.isfinite(output.one_step_residual), 'OLS_MARKET_VARIANCE_TOO_SMALL'),
            (output.residual_obs_count.lt(p.residual_window), 'RESIDUAL_WARMUP'),
            (~np.isfinite(output.residual_momentum), 'RESIDUAL_STD_TOO_SMALL'),
        ]
        for mask, label in checks:
            reason[(reason == '') & np.asarray(mask, dtype=bool)] = label
        output['invalid_reason'] = np.where(output.common_valid, '', reason)
        output['available_through'] = pd.Series(self.sessions, index=self.sessions).where(output.current_pair_valid)
        return output.reset_index(drop=True)

    def callback(self, version: str) -> Callable[[Any, pd.DataFrame], tuple[pd.DataFrame, dict[str, Any]]]:
        version = str(version).upper()
        if version not in ('A', 'B', 'C', 'D'):
            raise ValueError('v3 version must be A, B, C or D')

        def apply(day: Any, ranked: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
            return self._transform(version, day, ranked)
        return apply

    def _transform(self, version: str, signal_day: Any,
                   ranked: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
        required = {'symbol', 'score', 'entry_ok', 'exit'}
        if required - set(ranked):
            raise ValueError(f'v3 ranked frame missing {sorted(required-set(ranked))}')
        if ranked.symbol.isna().any() or ranked.symbol.astype(str).duplicated().any() or ranked.index.has_duplicates:
            raise ValueError('v3 ranked symbols/index must be unique and nonmissing')
        if list(ranked.index.astype(str)) != list(ranked.symbol.astype(str)):
            raise ValueError('v3 ranked index must equal symbol column')
        day = _day(signal_day)
        if 'date' in ranked and len(ranked) and not ranked.date.map(_day).eq(day).all():
            raise ValueError('v3 callback day differs from ranked observation date')
        out = ranked.copy(deep=True)
        symbols = out.symbol.astype(str)
        if day in self._state_index.index:
            state = self._state_index.loc[day]
            factors = self._feature_index.xs(day, level='date').reindex(symbols).copy()
            regime, reason = str(state.regime), str(state.regime_reason)
        else:
            factors = pd.DataFrame(index=symbols)
            regime, reason = 'UNKNOWN', 'SIGNAL_DATE_NOT_IN_CALENDAR'
        for name in ('beta', 'volatility', 'residual_momentum'):
            if name not in factors: factors[name] = np.nan
        valid = factors.get('common_valid', pd.Series(False, index=symbols)).fillna(False).astype(bool)
        reasons = factors.get('invalid_reason', pd.Series(index=symbols, dtype=object)).fillna('SYMBOL_NOT_IN_FEATURE_UNIVERSE')
        out['v3_factor_valid'] = valid.to_numpy(bool)
        out['v3_factor_reason'] = reasons.to_numpy()
        out['v3_regime'] = regime
        out['v3_beta'] = factors.beta.to_numpy(float)
        out['v3_volatility'] = factors.volatility.to_numpy(float)
        out['v3_residual_momentum'] = factors.residual_momentum.to_numpy(float)
        b = .5 * ((-factors.loc[valid, 'beta']).rank(method=self.parameters.rank_ties, pct=True)
                  + (-factors.loc[valid, 'volatility']).rank(method=self.parameters.rank_ties, pct=True))
        c = factors.loc[valid, 'residual_momentum'].rank(method=self.parameters.rank_ties, pct=True)
        d = .5 * (b.rank(method=self.parameters.rank_ties, pct=True) + c)
        for column, series in [('v3_defensive_score', b), ('v3_residual_percentile', c), ('v3_blended_score', d)]:
            out[column] = series.reindex(symbols).to_numpy(float)
        count = int(valid.sum())
        if version == 'A':
            status = 'BASELINE_A'
        elif regime == 'UNKNOWN':
            status = 'BASELINE_FALLBACK_MARKET_UNKNOWN'
        elif regime != 'WEAK':
            status = 'NORMAL_MARKET_BASELINE'
        elif count < self.parameters.minimum_common_valid:
            status = 'BASELINE_FALLBACK_INSUFFICIENT_COMMON_VALID'
            reason = 'COMMON_VALID_BELOW_MINIMUM'
        else:
            status = 'WEAK_MARKET_RERANKED'
            values = {'B': b, 'C': c, 'D': d}[version]
            out['score'] = values.reindex(symbols).fillna(0.).to_numpy(float)
        out['v3_rank_applied'] = status == 'WEAK_MARKET_RERANKED'
        meta = dict(version=version, status=status, signal_date=str(day.date()),
            market_regime=regime, market_regime_reason=reason,
            common_valid_count=count, ranked_count=len(out), invalid_count=len(out)-count,
            invalid_reason_counts={str(k):int(v) for k,v in reasons[~valid].value_counts().items()},
            minimum_common_valid=self.parameters.minimum_common_valid,
            rank_universe='ALL_RANKED_COMMON_VALID_NOT_ENTRY_FILTERED',
            invalid_stock_policy='SCORE_ZERO_WHEN_RERANKING_ENTRY_EXIT_UNCHANGED',
            selection_scope='FIXED_DEVELOPMENT_EXPERIMENT_NOT_AUTOMATIC_DEPLOYMENT',
            available_through=str(day.date()) if regime != 'UNKNOWN' else None,
            parameters=asdict(self.parameters))
        # Exact gate preservation is the scientific intervention boundary.
        for column in ranked.columns:
            if column != 'score':
                pd.testing.assert_series_equal(out[column], ranked[column], check_names=False)
        return out, meta
