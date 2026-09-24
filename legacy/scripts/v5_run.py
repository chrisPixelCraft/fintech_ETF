#!/usr/bin/env python3
"""V5 study harness (docs/v5_spec.md §6–§9).

Stages, each gated on the previous one's hash-chained completion event:

  s1          development grids for families A/B/C/E (+ A0_V3 reference), one winner per family
  s2          portfolio-layer treatments on each family winner (development only)
  validation  4 frozen family candidates + A0_V3 on validation
  freeze      freeze.json (preferred = best validation median passing §7), chained in events.jsonl
  holdout     holdout_retrospective + diagnostics, only after a verified freeze; writes manifest.json
  all         every stage in order

Every path written into a manifest, record or event is repo-relative. Episode
records are immutable: a completed record is reused only if its candidate hash
and every file hash verify; a partial directory is quarantined, never reused.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import inspect
import json
import math
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STUDY = 'config/v5_study.json'
GRIDS = 'config/v5_grids.json'
OUTPUT = 'outputs/v5/study'
SCORES = 'outputs/v5/scores'
CACHE = 'outputs/v5/cache'
STAGES = ('s1', 's2', 'validation', 'freeze', 'holdout')
SIMPLICITY = {'A': 0, 'E': 1, 'C': 2, 'B': 3, 'V3': 9}
EPS = 1e-12
FEASIBLE_COMPLETE, FEASIBLE_PASS = 0.95, 0.90
COUNTERS = ('cash_violation_days', 'weight_violation_days', 'holding_count_violation_days',
            'odd_lot_issue_days', 'missing_execution_price_days', 'unfilled_days',
            'no_valid_plan_days', 'assumption_odd_lot_days')


# ---------------------------------------------------------------- utilities
def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if value is pd.NaT:
        return None
    if isinstance(value, Path):
        return rel(value)
    return value


def canonical(value):
    text = json.dumps(clean(value), sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def rel(path):
    path = Path(path)
    absolute = path if path.is_absolute() else (ROOT / path)
    absolute = Path(os.path.normpath(absolute))
    try:
        return str(absolute.relative_to(ROOT))
    except ValueError:
        raise ValueError('Path escapes the repository: ' + str(path))


def repo(path):
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.pending')
    temporary.write_text(json.dumps(clean(value), indent=2, sort_keys=True, allow_nan=False) + '\n')
    temporary.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text())


def now():
    return datetime.now(timezone.utc).isoformat()


def log(output, message):
    line = f'{now()} {message}'
    print(line, flush=True)
    with open(Path(output) / 'run.log', 'a') as handle:
        handle.write(line + '\n')


# ------------------------------------------------------------- event chain
def read_events(output):
    path = Path(output) / 'events.jsonl'
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def verify_chain(events):
    """Raise on any broken predecessor link, hash mismatch or clock reversal."""
    previous, previous_time = None, None
    for index, row in enumerate(events):
        if row.get('previous_hash') != previous:
            raise ValueError(f'Event chain broken at {index}')
        body = {k: v for k, v in row.items() if k != 'hash'}
        if row.get('hash') != canonical(body):
            raise ValueError(f'Event hash mismatch at {index}')
        stamp = pd.Timestamp(row['time'])
        if previous_time is not None and stamp < previous_time:
            raise ValueError(f'Event clock reverses at {index}')
        previous, previous_time = row['hash'], stamp
    return True


def event(output, name, payload):
    events = read_events(output)
    verify_chain(events)
    row = dict(event=name, time=now(), previous_hash=events[-1]['hash'] if events else None,
               payload=clean(payload))
    row['hash'] = canonical(row)
    with open(Path(output) / 'events.jsonl', 'a') as handle:
        handle.write(json.dumps(row, sort_keys=True) + '\n')
    return row


def completed(output, stage):
    return any(e['event'] == stage + '_complete' for e in read_events(output))


def require_stage(output, stage):
    events = read_events(output)
    verify_chain(events)
    if not any(e['event'] == stage + '_complete' for e in events):
        raise RuntimeError(f'Stage order: {stage} must complete first')


# ------------------------------------------------------------ provenance
def load_study(study_path=STUDY):
    study = read_json(repo(study_path))
    inputs, outputs = study.get('inputs', study), study.get('outputs', study)
    paths = dict(daily=inputs['daily'], universe=inputs['universe'], calendar=inputs.get('calendar'),
                 base_config=inputs.get('base_config', 'configs/competition_24d_final.json'),
                 competition_rules=inputs.get('competition_rules'),
                 episode_registry=outputs['episode_registry'], execution_data=outputs['execution_data'],
                 execution_manifest=outputs.get('execution_manifest'))
    return study, {k: v for k, v in paths.items() if v}


def require_complete_execution(paths):
    """Acquisition gaps stop the study before any outcome is viewed (fail closed, as V4)."""
    if not paths.get('execution_manifest'):
        return None
    manifest = read_json(repo(paths['execution_manifest']))
    if not manifest.get('acquisition_complete', False) or str(manifest.get('status', '')).startswith('BLOCK'):
        raise RuntimeError('BLOCK_CANONICAL_V5: official execution data incomplete '
                           f"(status={manifest.get('status')}, raw_missing={manifest.get('raw_missing')}); "
                           'finish scripts/v5_data.py before running the study')
    return manifest.get('status')


def input_hashes(study_path, grids_path, paths):
    files = [Path(study_path), Path(grids_path), *[Path(p) for p in paths.values()],
             *sorted((ROOT / 'src').glob('*.py')), ROOT / 'scripts/v5_run.py']
    registry_manifest = repo(paths['episode_registry']).parent / 'registry_manifest.json'
    if registry_manifest.exists():
        files.append(registry_manifest)
    return {rel(p): sha256(repo(p)) for p in files if repo(p).is_file()}


def bind_inputs(output, study_path, grids_path, paths):
    hashes = input_hashes(study_path, grids_path, paths)
    path = Path(output) / 'inputs.json'
    if path.exists():
        old = read_json(path)
        if old != hashes:
            changed = sorted(k for k in set(old) | set(hashes) if old.get(k) != hashes.get(k))
            raise ValueError('Inputs changed since the study started; use a new --output. Changed: ' + ', '.join(changed))
    else:
        write_json(path, hashes)
        event(output, 'study_start', dict(inputs_sha256=canonical(hashes), study=rel(study_path), grids=rel(grids_path)))
    return hashes


def ensure_features(paths, cache=CACHE):
    """Build the V5 feature panel once; bind it to data and code hashes (fail closed on drift)."""
    from src.v5_features import build_features
    cache = repo(cache)
    cache.mkdir(parents=True, exist_ok=True)
    feature_path, manifest_path = cache / 'features.pkl', cache / 'features.manifest.json'
    sources = [paths['daily'], 'src/v5_features.py', 'src/strategy_24d.py']
    if paths.get('calendar'):
        sources.append(paths['calendar'])
    expected = {rel(p): sha256(repo(p)) for p in sources}
    if manifest_path.exists() and feature_path.exists():
        manifest = read_json(manifest_path)
        if manifest.get('inputs') == expected and sha256(feature_path) == manifest.get('features_sha256'):
            return feature_path
    daily = pd.read_parquet(repo(paths['daily']))
    daily['date'] = pd.to_datetime(daily.date)
    pending = cache / 'features.pending.pkl'
    panel = build_features(daily)
    if paths.get('calendar'):
        # Off-calendar rows (e.g. Saturday make-up days with partial vendor data and no
        # feature_ready names) would become the D-1 cross-section and blank every score.
        # Signals use the same observed-session calendar as the ledger (spec amendment 1).
        sessions = pd.to_datetime(read_json(repo(paths['calendar']))['calendar'])
        panel = panel.loc[pd.to_datetime(panel.date).isin(sessions)].reset_index(drop=True)
    panel.to_pickle(pending)
    pending.replace(feature_path)
    write_json(manifest_path, dict(inputs=expected, features_sha256=sha256(feature_path), built_at=now()))
    return feature_path


def load_panel(feature_path):
    manifest = read_json(Path(feature_path).with_name('features.manifest.json'))
    if sha256(feature_path) != manifest['features_sha256']:
        raise ValueError('Feature cache payload hash mismatch')
    return pd.read_pickle(feature_path)


# --------------------------------------------------------------- scores
def decision_dates(registry):
    return sorted({d for e in registry for d in e['sessions']})


def _score_job(task):
    signal_id, family, config, feature_path, dates, target = task
    from src.v5_strategy import build_score_table
    panel = load_panel(feature_path)
    started = time.time()
    table = build_score_table(family, panel, pd.to_datetime(dates), config)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_name(target.name + '.pending')
    table.to_parquet(pending, index=False)
    pending.replace(target)
    return signal_id, dict(rows=len(table), seconds=round(time.time() - started, 1))


def ensure_scores(grids, feature_path, registry, workers, scores=SCORES, only=None, output=None):
    """Precompute each declared signal once for every study decision date (spec §3.2)."""
    root = repo(scores)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / 'manifest.json'
    manifest = read_json(manifest_path) if manifest_path.exists() else {}
    dates = decision_dates(registry)
    dates_hash = canonical(dates)
    features_hash = read_json(Path(feature_path).with_name('features.manifest.json'))['features_sha256']
    jobs, keys = [], {}
    for signal_id, spec in sorted(grids['signals'].items()):
        if only is not None and signal_id not in only:
            continue
        family = spec['family']
        module = ROOT / (grids['families'][family]['module'].replace('.', '/') + '.py')
        key = dict(family=family, config=spec['config'], config_sha256=canonical(spec['config']),
                   module=rel(module), module_sha256=sha256(module), features_sha256=features_hash,
                   decision_dates_sha256=dates_hash, path=rel(root / family / f'{signal_id}.parquet'))
        keys[signal_id] = key
        entry = manifest.get(signal_id)
        target = repo(key['path'])
        fresh = (entry is not None and {k: entry.get(k) for k in key} == key and target.exists()
                 and sha256(target) == entry.get('sha256'))
        if not fresh:
            jobs.append((signal_id, family, spec['config'], str(feature_path), dates, str(target)))
    def record(signal_id, info):
        manifest[signal_id] = dict(keys[signal_id], sha256=sha256(repo(keys[signal_id]['path'])), built_at=now(), **info)
        write_json(manifest_path, manifest)   # incremental: a later failure keeps finished tables
        message = f'score {signal_id}: {info}'
        log(output, message) if output is not None else print(now(), message, flush=True)
    if jobs and workers <= 1:
        for job in jobs:
            record(*_score_job(job))
    elif jobs:
        with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
            futures = [pool.submit(_score_job, job) for job in jobs]
            for future in as_completed(futures):
                record(*future.result())
    return {k: manifest[k] for k in keys}


# ------------------------------------------------------------ candidates
def candidate_config(candidate):
    """Behaviour-defining part of a candidate (what the hash binds)."""
    keys = ('id', 'family', 'signal', 'signal_config', 'construction', 'axes', 'parent', 'treatment')
    return {k: candidate[k] for k in keys if k in candidate}


def baseline_candidate(grids):
    base = dict(grids.get('baseline', {}))
    return dict(id=base.get('id', 'A0_V3'), family='V3', signal=None, signal_config=None,
                construction=None, axes={}, baseline=base)


def behaviour_groups(candidates, score_manifest):
    """Identical score table bytes + identical construction => identical behaviour."""
    groups, representative = {}, {}
    for c in sorted(candidates, key=lambda x: x['id']):
        if c['family'] == 'V3':
            representative[c['id']] = c['id']
            continue
        key = (score_manifest[c['signal']]['sha256'], canonical(c['construction']))
        groups.setdefault(key, c['id'])
        representative[c['id']] = groups[key]
    return representative


# --------------------------------------------------------------- workers
STATE = {}


def _init_worker(study_path, feature_path, scores_root, grids_path):
    study, paths = load_study(study_path)
    daily = pd.read_parquet(repo(paths['daily']))
    daily['date'] = pd.to_datetime(daily.date)
    universe = pd.read_csv(repo(paths['universe']))
    if 'symbol' not in universe:
        universe['symbol'] = universe.yahoo_symbol.astype(str)
    if 'known_at' not in universe and 'attachment_created_at' in universe:
        universe['known_at'] = universe.attachment_created_at
    execution = pd.read_csv(repo(paths['execution_data']), parse_dates=['date'])
    base = read_json(repo(paths['base_config']))
    panel = load_panel(feature_path)
    grids = read_json(repo(grids_path))
    STATE.clear()
    STATE.update(study=study, paths=paths, daily=daily, universe=universe, execution=execution,
                 base_config=base, panel=panel, grids=grids, scores={}, regime=None, v3=None,
                 score_manifest=read_json(repo(scores_root) / 'manifest.json')
                 if (repo(scores_root) / 'manifest.json').exists() else {})


def _regime():
    if STATE['regime'] is None:
        from src.v5_portfolio import regime_table
        STATE['regime'] = regime_table(STATE['panel'], STATE['grids'].get('regime_overlay'))
    return STATE['regime']


def _scores(signal_id):
    if signal_id not in STATE['scores']:
        entry = STATE['score_manifest'][signal_id]
        path = repo(entry['path'])
        if sha256(path) != entry['sha256']:
            raise ValueError('Score table hash mismatch: ' + entry['path'])
        STATE['scores'] = {signal_id: pd.read_parquet(path)}  # one table resident per worker
    return STATE['scores'][signal_id]


def _call(function, **available):
    """Call with the keyword arguments the function declares (adapter across module revisions)."""
    parameters = inspect.signature(function).parameters
    if any(p.kind == p.VAR_KEYWORD for p in parameters.values()):
        return function(**available)
    return function(**{k: v for k, v in available.items() if k in parameters})


def run_candidate_episode(candidate, episode):
    """One episode through src.v5_episode (V5 families) or the frozen V3 path."""
    from src import v5_episode
    config = copy.deepcopy(STATE['base_config'])
    config['prior_session_date'] = episode['prior_session_date']
    sessions = list(episode['sessions'])
    dates = pd.DatetimeIndex(pd.to_datetime([episode['prior_session_date'], *sessions]))
    execution = STATE['execution'].loc[STATE['execution'].date.isin(dates)]
    common = dict(daily=STATE['daily'], universe=STATE['universe'], base_config=config, config=config,
                  session_dates=sessions, sessions=sessions, execution_data=execution, execution=execution,
                  episode=episode, panel=STATE['panel'], features=STATE['panel'],
                  prior_session_date=episode['prior_session_date'])
    if candidate['family'] == 'V3':
        runner = getattr(v5_episode, 'run_v3_baseline', None)
        if runner is None:
            return _v3_fallback(config, sessions, execution), None
        if STATE.get('v3') is None:
            from src.strategy_24d import build_features as v3_features
            STATE['v3'] = v3_features(STATE['daily'], STATE['base_config'])
        return _call(runner, v3_features=STATE['v3'], **common), None
    from src.v5_strategy import Strategy
    regime = _regime() if candidate['construction'].get('regime') else None
    strategy = Strategy(candidate, _scores(candidate['signal']), regime=regime)
    result = _call(v5_episode.run_episode, strategy=strategy, strategy_config=candidate, **common)
    return result, strategy


def _v3_fallback(config, sessions, execution):
    """Frozen V3 through the sealed Stage-1 baseline (same path V4 used for A0_V3)."""
    from src.v4_baseline import run_episode as baseline
    from src.strategy_24d import build_features as v3_features
    if STATE.get('v3') is None:
        STATE['v3'] = v3_features(STATE['daily'], STATE['base_config'])
    result = baseline(STATE['daily'], STATE['universe'], config, sessions, execution,
                      features=STATE['v3'], sizing_price_mode='official_close')
    metrics = result['metrics']
    equity = result.get('equity', pd.DataFrame())
    complete = bool(metrics.get('complete_period', False) and len(equity) == int(metrics.get('requested_sessions', 24))
                    and not metrics.get('disqualified', False))
    metrics['complete_episode'] = complete
    if metrics.get('disqualified', False):
        metrics['episode_return'] = None
        metrics['forensic_partial_return'] = (float(equity.economic_nav.iloc[-1] / float(config['initial_cash']) - 1)
                                              if len(equity) else None)
    metrics.setdefault('assumption_odd_lot_days', None)
    return result


def _number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def make_record(metrics, candidate, episode, started, location):
    complete = metrics.get('complete_episode')
    if complete is None:
        complete = bool(metrics.get('complete_period', False) and not metrics.get('disqualified', False))
    complete = bool(complete)
    ret = _number(metrics.get('episode_return')) if complete else None
    if complete and ret is None:
        complete = False
    record = dict(candidate=candidate['id'], family=candidate['family'], episode=episode['episode_id'],
                  split=episode['split'], start=episode.get('start', episode['sessions'][0]),
                  complete=complete, episode_return=ret,
                  forensic_return=_number(metrics.get('forensic_partial_return', metrics.get('episode_return'))) if not complete else None,
                  measured_pass=bool(metrics.get('v5_measured_pass', metrics.get('measured_pass', False))),
                  sealed_measured_pass=bool(metrics.get('measured_pass', False)),
                  canonical_status=metrics.get('canonical_status'), compliance_status=metrics.get('compliance_status'),
                  status=metrics.get('v5_episode_status', metrics.get('episode_status', metrics.get('status'))),
                  failure_reasons=metrics.get('v5_failure_reasons', metrics.get('failure_reasons', '')),
                  mdd=_number(metrics.get('episode_max_drawdown')), turnover=_number(metrics.get('episode_turnover')),
                  cost=_number(metrics.get('transaction_cost')),
                  day1_invested=_number(metrics.get('day_1_invested_ratio')),
                  day3_invested=_number(metrics.get('day_3_invested_ratio')),
                  day5_invested=_number(metrics.get('day_5_invested_ratio')),
                  run_started_at=started, path=rel(location))
    for key in COUNTERS:
        value = metrics.get(key)
        record[key] = int(value) if value is not None and _number(value) is not None else (0 if key != 'assumption_odd_lot_days' else None)
    odd = [int(metrics[k]) for k in ('odd_lot_residual_days', 'odd_lot_plan_days') if metrics.get(k) is not None]
    if odd:
        record['assumption_odd_lot_days'] = max(odd)
    record['odd_lot_assumption'] = metrics.get('odd_lot_assumption')
    record['odd_lot_policy'] = metrics.get('odd_lot_policy')
    return record


def _verified_record(location, candidate):
    complete = read_json(location / 'complete.json')
    if complete['candidate_hash'] != canonical(candidate_config(candidate)):
        raise ValueError('Attempted reuse with a changed candidate: ' + rel(location))
    for name, digest in complete['hashes'].items():
        if not (location / name).is_file() or sha256(location / name) != digest:
            raise ValueError('Corrupt episode evidence: ' + rel(location / name))
    return complete['record']


def evaluate(task):
    candidate, episodes, output = task
    output = Path(output)
    records, quarantined = [], []
    for episode in episodes:
        location = output / 'runs' / candidate['id'] / episode['episode_id']
        if (location / 'complete.json').exists():
            records.append(_verified_record(location, candidate))
            continue
        if location.exists():
            target = output / 'quarantine' / f"{candidate['id']}__{episode['episode_id']}__{int(time.time() * 1e6)}"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(location), str(target))
            quarantined.append(rel(target))
        started = now()
        result, strategy = run_candidate_episode(candidate, episode)
        location.mkdir(parents=True)
        for name, table in result.items():
            if isinstance(table, pd.DataFrame):
                table.to_csv(location / f'{name}.csv', index=False)
        if strategy is not None:
            strategy.predictions().to_csv(location / 'decisions.csv', index=False)
        metrics = clean(result['metrics'])
        write_json(location / 'metrics.json', metrics)
        write_json(location / 'config.json', result.get('config', {}))
        if isinstance(result.get('planner_config'), dict):
            write_json(location / 'planner_config.json', result['planner_config'])
        write_json(location / 'strategy_config.json', candidate_config(candidate))
        record = clean(make_record(metrics, candidate, episode, started, location))
        hashes = {p.name: sha256(p) for p in sorted(location.iterdir()) if p.is_file()}
        write_json(location / 'complete.json', dict(candidate_hash=canonical(candidate_config(candidate)),
                                                    hashes=hashes, record=record))
        records.append(record)
    return records, quarantined


def run_batch(output, workers, init, candidates, episodes, phase, chunk=12):
    """Evaluate candidates x episodes; the phase-start event declares the full denominator."""
    event(output, phase + '_start', dict(candidates=[c['id'] for c in candidates],
                                         episodes=[e['episode_id'] for e in episodes]))
    tasks = [(c, episodes[i:i + chunk], str(output)) for c in candidates for i in range(0, len(episodes), chunk)]
    records, quarantined = [], []
    if workers <= 1:
        _init_worker(*init)
        results = map(evaluate, tasks)
        for rows, moved in results:
            records.extend(rows)
            quarantined.extend(moved)
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=init) as pool:
            for index, (rows, moved) in enumerate(pool.map(evaluate, tasks)):
                records.extend(rows)
                quarantined.extend(moved)
                if index % 10 == 0 or index == len(tasks) - 1:
                    log(output, f'{phase}: {index + 1}/{len(tasks)} tasks, last {rows[0]["candidate"] if rows else "-"}')
    if quarantined:
        event(output, phase + '_quarantine', dict(paths=quarantined))
    frame = pd.DataFrame(records)
    expected = {(c['id'], e['episode_id']) for c in candidates for e in episodes}
    if set(zip(frame.candidate, frame.episode)) != expected or frame.duplicated(['candidate', 'episode']).any():
        raise AssertionError(phase + ': record set differs from the declared denominator')
    frame = frame.sort_values(['candidate', 'episode']).reset_index(drop=True)
    frame.to_csv(Path(output) / f'{phase}.csv', index=False)
    return frame


# ------------------------------------------------------ statistics & gates
def _q(values, q):
    values = np.asarray(values, float)
    return float(np.quantile(values, q)) if len(values) else float('nan')


def summarize(records, simplicity=SIMPLICITY):
    rows = []
    for cid, group in pd.DataFrame(records).groupby('candidate', sort=True):
        complete = group.loc[group.complete.astype(bool)]
        returns = pd.to_numeric(complete.episode_return, errors='coerce').dropna().to_numpy(float)
        attempted = len(group)
        n_complete = len(returns)
        measured = int(group.measured_pass.astype(bool).sum())
        family = str(group.family.iloc[0])
        rows.append(dict(
            candidate=cid, family=family, attempted=attempted, complete=n_complete,
            complete_rate=n_complete / attempted if attempted else 0., measured=measured,
            pass_rate=measured / attempted if attempted else 0.,
            feasible=bool(attempted and n_complete / attempted >= FEASIBLE_COMPLETE - EPS
                          and measured / attempted >= FEASIBLE_PASS - EPS),
            median=float(np.median(returns)) if n_complete else float('nan'),
            p25=_q(returns, .25), p10=_q(returns, .10),
            worst=float(returns.min()) if n_complete else float('nan'),
            mean=float(returns.mean()) if n_complete else float('nan'),
            positive_rate=float((returns > 0).mean()) if n_complete else float('nan'),
            mdd_max=float(pd.to_numeric(complete.mdd, errors='coerce').max()) if n_complete else float('nan'),
            mdd_median=float(pd.to_numeric(complete.mdd, errors='coerce').median()) if n_complete else float('nan'),
            turnover=float(pd.to_numeric(complete.turnover, errors='coerce').mean()) if n_complete else float('nan'),
            cost=float(pd.to_numeric(complete.cost, errors='coerce').mean()) if n_complete else float('nan'),
            day1_invested=float(pd.to_numeric(group.day1_invested, errors='coerce').mean()),
            day3_invested=float(pd.to_numeric(group.day3_invested, errors='coerce').mean()),
            day5_invested=float(pd.to_numeric(group.day5_invested, errors='coerce').mean()),
            **{f'{k}_total': int(pd.to_numeric(group[k], errors='coerce').fillna(0).sum()) for k in COUNTERS if k in group},
            assumption_odd_lot_episodes=int(pd.to_numeric(group.get('assumption_odd_lot_days', 0), errors='coerce').fillna(0).gt(0).sum()),
            simplicity=simplicity.get(family, 9)))
    return pd.DataFrame(rows)


def rank_key(row):
    """§7: feasibility → median → P25 → P10 → worst / MDD → mean → turnover / cost → simplicity."""
    def neg(x):
        return -x if x == x else math.inf
    def pos(x):
        return x if x == x else math.inf
    return (not row['feasible'], neg(row['median']), neg(row['p25']), neg(row['p10']), neg(row['worst']),
            pos(row['mdd_max']), neg(row['mean']), pos(row['turnover']), pos(row['cost']),
            row['simplicity'], row['candidate'])


def rank(summary):
    if summary.empty:
        return summary
    order = sorted(summary.to_dict('records'), key=rank_key)
    return pd.DataFrame(order).reset_index(drop=True)


def paired(records, a, b, splits=None):
    """Pair only episodes where both runs are complete (V4 defect fix)."""
    frame = pd.DataFrame(records)
    if splits is not None:
        frame = frame.loc[frame.split.isin(splits)]
    left = frame.loc[frame.candidate.eq(a)].set_index('episode')
    right = frame.loc[frame.candidate.eq(b)].set_index('episode')
    both = left.index.intersection(right.index)
    ok = [e for e in both if bool(left.at[e, 'complete']) and bool(right.at[e, 'complete'])
          and left.at[e, 'episode_return'] is not None and right.at[e, 'episode_return'] is not None
          and pd.notna(left.at[e, 'episode_return']) and pd.notna(right.at[e, 'episode_return'])]
    ok = sorted(ok)
    x = left.loc[ok, 'episode_return'].astype(float).to_numpy()
    y = right.loc[ok, 'episode_return'].astype(float).to_numpy()
    d = x - y
    return dict(a=a, b=b, n=len(ok), attempted_a=len(left), attempted_b=len(right), episodes=ok,
                median_delta=float(np.median(d)) if len(d) else float('nan'),
                p25_delta=_q(d, .25), p10_delta=_q(d, .10),
                mean_delta=float(d.mean()) if len(d) else float('nan'),
                win_rate=float((d > 0).mean()) if len(d) else float('nan'),
                a_median=float(np.median(x)) if len(x) else float('nan'), b_median=float(np.median(y)) if len(y) else float('nan'),
                a_p25=_q(x, .25), b_p25=_q(y, .25), a_p10=_q(x, .10), b_p10=_q(y, .10))


def behaviour_fingerprint(records, cid):
    frame = pd.DataFrame(records)
    rows = frame.loc[frame.candidate.eq(cid)].sort_values('episode')
    values = [(e, bool(c), None if r is None or pd.isna(r) else round(float(r), 10),
               None if t is None or pd.isna(t) else round(float(t), 10))
              for e, c, r, t in zip(rows.episode, rows.complete, rows.episode_return, rows.turnover)]
    return canonical(values)


def dedupe_behaviour(records, candidates, representative=None):
    """Map each candidate to the lowest-id candidate with identical realised behaviour."""
    representative = dict(representative or {})
    seen = {}
    for c in sorted(candidates, key=lambda x: x['id']):
        cid = representative.get(c['id'], c['id'])
        if cid != c['id']:
            continue
        key = behaviour_fingerprint(records, cid)
        seen.setdefault(key, cid)
        representative[c['id']] = seen[key]
    return representative


def neighbours(winner, candidates, axes):
    """One-axis neighbours over every declared grid axis (incl. N and rotation)."""
    out = []
    for c in candidates:
        if c['family'] != winner['family'] or c['id'] == winner['id']:
            continue
        diff = [a for a in axes if c['axes'].get(a) != winner['axes'].get(a)]
        if len(diff) == 1:
            out.append(dict(candidate=c['id'], axis=diff[0], value=c['axes'].get(diff[0])))
    return out


def stability(winner_id, candidates, axes, summary, representative):
    """≥ 2 behaviourally distinct one-axis neighbours; every one feasible and within 1.0pp of the winner."""
    lookup = {c['id']: c for c in candidates}
    stats = summary.set_index('candidate')
    base = stats.loc[representative.get(winner_id, winner_id)]
    rows, seen = [], set()
    for n in neighbours(lookup[winner_id], candidates, axes):
        rep = representative.get(n['candidate'], n['candidate'])
        duplicate = rep == representative.get(winner_id, winner_id) or rep in seen
        seen.add(rep)
        s = stats.loc[rep]
        gap = float(s['median'] - base['median']) if s['median'] == s['median'] else float('nan')
        rows.append(dict(n, representative=rep, duplicate=duplicate, feasible=bool(s['feasible']),
                         median=float(s['median']), median_gap=gap,
                         within=bool(gap == gap and abs(gap) <= 0.01 + EPS)))
    distinct = [r for r in rows if not r['duplicate']]
    passed = len(distinct) >= 2 and all(r['feasible'] and r['within'] for r in distinct)
    return dict(passed=passed, distinct=len(distinct), neighbours=rows,
                rule='>=2 behaviourally distinct one-axis neighbours; each feasible and |dev median gap| <= 1.0pp')


def s2_keep(p):
    return bool(p['n'] > 0 and p['median_delta'] >= -EPS and p['p25_delta'] >= -0.005 - EPS)


def validation_gates(records, frozen, baseline_id, stability_by_id, a_id):
    """§7 gates computable before the holdout (feasibility on validation, V3, complexity, stability)."""
    frame = pd.DataFrame(records)
    val = frame.loc[frame.split.eq('validation')]
    summary = summarize(val).set_index('candidate')
    gates = {}
    for cid in frozen:
        s = summary.loc[cid]
        v3 = paired(val, cid, baseline_id)
        g = dict(feasible_validation=bool(s['feasible']), complete_rate=float(s['complete_rate']),
                 pass_rate=float(s['pass_rate']), v3=v3)
        g['v3_median'] = bool(v3['n'] > 0 and v3['median_delta'] >= -EPS)
        g['v3_p25'] = bool(v3['n'] > 0 and v3['a_p25'] >= v3['b_p25'] - 0.01 - EPS)
        g['v3_p10'] = bool(v3['n'] > 0 and v3['a_p10'] >= v3['b_p10'] - 0.015 - EPS)
        if frame.loc[frame.candidate.eq(cid), 'family'].iloc[0] != 'A':
            vs_a = paired(val, cid, a_id)
            g['vs_A'] = vs_a
            g['complexity_earned'] = bool(vs_a['n'] > 0 and vs_a['median_delta'] >= -EPS)
        else:
            g['complexity_earned'] = True
        g['stability'] = stability_by_id[cid]
        g['stable'] = bool(stability_by_id[cid]['passed'])
        g['passes_pre_holdout'] = all(g[k] for k in ('feasible_validation', 'v3_median', 'v3_p25', 'v3_p10',
                                                      'complexity_earned', 'stable'))
        g['validation_median'] = float(s['median'])
        gates[cid] = g
    return gates, summary


def choose_preferred(gates, summary):
    """Best validation median among candidates passing the pre-holdout gates (ties by §7 ranking)."""
    passing = [cid for cid, g in gates.items() if g['passes_pre_holdout']]
    if not passing:
        return None
    table = rank(summary.loc[passing].reset_index())
    table = table.sort_values('median', ascending=False, kind='mergesort')
    best = table['median'].max()
    tied = rank(table.loc[(table['median'] - best).abs() <= EPS])
    return str(tied.candidate.iloc[0])


def final_gates(records, freeze):
    """Complete §7 decision, applied after holdout to the frozen preferred candidate only."""
    frame = pd.DataFrame(records)
    baseline = freeze['baseline_id']
    out = {}
    for cid, pre in freeze['gates'].items():
        both = frame.loc[frame.candidate.eq(cid) & frame.split.isin(['validation', 'holdout_retrospective'])]
        attempted = len(both)
        complete_rate = float(both.complete.astype(bool).sum() / attempted) if attempted else 0.
        pass_rate = float(both.measured_pass.astype(bool).sum() / attempted) if attempted else 0.
        hold = paired(frame.loc[frame.split.eq('holdout_retrospective')], cid, baseline)
        g = dict(pre_holdout=pre['passes_pre_holdout'], feasibility_attempted=attempted,
                 feasibility_complete_rate=complete_rate, feasibility_pass_rate=pass_rate,
                 feasible=bool(attempted and complete_rate >= FEASIBLE_COMPLETE - EPS and pass_rate >= FEASIBLE_PASS - EPS),
                 holdout=hold, holdout_not_reversed=bool(hold['n'] > 0 and hold['median_delta'] >= -0.005 - EPS),
                 assumption_odd_lot_days=int(pd.to_numeric(both.get('assumption_odd_lot_days', 0), errors='coerce').fillna(0).sum()))
        g['stable_candidate'] = bool(g['pre_holdout'] and g['feasible'] and g['holdout_not_reversed'])
        out[cid] = g
    preferred = freeze.get('preferred')
    outcome = 'STABLE_CANDIDATE' if preferred and out[preferred]['stable_candidate'] else 'NO_V5_WINNER'
    return outcome, out


# ------------------------------------------------------------------ stages
class Study:
    def __init__(self, output=OUTPUT, study=STUDY, grids=GRIDS, workers=8, scores=SCORES, cache=CACHE,
                 require_data=True):
        self.study, self.paths = load_study(study)
        if require_data:
            require_complete_execution(self.paths)
        self.output = repo(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.study_path, self.grids_path, self.scores, self.cache = study, grids, scores, cache
        self.workers = workers
        self.grids = read_json(repo(grids))
        self.registry = read_json(repo(self.paths['episode_registry']))
        ids = [e['episode_id'] for e in self.registry]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate episode ids in registry')
        self.inputs = bind_inputs(self.output, study, grids, self.paths)
        self.baseline = baseline_candidate(self.grids)
        self._features = None

    # -- helpers
    @property
    def features(self):
        if self._features is None:
            self._features = ensure_features(self.paths, self.cache)
        return self._features

    def split(self, *names):
        excluded = set(self.grids.get('excluded_episodes', {}))
        return [e for e in self.registry if e['split'] in names and e['episode_id'] not in excluded]

    def init(self):
        return (self.study_path, str(self.features), self.scores, self.grids_path)

    def score_manifest(self, only=None):
        return ensure_scores(self.grids, self.features, self.registry, self.workers, self.scores, only, self.output)

    def declared(self):
        path = self.output / 'candidates.json'
        cands = [candidate_config(c) for c in self.grids['candidates']]
        if path.exists():
            if read_json(path) != clean(cands):
                raise ValueError('Declared candidates changed after the study started')
        else:
            write_json(path, cands)
            event(self.output, 'candidates_declared', dict(sha256=sha256(path), count=len(cands)))
        return cands

    def load(self, phase):
        return pd.read_csv(self.output / f'{phase}.csv', keep_default_na=True).replace({np.nan: None})

    # -- S1
    def s1(self):
        if completed(self.output, 's1'):
            log(self.output, 's1 already complete')
            return read_json(self.output / 's1_selection.json')
        cands = self.declared()
        manifest = self.score_manifest()
        write_json(self.output / 'scores_used.json', manifest)
        representative = behaviour_groups(cands, manifest)
        runnable = [c for c in cands if representative[c['id']] == c['id']]
        event(self.output, 's1_scores', dict(sha256=sha256(self.output / 'scores_used.json'),
                                             score_duplicates={k: v for k, v in representative.items() if k != v}))
        frame = run_batch(self.output, self.workers, self.init(), [*runnable, self.baseline],
                          self.split('development'), 'development')
        records = frame.to_dict('records')
        representative = dedupe_behaviour(records, runnable, representative)
        summary = summarize(records)
        summary['representative'] = summary.candidate.map(lambda c: representative.get(c, c))
        summary.to_csv(self.output / 'development_summary.csv', index=False)
        selection = {}
        for family in ('A', 'B', 'C', 'E'):
            fam = [c for c in cands if c['family'] == family]
            ids = {representative[c['id']] for c in fam}
            ranked = rank(summary.loc[summary.candidate.isin(ids)])
            winner = str(ranked.candidate.iloc[0])
            stab = stability(winner, fam, self.grids['axes'][family], summary, representative)
            selection[family] = dict(winner=winner, ranking=ranked.candidate.tolist(), stability=stab,
                                     winner_config=next(c for c in cands if c['id'] == winner))
        write_json(self.output / 's1_selection.json', dict(selection=selection, representative=representative))
        event(self.output, 's1_complete', dict(development_sha256=sha256(self.output / 'development.csv'),
                                               selection_sha256=sha256(self.output / 's1_selection.json'),
                                               winners={f: s['winner'] for f, s in selection.items()}))
        return read_json(self.output / 's1_selection.json')

    # -- S2
    def treatments(self, winner):
        out = []
        for t in self.grids['s2_treatments']:
            c = copy.deepcopy(winner)
            c['id'] = f"{winner['id']}_{t['id']}"
            c['construction'] = dict(c['construction'], method=t['method'], regime=bool(t['regime']))
            c['parent'], c['treatment'] = winner['id'], t['id']
            out.append(candidate_config(c))
        return out

    def s2(self):
        require_stage(self.output, 's1')
        if completed(self.output, 's2'):
            log(self.output, 's2 already complete')
            return read_json(self.output / 's2_selection.json')
        s1 = read_json(self.output / 's1_selection.json')['selection']
        winners = {f: s['winner_config'] for f, s in s1.items()}
        treated = [t for f in sorted(winners) for t in self.treatments(winners[f])]
        path = self.output / 's2_candidates.json'
        if path.exists() and read_json(path) != clean(treated):
            raise ValueError('S2 treatments changed')
        write_json(path, treated)
        event(self.output, 's2_declared', dict(sha256=sha256(path)))
        frame = run_batch(self.output, self.workers, self.init(), treated, self.split('development'), 's2')
        records = [*self.load('development').to_dict('records'), *frame.to_dict('records')]
        summary = summarize(records).set_index('candidate')
        result = {}
        for family, winner in winners.items():
            rows = []
            for t in [c for c in treated if c['parent'] == winner['id']]:
                p = paired(records, t['id'], winner['id'], ['development'])
                p.pop('episodes')
                rows.append(dict(candidate=t['id'], treatment=t['treatment'], method=t['construction']['method'],
                                 regime=t['construction']['regime'], paired=p, kept=s2_keep(p)))
            kept = [r['candidate'] for r in rows if r['kept']]
            pool = rank(summary.loc[[winner['id'], *kept]].reset_index())
            adopted = str(pool.candidate.iloc[0])
            config = winner if adopted == winner['id'] else next(c for c in treated if c['id'] == adopted)
            result[family] = dict(winner=winner['id'], treatments=rows, kept=kept, adopted=adopted,
                                  adopted_config=config,
                                  rule='kept iff paired development median delta >= 0 and P25 delta >= -0.5pp; '
                                       'adopted = best of plain winner and kept treatments by §7 ranking')
        write_json(self.output / 's2_selection.json', result)
        event(self.output, 's2_complete', dict(s2_sha256=sha256(self.output / 's2.csv'),
                                               selection_sha256=sha256(self.output / 's2_selection.json'),
                                               adopted={f: r['adopted'] for f, r in result.items()}))
        return result

    # -- validation
    def frozen_candidates(self):
        s2 = read_json(self.output / 's2_selection.json')
        return {f: s2[f]['adopted_config'] for f in sorted(s2)}

    def validation(self):
        require_stage(self.output, 's2')
        if completed(self.output, 'validation'):
            log(self.output, 'validation already complete')
            return
        frozen = self.frozen_candidates()
        run_batch(self.output, self.workers, self.init(), [*frozen.values(), self.baseline],
                  self.split('validation'), 'validation')
        event(self.output, 'validation_complete', dict(validation_sha256=sha256(self.output / 'validation.csv')))

    # -- freeze
    def freeze(self):
        require_stage(self.output, 'validation')
        frozen = self.frozen_candidates()
        s1 = read_json(self.output / 's1_selection.json')
        records = self.load('validation').to_dict('records')
        stab = {c['id']: s1['selection'][f]['stability'] for f, c in frozen.items()}
        gates, summary = validation_gates(records, [c['id'] for c in frozen.values()], self.baseline['id'],
                                          stab, frozen['A']['id'])
        preferred = choose_preferred(gates, summary)
        body = dict(candidates=frozen, config_hashes={f: canonical(c) for f, c in frozen.items()},
                    candidate_ids={f: c['id'] for f, c in frozen.items()}, baseline_id=self.baseline['id'],
                    baseline=self.baseline, preferred=preferred, gates=gates,
                    evidence={k: sha256(self.output / k) for k in
                              ('validation.csv', 'development.csv', 's2.csv', 's1_selection.json', 's2_selection.json')},
                    decision_rule='docs/v5_spec.md §7: preferred = best validation median passing feasibility, '
                                  'paired V3 median/P25/P10 tolerances, complexity-earned vs A, stability; '
                                  'holdout can only reject; no swap after holdout')
        path = self.output / 'freeze.json'
        if path.exists():
            old = read_json(path)
            if {k: old.get(k) for k in ('candidates', 'preferred', 'config_hashes')} != clean(
                    {k: body[k] for k in ('candidates', 'preferred', 'config_hashes')}):
                raise ValueError('Frozen selection differs from the existing freeze; never re-freeze')
            log(self.output, 'freeze already written')
            return old
        body['frozen_at'] = now()
        write_json(path, body)
        event(self.output, 'freeze', dict(freeze_sha256=sha256(path), preferred=preferred,
                                          candidates={f: c['id'] for f, c in frozen.items()}))
        return read_json(path)

    def verified_freeze(self):
        """Holdout guard: freeze exists, is chained exactly once, and its content hashes verify."""
        path = self.output / 'freeze.json'
        if not path.exists():
            raise RuntimeError('Holdout refused: freeze.json does not exist')
        events = read_events(self.output)
        verify_chain(events)
        freezes = [e for e in events if e['event'] == 'freeze']
        if len(freezes) != 1 or freezes[0]['payload'].get('freeze_sha256') != sha256(path):
            raise RuntimeError('Holdout refused: freeze.json is not the single chained freeze')
        freeze = read_json(path)
        for family, config in freeze['candidates'].items():
            if canonical(config) != freeze['config_hashes'][family]:
                raise RuntimeError('Holdout refused: frozen candidate hash mismatch')
        for name, digest in freeze['evidence'].items():
            if sha256(self.output / name) != digest:
                raise RuntimeError('Holdout refused: frozen evidence changed: ' + name)
        return freeze

    # -- holdout + diagnostics
    def holdout(self):
        freeze = self.verified_freeze()
        run = [*freeze['candidates'].values(), self.baseline]
        if not completed(self.output, 'holdout'):
            run_batch(self.output, self.workers, self.init(), run, self.split('holdout_retrospective'), 'holdout')
            event(self.output, 'holdout_complete', dict(holdout_sha256=sha256(self.output / 'holdout.csv')))
        if not completed(self.output, 'diagnostics'):
            run_batch(self.output, self.workers, self.init(), run,
                      self.split('diagnostic_seasonal', 'diagnostic_recent'), 'diagnostics')
            event(self.output, 'diagnostics_complete', dict(diagnostics_sha256=sha256(self.output / 'diagnostics.csv')))
        self.seal()

    def seal(self):
        phases = ['development', 's2', 'validation', 'holdout', 'diagnostics']
        frames = [self.load(p) for p in phases if (self.output / f'{p}.csv').exists()]
        frame = pd.concat(frames, ignore_index=True).drop_duplicates(['candidate', 'episode'])
        frame.to_csv(self.output / 'all_episodes.csv', index=False)
        skip = {'manifest.json', 'verification.json', 'run.log', 'status.json', 'events.jsonl', 'final_decision.json'}
        outputs = {rel(p): sha256(p) for p in sorted(self.output.rglob('*'))
                   if p.is_file() and p.name not in skip and 'quarantine' not in p.parts}
        scores = read_json(self.output / 'scores_used.json')
        write_json(self.output / 'manifest.json', dict(
            study=rel(self.output), inputs=self.inputs, outputs=outputs,
            scores={k: dict(path=v['path'], sha256=v['sha256']) for k, v in scores.items()},
            features=read_json(repo(self.cache) / 'features.manifest.json'),
            attempts=frame[['candidate', 'episode', 'split', 'run_started_at', 'path']].to_dict('records'),
            completed_at=now()))
        event(self.output, 'manifest', dict(manifest_sha256=sha256(self.output / 'manifest.json')))
        write_json(self.output / 'status.json', dict(status='EXPERIMENTS_COMPLETE_PENDING_AUDIT', attempts=len(frame)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--stage', required=True, choices=[*STAGES, 'all', 'scores'])
    parser.add_argument('--output', default=OUTPUT)
    parser.add_argument('--study', default=STUDY)
    parser.add_argument('--grids', default=GRIDS)
    parser.add_argument('--scores', default=SCORES)
    parser.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 2))
    args = parser.parse_args(argv)
    if args.stage == 'scores':   # score tables need only daily data; no study directory is created
        study_cfg, paths = load_study(args.study)
        registry = read_json(repo(paths['episode_registry']))
        ensure_scores(read_json(repo(args.grids)), ensure_features(paths), registry, args.workers, args.scores)
        return
    study = Study(args.output, args.study, args.grids, args.workers, args.scores)
    stages = STAGES if args.stage == 'all' else [args.stage]
    for stage in stages:
        log(study.output, f'stage {stage} start')
        getattr(study, stage)()
        log(study.output, f'stage {stage} done')


if __name__ == '__main__':
    main()
