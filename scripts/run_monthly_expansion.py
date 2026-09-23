"""Resumable, finite cold-start tuning; never rank a noncompliant candidate."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import multiprocessing as mp
import os
from pathlib import Path
import subprocess
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from scripts import run_monthly_study as replay
from scripts.run_double_check import dump, sha, verify_hashes
from src import tuning_a_deep
from src.monthly_study import episode_windows
from src.monthly_expansion import ANCHORS, AXES, TRAIN, SCREEN, candidates, ranking, strict

DESIGN = ROOT / 'config/monthly_expansion.json'
OUTPUT = ROOT / 'outputs/monthly_expansion_20260923'
PARENT = ROOT / 'outputs/monthly_horizon_20260923'


def read(path):
    return json.loads(Path(path).read_text())


def make_design():
    archived = {r['candidate_id']: r['params'] for r in read(PARENT / 'candidates.json')}
    windows = episode_windows(pd.read_csv(ROOT / 'data/v2/market_daily.csv', usecols=['date']).date)
    return dict(study_id='monthly_expansion_20260923', anchors={k: archived[k] for k in ANCHORS},
                axes=AXES, candidates=candidates(archived), windows=windows,
                training_episodes=list(TRAIN), screen_episode=SCREEN,
                selection_rule='ALL_11_STRICT_THEN_ECONOMIC_MEDIAN_WORST_MEAN_TURNOVER_ID',
                scope='DEVELOPMENT_ONLY_KNOWN_FAILURE_TARGETED_NOT_UNSEEN_TEST',
                max_runs=825, submission_status='BLOCK_SUBMISSION')


def dependencies():
    prior = read(PARENT / 'audit.json')
    if prior['status'] != 'PASS' or prior['verified_runs'] != 88:
        raise ValueError('Expected independently verified prior monthly comparison')
    verify_hashes(ROOT, prior['input_sha256'])
    verify_hashes(PARENT, prior['artifact_sha256'])
    paths = ['src/monthly_expansion.py', 'scripts/run_monthly_expansion.py',
             'config/monthly_expansion.json', 'docs/monthly_expansion_protocol.md',
             'docs/monthly_expansion_design.md', 'outputs/monthly_horizon_20260923/audit.json',
             'outputs/monthly_horizon_20260923/candidates.json',
             'outputs/monthly_horizon_20260923/episodes.csv',
             'outputs/monthly_horizon_20260923/summary.json']
    hashes = dict(prior['input_sha256'])
    hashes.update({name: sha(ROOT / name) for name in paths})
    return hashes


def batch(tasks, root, workers, phase):
    rows = []
    if not tasks:
        return rows
    with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context('fork')) as pool:
        jobs = {pool.submit(replay.run_episode, track, trial, window, root):
                (track, trial['candidate_id'], window['episode']) for track, trial, window in tasks}
        for future in as_completed(jobs):
            identity = jobs[future]
            try:
                row = future.result()
            except Exception:
                dump(root / 'failure.json', dict(phase=phase, identity=identity, traceback=traceback.format_exc()))
                for other in jobs:
                    other.cancel()
                raise
            rows.append(row)
            dump(root / 'status.json', dict(status='RUNNING', phase=phase, completed=len(rows),
                 expected=len(tasks), pid=os.getpid(), last=list(identity),
                 updated_at=datetime.now(timezone.utc).isoformat()))
            print(f'{phase} {len(rows)}/{len(tasks)} {identity} eligible={row["research_eligible"]}', flush=True)
    return rows


def run(root, resume=False, workers=2):
    design = read(DESIGN)
    if design != make_design():
        raise ValueError('Frozen design differs from exact bounded grid')
    hashes = dependencies()
    if root.exists():
        if not resume or not (root / 'manifest.json').is_file():
            raise FileExistsError('Preserve output; only resume a valid unfinished run')
        manifest = read(root / 'manifest.json')
        if manifest['completed'] or manifest['input_sha256'] != hashes or (root / 'failure.json').exists():
            raise ValueError('Completed, changed or failed study cannot silently resume')
    else:
        root.mkdir(parents=True)
        manifest = dict(completed=False, study_id=design['study_id'], input_sha256=hashes,
            design_sha256=sha(DESIGN), code_commit=subprocess.check_output(
                ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            source_state='Study source frozen by input_sha256 before commit',
            started_at=datetime.now(timezone.utc).isoformat(), candidate_count=72, pid=os.getpid())
        dump(root / 'manifest.json', manifest)
    trials = design['candidates']
    windows = {r['episode']: r for r in design['windows']}
    replay.CONTEXTS['official_ex_post'] = tuning_a_deep.context('official_ex_post')
    replay.CONTEXTS['official_ex_post']['cache'].prewarm(trials)
    records = batch([('official_ex_post', trial, windows[SCREEN]) for trial in trials],
                    root, workers, 'april_screen')
    survivors = {r['candidate_id'] for r in records if strict(r)}
    tasks = [('official_ex_post', trial, windows[episode]) for trial in trials
             if trial['candidate_id'] in survivors for episode in TRAIN if episode != SCREEN]
    records += batch(tasks, root, workers, 'remaining_training')
    ranks, selected = ranking(records, trials)
    pd.DataFrame(ranks).to_csv(root / 'ranking.csv', index=False)
    selection = dict(status='SELECTED_ON_TRAINING' if selected else 'NO_ELIGIBLE_WINNER',
        candidate_id=selected, params=next((r['params'] for r in trials if r['candidate_id'] == selected), None),
        april_passers=len(survivors), training_eligible_count=sum(r['training_eligible'] for r in ranks),
        selection_rule=design['selection_rule'], training_episodes=list(TRAIN),
        scope=design['scope'], submission_status='BLOCK_SUBMISSION')
    if selected:
        trial = next(r for r in trials if r['candidate_id'] == selected)
        replay.CONTEXTS['historical_pit'] = tuning_a_deep.context('historical_pit')
        replay.CONTEXTS['historical_pit']['cache'].prewarm([trial])
        tasks = [(track, trial, window) for track in replay.TRACKS for window in design['windows']
                 if track != 'official_ex_post' or window['episode'] not in TRAIN]
        records += batch(tasks, root, workers, 'fixed_winner_diagnostics')
        selected_records = [r for r in records if r['candidate_id'] == selected]
        selection['all_tested_windows_eligible'] = all(strict(r) for r in selected_records)
        selection['adoption'] = ('HOLD_OFFICIAL_EVIDENCE_AND_UNSEEN_DATA' if selection['all_tested_windows_eligible']
                                 else 'FAILED_DIAGNOSTIC_NO_ADOPTION')
    else:
        selection.update(all_tested_windows_eligible=False, adoption='NO_ADOPTION')
    records.sort(key=lambda r: (r['track'], r['candidate_id'], r['episode']))
    pd.DataFrame(records).to_csv(root / 'episodes.csv', index=False)
    dump(root / 'selection.json', selection)
    verify_hashes(ROOT, hashes)
    expected = 72 + 10 * len(survivors) + (33 if selected else 0)
    if len(records) != expected:
        raise ValueError('Incomplete adaptive evaluation coverage')
    manifest.update(completed=True, completed_runs=len(records), expected_runs=expected,
                    april_passers=len(survivors), selected_candidate=selected,
                    completed_at=datetime.now(timezone.utc).isoformat())
    dump(root / 'manifest.json', manifest)
    dump(root / 'status.json', dict(status='COMPLETE_PENDING_INDEPENDENT_AUDIT',
                                   runs=len(records), selection_status=selection['status']))
    print(json.dumps(dict(runs=len(records), **selection)), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    if args.prepare:
        if DESIGN.exists():
            raise FileExistsError('Preserve existing frozen grid')
        dump(DESIGN, make_design())
        print(DESIGN)
    else:
        if not 1 <= args.workers <= 4:
            parser.error('Use 1–4 local workers')
        run(args.output.resolve(), args.resume, args.workers)


if __name__ == '__main__':
    main()
