#!/usr/bin/env python3
"""Independent Stage 2 accounting, prediction timing, and freeze provenance audit."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_24d import audit_episode as reconstruct_episode, verify_hashes
from scripts.v4_verify import _audit_market, _read, verify_execution_provenance


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def object_hash(value):
    encoded = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def audit_predictions(result):
    predictions = result.get('predictions', pd.DataFrame())
    if predictions.empty:
        return dict(prediction_rows=0, prediction_timing='NO_PREDICTIONS')
    required = {'decision_date', 'observed_date', 'symbol'}
    require(required <= set(predictions), 'Predictions lack decision/observation provenance')
    decision = pd.to_datetime(predictions.decision_date)
    observed = pd.to_datetime(predictions.observed_date)
    require((observed < decision).all(), 'Same-day/future observations in prediction')
    snapshots = result['snapshots']
    pairs = set(zip(snapshots.date.astype(str), snapshots.decision_date.astype(str)))
    require(all((str(a.date()), str(b.date())) in pairs for a, b in zip(observed, decision)),
            'Prediction dates differ from submitted signal calendar')
    require(not predictions.duplicated(['decision_date', 'symbol']).any(),
            'Duplicate prediction identity')
    for column in ('latest_label_end', 'model_train_end', 'training_cutoff'):
        if column in predictions:
            labels = pd.to_datetime(predictions[column].replace('', None))
            require((labels.isna() | labels.le(observed)).all(), 'Unmatured labels: ' + column)
    for column in ('score', 'expected_return', 'confidence', 'uncertainty', 'lower_bound', 'upper_bound'):
        if column in predictions:
            values = pd.to_numeric(predictions[column], errors='coerce')
            require(np.isfinite(values).all(), 'Nonfinite prediction: ' + column)
            if column == 'confidence':
                require(values.between(0, 1).all(), 'Confidence outside [0,1]')
            if column == 'uncertainty':
                require(values.ge(0).all(), 'Negative uncertainty')
    if {'lower_bound', 'upper_bound'} <= set(predictions):
        require(predictions.lower_bound.le(predictions.upper_bound).all(), 'Inverted forecast interval')
    if 'expert_weights' in predictions:
        for encoded in predictions.expert_weights.dropna().unique():
            if not encoded:
                continue
            weights = json.loads(encoded)
            if not weights:
                continue
            values = np.array(list(weights.values()), dtype=float)
            require(np.isfinite(values).all() and (values >= 0).all() and (values <= 1).all()
                    and np.isclose(values.sum(), 1), 'Invalid ensemble weights')
    return dict(prediction_rows=len(predictions), prediction_timing='PASS_DECLARED_CAUSAL_TIMESTAMPS')


def audit_episode_stage2(result, daily, universe, execution, episode=None, context_cache=None):
    """Reconstruct raw fills and books without importing strategy or ledger code."""
    config = result['config']
    require(config.get('sizing_price_mode') == 'official_close', 'Noncanonical sizing basis')
    require(config.get('execution') == 'official_average', 'Noncanonical fill basis')
    prior = config.get('prior_session_date')
    if prior is None:
        dates = pd.to_datetime(daily.date)
        before = dates[dates.lt(pd.Timestamp(config['start']))]
        require(not before.empty, 'Missing independent prior session')
        prior = before.max()
    cache_key = (str(prior), config['start'], config['end'])
    ctx = context_cache.get(cache_key) if context_cache is not None else None
    if ctx is None:
        dates = pd.to_datetime(daily.date)
        raw = daily.loc[dates.between(pd.Timestamp(prior), pd.Timestamp(config['end']))].copy()
        official_dates = pd.to_datetime(execution.date)
        quotes = execution.loc[official_dates.between(pd.Timestamp(prior), pd.Timestamp(config['end']))]
        market = _audit_market(raw, quotes, 'official_average', 'official_close')
        u = universe.copy()
        if 'symbol' not in u:
            u['symbol'] = u.yahoo_symbol.astype(str)
        ctx = dict(daily=market, universe=u)
        if episode is not None:
            ctx['session_dates'] = [episode['prior_session_date'], *episode['sessions']]
        if context_cache is not None:
            context_cache[cache_key] = ctx
    candidate = dict(result)
    if not candidate['warnings'].empty:
        candidate['warnings'] = candidate['warnings'].copy()
        candidate['warnings']['issue'] = candidate['warnings'].issue.replace({
            'UNFILLED_MISSING_OFFICIAL_AVERAGE': 'UNFILLED_MISSING_OPEN_PROXY'})
    audit = reconstruct_episode(candidate, ctx, episode)
    audit.update(audit_predictions(result))
    audit['verification_scope'] = 'INDEPENDENT_ACCOUNTING_AND_DECLARED_TIMING_NOT_PLATFORM_CERTIFICATION'
    return audit


def verify_events(directory, freeze, attempts):
    events = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines() if line]
    previous, previous_time, freezes, declarations = None, None, [], []
    planned = set()
    for event in events:
        require(event.get('previous_hash') == previous, 'Broken event predecessor')
        body = {k: v for k, v in event.items() if k != 'hash'}
        require(event.get('hash') == object_hash(body), 'Event hash mismatch')
        timestamp = pd.Timestamp(event['time'])
        require(previous_time is None or timestamp >= previous_time, 'Event clock reverses')
        previous, previous_time = event['hash'], timestamp
        if event['event'] == 'freeze':
            freezes.append(event)
        if event['event'].endswith('_start') and 'candidates' in event['payload']:
            planned.update((candidate, episode) for candidate in event['payload']['candidates']
                           for episode in event['payload']['episodes'])
        if event['event'] == 'ablations_declared':
            declarations.append(event)
    require(len(freezes) == 1, 'Expected exactly one architecture freeze event')
    event = freezes[0]
    require(event['payload'].get('freeze_sha256') == file_hash(directory / 'freeze.json'),
            'Freeze file differs from chained event')
    require(planned == {(a['candidate'], a['episode']) for a in attempts},
            'Attempted denominator differs from phase-start registry')
    if (directory / 'ablations.json').exists():
        expected_hash = file_hash(directory / 'ablations.json')
        sealed = [e for e in [event, *declarations]
                  if e['payload'].get('ablations_sha256') == expected_hash]
        require(bool(sealed), 'Ablation definitions not sealed before evaluation')
        ablation_ids = {a['id'] for a in json.loads((directory / 'ablations.json').read_text())}
        declaration_time = min(pd.Timestamp(e['time']) for e in sealed)
        require(all(pd.Timestamp(a['run_started_at']) >= declaration_time for a in attempts
                    if a['candidate'] in ablation_ids), 'Ablation was evaluated before declaration')
    frozen_at = pd.Timestamp(freeze['frozen_at'])
    require(pd.Timestamp(event['time']) >= frozen_at, 'Freeze event predates frozen configuration')
    for attempt in attempts:
        split = attempt['split']
        if split == 'historical_holdout' or split.endswith('_diagnostic'):
            require(pd.Timestamp(attempt['run_started_at']) >= pd.Timestamp(event['time']),
                    'Holdout/diagnostic run preceded freeze')
    return dict(events=len(events), freeze_status='PASS_HASH_CHAIN_AND_FREEZE_ORDER')


WORKER = {}


def _initialize_worker(study):
    daily = pd.read_parquet(ROOT / study['daily'])
    daily['date'] = pd.to_datetime(daily.date)
    WORKER.update(daily=daily, universe=pd.read_csv(ROOT / study['universe']),
                  execution=pd.read_csv(ROOT / study['execution_data'], parse_dates=['date']),
                  context_cache={})


def _audit_path(task):
    candidate, episode, directory = task
    path = Path(directory)
    result = {p.stem: _read(p) for p in path.glob('*.csv')}
    result['config'] = json.loads((path / 'config.json').read_text())
    result['metrics'] = json.loads((path / 'metrics.json').read_text())
    try:
        audit = audit_episode_stage2(result, WORKER['daily'], WORKER['universe'],
                                    WORKER['execution'], episode, WORKER['context_cache'])
    except Exception as error:
        raise AssertionError(f'{candidate}/{episode["episode_id"]}: {error}') from error
    return dict(candidate=candidate, episode=episode['episode_id'], **audit)


def verify_directory(directory, workers=8):
    directory = Path(directory).resolve()
    manifest = json.loads((directory / 'manifest.json').read_text())
    verify_hashes(directory, manifest['outputs'])
    for filename, expected in manifest['inputs'].items():
        path = Path(filename)
        path = path if path.is_absolute() else ROOT / path
        require(path.is_file() and file_hash(path) == expected, 'Input hash mismatch: ' + filename)
    feature_manifest = json.loads((directory / 'features.manifest.json').read_text())
    for filename, expected in feature_manifest['inputs'].items():
        path = Path(filename)
        path = path if path.is_absolute() else ROOT / path
        require(file_hash(path) == expected, 'Feature source hash mismatch: ' + filename)
        require(manifest['inputs'].get(filename) == expected, 'Feature source omitted from study provenance')
    if (directory / 'features.pkl').exists():
        require(file_hash(directory / 'features.pkl') == feature_manifest['features_sha256'],
                'Feature cache hash mismatch')
    study = json.loads((directory / 'study.json').read_text())
    registry = json.loads((ROOT / study['episode_registry']).read_text())
    by_id = {e['episode_id']: e for e in registry}
    require(len(by_id) == len(registry), 'Duplicate registered episodes')
    source = verify_execution_provenance(ROOT / study['execution_data'])
    freeze = json.loads((directory / 'freeze.json').read_text())
    require(freeze.get('decision_rule'), 'Freeze lacks predeclared decision rule')
    for family, config in freeze['selected'].items():
        require(object_hash(config) == freeze['config_hashes'][family], 'Frozen candidate hash mismatch')
        require(freeze['candidate_ids'][family] == config['id'], 'Frozen family identity differs')
    for relative, expected in freeze['validation_hashes'].items():
        path = (directory / relative).resolve()
        require(path.is_relative_to(directory) and file_hash(path) == expected,
                'Frozen validation evidence differs')
    attempts = manifest['attempts']
    pairs = [(a['candidate'], a['episode']) for a in attempts]
    require(len(set(pairs)) == len(pairs), 'Duplicate attempted candidate/episode')
    events = verify_events(directory, freeze, attempts)
    summaries = pd.read_csv(directory / 'all_episodes.csv')
    require(not summaries.duplicated(['candidate', 'episode']).any(), 'Duplicate summary attempts')
    require(set(zip(summaries.candidate, summaries.episode)) == set(pairs),
            'Summary excludes attempted failures or includes unregistered attempts')
    authorized = {c['id']: c for c in freeze['selected'].values()}
    authorized['A0_V3'] = dict(id='A0_V3', family='v3')
    if (directory / 'ablations.json').exists():
        for config in json.loads((directory / 'ablations.json').read_text()):
            require(config['id'] not in authorized or authorized[config['id']] == config,
                    'Ablation redefines a frozen candidate')
            authorized[config['id']] = config
    require(freeze.get('evaluation_configs') == authorized, 'Post-freeze evaluation configurations changed')
    required_post = {e['episode_id'] for e in registry
                     if e['split'] not in {'development', 'validation'}}
    for candidate in ['A0_V3', *[c['id'] for c in freeze['selected'].values()]]:
        actual = {a['episode'] for a in attempts if a['candidate'] == candidate}
        require(required_post <= actual, 'Frozen family lacks complete confirmation denominator')
    tasks = []
    for attempt in attempts:
        episode = by_id[attempt['episode']]
        require(attempt['split'] == episode['split'], 'Attempt split differs from registry')
        path = Path(attempt['path'])
        path = (path if path.is_absolute() else ROOT / path).resolve()
        require(path.is_relative_to(directory), 'Attempt path escapes study')
        for item in [*path.glob('*.csv'), *path.glob('*.json')]:
            require(str(item.relative_to(directory)) in manifest['outputs'], 'Unsealed run artifact')
        result = dict(config=json.loads((path / 'config.json').read_text()),
                      metrics=json.loads((path / 'metrics.json').read_text()))
        strategy = json.loads((path / 'strategy_config.json').read_text())
        require(strategy['id'] == attempt['candidate'], 'Candidate identity differs')
        if episode['split'] not in {'development', 'validation'}:
            require(strategy['id'] in authorized and strategy == authorized[strategy['id']],
                    'Unfrozen holdout/diagnostic candidate')
        require(result['config']['start'] == episode['start'] and result['config']['end'] == episode['end'],
                'Run window differs from registry')
        summary = summaries.loc[summaries.candidate.eq(attempt['candidate']) &
                                summaries.episode.eq(attempt['episode'])].iloc[0]
        for key, value in result['metrics'].items():
            require(key in summary, 'Summary omitted metric: ' + key)
            actual = summary[key]
            if value is None or value == '':
                require(pd.isna(actual) or actual == '', 'Summary metric differs: ' + key)
            elif isinstance(value, (float, int)) and not isinstance(value, bool):
                require(math.isclose(float(actual), value, rel_tol=1e-10, abs_tol=1e-8),
                        'Summary numeric metric differs: ' + key)
            else:
                require(actual == value, 'Summary metric differs: ' + key)
        tasks.append((attempt['candidate'], episode, str(path)))
    require(workers >= 1, 'Worker count must be positive')
    if workers == 1:
        _initialize_worker(study)
        audits = list(map(_audit_path, tasks))
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_initialize_worker,
                                 initargs=(study,)) as pool:
            audits = list(pool.map(_audit_path, tasks, chunksize=8))
    require(bool(audits), 'No audited attempts')
    return dict(status='PASS_INDEPENDENT_STAGE2_AUDIT', attempted=len(audits),
                manifest_sha256=file_hash(directory / 'manifest.json'),
                execution_provenance=source, **events, episodes=audits)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/v4/stage2')
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    print(json.dumps(verify_directory(args.output, args.workers), indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
