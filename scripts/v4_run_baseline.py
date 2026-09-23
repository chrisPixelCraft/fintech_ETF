#!/usr/bin/env python3
"""Paired, parameter-frozen V3 replay in the isolated V4 namespace."""
from __future__ import annotations
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    return value


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(clean(value), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n')


def episode_registry(study, calendar):
    dates = pd.DatetimeIndex(pd.to_datetime(calendar)).normalize()
    if not dates.is_unique or not dates.is_monotonic_increasing:
        raise ValueError('Calendar must contain unique increasing sessions')
    records = []
    for anchor in study['anchors']:
        start = (len(dates) - 24 - int(anchor['latest_offset']) if 'latest_offset' in anchor
                 else int(dates.searchsorted(pd.Timestamp(anchor['start']))))
        sessions = dates[start:start + 24]
        if start < 1 or len(sessions) != 24:
            raise ValueError('Incomplete requested window: ' + anchor['id'])
        records.append(dict(episode_id=anchor['id'], split=anchor['split'], start=str(sessions[0].date()),
                            end=str(sessions[-1].date()), prior_session_date=str(dates[start - 1].date()),
                            session_count=24, sessions=[str(d.date()) for d in sessions]))
    return records


def run(study_path, output):
    from src.strategy_24d import build_features, run_episode as legacy_episode
    from src.v4_baseline import run_episode
    from scripts.v4_verify import audit_episode, verify_execution_provenance
    study_path, output = Path(study_path), Path(output)
    if output.resolve() == ROOT / 'outputs' / 'v4' or ROOT / 'outputs' / 'v4' not in output.resolve().parents:
        raise ValueError('Run output must be a child of outputs/v4')
    if output.exists() and any(output.iterdir()):
        raise ValueError('Refusing to overwrite existing evidence; choose a new output directory')
    study = json.loads(study_path.read_text())
    config_path = Path(study['strategy_config'])
    config = json.loads(config_path.read_text())
    if study['stage'] != 1 or study['tuning'] != 'NONE' or config['initial_cash'] != 1e9:
        raise ValueError('Only the frozen Stage 1 baseline is supported')
    daily = pd.read_parquet(study['daily'])
    daily['date'] = pd.to_datetime(daily.date)
    universe = pd.read_csv(study['universe'])
    universe['symbol'] = universe.yahoo_symbol.astype(str)
    universe['known_at'] = universe.attachment_created_at
    execution = pd.read_csv(study['execution_data'], parse_dates=['date'])
    calendar = json.loads(Path(study['calendar']).read_text())['calendar']
    episodes = episode_registry(study, calendar)
    output.mkdir(parents=True)
    write_json(output / 'study.json', study)
    write_json(output / 'config.json', config)
    write_json(output / 'episodes.json', episodes)
    pd.DataFrame([{k: v for k, v in r.items() if k != 'sessions'} for r in episodes]).to_csv(output / 'episodes.csv', index=False)
    inputs = {name: study[name] for name in ['daily', 'universe', 'execution_data']}
    sources = [study_path, config_path, Path(study['calendar'])]
    sources += [Path(p) for p in inputs.values()]
    sources += sorted((ROOT / 'src').glob('*.py'))
    sources += [ROOT / 'scripts' / name for name in ['v4_run_baseline.py', 'v4_build_execution_data.py', 'v4_verify.py', 'audit_24d.py']]
    execution_manifest = Path(study['execution_data']).with_suffix('.manifest.json')
    sources.append(execution_manifest)
    metadata = json.loads(execution_manifest.read_text())
    if metadata['normalized_sha256'] != sha256(study['execution_data']):
        raise ValueError('Official normalized cache does not match its acquisition manifest')
    if metadata['universe_sha256'] != sha256(study['universe']):
        raise ValueError('Official cache uses a different universe')
    sources += [Path(a['raw_path']) for a in metadata['attempts'] if a['status'] == 'OK']
    sources = [p.relative_to(ROOT) if p.is_absolute() else p for p in sources]
    input_hashes = {str(p): sha256(p) for p in sources}
    verify_execution_provenance(study['execution_data'])
    panel = build_features(daily, config)
    rows, comparisons, price_rows, audits = [], [], [], []
    for episode in episodes:
        episode_config = dict(config, prior_session_date=episode['prior_session_date'])
        results = {}
        for mode in study['execution_modes']:
            actual_mode = 'official_average' if mode == 'official_average_official_close' else mode
            sizing = 'official_close' if mode == 'official_average_official_close' else 'signal_close'
            result = run_episode(daily, universe, episode_config, episode['sessions'],
                                 execution_data=execution, execution_mode=actual_mode, features=panel,
                                 sizing_price_mode=sizing)
            audit = audit_episode(result, daily, universe, execution, actual_mode)
            audits.append(dict(episode_id=episode['episode_id'], execution_mode=mode, audit=audit))
            location = output / 'ledgers' / mode / episode['episode_id']
            location.mkdir(parents=True)
            for name, table in result.items():
                if isinstance(table, pd.DataFrame):
                    table.to_csv(location / (name + '.csv'), index=False)
            write_json(location / 'config.json', result.get('config', episode_config))
            write_json(location / 'metrics.json', result['metrics'])
            write_json(location / 'audit.json', audit)
            rows.append({**result['metrics'], 'episode_id': episode['episode_id'],
                         'split': episode['split'], 'execution_mode': mode})
            results[mode] = result
            print(json.dumps(dict(episode=episode['episode_id'], mode=mode,
                                  status=result['metrics'].get('episode_status'))), flush=True)
        # Exact reproduction is checked against the original callable, not a copied implementation.
        old = legacy_episode(daily, universe, episode_config, episode['sessions'], features=panel)
        exact = {}
        for name in ['equity', 'trades', 'orders', 'holdings', 'compliance_daily', 'snapshots']:
            left, right = old[name], results['open_proxy'][name]
            try:
                pd.testing.assert_frame_equal(left, right[list(left.columns)], check_dtype=False)
                exact[name] = True
            except AssertionError:
                exact[name] = False
        comparisons.append(dict(episode_id=episode['episode_id'], exact=exact))
        if not all(exact.values()):
            raise ValueError('Open reproduction drift: ' + str(comparisons[-1]))
        for mode, result in results.items():
            for trade in result['trades'].to_dict('records'):
                date, symbol = pd.Timestamp(trade['date']), str(trade['symbol'])
                official = execution.loc[execution.date.eq(date) & execution.symbol.eq(symbol)]
                vendor = daily.loc[daily.date.eq(date) & daily.symbol.eq(symbol)]
                avg = official.average_execution_price.iloc[0] if len(official) == 1 else np.nan
                op = vendor.open.iloc[0] if len(vendor) == 1 else np.nan
                price_rows.append(dict(episode_id=episode['episode_id'], execution_mode=mode,
                                       date=str(date.date()), symbol=symbol, side='BUY' if trade['shares'] > 0 else 'SELL',
                                       shares=trade.get('shares'), open_price=op, official_average_price=avg,
                                       official_minus_open=avg-op, relative_difference=avg/op-1 if op > 0 else np.nan))
    pd.DataFrame(rows).to_csv(output / 'results.csv', index=False)
    pd.DataFrame(price_rows, columns=['episode_id','execution_mode','date','symbol','side','shares',
        'open_price','official_average_price','official_minus_open','relative_difference']).to_csv(output / 'trade_price_comparison.csv', index=False)
    write_json(output / 'open_reproduction.json', comparisons)
    write_json(output / 'audits.json', audits)
    if any(sha256(p) != digest for p, digest in input_hashes.items()):
        raise ValueError('Inputs changed during execution')
    output_hashes = {str(p.relative_to(output)): sha256(p) for p in sorted(output.rglob('*')) if p.is_file()}
    write_json(output / 'manifest.json', dict(schema_version=1, inputs=inputs, input_hashes=input_hashes,
               output_hashes=output_hashes, code_revision=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
               code_state='Source hashes bind working-tree implementation; revision is base commit',
               submission_status='BLOCK_SUBMISSION', study_id=study['study_id']))
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', default='config/v4_study.json')
    parser.add_argument('--output', default='outputs/v4/stage1')
    args = parser.parse_args()
    run(args.study, args.output)


if __name__ == '__main__':
    main()
