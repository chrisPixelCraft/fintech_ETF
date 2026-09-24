#!/usr/bin/env python3
"""Predeclare Stage 2 windows and build an isolated resumable official cache."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import gzip
from types import FunctionType
import time
from contextlib import nullcontext

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.v4_build_execution_data import build_execution_data
from scripts.v4_run_baseline import episode_registry
from src.v4_execution import official_url, parse_official_day, content_sha256


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def prepare(study_path):
    study_path = Path(study_path)
    study = json.loads(study_path.read_text())
    output = ROOT / 'outputs/v4/stage2_data'
    output.mkdir(parents=True, exist_ok=True)
    calendar = json.loads(Path(study['calendar']).read_text())['calendar']
    episodes = episode_registry(study, calendar)
    dates = sorted({date for episode in episodes
                    for date in [episode['prior_session_date'], *episode['sessions']]})
    # Save the registry and requested dates before any market query or result.
    if (output / 'episodes.json').exists():
        if json.loads((output / 'episodes.json').read_text()) != episodes:
            raise ValueError('Refusing to change predeclared Stage 2 windows')
    else:
        write_json(output / 'episodes.json', episodes)
        pd.DataFrame([{k: v for k, v in e.items() if k != 'sessions'}
                      for e in episodes]).to_csv(output / 'episodes.csv', index=False)
        pd.DataFrame({'date': dates}).to_csv(output / 'requested_dates.csv', index=False)
    manifest = dict(schema_version=1, status='PREDECLARED_BEFORE_EXECUTION',
                    study_path=str(study_path), study_sha256=digest(study_path),
                    calendar_sha256=digest(study['calendar']),
                    daily_sha256=digest(study['daily']),
                    universe_sha256=digest(study['universe']),
                    episodes_sha256=digest(output / 'episodes.json'),
                    requested_dates_sha256=digest(output / 'requested_dates.csv'),
                    episode_count=len(episodes), unique_dates=len(dates),
                    split_counts=pd.Series([e['split'] for e in episodes]).value_counts().to_dict(),
                    selection_policy=study['selection_policy'],
                    diagnostics_policy='AFTER_FREEZE_ONLY_NEVER_PARAMETER_SELECTION')
    if (output / 'registry_manifest.json').exists():
        if json.loads((output / 'registry_manifest.json').read_text()) != manifest:
            raise ValueError('Predeclared study/input hashes changed')
    else:
        write_json(output / 'registry_manifest.json', manifest)
    raw = output / 'official_raw'
    raw.mkdir(exist_ok=True)
    reused = 0
    for date in dates:
        for market in ('TWSE', 'TPEx'):
            name = f"{market}_{date.replace('-', '')}.json.gz"
            source = ROOT / 'outputs/v4/official_raw' / name
            target = raw / name
            if source.exists() and not target.exists():
                shutil.copyfile(source, target)
                reused += 1
    print(json.dumps(dict(episodes=len(episodes), unique_dates=len(dates),
                          requests=2 * len(dates), reused_raw=reused)), flush=True)
    return study, dates, output


def recover_missing(dates, output, workers, delay=0.):
    """Recover transient acquisition failures without changing the sealed parser.

    Requests handles the exchanges' redirects separately from urllib. Only
    absent raw files are fetched; accepted dates/schemas are checked before save.
    The original acquisition evidence is retained before rebuilding the table.
    """
    import requests
    archive = output / 'acquisition_round1'
    if not archive.exists():
        archive.mkdir()
        for name in ('execution_data.csv', 'execution_data.manifest.json', 'download_status.json'):
            if (output / name).exists():
                shutil.copyfile(output / name, archive / name)
    raw_dir = output / 'official_raw'
    tasks = [(date, market) for date in dates for market in ('TWSE', 'TPEx')
             if not (raw_dir / f"{market}_{date.replace('-', '')}.json.gz").exists()]

    def fetch(task):
        date, market = task
        requested_url = official_url(market, date)
        record = dict(date=date, market=market, requested_url=requested_url)
        try:
            if delay:
                time.sleep(delay)
            response = requests.get(requested_url, headers={'User-Agent': 'Python-urllib/3.10'}, timeout=30)
            response.raise_for_status()
            content = response.content
            parse_official_day(response.json(), market, date)
            path = raw_dir / f"{market}_{date.replace('-', '')}.json.gz"
            path.write_bytes(gzip.compress(content, mtime=0))
            record.update(status='OK', response_url=response.url,
                          raw_sha256=content_sha256(content), raw_path=str(path))
        except Exception as error:
            record.update(status='FAILED', error=f'{type(error).__name__}: {error}')
        return record

    records = []
    log = output / 'recovery_attempts.jsonl'
    progress = dict(pid=os.getpid(), status='RECOVERING_SERIAL' if workers == 1 else 'RECOVERING',
                    workers=workers, expected_requests=len(tasks), completed_requests=0,
                    recovered_requests=0, last_success=None)
    write_json(output / 'serial_recovery_status.json', progress)
    context = ThreadPoolExecutor(max_workers=workers) if workers > 1 else nullcontext()
    with context as pool, log.open('a') as handle:
        iterator = pool.map(fetch, tasks) if pool is not None else map(fetch, tasks)
        for record in iterator:
            records.append(record)
            handle.write(json.dumps(record, sort_keys=True) + '\n')
            handle.flush()
            progress.update(completed_requests=len(records),
                            recovered_requests=sum(r['status'] == 'OK' for r in records),
                            checked_at=datetime.now(timezone.utc).isoformat())
            if record['status'] == 'OK':
                progress['last_success'] = record['date']
            write_json(output / 'serial_recovery_status.json', progress)
            if '428' in record.get('error', ''):
                raise RuntimeError('Official HTTP428 challenge recurred; stop acquisition and preserve raw cache')
            if len(records) % 25 == 0 or len(records) == len(tasks):
                print(json.dumps(dict(recovery_completed=len(records), recovery_total=len(tasks),
                                      recovered=sum(r['status'] == 'OK' for r in records))), flush=True)


def build_offline(dates, universe_path, output, workers):
    """Materialize all acquired raw rows and explicit gaps without network I/O.

    A private function namespace substitutes only the network opener, leaving
    the sealed Stage 1 builder module and its parser unchanged.
    """
    def no_network(*args, **kwargs):
        raise ConnectionError('OFFLINE_UNAVAILABLE_AFTER_OFFICIAL_HTTP428_CHALLENGE')
    namespace = dict(build_execution_data.__globals__, build_opener=no_network)
    builder = FunctionType(build_execution_data.__code__, namespace,
                           build_execution_data.__name__, build_execution_data.__defaults__,
                           build_execution_data.__closure__)
    return builder(dates, universe_path, output, workers=workers)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--study', default='config/v4_stage2_study.json')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--recover-missing', action='store_true')
    parser.add_argument('--offline', action='store_true', help='Rebuild only already acquired official rows; never access network')
    parser.add_argument('--recovery-delay', type=float, default=0., help='Seconds before each recovery request')
    args = parser.parse_args()
    study, dates, output = prepare(args.study)
    if args.prepare_only:
        return
    status = dict(pid=os.getpid(), status='RECOVERING_SERIAL' if args.recover_missing and args.workers == 1 else 'RUNNING', workers=args.workers,
                  started_at=datetime.now(timezone.utc).isoformat(),
                  command=f'{sys.executable} scripts/v4_stage2_data.py --workers {args.workers}')
    write_json(output / 'download_status.json', status)
    try:
        if args.recover_missing and args.offline:
            raise ValueError('Recovery and offline modes are mutually exclusive')
        if args.recover_missing:
            recover_missing(dates, output, args.workers, args.recovery_delay)
        builder = build_offline if args.offline else build_execution_data
        result = builder(dates, study['universe'], output / 'execution_data.csv', workers=args.workers)
        if args.offline:
            result.update(acquisition_mode='OFFLINE_CACHE_IMPORT_NO_NETWORK',
                          initial_attempt_manifest='acquisition_round1/execution_data.manifest.json',
                          recovery_attempt_log='recovery_attempts.jsonl')
            write_json(output / 'execution_data.manifest.json', result)
        status.update(status='FINISHED', finished_at=datetime.now(timezone.utc).isoformat(),
                      request_count=len(result['attempts']),
                      failed_requests=sum(x['status'] != 'OK' for x in result['attempts']),
                      rows=result['rows'], available_rows=result['available_rows'],
                      coverage_status=result['status'])
        if args.offline:
            status['status'] = 'BLOCKED_OFFICIAL_ACQUISITION' if status['failed_requests'] else 'FINISHED'
        print(json.dumps(status, sort_keys=True), flush=True)
    except BaseException as error:
        status.update(status='FAILED', error=f'{type(error).__name__}: {error}')
        raise
    finally:
        write_json(output / 'download_status.json', status)


if __name__ == '__main__':
    main()
