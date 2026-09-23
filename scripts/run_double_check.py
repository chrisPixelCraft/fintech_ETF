"""Resumable bounded study with independent per-trial accounting verification."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import multiprocessing as mp
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from src import double_check_tuning as model, tuning_a_deep
from scripts.prepare_double_check import local_grid
from scripts.prepare_official_deep import canonical
from scripts.audit_official_v2 import primary_months
from src.backtest import save_result

CONTEXTS = {}
RUN_OWNED = False
TABLES = ('equity', 'orders', 'trades', 'holdings', 'warnings', 'snapshots',
          'plan_audit', 'compliance_daily', 'rejected_trades')
TRIAL_FILES = {name + '.csv' for name in TABLES} | {
    'config.json', 'independent_audit.json', 'monthly.csv', 'metrics.json'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def verify_hashes(root, hashes):
    for relative, expected in hashes.items():
        if sha(root / relative) != expected:
            raise ValueError('Frozen file changed: ' + relative)


def trial_run(track, trial, root):
    from scripts.audit_double_check import audit_result
    folder = root / track / 'trials' / trial['candidate_id']
    folder.mkdir(parents=True, exist_ok=False)
    start = time.monotonic()
    try:
        context = CONTEXTS[track]
        result = model.run_model(context, model.config_for(context, trial))
        audit = audit_result(result, context)
        for name in TABLES:
            result[name].to_csv(folder / (name + '.csv'), index=False)
        dump(folder / 'config.json', result['config'])
        dump(folder / 'independent_audit.json', audit)
        pd.DataFrame(primary_months(result['equity'], result['config']['initial_cash'])).to_csv(folder / 'monthly.csv', index=False)
        row = dict(**result['metrics'], **trial['params'], candidate_id=trial['candidate_id'],
                   phase=trial['phase'], track=track, status='COMPLETE',
                   elapsed_seconds=time.monotonic() - start)
        row['eligible'] = model.eligible(row)
        row['research_eligible'] = row['eligible']
        dump(folder / 'metrics.json', row)
        dump(folder / 'receipt.json', {p.name: sha(p) for p in folder.iterdir() if p.is_file()})
        return row
    except Exception as exc:
        dump(folder / 'failure.json', dict(error=str(exc), traceback=traceback.format_exc()))
        raise


def batch(trials, root, workers, phase):
    rows, pending = [], []
    for track, context in CONTEXTS.items():
        context['cache'].prewarm(trials)
        for trial in trials:
            folder = root / track / 'trials' / trial['candidate_id']
            if (folder / 'receipt.json').exists():
                receipt = json.loads((folder / 'receipt.json').read_text())
                if set(receipt) != TRIAL_FILES or (folder / 'failure.json').exists():
                    raise ValueError('Incomplete/failed resumed trial receipt')
                verify_hashes(folder, receipt)
                if json.loads((folder / 'config.json').read_text()) != model.config_for(context, trial):
                    raise ValueError('Resumed configuration changed')
                row = json.loads((folder / 'metrics.json').read_text())
                audit = json.loads((folder / 'independent_audit.json').read_text())
                if (row['track'] != track or row['candidate_id'] != trial['candidate_id']
                        or row['status'] != 'COMPLETE' or row['phase'] != trial['phase']
                        or any(row[k] != v for k, v in trial['params'].items())
                        or audit.get('independent_audit') != 'PASS'
                        or row['eligible'] != model.eligible(row)
                        or row.get('research_eligible') != model.eligible(row)):
                    raise ValueError('Invalid resumed trial identity/audit/eligibility')
                rows.append(row)
            elif folder.exists():
                raise ValueError('Quarantine incomplete trial before resuming: ' + str(folder))
            else:
                pending.append((track, trial))
    with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context('fork')) as pool:
        jobs = {pool.submit(trial_run, track, trial, root): (track, trial) for track, trial in pending}
        for job in as_completed(jobs):
            try:
                row = job.result()
            except Exception:
                for other in jobs:
                    other.cancel()
                raise
            rows.append(row)
            dump(root / 'status.json', dict(status='RUNNING', phase=phase,
                 completed=len(rows), expected=2 * len(trials), pid=os.getpid(),
                 last_candidate=row['candidate_id'], last_track=row['track'], updated_at=now()))
            print(f"{phase} {len(rows)}/{2 * len(trials)} {row['track']} {row['candidate_id']} "
                  f"eligible={row['eligible']} return={row['total_return']:.6f}", flush=True)
    return rows


def now():
    return datetime.now(timezone.utc).isoformat()


def dependencies():
    old = json.loads((ROOT / 'outputs/full_tuned_v2/audit.json').read_text())['input_hashes']
    new = ['src/double_check_ledger.py', 'src/double_check_tuning.py',
           'scripts/prepare_double_check.py', 'scripts/run_double_check.py',
           'scripts/audit_double_check.py', 'config/v2_double_check_study.json',
           'outputs/full_tuned_v2/selection.json', 'outputs/full_tuned_v2/local_candidates.json']
    return {relative: sha(ROOT / relative) for relative in dict.fromkeys([*old, *new])}


def finalize(root, study, rows, trials):
    from scripts.audit_double_check import audit_result
    selected = model.choose([row for row in rows if row['track'] == study['selection_track']])
    if selected is None:
        dump(root / 'selection.json', dict(status='NO_ELIGIBLE_WINNER', submission_status='BLOCK_SUBMISSION'))
        raise ValueError('NO_ELIGIBLE_WINNER: no rule relaxation permitted')
    trial = next(t for t in trials if t['candidate_id'] == selected['candidate_id'])
    dump(root / 'selection.json', dict(**trial, selected_on='official_ex_post',
         scope='EX_POST_DEVELOPMENT_ONLY', formal_submission='BLOCK_IF_UNKNOWN',
         global_exhaustive=False, local_grid_exhaustive=True,
         rule='ZERO_ALL_DECLARED_GUARDS_THEN_BOOK_RETURN_MDD_TURNOVER_ID'))
    comparisons, monthly, final_audits = [], [], {}
    for track, context in CONTEXTS.items():
        result = model.run_model(context, model.config_for(context, trial))
        final_audits[track] = audit_result(result, context)
        previous = root / track / 'trials' / trial['candidate_id']
        for table in TABLES:
            expected = pd.read_csv(previous / (table + '.csv'), keep_default_na=False, float_precision='round_trip')
            pd.testing.assert_frame_equal(result[table].fillna('').reset_index(drop=True), expected,
                                          check_dtype=False, rtol=1e-12, atol=1e-5)
        folder = root / track / 'final' / 'v2_double_check_fintuned'
        if folder.exists():
            receipt = json.loads((folder / 'receipt.json').read_text())
            verify_hashes(folder, receipt)
            if json.loads((folder / 'config.json').read_text()) != result['config']:
                raise ValueError('Final config changed')
            for table in TABLES:
                pd.testing.assert_frame_equal(result[table].fillna('').reset_index(drop=True),
                    pd.read_csv(folder / (table + '.csv'), keep_default_na=False, float_precision='round_trip'),
                    check_dtype=False, rtol=1e-12, atol=1e-5)
        else:
            folder.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=folder.parent, prefix='.candidate-') as temporary:
                staged = Path(temporary) / 'release'
                save_result(result, staged)
                for table in ('plan_audit', 'compliance_daily', 'rejected_trades'):
                    result[table].to_csv(staged / (table + '.csv'), index=False)
                dump(staged / 'independent_audit.json', final_audits[track])
                dump(staged / 'receipt.json', {p.name: sha(p) for p in staged.iterdir() if p.is_file()})
                staged.rename(folder)
        comparisons.append(dict(track=track, model='v2_double_check_fintuned', **result['metrics']))
        monthly += [dict(track=track, model='v2_double_check_fintuned', **r)
                    for r in primary_months(result['equity'], 1e9)]
    # Controls are immutable prior artifacts; new research accounting is never
    # passed off as a recomputation of their frozen reported performance.
    old_comparison = pd.read_csv(ROOT / 'outputs/full_tuned_v2/comparison.csv')
    for row in old_comparison.to_dict('records'):
        row['model'] = 'incumbent_f0019' if row['model'] == 'full_tuned_v2' else row['model']
        comparisons.append(row)
    for row in rows:
        if row['candidate_id'] == 'd0000':
            comparisons.append(dict(row, model='incumbent_replayed_new_ledger'))
    pd.DataFrame(comparisons).to_csv(root / 'comparison.csv', index=False)
    pd.DataFrame(monthly).to_csv(root / 'monthly.csv', index=False)
    return final_audits


def main(args):
    global RUN_OWNED
    root = ROOT / args.output
    study = json.loads((ROOT / 'config/v2_double_check_study.json').read_text())
    manifest_path = root / 'manifest.json'
    if root.exists():
        if not args.resume or not manifest_path.is_file():
            raise FileExistsError('Use --resume only for a valid unfinished manifest')
        manifest = json.loads(manifest_path.read_text())
        if manifest['outputs_complete']:
            raise ValueError('Completed run preserved')
        verify_hashes(ROOT, manifest['input_hashes'])
        if manifest['study'] != study:
            raise ValueError('Study changed')
    else:
        root.mkdir(parents=True)
        manifest = dict(study=study, input_hashes=dependencies(), outputs_complete=False,
                        git_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                        git_status=subprocess.check_output(['git', 'status', '--short'], cwd=ROOT, text=True),
                        python=platform.python_version(), started_at=now(), command=sys.argv,
                        pid=os.getpid(), trust='CANDIDATE', workers=args.workers)
        dump(manifest_path, manifest)
    RUN_OWNED = True
    for track in study['tracks']:
        print('CONTEXT ' + track, flush=True)
        CONTEXTS[track] = tuning_a_deep.context(track)
    rows = batch(study['candidates'], root, args.workers, 'global')
    pd.DataFrame(rows).sort_values(['track', 'candidate_id']).to_csv(root / 'global.csv', index=False)
    parent = model.choose([r for r in rows if r['track'] == study['selection_track']])
    if parent is None:
        raise ValueError('NO_ELIGIBLE_GLOBAL_PARENT')
    parent_params = next(t['params'] for t in study['candidates'] if t['candidate_id'] == parent['candidate_id'])
    grid = local_grid(study, parent_params)
    grid.update(parent=parent['candidate_id'], global_sha256=sha(root / 'global.csv'))
    grid_path = root / 'local_grid.json'
    if grid_path.exists() and json.loads(grid_path.read_text()) != grid:
        raise ValueError('Adaptive grid changed')
    dump(grid_path, grid)
    rows += batch(grid['candidates'], root, args.workers, 'local')
    trials = study['candidates'] + grid['candidates']
    if len(rows) != 2 * len(trials) or len({canonical(t['params']) for t in trials}) != len(trials):
        raise ValueError('Incomplete or duplicated search')
    pd.DataFrame(rows).sort_values(['track', 'candidate_id']).to_csv(root / 'trials.csv', index=False)
    final_audits = finalize(root, study, rows, trials)
    verify_hashes(ROOT, manifest['input_hashes'])
    manifest.update(outputs_complete=True, completed_trials=len(rows), expected_trials=2 * len(trials),
                    completed_at=now(), trust='CANDIDATE_PENDING_RELEASE_VERIFICATION')
    dump(root / 'study_audit.json', dict(status='PASS', verified_trials=len(rows),
         auditor_sha256=sha(ROOT / 'scripts/audit_double_check.py'), final=final_audits,
         global_exhaustive=False, local_raw_combinations=grid['raw_combinations'],
         official_compliance='UNKNOWN_BLOCK_SUBMISSION'))
    dump(root / 'status.json', dict(status='COMPLETE_PENDING_RELEASE_VERIFICATION',
         completed=len(rows), expected=len(rows), completed_at=now()))
    dump(manifest_path, manifest)
    print('COMPLETE_PENDING_RELEASE_VERIFICATION', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='outputs/v2_double_check_fintuned')
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    try:
        main(args)
    except Exception as exc:
        if RUN_OWNED:
            dump(ROOT / args.output / 'failure.json', dict(error=str(exc), traceback=traceback.format_exc(), time=now()))
        raise
