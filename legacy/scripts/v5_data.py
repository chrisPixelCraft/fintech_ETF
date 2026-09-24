#!/usr/bin/env python3
"""V5 episode registry and resumable official execution cache.

Subcommands (run from anywhere; paths are resolved against the repo root):

  registry  write outputs/v5/data/episodes.json (+csv, requested_dates.csv); write-once
  seed      copy already-acquired raw responses from the V4 caches (never mutates them)
  fetch     politely fetch the remaining raw responses (one worker per market,
            fixed delay, backoff, throttle cooldown); resumable; ends with `build`
  build     offline: normalize every requested date with the sealed V4 parser
  status    print coverage / progress
  all       registry + seed + fetch + build

Every path written to a manifest is repo-relative. Missing official rows stay
explicit (official_execution_available=false); Open/Close are never substituted.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.v4_execution import content_sha256, official_url, parse_official_day  # noqa: E402

STUDY_PATH = 'config/v5_study.json'
MARKETS = ('TWSE', 'TPEx')


# ---------------------------------------------------------------- utilities
def now():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def rel(path):
    """Repo-relative POSIX string; refuses paths outside the repository."""
    path = Path(path)
    if path.is_absolute():
        path = path.resolve().relative_to(ROOT)
    return path.as_posix()


def write_json(path, value):
    path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n')
    tmp.replace(path)


def load_study(path=STUDY_PATH):
    return json.loads((ROOT / path).read_text())


def raw_name(date, market):
    return f"{market}_{pd.Timestamp(date):%Y%m%d}.json.gz"


# ---------------------------------------------------------------- registry
def load_calendar(path):
    dates = pd.DatetimeIndex(pd.to_datetime(json.loads((ROOT / path).read_text())['calendar'])).normalize()
    if not dates.is_unique or not dates.is_monotonic_increasing:
        raise ValueError('Calendar must contain unique increasing sessions')
    return dates


def _window(dates, start, length):
    if start < 1 or start + length > len(dates):
        return None
    sessions = dates[start:start + length]
    return dict(start=str(sessions[0].date()), end=str(sessions[-1].date()),
                prior_session_date=str(dates[start - 1].date()), session_count=length,
                sessions=[str(d.date()) for d in sessions])


def build_registry(study, dates):
    """Deterministic episode list from the observed-session calendar (spec §2)."""
    length = int(study['episode_length'])
    episodes = []
    for split, rule in study['splits'].items():
        found = []
        if rule['kind'] == 'monthly':
            month = pd.Period(rule['first_month'], 'M')
            last = (pd.Period(dates[-1], 'M') if rule['last_month'] == 'LATEST_COMPLETE'
                    else pd.Period(rule['last_month'], 'M'))
            while month <= last:
                target = month.to_timestamp()
                start = int(dates.searchsorted(target))
                window = _window(dates, start, length) if start < len(dates) else None
                if window is not None and pd.Timestamp(window['start']).to_period('M') != month:
                    raise ValueError(f'No session in month {month}')
                if window is None:
                    if rule['last_month'] != 'LATEST_COMPLETE':
                        raise ValueError(f'Incomplete predeclared window {split} {month}')
                    break  # later months cannot be complete either
                found.append(dict(episode_id=f'{split}_{month.year}_{month.month:02d}', split=split,
                                  anchor_target=str(target.date()), anchor=window['start'], **window))
                month += 1
        elif rule['kind'] == 'seasonal':
            md = rule['month_day']
            for year in range(int(rule['first_year']), int(rule['last_year']) + 1):
                target = pd.Timestamp(f'{year}-{md}')
                right = int(dates.searchsorted(target))
                options = [i for i in (right - 1, right) if 0 <= i < len(dates)]
                # Minimum absolute distance; ties prefer the later session.
                start = min(options, key=lambda i: (abs((dates[i] - target).days), -i))
                window = _window(dates, start, length)
                if window is None:
                    raise ValueError(f'Incomplete seasonal window {year}')
                found.append(dict(episode_id=f'{split}_{year}', split=split,
                                  anchor_target=str(target.date()), anchor=window['start'], **window))
        else:
            raise ValueError('Unknown split kind ' + rule['kind'])
        if 'expected_count' in rule and len(found) != rule['expected_count']:
            raise ValueError(f'{split}: {len(found)} episodes, expected {rule["expected_count"]}')
        episodes.extend(found)
    return episodes


def requested_dates(episodes):
    return sorted({d for e in episodes for d in [e['prior_session_date'], *e['sessions']]})


def cmd_registry(study):
    out = study['outputs']
    dates = load_calendar(study['inputs']['calendar'])
    episodes = build_registry(study, dates)
    target = ROOT / out['episode_registry']
    if target.exists():
        if json.loads(target.read_text()) != episodes:
            raise ValueError('Refusing to change the predeclared V5 episode registry')
    else:
        write_json(out['episode_registry'], episodes)
    csv = target.with_suffix('.csv')
    pd.DataFrame([{k: v for k, v in e.items() if k != 'sessions'} for e in episodes]).to_csv(csv, index=False)
    days = requested_dates(episodes)
    pd.DataFrame({'date': days}).to_csv(ROOT / out['requested_dates'], index=False)
    manifest = dict(schema_version=1, status='PREDECLARED_BEFORE_EXECUTION',
                    study_path=STUDY_PATH, study_sha256=sha256(STUDY_PATH),
                    input_sha256={k: sha256(v) for k, v in study['inputs'].items()},
                    episodes_path=out['episode_registry'], episodes_sha256=sha256(out['episode_registry']),
                    requested_dates_path=out['requested_dates'], requested_dates_sha256=sha256(out['requested_dates']),
                    latest_calendar_date=str(dates[-1].date()), episode_count=len(episodes),
                    unique_dates=len(days),
                    split_counts=pd.Series([e['split'] for e in episodes]).value_counts().sort_index().to_dict())
    registry_manifest = Path(out['data_dir']) / 'registry_manifest.json'
    if (ROOT / registry_manifest).exists():
        old = json.loads((ROOT / registry_manifest).read_text())
        if old['episodes_sha256'] != manifest['episodes_sha256']:
            raise ValueError('Registry manifest/episode hash mismatch')
    write_json(registry_manifest, manifest)
    print(json.dumps({k: manifest[k] for k in ('episode_count', 'unique_dates', 'split_counts', 'latest_calendar_date')}))
    return episodes, days


def load_requested(study):
    path = ROOT / study['outputs']['requested_dates']
    if not path.exists():
        return cmd_registry(study)[1]
    return pd.read_csv(path).date.astype(str).tolist()


# ---------------------------------------------------------------- raw cache
def raw_dir(study):
    return ROOT / study['outputs']['data_dir'] / 'official_raw'


def cmd_seed(study):
    """Copy V4 raw responses (byte-identical) and record where each came from."""
    days = load_requested(study)
    target_dir = raw_dir(study)
    target_dir.mkdir(parents=True, exist_ok=True)
    provenance_path = Path(study['outputs']['data_dir']) / 'raw_provenance.json'
    provenance = json.loads((ROOT / provenance_path).read_text()) if (ROOT / provenance_path).exists() else {}
    copied = present = 0
    for day in days:
        for market in MARKETS:
            name = raw_name(day, market)
            target = target_dir / name
            if target.exists():
                present += 1
                continue
            for source_dir in study['raw_cache_sources']:
                source = ROOT / source_dir / name
                if source.exists():
                    content = gzip.decompress(source.read_bytes())
                    parse_official_day(json.loads(content), market, day)  # validate before reuse
                    tmp = target.with_suffix('.tmp')
                    shutil.copyfile(source, tmp)
                    tmp.replace(target)
                    provenance[name] = dict(origin='REUSED_V4_CACHE', source=rel(source),
                                            raw_sha256=content_sha256(content),
                                            compressed_sha256=content_sha256(target.read_bytes()))
                    copied += 1
                    break
    write_json(provenance_path, dict(sorted(provenance.items())))
    total = 2 * len(days)
    print(json.dumps(dict(requested=total, already_present=present, copied=copied,
                          missing=total - present - copied)), flush=True)


class Fetcher:
    """One polite serial worker per market; resumable via on-disk raw files."""

    def __init__(self, study, max_requests=None):
        self.study, self.policy = study, study['fetch_policy']
        self.data_dir = Path(study['outputs']['data_dir'])
        self.raw = raw_dir(study)
        self.rejected = ROOT / self.data_dir / 'official_rejected'
        self.log_path = ROOT / self.data_dir / 'fetch_attempts.jsonl'
        self.status_path = self.data_dir / 'fetch_status.json'
        self.lock = threading.Lock()
        self.max_requests = max_requests
        self.status = dict(pid=os.getpid(), state='RUNNING', started_at=now(), markets={},
                           resume_command='.venv/bin/python scripts/v5_data.py fetch',
                           log='outputs/v5/data/fetch.log', attempts_log=rel(self.log_path))

    def rejected_names(self):
        if not self.log_path.exists():
            return set()
        names = set()
        for line in self.log_path.read_text().splitlines():
            record = json.loads(line)
            if record.get('status') == 'REJECTED_NO_DATA':
                names.add(record['name'])
        return names

    def record(self, entry):
        with self.lock:
            with self.log_path.open('a') as handle:
                handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=False) + '\n')

    def save_status(self, market=None, market_state=None, **update):
        with self.lock:
            if market is not None:
                self.status['markets'][market] = market_state
            self.status.update(update, checked_at=now())
            write_json(self.status_path, self.status)

    def fetch_one(self, session, day, market):
        """Return (status, detail). Status: OK, REJECTED_NO_DATA, THROTTLED, TRANSIENT."""
        import requests
        url = official_url(market, day)
        try:
            response = session.get(url, headers={'User-Agent': 'Python-urllib/3.10'}, timeout=40)
        except requests.RequestException as error:
            return 'TRANSIENT', f'{type(error).__name__}: {error}', url
        if response.status_code in self.policy['throttle_status_codes']:
            return 'THROTTLED', f'HTTP{response.status_code}', url
        if response.status_code >= 500:
            return 'TRANSIENT', f'HTTP{response.status_code}', url
        if response.status_code != 200:
            return 'TRANSIENT', f'HTTP{response.status_code}', url
        content = response.content
        try:
            payload = json.loads(content)
        except ValueError:
            # HTML block pages arrive as HTTP 200; treat as throttling, never save.
            return 'THROTTLED', 'NON_JSON_200', url
        try:
            parse_official_day(payload, market, day)
        except Exception as error:  # schema/date mismatch or genuine no-data day
            self.rejected.mkdir(parents=True, exist_ok=True)
            (self.rejected / raw_name(day, market)).write_bytes(gzip.compress(content, mtime=0))
            return 'REJECTED_NO_DATA', f'{type(error).__name__}: {error}; stat={payload.get("stat")}', url
        target = self.raw / raw_name(day, market)
        tmp = target.with_suffix('.tmp')
        tmp.write_bytes(gzip.compress(content, mtime=0))
        tmp.replace(target)
        return 'OK', content_sha256(content), url

    def run_market(self, market, days):
        import requests
        delay = float(self.policy['delay_seconds'][market])
        backoff = list(self.policy['transient_retry_backoff_seconds'])
        cooldown = float(self.policy['throttle_cooldown_seconds'])
        cooldowns_left = int(self.policy['max_throttle_cooldowns_per_run'])
        skip = self.rejected_names()
        todo = [d for d in days if not (self.raw / raw_name(d, market)).exists()
                and raw_name(d, market) not in skip]
        if self.max_requests is not None:
            todo = todo[:self.max_requests]
        state = dict(todo=len(todo), done=0, ok=0, rejected=0, failed=0, state='RUNNING', last_ok=None)
        self.save_status(market, state)
        session = requests.Session()
        for day in todo:
            attempt = 0
            while True:
                time.sleep(delay)
                status, detail, url = self.fetch_one(session, day, market)
                attempt += 1
                entry = dict(time=now(), date=day, market=market, name=raw_name(day, market),
                             url=url, attempt=attempt, status=status, detail=detail)
                if status == 'OK':
                    entry['raw_path'] = rel(self.raw / raw_name(day, market))
                self.record(entry)
                if status == 'THROTTLED':
                    if cooldowns_left <= 0:
                        state.update(state='BLOCKED_THROTTLED', blocked_at=day)
                        self.save_status(market, state)
                        print(f'[{market}] throttled repeatedly; stopping (resume later)', flush=True)
                        return
                    cooldowns_left -= 1
                    print(f'[{market}] {detail} at {day}; cooling down {cooldown:.0f}s', flush=True)
                    state['state'] = 'COOLDOWN'
                    self.save_status(market, state)
                    time.sleep(cooldown)
                    state['state'] = 'RUNNING'
                    session = requests.Session()
                    continue
                if status == 'TRANSIENT' and attempt <= len(backoff):
                    time.sleep(backoff[attempt - 1])
                    continue
                break
            state['done'] += 1
            key = {'OK': 'ok', 'REJECTED_NO_DATA': 'rejected'}.get(status, 'failed')
            state[key] += 1
            if status == 'OK':
                state['last_ok'] = day
            if state['done'] % 20 == 0 or state['done'] == len(todo):
                print(f"[{market}] {state['done']}/{len(todo)} ok={state['ok']} "
                      f"rejected={state['rejected']} failed={state['failed']} last={day}", flush=True)
            self.save_status(market, state)
        state['state'] = 'FINISHED'
        self.save_status(market, state)

    def run(self, days):
        self.raw.mkdir(parents=True, exist_ok=True)
        self.save_status()
        threads = [threading.Thread(target=self.run_market, args=(m, days), name=m) for m in MARKETS]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        states = {m: s['state'] for m, s in self.status['markets'].items()}
        done = all(s == 'FINISHED' for s in states.values())
        self.save_status(state='FETCH_FINISHED' if done else 'FETCH_STOPPED', finished_at=now())
        return done


def cmd_fetch(study, max_requests=None, build_after=True):
    days = load_requested(study)
    fetcher = Fetcher(study, max_requests=max_requests)
    fetcher.run(days)
    if build_after:
        cmd_build(study)
        fetcher.save_status(state=fetcher.status['state'] + '_BUILT', built_at=now())


# ---------------------------------------------------------------- build
def cmd_build(study, workers=4):
    """Offline normalization of all requested dates via the sealed V4 builder."""
    from scripts.v4_stage2_data import build_offline
    days = load_requested(study)
    out = study['outputs']
    os.chdir(ROOT)  # relative output path => relative raw_path in the V4 builder
    result = build_offline(days, study['inputs']['universe'], Path(out['execution_data']), workers)
    frame = pd.read_csv(out['execution_data'], dtype={'symbol': str}, keep_default_na=False)
    available = frame.official_execution_available.astype(str).str.lower().eq('true')
    per_date = available.groupby(frame.date).mean()
    attempts = []
    raw = raw_dir(study)
    rejected = Fetcher(study).rejected_names()
    for attempt in result['attempts']:
        record = dict(attempt)
        record['raw_path'] = rel(record['raw_path'])
        name = Path(record['raw_path']).name
        if record['status'] != 'OK':
            record['status'] = 'REJECTED_NO_DATA' if name in rejected else 'MISSING_RAW'
            record['raw_path'] = None
        attempts.append(record)
    provenance_path = ROOT / out['data_dir'] / 'raw_provenance.json'
    provenance = json.loads(provenance_path.read_text()) if provenance_path.exists() else {}
    for record in attempts:
        if record['raw_path'] is not None:
            name = Path(record['raw_path']).name
            record['origin'] = provenance.get(name, {}).get('origin', 'FETCHED_V5')
            if name in provenance:
                record['reused_from'] = provenance[name]['source']
    raw_ok = sum(r['status'] == 'OK' for r in attempts)
    manifest = dict(
        {k: v for k, v in result.items() if k != 'attempts'},
        schema_version=2, builder='scripts/v4_build_execution_data.build_execution_data (offline binding from scripts/v4_stage2_data.build_offline)',
        path=out['execution_data'], raw_dir=rel(raw),
        requested_dates_path=out['requested_dates'], requested_dates_sha256=sha256(out['requested_dates']),
        episodes_path=out['episode_registry'], episodes_sha256=sha256(out['episode_registry']),
        universe_path=study['inputs']['universe'],
        requested_dates=len(days), requested_raw=2 * len(days), raw_ok=raw_ok,
        raw_missing=sum(r['status'] == 'MISSING_RAW' for r in attempts),
        raw_rejected_no_data=sum(r['status'] == 'REJECTED_NO_DATA' for r in attempts),
        acquisition_complete=raw_ok + sum(r['status'] == 'REJECTED_NO_DATA' for r in attempts) == 2 * len(days),
        available_fraction=float(available.mean()),
        dates_with_zero_available=int((per_date == 0).sum()),
        dates_fully_available=int((per_date == 1).sum()),
        missing_policy='Missing official rows keep official_execution_available=false; Open/Close never substituted',
        built_at=now(), attempts=attempts)
    for record in attempts:
        for key in ('raw_path', 'reused_from'):
            if record.get(key) and Path(record[key]).is_absolute():
                raise ValueError('Absolute path leaked into manifest')
    write_json(out['execution_manifest'], manifest)
    print(json.dumps({k: v for k, v in manifest.items() if k not in ('attempts',)}, ensure_ascii=False), flush=True)
    return manifest


def cmd_status(study):
    days = load_requested(study)
    raw = raw_dir(study)
    have = {m: sum((raw / raw_name(d, m)).exists() for d in days) for m in MARKETS}
    status_path = ROOT / study['outputs']['data_dir'] / 'fetch_status.json'
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    alive = None
    if status.get('pid'):
        try:
            os.kill(status['pid'], 0)
            alive = True
        except OSError:
            alive = False
    print(json.dumps(dict(requested_dates=len(days), raw_present=have, fetch_state=status.get('state'),
                          fetch_pid=status.get('pid'), fetch_alive=alive,
                          markets=status.get('markets')), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=['registry', 'seed', 'fetch', 'build', 'status', 'all'])
    parser.add_argument('--study', default=STUDY_PATH)
    parser.add_argument('--max-requests', type=int, default=None, help='per market (smoke tests)')
    parser.add_argument('--no-build', action='store_true')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    study = load_study(args.study)
    if args.command in ('registry', 'all'):
        cmd_registry(study)
    if args.command in ('seed', 'all'):
        cmd_seed(study)
    if args.command in ('fetch', 'all'):
        cmd_fetch(study, args.max_requests, build_after=not args.no_build)
    if args.command == 'build':
        cmd_build(study, args.workers)
    if args.command == 'status':
        cmd_status(study)


if __name__ == '__main__':
    main()
