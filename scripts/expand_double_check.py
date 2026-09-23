"""Frozen, resumable six-axis expansion of the audited double-check study.

This is an additional development-data search. It cannot certify an official
policy or change the pinned v2_double_check_fintuned release.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import itertools
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from src import double_check_tuning as model, tuning_a_deep
from src.backtest import save_result
from scripts import run_double_check as base_runner
from scripts.audit_double_check import audit_result
from scripts.audit_official_v2 import primary_months
from scripts.prepare_official_deep import apply_axis, canonical

STUDY_PATH = ROOT / 'config/v2_double_check_expansion.json'
PARENT_ROOT = ROOT / 'outputs/v2_double_check_fintuned'
TRACKS = ('official_ex_post', 'historical_pit')
AXES = {
    'momentum_weight': [0.35, 0.45, 0.55, 0.65],
    'long_return_fraction': [0.1, 0.2, 0.3],
    'volume_low': [0.7, 0.8, 0.9],
    'max_replacements_per_day': [0, 1],
    'macd_tuple': [[8, 21, 5], [12, 26, 9]],
    'volume_high': [2.5, 3.0, 4.0, 5.0],
}
PARENT_FINAL = 'v2_double_check_fintuned'
FINAL_NAME = 'expansion_selected'
RUN_OWNED = False


def now():
    return datetime.now(timezone.utc).isoformat()


def _parent_audit():
    audit = json.loads((PARENT_ROOT / 'audit.json').read_text())
    if (audit.get('status') != 'PASS' or audit.get('selected_candidate') != 'd0034'
            or audit.get('submission_status') != 'BLOCK_SUBMISSION'
            or audit.get('verified_trials') != 1672):
        raise ValueError('Parent release is not the expected audited study')
    base_runner.verify_hashes(ROOT, audit['input_hashes'])
    base_runner.verify_hashes(PARENT_ROOT, audit['artifact_hashes'])
    for track in TRACKS:
        folder = PARENT_ROOT / track / 'final' / PARENT_FINAL
        base_runner.verify_hashes(folder, json.loads((folder / 'receipt.json').read_text()))
        metrics = json.loads((folder / 'metrics.json').read_text())
        if metrics.get('official_compliance') != 'UNKNOWN_BLOCK_SUBMISSION':
            raise ValueError('Parent final pretends formal compliance')
    return audit


def make_study():
    """Resolve the exact 576 grid positions against all 836 prior configs."""
    _parent_audit()
    previous = json.loads((ROOT / 'config/v2_double_check_study.json').read_text())
    previous_local = json.loads((PARENT_ROOT / 'local_grid.json').read_text())
    parent = json.loads((PARENT_ROOT / 'selection.json').read_text())
    if (parent['candidate_id'] != 'd0034'
            or previous_local['raw_combinations'] != 128):
        raise ValueError('Unexpected parent selection/grid')
    prior = {}
    for trial in previous['candidates'] + previous_local['candidates']:
        key = canonical(trial['params'])
        if key in prior:
            raise ValueError('Parent contains duplicate effective configuration')
        prior[key] = trial['candidate_id']
    if len(prior) != 836:
        raise ValueError('Parent candidate count changed')
    anchor = parent['params']
    grid, trials, added = [], [], {}
    for grid_index, combination in enumerate(itertools.product(*AXES.values())):
        params = copy.deepcopy(anchor)
        values = {}
        for axis, value in zip(AXES, combination):
            params = apply_axis(params, axis, value)
            values[axis] = value
        key = canonical(params)
        if key in prior:
            candidate_id, source = prior[key], 'parent'
        elif key in added:
            candidate_id, source = added[key], 'new_reused'
        else:
            candidate_id, source = f'x{len(trials):04d}', 'new'
            added[key] = candidate_id
            trials.append(dict(candidate_id=candidate_id, params=params,
                               phase='exhaustive_six_axis_grid', varied='joint6',
                               grid_index=grid_index))
        grid.append(dict(grid_index=grid_index, candidate_id=candidate_id,
                         source=source, reused_prior=source == 'parent', values=values))
    if (len(grid), len(added), len(trials), sum(r['reused_prior'] for r in grid)) != (576, 573, 573, 3):
        raise ValueError('Frozen expansion counts changed')
    if len({canonical(t['params']) for t in trials}) != 573:
        raise ValueError('New effective configurations are duplicated')
    return dict(study_id='v2_double_check_expansion_20260923',
                parent_study_id='v2_double_check_fintuned',
                parent_candidate_id='d0034',
                parent_audit_sha256=base_runner.sha(PARENT_ROOT / 'audit.json'),
                axes=AXES, grid=grid, candidates=trials,
                raw_grid_combinations=576, effective_grid_combinations=576,
                reused_parent_count=3, new_candidate_count=573,
                tracks=list(TRACKS), selection_track='official_ex_post',
                selection_rule='ZERO_ALL_DECLARED_GUARDS_THEN_BOOK_RETURN_MDD_TURNOVER_ID',
                candidate_scope='DEVELOPMENT_ONLY_NO_UNSEEN_HOLDOUT',
                official_compliance='UNKNOWN_BLOCK_SUBMISSION',
                stop='all 573 new candidates replayed in both tracks and independently audited; compare with audited parent d0034')


def prepare():
    if STUDY_PATH.exists():
        raise FileExistsError('Frozen expansion design exists: ' + str(STUDY_PATH))
    study = make_study()
    base_runner.dump(STUDY_PATH, study)
    print(json.dumps({k: study[k] for k in ('study_id', 'raw_grid_combinations',
        'reused_parent_count', 'new_candidate_count', 'tracks')}, indent=2))


def dependencies():
    parent_manifest = json.loads((PARENT_ROOT / 'manifest.json').read_text())
    hashes = dict(parent_manifest['input_hashes'])
    for relative in ('scripts/expand_double_check.py',
                     'config/v2_double_check_expansion.json',
                     'outputs/v2_double_check_fintuned/audit.json',
                     'outputs/v2_double_check_fintuned/selection.json',
                     'outputs/v2_double_check_fintuned/local_grid.json',
                     'outputs/v2_double_check_fintuned/manifest.json',
                     'outputs/v2_double_check_fintuned/study_audit.json'):
        hashes[relative] = base_runner.sha(ROOT / relative)
    return hashes


def _parent_row():
    metrics = json.loads((PARENT_ROOT / 'official_ex_post/final' / PARENT_FINAL / 'metrics.json').read_text())
    row = dict(metrics, candidate_id='d0034', status='COMPLETE',
               phase='parent_audited_release', track='official_ex_post')
    if not model.eligible(row):
        raise ValueError('Audited parent is not eligible under strict gate')
    return row


def _assert_same_tables(result, folder):
    for table in base_runner.TABLES:
        stored = pd.read_csv(folder / (table + '.csv'), keep_default_na=False,
                             float_precision='round_trip')
        pd.testing.assert_frame_equal(result[table].fillna('').reset_index(drop=True),
                                      stored, check_dtype=False, rtol=1e-12, atol=1e-5)


def _finalize(root, study, rows):
    parent_row = _parent_row()
    ranking = [r for r in rows if r['track'] == 'official_ex_post'] + [parent_row]
    selected = model.choose(ranking)
    if selected is None:
        raise ValueError('No strict eligible selection, including audited parent')
    selected_id = selected['candidate_id']
    selected_trial = (dict(candidate_id='d0034', params=json.loads(
        (PARENT_ROOT / 'selection.json').read_text())['params'],
        phase='parent_audited_release', varied='none') if selected_id == 'd0034'
        else next(t for t in study['candidates'] if t['candidate_id'] == selected_id))
    selection = dict(candidate_id=selected_id, params=selected_trial['params'],
                     source='prior' if selected_id == 'd0034' else 'new',
                     selected_on='official_ex_post', total_return=selected['total_return'],
                     max_drawdown=selected['max_drawdown'],
                     turnover_two_way=selected['turnover_two_way'],
                     incumbent_candidate_id='d0034',
                     incumbent_total_return=parent_row['total_return'],
                     official_compliance='UNKNOWN_BLOCK_SUBMISSION',
                     formal_submission='BLOCK_IF_UNKNOWN',
                     submission_status='BLOCK_SUBMISSION',
                     scope='EX_POST_DEVELOPMENT_ONLY')
    base_runner.dump(root / 'selection.json', selection)
    audits, comparison, monthly = {}, [], []
    for track in TRACKS:
        context = base_runner.CONTEXTS[track]
        result = model.run_model(context, model.config_for(context, selected_trial))
        audits[track] = audit_result(result, context)
        expected = (PARENT_ROOT / track / 'final' / PARENT_FINAL if selected_id == 'd0034'
                    else root / track / 'trials' / selected_id)
        _assert_same_tables(result, expected)
        folder = root / track / 'final' / FINAL_NAME
        if folder.exists():
            receipt = json.loads((folder / 'receipt.json').read_text())
            base_runner.verify_hashes(folder, receipt)
            _assert_same_tables(result, folder)
            if json.loads((folder / 'config.json').read_text()) != result['config']:
                raise ValueError('Resumed selected config differs')
        else:
            folder.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=folder.parent, prefix='.candidate-') as temp:
                staged = Path(temp) / 'release'
                save_result(result, staged)
                for table in ('plan_audit', 'compliance_daily', 'rejected_trades'):
                    result[table].to_csv(staged / (table + '.csv'), index=False)
                base_runner.dump(staged / 'independent_audit.json', audits[track])
                base_runner.dump(staged / 'receipt.json', {
                    p.name: base_runner.sha(p) for p in staged.iterdir() if p.is_file()})
                staged.rename(folder)
        comparison.append(dict(track=track, model='expansion_selected', **result['metrics']))
        prior_metrics = json.loads((PARENT_ROOT / track / 'final' / PARENT_FINAL / 'metrics.json').read_text())
        comparison.append(dict(track=track, model='parent_d0034', **prior_metrics))
        monthly += [dict(track=track, model='expansion_selected', **r)
                    for r in primary_months(result['equity'], result['config']['initial_cash'])]
    pd.DataFrame(comparison).to_csv(root / 'comparison.csv', index=False)
    pd.DataFrame(monthly).to_csv(root / 'monthly.csv', index=False)
    return selection, audits


def run(args):
    global RUN_OWNED
    study = json.loads(STUDY_PATH.read_text())
    expected = make_study()
    if study != expected:
        raise ValueError('Frozen study/config does not match deterministic design')
    root = ROOT / args.output
    manifest_path = root / 'manifest.json'
    if root.exists():
        if not args.resume or not manifest_path.is_file():
            raise FileExistsError('Use --resume only for a valid unfinished expansion')
        manifest = json.loads(manifest_path.read_text())
        if manifest['outputs_complete']:
            raise ValueError('Completed expansion is immutable')
        base_runner.verify_hashes(ROOT, manifest['input_hashes'])
        if manifest['study_sha256'] != base_runner.sha(STUDY_PATH) or manifest['study'] != study:
            raise ValueError('Frozen expansion study changed')
    else:
        root.mkdir(parents=True)
        manifest = dict(study=study, study_sha256=base_runner.sha(STUDY_PATH),
                        input_hashes=dependencies(), outputs_complete=False,
                        expected_trials=2 * len(study['candidates']),
                        git_head=subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                                         cwd=ROOT, text=True).strip(),
                        git_status=subprocess.check_output(['git', 'status', '--short'],
                                                           cwd=ROOT, text=True),
                        python=platform.python_version(), started_at=now(),
                        command=sys.argv, pid=os.getpid(), workers=args.workers,
                        trust='CANDIDATE', official_compliance='UNKNOWN_BLOCK_SUBMISSION')
        base_runner.dump(manifest_path, manifest)
    RUN_OWNED = True
    base_runner.CONTEXTS.clear()
    for track in TRACKS:
        print('CONTEXT ' + track, flush=True)
        base_runner.CONTEXTS[track] = tuning_a_deep.context(track)
    base_runner.dump(root / 'grid.json', dict(
        axes=study['axes'], grid=study['grid'], raw_grid_combinations=576,
        effective_grid_combinations=576, reused_parent_count=3,
        new_candidate_count=573, parent_candidate_id='d0034'))
    rows = base_runner.batch(study['candidates'], root, args.workers, 'expansion')
    if (len(rows) != 2 * len(study['candidates'])
            or len({canonical(t['params']) for t in study['candidates']}) != 573):
        raise ValueError('Expansion is incomplete or duplicated')
    pd.DataFrame(rows).sort_values(['track', 'candidate_id']).to_csv(root / 'trials.csv', index=False)
    selection, audits = _finalize(root, study, rows)
    base_runner.verify_hashes(ROOT, manifest['input_hashes'])
    base_runner.dump(root / 'study_audit.json', dict(
        status='PASS', verified_trials=len(rows),
        auditor_sha256=base_runner.sha(ROOT / 'scripts/audit_double_check.py'),
        final=audits, selected_candidate=selection['candidate_id'],
        grid_raw=576, new_effective=573, parent_reused=3,
        official_compliance='UNKNOWN_BLOCK_SUBMISSION',
        submission_status='BLOCK_SUBMISSION'))
    base_runner.dump(root / 'status.json', dict(
        status='COMPLETE_PENDING_RELEASE_VERIFICATION',
        completed=len(rows), expected=len(rows), completed_at=now(),
        submission_status='BLOCK_SUBMISSION'))
    manifest.update(outputs_complete=True, completed_trials=len(rows),
                    completed_at=now(), selected_candidate=selection['candidate_id'],
                    trust='CANDIDATE_PENDING_RELEASE_VERIFICATION')
    base_runner.dump(manifest_path, manifest)
    print('COMPLETE_PENDING_RELEASE_VERIFICATION ' + selection['candidate_id'], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true',
                        help='freeze the design without starting expensive runs')
    parser.add_argument('--output', default='outputs/v2_double_check_expansion')
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.prepare:
        prepare()
        return
    if args.workers < 1:
        raise ValueError('workers must be positive')
    try:
        run(args)
    except Exception as exc:
        if RUN_OWNED:
            base_runner.dump(ROOT / args.output / 'failure.json', dict(
                error=str(exc), traceback=traceback.format_exc(), time=now()))
        raise


if __name__ == '__main__':
    main()
