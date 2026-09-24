"""Resumable, immutable v3 Yahoo daily acquisition; reruns verify existing snapshots."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from importlib.metadata import version
import json
from pathlib import Path
import platform
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
import yfinance as yf
from src.yahoo_daily import (CONVENTION, DEFAULT_CACHE, UNIVERSE, calendar_amendment, load_metadata,
                             nominal_daily, official_symbols, sha256, validate_frame)


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def request_args(end=None):
    now = datetime.now(ZoneInfo('Asia/Taipei'))
    # Current session may be provisional before the market has closed.
    end = end or (now.date() + timedelta(days=int(now.hour >= 14))).isoformat()
    return dict(start='2009-01-01', end=end, interval='1d', auto_adjust=False,
                back_adjust=False, actions=True, repair=True, keepna=True,
                rounding=False, prepost=False, timeout=30, raise_errors=True)


def write_json(path, value):
    path = Path(path)
    if path.exists():
        if json.loads(path.read_text()) != value:
            raise ValueError(f'Immutable JSON differs: {path}')
        return
    temporary = path.with_suffix(path.suffix + '.pending')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    temporary.replace(path)


def acquire(cache_dir=DEFAULT_CACHE, end=None, attempts=3):
    folder = Path(cache_dir)
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / 'metadata.json').exists():
        metadata = load_metadata(folder)
        if end is not None and metadata['request']['end'] != end:
            raise ValueError('Different end date requires a new immutable cache directory')
        calendar_amendment(folder, write=True)
        print(json.dumps({'reused': True, 'status': metadata['status'], 'downloaded': metadata['downloaded_count']}), flush=True)
        return metadata
    request_path = folder / 'request.json'
    if request_path.exists():
        acquisition = json.loads(request_path.read_text())
        if acquisition['universe_sha256'] != sha256(UNIVERSE):
            raise ValueError('Cannot resume against changed official universe')
        if end is not None and acquisition['request']['end'] != end:
            raise ValueError('Cannot resume with a different end date')
    else:
        acquisition = {'request': request_args(end), 'created_utc': utcnow(),
                       'universe_sha256': sha256(UNIVERSE),
                       'versions': {n: version(n) for n in ['yfinance', 'pyarrow', 'pandas', 'numpy', 'curl_cffi']},
                       'python_version': platform.python_version(),
                       'code_sha256': {p: sha256(ROOT / p) for p in ['scripts/download_yahoo_daily.py', 'src/yahoo_daily.py', 'requirements-yahoo.txt']}}
        write_json(request_path, acquisition)
    request = acquisition['request']
    symbols = ['0050.TW'] + official_symbols()
    yf.set_tz_cache_location('/tmp/fintech_etf_yfinance_v3_cache')
    entries = {}

    def log(message):
        line = utcnow() + ' ' + message
        print(line, flush=True)
        with (folder / 'acquisition.log').open('a') as stream:
            stream.write(line + '\n')

    for number, symbol in enumerate(symbols, 1):
        parquet = folder / f'{symbol}.parquet'
        sidecar = folder / f'{symbol}.json'
        if sidecar.exists():
            entry = json.loads(sidecar.read_text())
            if entry['request'] != request:
                raise ValueError(f'{symbol}: request mismatch')
            if entry['status'] == 'DOWNLOADED' and sha256(parquet) != entry['sha256']:
                raise ValueError(f'{symbol}: raw cache hash mismatch')
            entries[symbol] = entry
            log(f'{number}/151 {symbol} REUSED {entry["status"]}')
            continue
        if parquet.exists():
            raise ValueError(f'Unregistered source file requires inspection: {parquet}')
        entry = {'symbol': symbol, 'request': request, 'errors': [], 'status': 'FAILED'}
        for attempt in range(1, attempts + 1):
            fetched = utcnow()
            try:
                raw = yf.Ticker(symbol).history(**request)
                if raw is None or raw.empty:
                    raise ValueError('Yahoo returned no daily observations in requested range')
                temporary = parquet.with_suffix('.parquet.pending')
                raw.to_parquet(temporary, engine='pyarrow', index=True)
                temporary.replace(parquet)
                entry.update(status='DOWNLOADED', file=parquet.name, sha256=sha256(parquet),
                             fetch_utc=fetched, completed_utc=utcnow(), rows=len(raw), columns=list(raw.columns))
                break
            except Exception as exc:
                entry['errors'].append({'attempt': attempt, 'utc': fetched, 'type': type(exc).__name__, 'message': str(exc)})
                log(f'{symbol} attempt={attempt} {type(exc).__name__}: {exc}')
                if attempt < attempts:
                    time.sleep(min(2 ** attempt, 8))
        write_json(sidecar, entry)
        entries[symbol] = entry
        log(f'{number}/151 {symbol} {entry["status"]} rows={entry.get("rows", 0)}')
    frames = {s: pd.read_parquet(folder / e['file']) for s, e in entries.items() if e['status'] == 'DOWNLOADED'}
    benchmark = frames.get('0050.TW')
    calendar = [] if benchmark is None else benchmark.index[(benchmark.Close > 0) & (benchmark.Volume > 0)].strftime('%Y-%m-%d').tolist()
    validation = {'calendar_definition': '0050.TW observed finite positive Close and Volume; invalid OHLC rows retained, not an official exchange calendar.',
                  'calendar_dates': calendar, 'symbols': {}, 'missing_symbols': [s for s in symbols if s not in frames]}
    derived = []
    for symbol, frame in frames.items():
        evidence = validate_frame(frame, symbol, calendar)
        validation['symbols'][symbol] = evidence
        if evidence['derived_error'] is None:
            derived.append(nominal_daily(frame, symbol))
    if not derived:
        raise ValueError('No usable derived frames; raw failures preserved for investigation')
    daily = pd.concat(derived, ignore_index=True).sort_values(['date', 'symbol']).reset_index(drop=True)
    derived_path = folder / 'nominal_daily.parquet'
    if derived_path.exists():
        pd.testing.assert_frame_equal(pd.read_parquet(derived_path), daily)
    else:
        daily.to_parquet(derived_path, index=False)
    write_json(folder / 'validation.json', validation)
    artifacts = {path.name: sha256(path) for path in folder.iterdir() if path.suffix in {'.parquet', '.json'} and path.name != 'metadata.json'}
    metadata = dict(acquisition)
    metadata.update(schema_version=3, dataset='competition_24d_yahoo_daily',
                    status='COMPLETE_WITH_QUALITY_FLAGS' if len(frames) == len(symbols) else 'PARTIAL_COVERAGE',
                    finished_utc=utcnow(), universe_path=str(UNIVERSE.relative_to(ROOT)),
                    requested_symbols=symbols, requested_count=len(symbols), downloaded_count=len(frames),
                    symbols=entries, calendar=calendar, derived_file=derived_path.name,
                    artifact_sha256=artifacts, share_convention=CONVENTION,
                    interpretation='COMPETITION_UNIVERSE_STRESS_TEST',
                    universe_bias='2026 official whitelist applied retrospectively; composition and survivorship look-ahead, not historically deployable point-in-time results.',
                    repair_provenance='Preserved exact yfinance repair=True daily output including Repaired?. Library may internally use intraday reconstruction; strategy/cache inputs are daily only. Vendor repair is ex-post and may be wrong.',
                    source_links=['https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html', 'https://github.com/ranaroussi/yfinance/wiki/Price-repair'],
                    attempts_per_symbol_max=attempts,
                    missing_symbols=validation['missing_symbols'],
                    invalid_price_rows=int((~daily.valid_price).sum()),
                    quality_alert_rows=int(daily.quality_flags.ne('').sum()))
    write_json(folder / 'metadata.json', metadata)
    load_metadata(folder)
    calendar_amendment(folder, write=True)
    log(f'FINISHED {metadata["status"]} downloaded={len(frames)}/151 rows={len(daily)} invalid={metadata["invalid_price_rows"]}')
    return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', type=Path, default=DEFAULT_CACHE)
    parser.add_argument('--end', help='Exclusive end date; default latest completed Taipei session')
    parser.add_argument('--attempts', type=int, default=3, choices=range(1, 6))
    args = parser.parse_args()
    acquire(args.cache_dir, args.end, args.attempts)
