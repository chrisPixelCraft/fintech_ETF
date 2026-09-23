#!/usr/bin/env python3
"""Resumable chronological 24-session study; no holdout-dependent tuning.

Run ``python scripts/run_24d.py baseline`` before ``... all``. Every group has
an immutable input receipt; a changed source/data/design refuses stale resume.
All attempted episodes, including disqualified and empty failures, are retained.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.audit_24d import (audit_attempt_coverage, audit_episode,
    audit_episode_registry, audit_saved_group, audit_selection_provenance)
from src.strategy_24d import build_config, build_features, run_episode
from src.yahoo_daily import load_daily, load_metadata, load_calendar, resolve_cache

TABLES = ('equity', 'trades', 'orders', 'holdings', 'compliance_daily',
          'rejected_trades', 'warnings', 'snapshots', 'plan_audit')
_WORKER = None
_FEATURES = OrderedDict()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def digest(value):
    return hashlib.sha256(json.dumps(clean(value), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            hasher.update(block)
    return hasher.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp.' + str(os.getpid()))
    temporary.write_text(json.dumps(clean(value), indent=2, ensure_ascii=False, allow_nan=False) + '\n')
    os.replace(temporary, path)


def atomic_frame(path, frame):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp.' + str(os.getpid()))
    if path.suffix == '.parquet':
        frame.to_parquet(temporary, index=False, compression='zstd')
    else:
        frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def load_study(path=None):
    value = json.loads(Path(path or ROOT / 'config/24d_study.json').read_text())
    if value['required_measured_pass_rate'] != 1. or value['episode_length'] != 24:
        raise ValueError('Competition gate and episode length are fixed')
    return value


def interval_split(start, end, study):
    for name, (lower, upper) in study['splits'].items():
        if lower <= start <= end <= upper:
            return name
    return 'purged' if '2010-01-01' <= start <= '2024-12-31' else 'outside'


def make_registry(calendar, study=None):
    study = study or load_study()
    sessions = sorted(set(str(pd.Timestamp(d).date()) for d in calendar))
    positions = {day: i for i, day in enumerate(sessions)}
    rows = []
    def add(kind, start):
        index = positions[start]
        if index == 0 or index + 24 > len(sessions):
            raise ValueError('Missing 24-session or prior-session coverage: ' + start)
        end = sessions[index + 23]
        rows.append(dict(episode_id=kind + '_' + start, kind=kind, start=start, end=end,
            split=interval_split(start, end, study), session_count=24,
            prior_session_date=sessions[index - 1]))
    for month in pd.period_range('2010-01', '2024-12', freq='M').astype(str):
        matches = [d for d in sessions if d.startswith(month)]
        if not matches:
            raise ValueError('Missing monthly start: ' + month)
        add('monthly', matches[0])
    for year in range(2010, study['seasonal_end_year'] + 1):
        matches = [d for d in sessions if f'{year}-10-26' <= d <= f'{year}-12-31']
        if not matches:
            raise ValueError('Missing October/November analogue: ' + str(year))
        add('oct_nov', matches[0])
    for day in sessions:
        if '2010-01-01' <= day <= '2024-12-31':
            add('rolling', day)
    return pd.DataFrame(rows)


def candidate_specs(study=None):
    study = study or load_study()
    result = [dict(candidate_id=study['baseline_id'], params={}, source='baseline')]
    for field, values in study['coarse_neighbors'].items():
        for value in values:
            identifier = f'coarse_{len(result):03d}_{field}_{str(value).replace(".", "p")}'
            # Factory validation makes all registered candidates executable.
            build_config({field: value})
            result.append(dict(candidate_id=identifier, params={field: value}, source='coarse'))
    return result


def aggregate(rows, penalty=-1.):
    """Return successful-subset metrics plus explicit full-denominator penalties."""
    summaries = []
    for candidate, group in rows.groupby('candidate_id', sort=True):
        accepted = group.measured_pass.fillna(False).astype(bool)
        complete = group.complete_period.fillna(False).astype(bool)
        valid = group.loc[accepted & complete & group.episode_return.notna()]
        returns = valid.episode_return.astype(float)
        failed = len(group) - len(valid)
        penalized = pd.concat([returns, pd.Series([penalty] * failed, dtype=float)], ignore_index=True)
        summarize = lambda column, method: float(getattr(valid[column].astype(float).dropna(), method)()) if len(valid) and valid[column].notna().any() else None
        summaries.append(dict(candidate_id=candidate, attempted=len(group), passed=int(accepted.sum()),
            completed=int(complete.sum()), failed=failed,
            compliance_pass_rate=float(accepted.mean()), valid_episode_rate=float(complete.mean()),
            metric_population='MEASURED_PASS_COMPLETE_EPISODES_ONLY',
            median_24d_return=float(returns.median()) if len(returns) else None,
            mean_24d_return=float(returns.mean()) if len(returns) else None,
            p25_24d_return=float(returns.quantile(.25)) if len(returns) else None,
            p10_24d_return=float(returns.quantile(.10)) if len(returns) else None,
            worst_24d_return=float(returns.min()) if len(returns) else None,
            positive_episode_rate=float(returns.gt(0).mean()) if len(returns) else None,
            loss_episode_rate=float(returns.lt(0).mean()) if len(returns) else None,
            positive_attempt_rate=float(returns.gt(0).sum()/len(group)),
            median_mdd=summarize('episode_max_drawdown', 'median'),
            worst_mdd=summarize('episode_max_drawdown', 'max'),
            median_turnover=summarize('episode_turnover', 'median'),
            median_days_to_20_holdings=summarize('days_to_20_holdings', 'median') if 'days_to_20_holdings' in valid else None,
            penalized_mean_return=float(penalized.mean()), penalized_median_return=float(penalized.median()),
            penalized_p25_return=float(penalized.quantile(.25)),
            failed_episode_penalty_return=penalty))
    return pd.DataFrame(summaries)


def rank_candidates(summary):
    keys = ['compliance_pass_rate', 'valid_episode_rate', 'median_24d_return',
            'p25_24d_return', 'median_mdd', 'mean_24d_return', 'median_turnover', 'candidate_id']
    return summary.sort_values(keys, ascending=[False, False, False, False, True, False, True, True],
                               na_position='last', kind='stable').reset_index(drop=True)


def refinement_specs(summary, specs, study):
    """At most two nearby values for each of three eligible coarse leaders."""
    baseline = study['baseline_id']
    lookup = {row['candidate_id']: row for row in specs}
    base_params = build_config()['full_tuning_params']
    seen = {digest(row['params']) for row in specs}
    refined = []
    leaders = rank_candidates(summary)
    leaders = leaders[leaders.compliance_pass_rate.ge(study['refinement_requires_development_pass_rate'])]
    for identifier in leaders.candidate_id.head(3):
        parent = lookup[identifier]
        if identifier == baseline or len(parent['params']) != 1:
            continue
        field, value = next(iter(parent['params'].items()))
        distance = value - base_params[field]
        for factor in [.5, 1.5]:
            proposed = base_params[field] + factor * distance
            proposed = int(round(proposed)) if isinstance(base_params[field], int) else round(proposed, 6)
            params = {field: proposed}
            if digest(params) in seen or proposed == base_params[field]:
                continue
            try:
                build_config(params)
            except ValueError:
                continue
            seen.add(digest(params))
            refined.append(dict(candidate_id=f'local_{len(refined)+1:03d}', params=params,
                                source='local_refinement', parent_candidate_id=identifier))
            if len(refined) >= study['local_refinement_limit']:
                return refined
    return refined


def source_hashes(study_path):
    paths = list((ROOT / 'src').glob('*.py'))
    paths += [ROOT / p for p in ['scripts/run_24d.py', 'scripts/audit_24d.py',
        'tests/test_strategy_24d.py', 'tests/test_audit_24d.py', 'tests/test_24d_study.py',
        'config/strategy_v2.json', 'outputs/best_v2/official_ex_post/config.json',
        'data/reference/universe_competition_20260731.csv', 'docs/v3_spec.md', 'docs/v2_double_check_rules.md']]
    paths += [Path(study_path)]
    return {str(path.relative_to(ROOT)): file_hash(path) for path in sorted(set(paths))}


def make_context(output, cache=None, study_path=None):
    study_path = Path(study_path or ROOT / 'config/24d_study.json')
    study = load_study(study_path)
    folder = resolve_cache(cache)
    metadata = load_metadata(folder)
    daily = load_daily(folder)
    universe = pd.read_csv(ROOT / 'data/reference/universe_competition_20260731.csv')
    universe['symbol'] = universe.yahoo_symbol.astype(str)
    universe['known_at'] = universe.attachment_created_at
    hashes = source_hashes(study_path)
    calendar = load_calendar(folder)
    guard = dict(source_sha256=hashes, metadata_sha256=file_hash(folder / 'metadata.json'),
                 calendar_sha256=digest(calendar), calendar_amendment_sha256=file_hash(folder / 'calendar_v2.json'),
                 artifact_sha256=metadata['artifact_sha256'], study=study)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / 'study_manifest.json'
    if manifest.exists() and json.loads(manifest.read_text())['guard_sha256'] != digest(guard):
        raise ValueError('Study inputs changed; refuse stale resume. Use a new output directory.')
    if not manifest.exists():
        atomic_json(manifest, dict(created_at=utc_now(), guard_sha256=digest(guard), guard=guard,
            git_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            git_status=subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, text=True),
            launch_command=sys.argv, python=sys.version, pandas=pd.__version__, numpy=np.__version__,
            pid=os.getpid(), cache=str(folder), source_accuracy_verified=False))
    return dict(output=output, study=study, daily=daily, universe=universe,
                session_dates=calendar, guard_sha256=digest(guard),
                source_hashes=hashes, cache=str(folder))


def _initialize_worker(context):
    global _WORKER, _FEATURES
    _WORKER, _FEATURES = context, OrderedDict()


def _candidate_config(spec):
    config = build_config(spec['params'])
    config['strategy_id'] = spec['candidate_id']
    config['candidate_id'] = spec['candidate_id']
    if spec.get('research_reference'):
        config['research_reference'] = spec['research_reference']
    return config


def _group_job(spec, records, phase):
    context = _WORKER
    candidate = spec['candidate_id']
    folder = context['output'] / 'ledgers' / phase / candidate
    config = _candidate_config(spec)
    expected = digest(dict(study=context['guard_sha256'], phase=phase, config=config, episodes=records))
    receipt_path = folder / 'receipt.json'
    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt['input_sha256'] != expected:
            raise ValueError('Group input guard changed: ' + str(folder))
        for filename, checksum in receipt['artifact_sha256'].items():
            if file_hash(folder / filename) != checksum:
                raise ValueError('Group artifact changed: ' + str(folder / filename))
        return pd.read_csv(folder / 'metrics.csv', float_precision='round_trip')
    key = digest(config)
    if key not in _FEATURES:
        _FEATURES[key] = build_features(context['daily'], config)
        while len(_FEATURES) > 2:
            _FEATURES.popitem(last=False)
    panel = _FEATURES[key]
    calendar = context['session_dates']
    positions = {d: i for i, d in enumerate(calendar)}
    tables = {name: [] for name in TABLES}
    metric_rows, audit_rows = [], []
    last_heartbeat = 0.
    for episode in records:
        if time.monotonic() - last_heartbeat >= 30:
            atomic_json(folder / 'status.json', dict(status='RUNNING', updated_at=utc_now(),
                completed_episodes=len(metric_rows), total_episodes=len(records),
                phase=phase, candidate_id=candidate, pid=os.getpid()))
            last_heartbeat = time.monotonic()
        begin = positions[episode['start']]
        cfg = {**config, 'prior_session_date': episode['prior_session_date']}
        try:
            result = run_episode(context['daily'], context['universe'], cfg, calendar[begin:begin+24], panel)
            if result['metrics'].get('error') and result['metrics'].get('episode_status') == 'FAIL_OTHER':
                raise RuntimeError('Unexpected engine failure: ' + result['metrics']['error'])
        except Exception:
            atomic_json(folder / 'failure.json', dict(episode=episode, config=cfg,
                failed_at=utc_now(), traceback=traceback.format_exc(), status='ENGINEERING_FAILURE'))
            raise
        # An audit exception stops the group/search; it is an engineering error,
        # never recoded as a harmless strategy failure or silently omitted.
        try:
            checked = audit_episode(result, context, episode)
        except Exception:
            atomic_json(folder / 'failure.json', dict(episode=episode, config=cfg,
                failed_at=utc_now(), traceback=traceback.format_exc(), status='AUDIT_FAILURE'))
            raise
        audit_rows.append(dict(episode_id=episode['episode_id'], **checked))
        equity = result['equity']
        days = None
        if len(equity) and equity.holdings.ge(20).any():
            days = int(np.flatnonzero(equity.holdings.ge(20).to_numpy())[0] + 1)
        metric_rows.append(dict({**result['metrics'], **episode}, candidate_id=candidate, phase=phase,
                               realized_start=result['metrics'].get('start'),
                               realized_end=result['metrics'].get('end'), days_to_20_holdings=days,
                               independent_audit=checked['independent_audit']))
        for name in TABLES:
            frame = result[name].copy()
            frame['episode_id'] = episode['episode_id']
            tables[name].append(frame)
    folder.mkdir(parents=True, exist_ok=True)
    rows = pd.DataFrame(metric_rows)
    atomic_frame(folder / 'metrics.csv', rows)
    atomic_frame(folder / 'audit.csv', pd.DataFrame(audit_rows))
    for name, pieces in tables.items():
        frame = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame(columns=['episode_id'])
        atomic_frame(folder / (name + '.parquet'), frame)
    atomic_json(folder / 'config.json', config)
    reread = pd.read_csv(folder / 'metrics.csv', float_precision='round_trip')
    verified = audit_saved_group(folder, reread, {candidate: config}, context, pd.DataFrame(records))
    atomic_frame(folder / 'serialized_audit.csv', verified)
    atomic_json(folder / 'status.json', dict(status='COMPLETE', updated_at=utc_now(),
        completed_episodes=len(rows), total_episodes=len(records), phase=phase, candidate_id=candidate))
    artifacts = ['metrics.csv', 'audit.csv', 'serialized_audit.csv', 'config.json'] + [name + '.parquet' for name in TABLES]
    atomic_json(receipt_path, dict(input_sha256=expected, completed_at=utc_now(),
        candidate_id=candidate, phase=phase, episodes=len(rows),
        artifact_sha256={name: file_hash(folder / name) for name in artifacts}))
    return rows


def run_groups(specs, episodes, phase, context, workers=4):
    """Run candidate groups; rolling phases are divided by year for bounded RAM."""
    workers = max(1, min(int(workers), int(context['study']['max_workers']), 4))
    tasks = []
    if phase == 'rolling':
        for year, subset in episodes.groupby(episodes.start.str[:4], sort=True):
            tasks.extend((spec, subset.to_dict('records'), phase + '_' + year) for spec in specs)
    else:
        tasks = [(spec, episodes.to_dict('records'), phase) for spec in specs]
    if not tasks:
        return pd.DataFrame()
    for spec in specs:
        path = context['output'] / 'configs' / (spec['candidate_id'] + '.json')
        config = _candidate_config(spec)
        if path.exists() and json.loads(path.read_text()) != clean(config):
            raise ValueError('Candidate identifier/config changed: ' + spec['candidate_id'])
        if not path.exists():
            atomic_json(path, config)
    results = []
    if workers == 1:
        _initialize_worker(context)
        for task in tasks:
            results.append(_group_job(*task))
            print(json.dumps(dict(event='group_complete', phase=task[2], candidate_id=task[0]['candidate_id'])), flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context('fork'),
                                 initializer=_initialize_worker, initargs=(context,)) as pool:
            futures = {pool.submit(_group_job, *task): task for task in tasks}
            for future in as_completed(futures):
                task = futures[future]
                results.append(future.result())
                print(json.dumps(dict(event='group_complete', phase=task[2], candidate_id=task[0]['candidate_id'])), flush=True)
    rows = pd.concat(results, ignore_index=True).sort_values(['candidate_id', 'episode_id']).reset_index(drop=True)
    audit_attempt_coverage(rows, episodes.episode_id, [row['candidate_id'] for row in specs])
    atomic_frame(context['output'] / (phase + '.csv'), rows)
    atomic_frame(context['output'] / (phase + '_summary.csv'), aggregate(rows))
    return rows


def test_gate(context):
    completed = subprocess.run([sys.executable, '-m', 'unittest', 'tests.test_strategy_24d',
        'tests.test_audit_24d', 'tests.test_24d_study', '-q'], cwd=ROOT,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    record = dict(completed_at=utc_now(), returncode=completed.returncode,
                  output=completed.stdout, source_guard=context['guard_sha256'])
    atomic_json(context['output'] / 'test_gate.json', record)
    if completed.returncode:
        raise RuntimeError('Baseline test gate failed: ' + completed.stdout)


def _write_candidates(context, specs):
    frame = pd.DataFrame([dict(candidate_id=s['candidate_id'], source=s['source'],
        parent_candidate_id=s.get('parent_candidate_id'), params_json=json.dumps(s['params'], sort_keys=True)) for s in specs])
    atomic_frame(context['output'] / 'candidates.csv', frame)
    return frame


def freeze_selection(context, registry, specs, development, validation):
    study = context['study']
    ids = set(validation.candidate_id)
    dev = aggregate(development[development.candidate_id.isin(ids)])
    val = aggregate(validation)
    eligible_ids = set(dev.loc[dev.compliance_pass_rate.eq(1.), 'candidate_id']) & set(val.loc[val.compliance_pass_rate.eq(1.), 'candidate_id'])
    ranked = rank_candidates(val)
    selectable = ranked[ranked.candidate_id.isin(eligible_ids)]
    research_best = str(ranked.iloc[0].candidate_id)
    chosen = str(selectable.iloc[0].candidate_id) if len(selectable) else study['baseline_id']
    decision = 'FROZEN_MEASURED_CANDIDATE' if len(selectable) else 'NO_ELIGIBLE_CANDIDATE'
    selection_ids = sorted(set(development.episode_id) | set(validation.episode_id))
    holdout_ids = registry.loc[registry.kind.eq('monthly') & registry.split.eq('holdout'), 'episode_id'].tolist()
    inputs = {str(path.relative_to(ROOT)): file_hash(path) for path in [
        context['output'] / 'development.csv', context['output'] / 'validation.csv',
        context['output'] / 'candidates.csv', context['output'] / 'episodes.csv']}
    inputs.update(context['source_hashes'])
    config_path = context['output'] / 'configs' / (chosen + '.json')
    selection = dict(candidate_id=chosen, diagnostic_candidate_id=study['baseline_id'] if not len(selectable) else None,
        research_best_candidate_id=research_best, decision=decision,
        comparison_candidate_ids=list(dict.fromkeys([study['baseline_id'], chosen, research_best] if not len(selectable) else [study['baseline_id'], chosen])),
        required_measured_pass_rate=1., selection_frozen_at=utc_now(),
        selection_episode_ids=selection_ids, holdout_episode_ids=holdout_ids,
        candidate_config_sha256=file_hash(config_path), candidate_config_path=str(config_path.relative_to(ROOT)),
        selection_input_hashes=inputs,
        number_of_candidates_evaluated=len(specs), selection_data=study['splits'],
        submission_status='BLOCK_SUBMISSION', ready_status='BLOCK_READY',
        active_share_status='ACTIVE_SHARE_NOT_VERIFIED',
        reason='Missing official Active Share/settlement and capacity evidence',
        prior_2025_2026_baseline_selection_confound=True)
    frozen_path = context['output'] / 'final_selection.json'
    if frozen_path.exists():
        previous = json.loads(frozen_path.read_text())
        comparable = {k: v for k, v in selection.items() if k != 'selection_frozen_at'}
        if comparable != {k: v for k, v in previous.items() if k != 'selection_frozen_at'}:
            raise ValueError('Frozen selection differs; never revise using holdout observations')
        selection = previous
    else:
        atomic_json(frozen_path, selection)
    final_config = {**json.loads(config_path.read_text()), 'selection_provenance': selection}
    atomic_json(context['output'] / 'frozen_candidate.json', final_config)
    if context['output'].resolve() == (ROOT / 'outputs/24d').resolve():
        canonical = ROOT / 'configs/competition_24d_final.json'
        if canonical.exists() and json.loads(canonical.read_text()) != clean(final_config):
            raise ValueError('Canonical final configuration already exists and differs; refusing overwrite')
        if not canonical.exists():
            atomic_json(canonical, final_config)
    return selection


def walk_forward(rows, specs, registry, context):
    """Predeclared family only; each test window fully follows training cutoff."""
    study, decisions, chosen_rows = context['study'], [], []
    for year in range(study['walk_forward_first_test_year'], study['walk_forward_last_test_year'] + 1):
        train = rows[rows.end.lt(f'{year}-01-01')]
        test = rows[rows.start.ge(f'{year}-01-01') & rows.end.le(f'{year}-12-31')]
        ranked = rank_candidates(aggregate(train))
        eligible = ranked[ranked.compliance_pass_rate.eq(1.)]
        identifier = str(eligible.iloc[0].candidate_id) if len(eligible) else study['baseline_id']
        decisions.append(dict(test_year=year, candidate_id=identifier,
            training_end=f'{year-1}-12-31', train_episodes=int(train.episode_id.nunique()),
            test_episodes=int(test.episode_id.nunique()),
            decision='MEASURED_ELIGIBLE' if len(eligible) else 'NO_ELIGIBLE_BASELINE_DIAGNOSTIC'))
        picked = test[test.candidate_id.eq(identifier)].copy()
        picked['test_year'] = year
        chosen_rows.append(picked)
    result = pd.concat(chosen_rows, ignore_index=True)
    atomic_frame(context['output'] / 'walk_forward_decisions.csv', pd.DataFrame(decisions))
    atomic_frame(context['output'] / 'walk_forward.csv', result)
    return result


def execute(command, context, workers=4):
    study, out = context['study'], context['output']
    registry = make_registry(context['session_dates'], study)
    registry_audit = audit_episode_registry(registry, context['session_dates'])
    atomic_frame(out / 'episodes.csv', registry)
    test_gate(context)
    specs = candidate_specs(study)
    _write_candidates(context, specs)
    monthly = registry[registry.kind.eq('monthly')]
    dev_ep = monthly[monthly.split.eq('development')]
    val_ep = monthly[monthly.split.eq('validation')]
    baseline = [specs[0]]
    baseline_dev = run_groups(baseline, dev_ep, 'baseline_development', context, workers=1)
    if command == 'baseline':
        return baseline_dev
    coarse_dev = run_groups(specs, dev_ep, 'development_coarse', context, workers)
    refinements = refinement_specs(aggregate(coarse_dev), specs, study)
    refinement_path = out / 'refinement_design.json'
    refinement_design = dict(parameters=refinements, source='DEVELOPMENT_ONLY',
        input_sha256=file_hash(out / 'development_coarse.csv'))
    if refinement_path.exists():
        prior_design = json.loads(refinement_path.read_text())
        if {k: v for k, v in prior_design.items() if k != 'created_at'} != refinement_design:
            raise ValueError('Frozen development refinement design changed')
    else:
        atomic_json(refinement_path, dict(created_at=utc_now(), **refinement_design))
    if refinements:
        local_dev = run_groups(refinements, dev_ep, 'development_local', context, workers)
        development = pd.concat([coarse_dev, local_dev], ignore_index=True)
    else:
        development = coarse_dev.copy()
    all_specs = specs + refinements
    _write_candidates(context, all_specs)
    atomic_frame(out / 'development.csv', development)
    atomic_frame(out / 'development_summary.csv', aggregate(development))
    top = rank_candidates(aggregate(development)).candidate_id.head(study['validation_shortlist']).tolist()
    shortlist = [s for s in all_specs if s['candidate_id'] in set(top + [study['baseline_id']])]
    validation = run_groups(shortlist, val_ep, 'validation', context, workers)
    selection = freeze_selection(context, registry, all_specs, development, validation)
    selected = next(s for s in all_specs if s['candidate_id'] == selection['candidate_id'])
    comparison = [s for s in all_specs if s['candidate_id'] in selection['comparison_candidate_ids']]
    holdout_stamp = out / 'holdout_started.json'
    if not holdout_stamp.exists():
        atomic_json(holdout_stamp, dict(started_at=utc_now(), frozen_selection_sha256=file_hash(out / 'final_selection.json')))
    started = json.loads(holdout_stamp.read_text())
    if started['frozen_selection_sha256'] != file_hash(out / 'final_selection.json'):
        raise ValueError('Holdout exists for a different frozen selection')
    provenance = audit_selection_provenance(selection, registry, pd.read_csv(out / 'candidates.csv'),
        root=ROOT, holdout_started_at=started['started_at'])
    holdout = run_groups(comparison, monthly[monthly.split.eq('holdout')], 'holdout', context, workers)
    # Evaluation-only panels after freeze. They cannot update selection or refinement.
    comparison_rows = run_groups(comparison, monthly, 'monthly_comparison', context, workers)
    seasonal = run_groups(comparison, registry[registry.kind.eq('oct_nov')], 'oct_nov', context, workers)
    rolling = run_groups(comparison, registry[registry.kind.eq('rolling')], 'rolling', context, workers)
    sanity = dict(candidate_id='simple_momentum_reference', params={}, source='sanity', research_reference='simple_momentum')
    run_groups([sanity], monthly, 'sanity_monthly', context, workers=1)
    run_groups([sanity], registry[registry.kind.eq('oct_nov')], 'sanity_oct_nov', context, workers=1)
    recent = []
    calendar = context['session_dates']
    for month in sorted({d[:7] for d in calendar if d >= '2025-01-01'}):
        start = next(d for d in calendar if d.startswith(month))
        index = calendar.index(start)
        if index + 24 <= len(calendar):
            recent.append(dict(episode_id='recent_' + start, kind='recent_monthly', start=start,
                end=calendar[index+23], split='outside', session_count=24, prior_session_date=calendar[index-1]))
    recent_registry = pd.DataFrame(recent)
    atomic_frame(out / 'recent_episodes.csv', recent_registry)
    if len(recent_registry):
        run_groups(comparison + [sanity], recent_registry, 'recent_stress', context, workers)
    wf_later = run_groups(specs, monthly[~monthly.episode_id.isin(dev_ep.episode_id)], 'walk_forward_later', context, workers)
    wf_rows = pd.concat([coarse_dev.assign(source_phase='development_coarse'),
                         wf_later.assign(source_phase='walk_forward_later')], ignore_index=True)
    audit_attempt_coverage(wf_rows, monthly.episode_id, [spec['candidate_id'] for spec in specs])
    atomic_frame(out / 'walk_forward_family.csv', wf_rows)
    walk_forward(wf_rows, specs, registry, context)
    audit = dict(completed_at=utc_now(), registry=registry_audit, selection=provenance,
                 group_audits='EVERY_ATTEMPT_RECONSTRUCTED_BEFORE_SAVE',
                 source_accuracy_verified=False, active_share_status='ACTIVE_SHARE_NOT_VERIFIED',
                 ready_status='BLOCK_READY', rolling_windows_independent=False)
    receipts = {str(path.relative_to(out)): json.loads(path.read_text())
                for path in sorted((out / 'ledgers').glob('*/*/receipt.json'))}
    audit['groups'] = len(receipts)
    audit['audited_attempts'] = sum(row['episodes'] for row in receipts.values())
    atomic_json(out / 'audit.json', audit)
    atomic_json(out / 'run_manifest.json', dict(completed_at=utc_now(),
        source_guard=context['guard_sha256'], group_receipts=receipts,
        output_sha256={str(path.relative_to(out)): file_hash(path)
            for path in sorted(out.glob('*')) if path.is_file() and path.name != 'run_manifest.json'}))
    return comparison_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['baseline', 'all'])
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/24d')
    parser.add_argument('--cache', type=Path)
    parser.add_argument('--study', type=Path, default=ROOT / 'config/24d_study.json')
    args = parser.parse_args()
    context = make_context(args.output, args.cache, args.study)
    execute(args.command, context, args.workers)


if __name__ == '__main__':
    main()
