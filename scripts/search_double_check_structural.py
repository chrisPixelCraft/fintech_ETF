"""Frozen, resumable four-axis structural search of the audited double-check refinement.

This is a further development-data search. It cannot certify an official
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

STUDY_PATH = ROOT / 'config/v2_double_check_structural.json'
PARENT_ROOT = ROOT / 'outputs/v2_double_check_refinement'
TRACKS = ('official_ex_post', 'historical_pit')
AXES = {
    'target_count': [20, 25, 30],
    'cash_guard_ratio': [0.08, 0.12, 0.16],
    'return_pair': [[5, 20], [10, 30], [15, 40]],
    'ema_pair': [[5, 20], [10, 30], [15, 40]],
}
PARENT_FINAL = 'refinement_selected'
FINAL_NAME = 'structural_selected'
RUN_OWNED = False


def now():
    return datetime.now(timezone.utc).isoformat()


def _parent_audit():
    from scripts.verify_double_check_refinement import verify_release
    audit = verify_release(PARENT_ROOT)
    if (audit.get('status') != 'PASS' or audit.get('selected_candidate') != 'x0352'
            or audit.get('submission_status') != 'BLOCK_SUBMISSION'
            or audit.get('verified_trials') != 160):
        raise ValueError('Parent release is not the expected audited refinement')
    for track in TRACKS:
        folder = PARENT_ROOT / track / 'final' / PARENT_FINAL
        base_runner.verify_hashes(folder, json.loads((folder / 'receipt.json').read_text()))
        metrics = json.loads((folder / 'metrics.json').read_text())
        if metrics.get('official_compliance') != 'UNKNOWN_BLOCK_SUBMISSION':
            raise ValueError('Parent final pretends formal compliance')
    return audit


def make_study():
    """Resolve the exact 81 positions against all 1,489 prior configurations."""
    _parent_audit()
    first_root = ROOT / 'outputs/v2_double_check_fintuned'
    first_manifest = json.loads((first_root / 'manifest.json').read_text())
    first_local = json.loads((first_root / 'local_grid.json').read_text())
    expansion = json.loads((ROOT / 'config/v2_double_check_expansion.json').read_text())
    refinement = json.loads((ROOT / 'config/v2_double_check_refinement.json').read_text())
    parent = json.loads((PARENT_ROOT / 'selection.json').read_text())
    if (parent['candidate_id'] != 'x0352'
            or first_local['raw_combinations'] != 128
            or len(expansion['candidates']) != 573
            or len(refinement['candidates']) != 80):
        raise ValueError('Unexpected parent selection/grid')
    prior = {}
    for trial in (first_manifest['study']['candidates'] + first_local['candidates']
                  + expansion['candidates'] + refinement['candidates']):
        key = canonical(trial['params'])
        if key in prior:
            raise ValueError('Prior releases contain duplicate effective configuration')
        prior[key] = trial['candidate_id']
    if len(prior) != 1489:
        raise ValueError('Prior candidate count changed')
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
            candidate_id, source = f'z{len(trials):04d}', 'new'
            added[key] = candidate_id
            trials.append(dict(candidate_id=candidate_id, params=params,
                               phase='exhaustive_four_axis_grid', varied='joint4',
                               grid_index=grid_index))
        grid.append(dict(grid_index=grid_index, candidate_id=candidate_id,
                         source=source, reused_prior=source == 'parent', values=values))
    if (len(grid), len(added), len(trials), sum(r['reused_prior'] for r in grid)) != (81, 80, 80, 1):
        raise ValueError('Frozen structural search counts changed')
    if len({canonical(t['params']) for t in trials}) != 80:
        raise ValueError('New effective configurations are duplicated')
    if next(r for r in grid if r['reused_prior'])['candidate_id'] != 'x0352':
        raise ValueError('The sole reused configuration is not the parent winner')
    return dict(study_id='v2_double_check_structural_20260923',
                parent_study_id='v2_double_check_refinement_20260923',
                parent_candidate_id='x0352',
                parent_audit_sha256=base_runner.sha(PARENT_ROOT / 'audit.json'),
                axes=AXES, grid=grid, candidates=trials,
                raw_grid_combinations=81, effective_grid_combinations=81,
                reused_parent_count=1, new_candidate_count=80,
                tracks=list(TRACKS), selection_track='official_ex_post',
                selection_rule='ZERO_ALL_DECLARED_GUARDS_THEN_BOOK_RETURN_MDD_TURNOVER_ID',
                candidate_scope='DEVELOPMENT_ONLY_NO_UNSEEN_HOLDOUT',
                official_compliance='UNKNOWN_BLOCK_SUBMISSION',
                stop='all 80 new candidates replayed in both tracks and independently audited; compare with audited parent x0352')


def prepare():
    if STUDY_PATH.exists():
        raise FileExistsError('Frozen structural design exists: ' + str(STUDY_PATH))
    study = make_study()
    base_runner.dump(STUDY_PATH, study)
    print(json.dumps({k: study[k] for k in ('study_id', 'raw_grid_combinations',
        'reused_parent_count', 'new_candidate_count', 'tracks')}, indent=2))


def dependencies():
    parent_audit = _parent_audit()
    hashes = dict(parent_audit['input_hashes'])
    for relative in ('scripts/search_double_check_structural.py',
                     'config/v2_double_check_structural.json',
                     'docs/v2_double_check_structural_protocol.md',
                     'config/v2_double_check_study.json',
                     'config/v2_double_check_expansion.json',
                     'config/v2_double_check_refinement.json',
                     'outputs/v2_double_check_fintuned/manifest.json',
                     'outputs/v2_double_check_fintuned/local_grid.json',
                     'outputs/v2_double_check_refinement/audit.json',
                     'outputs/v2_double_check_refinement/selection.json',
                     'outputs/v2_double_check_refinement/grid.json',
                     'outputs/v2_double_check_refinement/manifest.json',
                     'outputs/v2_double_check_refinement/study_audit.json'):
        hashes[relative] = base_runner.sha(ROOT / relative)
    return hashes


def _parent_row():
    metrics = json.loads((PARENT_ROOT / 'official_ex_post/final' / PARENT_FINAL / 'metrics.json').read_text())
    row = dict(metrics, candidate_id='x0352', status='COMPLETE',
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
    selected_trial = (dict(candidate_id='x0352', params=json.loads(
        (PARENT_ROOT / 'selection.json').read_text())['params'],
        phase='parent_audited_release', varied='none') if selected_id == 'x0352'
        else next(t for t in study['candidates'] if t['candidate_id'] == selected_id))
    selection = dict(candidate_id=selected_id, params=selected_trial['params'],
                     source='prior' if selected_id == 'x0352' else 'new',
                     selected_on='official_ex_post', total_return=selected['total_return'],
                     max_drawdown=selected['max_drawdown'],
                     turnover_two_way=selected['turnover_two_way'],
                     incumbent_candidate_id='x0352',
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
        expected = (PARENT_ROOT / track / 'final' / PARENT_FINAL if selected_id == 'x0352'
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
        comparison.append(dict(track=track, model='structural_selected', **result['metrics']))
        prior_metrics = json.loads((PARENT_ROOT / track / 'final' / PARENT_FINAL / 'metrics.json').read_text())
        comparison.append(dict(track=track, model='parent_x0352', **prior_metrics))
        monthly += [dict(track=track, model='structural_selected', **r)
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
            raise FileExistsError('Use --resume only for a valid unfinished structural search')
        manifest = json.loads(manifest_path.read_text())
        if manifest['outputs_complete']:
            raise ValueError('Completed structural search is immutable')
        base_runner.verify_hashes(ROOT, manifest['input_hashes'])
        if manifest['study_sha256'] != base_runner.sha(STUDY_PATH) or manifest['study'] != study:
            raise ValueError('Frozen structural study changed')
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
        axes=study['axes'], grid=study['grid'], raw_grid_combinations=81,
        effective_grid_combinations=81, reused_parent_count=1,
        new_candidate_count=80, parent_candidate_id='x0352'))
    rows = base_runner.batch(study['candidates'], root, args.workers, 'structural')
    if (len(rows) != 2 * len(study['candidates'])
            or len({canonical(t['params']) for t in study['candidates']}) != 80):
        raise ValueError('Structural search is incomplete or duplicated')
    pd.DataFrame(rows).sort_values(['track', 'candidate_id']).to_csv(root / 'trials.csv', index=False)
    selection, audits = _finalize(root, study, rows)
    base_runner.verify_hashes(ROOT, manifest['input_hashes'])
    base_runner.dump(root / 'study_audit.json', dict(
        status='PASS', verified_trials=len(rows),
        auditor_sha256=base_runner.sha(ROOT / 'scripts/audit_double_check.py'),
        final=audits, selected_candidate=selection['candidate_id'],
        grid_raw=81, new_effective=80, parent_reused=1,
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
    parser.add_argument('--output', default='outputs/v2_double_check_structural')
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
