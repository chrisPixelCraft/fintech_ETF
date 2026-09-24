"""V3 Yahoo daily cache and explicit nominal-share research conversion.

Source Parquets are untouched yfinance history(auto_adjust=False, repair=True)
responses, not original exchange tapes. Yahoo OHLC/dividends/volume are expressed
in split-adjusted share units. Derived nominal prices reverse strictly later
splits; no dividend adjustment from Adj Close enters execution or indicators.
This assumes Yahoo's split/event history is complete and internally consistent.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE_ROOT = ROOT / 'data/yahoo_daily'
DEFAULT_CACHE = CACHE_ROOT / ('v3_' + datetime.now(ZoneInfo('Asia/Taipei')).strftime('%Y%m%d'))
UNIVERSE = ROOT / 'data/reference/universe_competition_20260731.csv'
REQUIRED = ['Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume', 'Dividends', 'Stock Splits']
CONVENTION = {
    'name': 'NOMINAL_SHARES_REVERSE_FUTURE_YAHOO_SPLITS_V1',
    'ohlc': 'Yahoo OHLC multiplied by product of split ratios strictly after row date through snapshot end.',
    'volume': 'Yahoo split-adjusted Volume divided by that same future split factor.',
    'dividend': 'Yahoo Dividends multiplied by future factor and same-day split ratio: per pre-action share.',
    'split': 'Yahoo Stock Splits; zero means no event and maps to ratio 1.',
    'adj_close': 'Retained unmodified for audit only; forbidden as an execution price or full-history signal adjustment.',
    'causality': 'Reverse normalization removes future splits from nominal units; strategy uses only observations <= previous close. Vendor revisions/repair are ex-post, not point-in-time evidence.',
    'limitation': 'Complete and correct Yahoo splits/normalization are assumptions; no official company-action reconciliation. Simultaneous dividend/split convention is research-only.',
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def official_symbols():
    frame = pd.read_csv(UNIVERSE, dtype={'ticker': str})
    symbols = frame.yahoo_symbol.tolist()
    if len(symbols) != 150 or len(set(symbols)) != 150 or '0050.TW' in symbols:
        raise ValueError('Expected exactly 150 official stocks, separate from 0050 calendar reference')
    return sorted(symbols)


def price_validity(frame):
    if not set(REQUIRED).issubset(frame.columns):
        return pd.Series(False, index=frame.index)
    values = frame[REQUIRED].apply(pd.to_numeric, errors='coerce')
    prices = values[['Open', 'High', 'Low', 'Close']]
    valid = np.isfinite(prices).all(axis=1) & (prices > 0).all(axis=1)
    valid &= np.isfinite(values.Volume) & (values.Volume >= 0)
    valid &= np.isfinite(values.Dividends) & (values.Dividends >= 0)
    valid &= np.isfinite(values['Stock Splits']) & (values['Stock Splits'] >= 0)
    tolerance = values.High.abs() * 1e-6 + 1e-8
    valid &= values.Low <= values[['Open', 'Close']].min(axis=1) + tolerance
    valid &= values.High + tolerance >= values[['Open', 'Close']].max(axis=1)
    valid &= values.High + tolerance >= values.Low
    return valid


def nominal_daily(raw, symbol):
    """Return separate derived rows; never mutate or remove raw observations.

    The future split factor is a unit conversion, not a forward trading signal.
    On a 2:1 split date, yesterday's nominal price is twice today's nominal price
    and holdings double exactly once. Future-split append invariance is tested.
    """
    if not isinstance(raw.index, pd.DatetimeIndex):
        raise ValueError(f'{symbol}: expected dated Yahoo index')
    dates = pd.Index(raw.index.strftime('%Y-%m-%d'))
    if not dates.is_unique or not dates.is_monotonic_increasing:
        raise ValueError(f'{symbol}: duplicate or unsorted dates cannot enter derived data')
    missing = set(REQUIRED) - set(raw.columns)
    if missing:
        raise ValueError(f'{symbol}: missing required columns {sorted(missing)}')
    numeric = raw[REQUIRED].apply(pd.to_numeric, errors='coerce')
    splits = numeric['Stock Splits'].where(numeric['Stock Splits'] != 0, 1.)
    if not (np.isfinite(splits) & (splits > 0)).all():
        raise ValueError(f'{symbol}: invalid split prevents nominal share conversion')
    # Exclude today's event: today's OHLC already trades in post-event units.
    future = splits.iloc[::-1].cumprod().iloc[::-1] / splits
    result = pd.DataFrame({'date': dates, 'symbol': symbol})
    for column in ['Open', 'High', 'Low', 'Close']:
        result[column.lower()] = (numeric[column] * future).to_numpy()
    result['adj_close'] = numeric['Adj Close'].to_numpy()
    result['volume'] = (numeric.Volume / future).to_numpy()
    result['dividend'] = (numeric.Dividends * future * splits).to_numpy()
    result['split_ratio'] = splits.to_numpy()
    result['split'] = splits.to_numpy()
    result['future_split_factor'] = future.to_numpy()
    result['vendor_repaired'] = raw.get('Repaired?', pd.Series(False, index=raw.index)).fillna(False).astype(bool).to_numpy()
    result['valid_price'] = price_validity(raw).to_numpy()
    result['tradable'] = result.valid_price & result.volume.gt(0)
    gross = (result.close * result.split + result.dividend) / result.close.shift()
    result['action_neutral_return'] = gross - 1.
    # Flags describe observations; they do not exclude the entire security.
    suspicious = gross.notna() & (~np.isfinite(gross) | (gross - 1).abs().gt(.30))
    result['quality_flags'] = np.where(result.valid_price, '', 'INVALID_OHLCV_OR_ACTION')
    result.loc[suspicious, 'quality_flags'] += '|ACTION_NEUTRAL_RETURN_GT_30PCT'
    result['quality_flags'] = result.quality_flags.str.strip('|')
    result['valid_for_research'] = result.valid_price & ~suspicious
    return result


def validate_frame(raw, symbol, calendar):
    dates = pd.Index(raw.index.strftime('%Y-%m-%d'))
    valid = price_validity(raw)
    first, last = (dates.min(), dates.max()) if len(dates) else (None, None)
    missing = sorted(set(calendar) - set(dates))
    result = {
        'rows': len(raw), 'first_date': first, 'last_date': last,
        'unique_dates': bool(dates.is_unique), 'monotonic_dates': bool(dates.is_monotonic_increasing),
        'missing_columns': sorted(set(REQUIRED) - set(raw.columns)),
        'invalid_price_dates': dates[~valid].tolist(),
        'leading_missing_dates': [d for d in missing if first and d < first],
        'internal_missing_dates': [d for d in missing if first and first <= d <= last],
        'trailing_missing_dates': [d for d in missing if last and d > last],
        'leading_missing_interpretation': 'Possible pre-listing or Yahoo truncation; first observation does not establish listing date.',
        'vendor_repaired_count': int(raw.get('Repaired?', pd.Series(False, index=raw.index)).fillna(False).astype(bool).sum()),
        'action_rows': [], 'derived_error': None,
    }
    for pos in np.flatnonzero(raw[['Dividends', 'Stock Splits']].fillna(0).ne(0).any(axis=1).to_numpy()):
        row = raw.iloc[pos]
        result['action_rows'].append({'date': dates[pos], 'dividend': float(row.Dividends), 'split': float(row['Stock Splits'])})
    try:
        derived = nominal_daily(raw, symbol)
        result['quality_alerts'] = derived.loc[derived.quality_flags.ne(''), ['date', 'quality_flags']].to_dict('records')
    except ValueError as exc:
        result['derived_error'] = str(exc)
        result['quality_alerts'] = []
    return result


def resolve_cache(cache_dir=None):
    if cache_dir is not None:
        return Path(cache_dir)
    if (DEFAULT_CACHE / 'metadata.json').exists():
        return DEFAULT_CACHE
    complete = sorted(CACHE_ROOT.glob('v3_*/metadata.json'))
    if not complete:
        raise FileNotFoundError('Run scripts/download_yahoo_daily.py to acquire the v3 Yahoo cache')
    return complete[-1].parent


def load_metadata(cache_dir=None):
    folder = resolve_cache(cache_dir)
    metadata = json.loads((folder / 'metadata.json').read_text())
    if metadata['universe_sha256'] != sha256(UNIVERSE):
        raise ValueError('Official universe differs from immutable snapshot')
    for filename, digest in metadata['artifact_sha256'].items():
        if sha256(folder / filename) != digest:
            raise ValueError(f'Yahoo cache hash mismatch: {filename}')
    return metadata


def load_daily(cache_dir=None, include_benchmark=False):
    """Load all derived rows, including flagged rows; callers choose per-date eligibility.

    Default contains only official stocks. Calendar combines the ETF reference
    with broad stock observations, so ETF suspensions cannot remove market days.
    Missing symbols remain recorded in metadata.
    """
    folder = resolve_cache(cache_dir)
    metadata = load_metadata(folder)
    daily = pd.read_parquet(folder / metadata['derived_file'])
    if not include_benchmark:
        daily = daily.loc[daily.symbol.ne('0050.TW')].copy()
    daily.attrs['metadata'] = metadata
    daily.attrs['calendar'] = load_calendar(folder)
    return daily


def broad_observation_calendar(daily, benchmark_dates, minimum_stocks=20):
    """Observed weekday calendar; an ETF suspension is not a market closure.

    A stock contributes only with positive finite OHLC and volume, valid bounds,
    and (when present) valid_price. This is a research proxy, not an official
    exchange calendar. The threshold is fixed before episode evaluation.
    """
    stock = daily.loc[daily.symbol.isin(official_symbols())].copy()
    numeric = stock[['open', 'high', 'low', 'close', 'volume']].apply(pd.to_numeric, errors='coerce')
    valid = np.isfinite(numeric).all(axis=1) & numeric.gt(0).all(axis=1)
    valid &= numeric.high + 1e-5 >= numeric[['open', 'low', 'close']].max(axis=1)
    valid &= numeric.low - 1e-5 <= numeric[['open', 'high', 'close']].min(axis=1)
    if 'valid_price' in stock:
        valid &= stock.valid_price.fillna(False).astype(bool)
    stock['date'] = pd.to_datetime(stock.date).dt.strftime('%Y-%m-%d')
    counts = stock.loc[valid].groupby('date').symbol.nunique()
    dates = set(benchmark_dates) | set(counts.index[counts.ge(minimum_stocks)])
    calendar = sorted(d for d in dates if pd.Timestamp(d).dayofweek < 5)
    return calendar, {d: int(counts.get(d, 0)) for d in calendar}


def calendar_amendment(cache_dir=None, write=False):
    """Create/verify a separate deterministic amendment; never change old metadata."""
    folder = resolve_cache(cache_dir)
    metadata = load_metadata(folder)
    daily = pd.read_parquet(folder / metadata['derived_file'])
    calendar, counts = broad_observation_calendar(daily, metadata['calendar'])
    def digest(value):
        return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    expected = {
        'schema_version': 2,
        'policy': 'WEEKDAY_0050_OBSERVATIONS_UNION_AT_LEAST_20_VALID_POSITIVE_VOLUME_OFFICIAL_STOCKS',
        'interpretation': 'Research observed-session proxy; official exchange calendar remains unverified.',
        'reason': 'ETF suspension or missing ETF bar must not remove a broadly observed stock-market session.',
        'source_metadata_sha256': sha256(folder / 'metadata.json'),
        'source_derived_sha256': sha256(folder / metadata['derived_file']),
        'original_calendar_sha256': digest(metadata['calendar']),
        'amended_calendar_sha256': digest(calendar),
        'calendar': calendar,
        'added_dates': sorted(set(calendar) - set(metadata['calendar'])),
        'removed_dates': sorted(set(metadata['calendar']) - set(calendar)),
        'valid_stock_count': counts,
    }
    path = folder / 'calendar_v2.json'
    if path.exists():
        if json.loads(path.read_text()) != expected:
            raise ValueError('Calendar amendment mismatch against immutable source observations')
    elif write:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(expected, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write('\n')
    else:
        raise FileNotFoundError('Calendar amendment missing; rerun scripts/download_yahoo_daily.py to verify and create it')
    return expected


def load_calendar(cache_dir=None):
    return list(calendar_amendment(cache_dir)['calendar'])
