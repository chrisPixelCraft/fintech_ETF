"""Independent Stage 1 accounting reconstruction and artifact authentication.

The existing mathematical auditor is reused with an independently reconstructed
market panel. No V4 producer price-selection or ledger helper is imported.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import gzip
import json
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_24d import audit_episode as audit_v3_episode
from scripts.audit_24d import verify_hashes


def _require(condition, message):
    if not condition:
        raise AssertionError(message)


def _audit_market(daily, execution_data, execution_mode, sizing_price_mode='signal_close'):
    market = daily.copy()
    market['date'] = pd.to_datetime(market.date).dt.normalize()
    if execution_mode == 'open_proxy' and sizing_price_mode == 'signal_close':
        return market
    _require(execution_mode in {'official_average', 'open_proxy'}, 'Unknown execution mode')
    columns = ['date', 'symbol', 'close', 'volume', 'trading_value',
               'average_execution_price', 'source', 'quality_flags',
               'official_execution_available']
    official = pd.DataFrame(columns=columns) if execution_data is None else execution_data.copy()
    official['date'] = pd.to_datetime(official.date).dt.normalize()
    _require(not official.duplicated(['date', 'symbol']).any(), 'Duplicate official observations')
    selected = official.set_index(['date', 'symbol'])
    index = pd.MultiIndex.from_frame(market[['date', 'symbol']])
    aligned = selected.reindex(index)
    volume = pd.to_numeric(aligned['volume'], errors='coerce').to_numpy(float)
    value = pd.to_numeric(aligned['trading_value'], errors='coerce').to_numpy(float)
    names = aligned.index.get_level_values('symbol').astype(str)
    available = ((names.str.endswith('.TW') & aligned.source.eq('TWSE_OFFICIAL').to_numpy())
                 | (names.str.endswith('.TWO') & aligned.source.eq('TPEX_OFFICIAL').to_numpy()))
    valid = available & np.isfinite(volume) & (volume > 0) & np.isfinite(value) & (value > 0)
    expected = np.divide(value, volume, out=np.full(len(value), np.nan), where=valid)
    recorded = pd.to_numeric(aligned.average_execution_price, errors='coerce').to_numpy(float)
    _require(np.all(np.isclose(expected[valid], recorded[valid], rtol=1e-12, atol=1e-10)),
             'Official price differs from raw trading value / shares')
    if execution_mode == 'official_average':
        market['open'] = expected
        market['execution_volume'] = volume
    if sizing_price_mode == 'official_close':
        close = pd.to_numeric(aligned.close, errors='coerce').to_numpy(float)
        market['close'] = np.where(available & np.isfinite(close) & (close > 0), close, np.nan)
    # Stage 1 isolates execution: both tracks retain identical Yahoo sizing and
    # valuation closes. Exchange-close parity is not certified by this audit.
    return market


def audit_episode(result, daily, universe, execution_data=None,
                  execution_mode='official_average'):
    """Audit prices, submitted orders, actions, fees, rollback and settled NAV."""
    universe = universe.copy()
    if 'symbol' not in universe:
        universe['symbol'] = universe.yahoo_symbol.astype(str)
    if 'known_at' not in universe and 'attachment_created_at' in universe:
        universe['known_at'] = universe.attachment_created_at
    market = _audit_market(daily, execution_data, execution_mode,
                           result['config'].get('sizing_price_mode', 'signal_close'))
    candidate = deepcopy(result)
    if execution_mode == 'official_average' and not candidate['warnings'].empty:
        candidate['warnings']['issue'] = candidate['warnings'].issue.replace({
            'UNFILLED_MISSING_OFFICIAL_AVERAGE': 'UNFILLED_MISSING_OPEN_PROXY',
            'UNFILLED_MISSING_OFFICIAL_EXECUTION': 'UNFILLED_MISSING_OPEN_PROXY',
            'UNFILLED_MISSING_EXECUTION_PRICE': 'UNFILLED_MISSING_OPEN_PROXY'})
    audit = audit_v3_episode(candidate, {'daily': market, 'universe': universe})
    audit['execution_mode'] = execution_mode
    audit['verification_scope'] = 'INDEPENDENT_ACCOUNTING_NOT_OFFICIAL_CERTIFICATION'
    return audit


def _read(path):
    if path.suffix == '.parquet':
        return pd.read_parquet(path)
    try:
        frame = pd.read_csv(path, keep_default_na=False)
        for column in frame:
            if column in {'volume_participation', 'execution_volume', 'max_daily_volume_participation'}:
                frame[column] = pd.to_numeric(frame[column], errors='coerce')
        return frame
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def verify_execution_provenance(table_path):
    """Bind normalized quantities to authenticated, header-identified raw rows.

    This intentionally does not import the producer's parser or normalizer.
    A failed fetch authenticates no price and may only back missing rows.
    """
    table_path = Path(table_path)
    metadata = json.loads(table_path.with_suffix('.manifest.json').read_text())
    _require(hashlib.sha256(table_path.read_bytes()).hexdigest() == metadata['normalized_sha256'],
             'Normalized execution hash mismatch')
    frame = pd.read_csv(table_path, keep_default_na=False)
    _require(not frame.duplicated(['date', 'symbol']).any(), 'Duplicate normalized rows')
    attempts = {(r['date'], r['market']): r for r in metadata['attempts']}
    _require(len(attempts) == len(metadata['attempts']), 'Duplicate official fetch attempts')
    parsed = {}

    def number(value):
        try:
            return float(str(value).replace(',', '').strip())
        except (TypeError, ValueError):
            return float('nan')

    for key, attempt in attempts.items():
        if attempt['status'] != 'OK':
            continue
        raw_path = Path(attempt['raw_path'])
        if not raw_path.is_absolute():
            raw_path = ROOT / raw_path
        content = raw_path.read_bytes()
        if raw_path.suffix == '.gz':
            _require(hashlib.sha256(content).hexdigest() == attempt['compressed_sha256'],
                     'Compressed raw hash mismatch')
            content = gzip.decompress(content)
        _require(hashlib.sha256(content).hexdigest() == attempt['raw_sha256'],
                 'Raw HTTP body hash mismatch')
        payload = json.loads(content)
        raw_date = str(payload['date'])
        if '/' in raw_date and int(raw_date.split('/')[0]) < 1911:
            year, month, day = raw_date.split('/')
            raw_date = f'{int(year)+1911}-{month}-{day}'
        _require(str(pd.Timestamp(raw_date).date()) == key[0], 'Raw response date differs')
        fields = ({'symbol': '證券代號', 'open': '開盤價', 'high': '最高價',
                   'low': '最低價', 'close': '收盤價', 'volume': '成交股數',
                   'trading_value': '成交金額'} if key[1] == 'TWSE' else
                  {'symbol': '代號', 'open': '開盤', 'high': '最高', 'low': '最低',
                   'close': '收盤', 'volume': '成交股數', 'trading_value': '成交金額(元)'})
        tables = [t for t in payload.get('tables', [])
                  if set(fields.values()) <= set(t.get('fields', []))
                  and (key[1] != 'TPEx' or t.get('title', '上櫃股票行情') == '上櫃股票行情')]
        _require(len(tables) == 1, 'Raw exact-unit table is ambiguous')
        table = tables[0]
        records = {}
        for values in table['data']:
            raw = {k: values[table['fields'].index(v)] for k, v in fields.items()}
            symbol = str(raw.pop('symbol')) + ('.TW' if key[1] == 'TWSE' else '.TWO')
            _require(symbol not in records, 'Duplicate raw security')
            records[symbol] = {k: number(v) for k, v in raw.items()}
        parsed[key] = records
    available_count = 0
    for row in frame.to_dict('records'):
        symbol, date = str(row['symbol']), str(row['date'])
        market = 'TPEx' if symbol.endswith('.TWO') else 'TWSE'
        key = (date, market)
        _require(key in attempts, 'Normalized row lacks fetch attempt')
        raw = parsed.get(key, {}).get(symbol)
        if raw is None:
            _require(row['source'] == 'MISSING_OFFICIAL', 'Missing raw row claims official source')
            _require(str(row['official_execution_available']).lower() == 'false',
                     'Missing raw row claims available execution')
            _require(not math.isfinite(number(row['average_execution_price'])),
                     'Missing raw row contains fill price')
            _require(not math.isfinite(number(row['close'])), 'Missing raw row contains sizing close')
            continue
        source = 'TWSE_OFFICIAL' if market == 'TWSE' else 'TPEX_OFFICIAL'
        _require(row['source'] == source, 'Normalized official market mismatch')
        _require(row['raw_sha256'] == attempts[key]['raw_sha256']
                 and row['source_url'] == attempts[key]['url'], 'Normalized raw provenance differs')
        for field, value in raw.items():
            observed = number(row[field])
            _require((math.isnan(value) and math.isnan(observed)) or
                     math.isclose(value, observed, rel_tol=1e-12, abs_tol=1e-9),
                     'Normalized value differs from raw: ' + field)
        valid = all(math.isfinite(raw[k]) and raw[k] > 0 for k in ['volume', 'trading_value'])
        _require((str(row['official_execution_available']).lower() == 'true') == valid,
                 'Normalized availability differs from raw')
        if valid:
            _require(math.isclose(number(row['average_execution_price']),
                                 raw['trading_value'] / raw['volume'], rel_tol=1e-12, abs_tol=1e-9),
                     'Normalized average differs from raw')
            available_count += 1
    _require(len(frame) == metadata['rows'] and available_count == metadata['available_rows'],
             'Normalized coverage summary differs')
    return {'status': 'PASS_RAW_PROVENANCE', 'rows': len(frame), 'available_rows': available_count}


def verify_directory(directory):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / 'manifest.json').read_text())
    verify_hashes(directory, manifest['output_hashes'])
    for filename, digest in manifest['input_hashes'].items():
        path = Path(filename)
        if not path.is_absolute():
            path = ROOT / path
        _require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest,
                 'Input hash mismatch: ' + filename)
    inputs = {}
    for key, filename in manifest['inputs'].items():
        path = Path(filename)
        inputs[key] = _read(path if path.is_absolute() else ROOT / path)
    execution_path = Path(manifest['inputs']['execution_data'])
    if not execution_path.is_absolute():
        execution_path = ROOT / execution_path
    provenance = verify_execution_provenance(execution_path)
    rows = []
    expected = pd.read_csv(directory / 'results.csv')
    registry = json.loads((directory / 'episodes.json').read_text())
    study = json.loads((directory / 'study.json').read_text())
    expected_pairs = {(mode, episode['episode_id']) for mode in study['execution_modes']
                      for episode in registry}
    _require(len(expected_pairs) == len(study['execution_modes']) * len(registry),
             'Duplicate registered attempts')
    actual_pairs = set(zip(expected.execution_mode, expected.episode_id))
    _require(actual_pairs == expected_pairs, 'Attempted episode/mode denominator differs from registry')
    by_episode = {episode['episode_id']: episode for episode in registry}
    _require(not expected.duplicated(['execution_mode', 'episode_id']).any(), 'Duplicate result rows')
    for row in expected.itertuples(index=False):
        relative = Path('ledgers') / row.execution_mode / row.episode_id
        path = directory / relative
        _require(path.is_dir(), 'Missing attempted episode ledger')
        result = {p.stem: _read(p) for p in path.glob('*.csv')}
        result['config'] = json.loads((path / 'config.json').read_text())
        result['metrics'] = json.loads((path / 'metrics.json').read_text())
        for filename in [*path.glob('*.csv'), path / 'metrics.json', path / 'config.json']:
            _require(str(filename.relative_to(directory)) in manifest['output_hashes'],
                     'Unsealed ledger artifact: ' + str(filename))
        episode = by_episode[row.episode_id]
        _require(result['config']['start'] == episode['start']
                 and result['config']['end'] == episode['end']
                 and len(episode['sessions']) == episode['session_count'] == 24,
                 'Ledger window differs from registry')
        published = row._asdict()
        for key, value in result['metrics'].items():
            _require(key in published, 'Summary omitted metric: ' + key)
            actual = published[key]
            if value is None or value == '':
                _require(pd.isna(actual) or actual == '', 'Summary metric differs: ' + key)
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                _require(math.isclose(float(actual), value, rel_tol=1e-11, abs_tol=1e-10),
                         'Summary metric differs: ' + key)
            elif key == 'execution_mode':
                _require(value == result['config'].get('execution', value), 'Execution identity differs')
            else:
                _require(actual == value, 'Summary metric differs: ' + key)
        mode = result['config'].get('execution', row.execution_mode)
        audit = audit_episode(result, inputs['daily'], inputs['universe'],
                              inputs.get('execution_data'), mode)
        rows.append(dict(episode_id=row.episode_id, **audit))
    _require(bool(rows), 'No attempted episodes')
    return {'status': 'PASS_INDEPENDENT_ACCOUNTING', 'attempted': len(rows),
            'execution_provenance': provenance, 'episodes': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', '--output', type=Path, default=ROOT / 'outputs/v4/stage1')
    args = parser.parse_args()
    report = verify_directory(args.output_dir)
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
