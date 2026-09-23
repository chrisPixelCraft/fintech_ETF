"""Replay fixed monthly-objective finalists with independently audited resets."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import multiprocessing as mp
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
from scripts.run_double_check import TABLES, dump, sha, verify_hashes
from scripts.audit_double_check import audit_result
from src import double_check_tuning as model, tuning_a_deep
from src.monthly_study import monthly_returns, selection_row, select_candidate, episode_windows

DEFAULT_OUTPUT = ROOT / 'outputs/monthly_horizon_20260923'
TRACKS = ('official_ex_post', 'historical_pit')
STUDIES = tuple('outputs/v2_double_check_' + suffix for suffix in
                ('fintuned', 'expansion', 'refinement', 'structural'))
COMPACT_FILES = ('config.json', 'equity.csv', 'metrics.json', 'independent_audit.json')
CONTEXTS = {}


def read(path):
    return json.loads(Path(path).read_text())


def load_result(folder):
    return dict(config=read(folder / 'config.json'), metrics=read(folder / 'metrics.json'),
                **{name: pd.read_csv(folder / (name + '.csv'), keep_default_na=False,
                                    float_precision='round_trip') for name in TABLES})


def source_folder(study, candidate_id):
    original = ROOT / study / 'official_ex_post/trials' / candidate_id
    if (original / 'receipt.json').is_file():
        return original
    # Compact evidence is bound to the original independently audited receipt.
    # A clean checkout need not contain every discarded trial's full ledger.
    return DEFAULT_OUTPUT / 'screening' / candidate_id


def source_candidates():
    """Bind all 48 eligible source ledgers to the earlier independent releases."""
    rows = []
    for study, expected in zip(STUDIES, (4, 40, 4, 0)):
        directory = ROOT / study
        audit = read(directory / 'audit.json')
        trials = pd.read_csv(directory / 'trials.csv')
        selected = trials.loc[trials.track.eq('official_ex_post') & trials.eligible.eq(True)]
        if len(selected) != expected or audit['status'] != 'PASS':
            raise ValueError('Frozen candidate membership changed: ' + study)
        for cid in sorted(selected.candidate_id):
            folder = source_folder(study, cid)
            receipt = read(folder / 'receipt.json')
            key = 'official_ex_post/' + cid
            if sha(folder / 'receipt.json') != audit['trial_receipt_sha256'][key]:
                raise ValueError('Source receipt is not bound to prior independent audit')
            if folder == DEFAULT_OUTPUT / 'screening' / cid:
                verify_hashes(folder, {name: receipt[name] for name in COMPACT_FILES})
            else:
                verify_hashes(folder, receipt)
            independent = read(folder / 'independent_audit.json')
            if independent.get('independent_audit') != 'PASS' or not model.eligible(
                    dict(status='COMPLETE', **independent)):
                raise ValueError('Source candidate failed independent strict eligibility')
            config = read(folder / 'config.json')
            rows.append(dict(candidate_id=cid, source_study=study, source_receipt_key=key,
                             params=config['full_tuning_params']))
    if len(rows) != 48 or len({r['candidate_id'] for r in rows}) != 48:
        raise ValueError('Candidate membership incomplete or duplicated')
    if len({json.dumps(r['params'], sort_keys=True) for r in rows}) != 48:
        raise ValueError('Duplicated effective candidate parameters')
    return rows


def archive_sources(root, candidates):
    ranking = []
    for row in candidates:
        source = source_folder(row['source_study'], row['candidate_id'])
        dest = root / 'screening' / row['candidate_id']
        dest.mkdir(parents=True, exist_ok=False)
        for name in (*COMPACT_FILES, 'receipt.json'):
            shutil.copyfile(source / name, dest / name)
        equity = pd.read_csv(dest / 'equity.csv', float_precision='round_trip')
        ranking.append(selection_row(row['candidate_id'], equity))
    pd.DataFrame(ranking).to_csv(root / 'ranking.csv', index=False)
    return select_candidate(ranking)


def history_rows():
    from scripts.audit_x0352_history_coverage import build
    evidence = build()
    if read(ROOT / 'reports/x0352_2010_2024_coverage.json') != evidence:
        raise ValueError('Historical coverage evidence changed')
    rows = []
    for year in range(2010, 2025):
        for month in range(1, 13):
            reason = ('NO_DAILY_OR_INTRADAY_OR_PIT_UNIVERSE' if year < 2024 else
                      'NO_PIT_UNIVERSE_AND_NO_INTRADAY' if month < 10 else
                      'NO_MONTH_START_KNOWN_PIT_UNIVERSE')
            rows.append(dict(month=f'{year}-{month:02}', return_net=None,
                             status='UNVERIFIABLE', reason=reason))
    return rows


def dependency_hashes():
    from scripts.verify_double_check_structural import verify_release
    audit = verify_release(ROOT / STUDIES[-1])
    hashes = dict(audit['input_hashes'])
    paths = [
        'src/monthly_study.py', 'scripts/run_monthly_study.py',
        'docs/monthly_strategy_protocol.md', 'docs/monthly_strategy_research.md',
        'scripts/audit_x0352_history_coverage.py', 'reports/x0352_2010_2024_coverage.json',
        'outputs/v2_double_check_structural/audit.json',
        'outputs/v2_double_check_structural/selection.json',
    ]
    for study in STUDIES:
        paths.extend([study + '/audit.json', study + '/trials.csv'])
    hashes.update({name: sha(ROOT / name) for name in paths})
    return hashes


def run_episode(track, trial, window, root):
    folder = root / 'runs' / track / trial['candidate_id'] / window['episode']
    context = CONTEXTS[track]
    config = model.config_for(context, trial)
    config.update(start=window['start'], end=window['end'])
    if folder.exists():
        verify_hashes(folder, read(folder / 'receipt.json'))
        if read(folder / 'config.json') != config:
            raise ValueError('Resumed episode config differs')
        independent = audit_result(load_result(folder), context)
    else:
        result = model.run_model(context, config)
        independent = audit_result(result, context)
        folder.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=folder.parent, prefix='.monthly-') as temp:
            staged = Path(temp) / 'run'
            staged.mkdir()
            for name in TABLES:
                result[name].to_csv(staged / (name + '.csv'), index=False)
            dump(staged / 'config.json', result['config'])
            dump(staged / 'metrics.json', result['metrics'])
            dump(staged / 'independent_audit.json', independent)
            dump(staged / 'receipt.json', {p.name: sha(p) for p in staged.iterdir()})
            staged.rename(folder)
    if independent.get('independent_audit') != 'PASS':
        raise ValueError('Independent accounting failed')
    return dict(track=track, candidate_id=trial['candidate_id'], **window, **independent,
                research_eligible=model.eligible(dict(status='COMPLETE', **independent)))


def summarize(root, rows, finalist_ids):
    monthly, summaries = [], []
    for track in TRACKS:
        for cid in finalist_ids:
            full = next(r for r in rows if r['track'] == track and r['candidate_id'] == cid
                        and r['episode'] == 'full')
            equity = pd.read_csv(root / 'runs' / track / cid / 'full/equity.csv',
                                 float_precision='round_trip')
            if not full['complete_period']:
                raise ValueError('Selected full-period ledger unexpectedly incomplete')
            values = monthly_returns(equity)
            for row in values:
                monthly.append(dict(track=track, candidate_id=cid, **row,
                                    partial_period=row['month'] == '2026-09',
                                    research_eligible=full['research_eligible']))
            complete = [r['return_net'] for r in values if r['month'] != '2026-09']
            reset = [r for r in rows if r['track'] == track and r['candidate_id'] == cid
                     and r['kind'] == 'reset_25_sessions']
            valid = [r for r in reset if r['research_eligible']]
            nav_2025 = float(equity.loc[equity.date.str.startswith('2025'), 'economic_nav'].iloc[-1])
            summaries.append(dict(track=track, candidate_id=cid,
                full_research_eligible=full['research_eligible'],
                book_total_return=full['total_return'], economic_total_return=full['economic_total_return'],
                annual_2025=nav_2025 / 1e9 - 1,
                ytd_2026=float(equity.economic_nav.iloc[-1]) / nav_2025 - 1,
                complete_months=len(complete), median_month=float(np.median(complete)),
                worst_month=float(min(complete)), negative_months=sum(v < 0 for v in complete),
                reset_count=len(reset), reset_eligible=len(valid),
                eligible_reset_median=float(np.median([r['economic_total_return'] for r in valid])) if valid else None,
                eligible_reset_worst=min((r['economic_total_return'] for r in valid), default=None),
                eligible_reset_negative=sum(r['economic_total_return'] < 0 for r in valid),
                max_daily_volume_participation=full['max_daily_volume_participation']))
    paired = []
    if len(finalist_ids) == 2:
        selected = next(c for c in finalist_ids if c != 'x0352')
        for track in TRACKS:
            base = {r['episode']: r for r in rows if r['track'] == track
                    and r['candidate_id'] == 'x0352' and r['kind'] == 'reset_25_sessions'}
            challenger = {r['episode']: r for r in rows if r['track'] == track
                          and r['candidate_id'] == selected and r['kind'] == 'reset_25_sessions'}
            common = sorted(k for k in base if base[k]['research_eligible']
                            and challenger[k]['research_eligible'])
            differences = [challenger[k]['economic_total_return'] - base[k]['economic_total_return'] for k in common]
            paired.append(dict(track=track, candidate_id=selected, paired_eligible=len(common),
                selected_wins=sum(d > 1e-12 for d in differences),
                ties=sum(abs(d) <= 1e-12 for d in differences),
                mean_difference=float(np.mean(differences)) if differences else None,
                episodes=common))
    pd.DataFrame(monthly).to_csv(root / 'monthly.csv', index=False)
    dump(root / 'summary.json', dict(scope='EX_POST_DEVELOPMENT_NOT_UNSEEN_TEST',
        submission_status='BLOCK_SUBMISSION', candidates=summaries, paired_resets=paired))


def run(root, resume=False, workers=2):
    hashes = dependency_hashes()
    if root.exists():
        if not resume or not (root / 'manifest.json').is_file():
            raise FileExistsError('Preserve existing output; use --resume for unfinished study')
        manifest = read(root / 'manifest.json')
        if manifest['completed'] or manifest['input_sha256'] != hashes:
            raise ValueError('Completed study or changed dependencies')
        candidates = read(root / 'candidates.json')
        selected = read(root / 'selection.json')['candidate_id']
    else:
        candidates = source_candidates()
        root.mkdir(parents=True)
        selected = archive_sources(root, candidates)
        dump(root / 'candidates.json', candidates)
        calendar = pd.read_csv(ROOT / 'data/v2/market_daily.csv', usecols=['date']).date
        manifest = dict(completed=False, created_at=datetime.now(timezone.utc).isoformat(),
            code_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
            source_state='New study source pinned by input_sha256 before commit',
            input_sha256=hashes, pid=os.getpid(),
            study=dict(candidate_count=48, windows=episode_windows(calendar),
                selection_rule='2025_ECONOMIC_MONTH_MEDIAN_WORST_TURNOVER_ID',
                scope='EX_POST_ELIGIBILITY_AND_DEVELOPMENT_NOT_UNSEEN_TEST'))
        dump(root / 'selection.json', dict(baseline='x0352', candidate_id=selected,
            params=next(r['params'] for r in candidates if r['candidate_id'] == selected),
            scope=manifest['study']['scope'], submission_status='BLOCK_SUBMISSION'))
        pd.DataFrame(history_rows()).to_csv(root / 'history_monthly.csv', index=False)
        dump(root / 'manifest.json', manifest)
    finalist_ids = sorted({'x0352', selected})
    finalists = [dict(candidate_id=cid, params=next(r['params'] for r in candidates if r['candidate_id'] == cid))
                 for cid in finalist_ids]
    windows = manifest['study']['windows']
    tasks = [(track, trial, window) for track in TRACKS for trial in finalists for window in windows]
    manifest['expected_runs'] = len(tasks)
    dump(root / 'manifest.json', manifest)
    for track in TRACKS:
        CONTEXTS[track] = tuning_a_deep.context(track)
        CONTEXTS[track]['cache'].prewarm(finalists)
    rows = []
    with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context('fork')) as pool:
        jobs = {pool.submit(run_episode, track, trial, window, root): (track, trial['candidate_id'], window['episode'])
                for track, trial, window in tasks}
        for future in as_completed(jobs):
            identity = jobs[future]
            try:
                row = future.result()
            except Exception:
                dump(root / 'failure.json', dict(identity=identity, traceback=traceback.format_exc()))
                for other in jobs:
                    other.cancel()
                raise
            rows.append(row)
            dump(root / 'status.json', dict(status='RUNNING', pid=os.getpid(), completed=len(rows),
                 expected=len(tasks), last=list(identity), timestamp=datetime.now(timezone.utc).isoformat()))
            print(f'{len(rows)}/{len(tasks)} {identity} eligible={row["research_eligible"]}', flush=True)
    rows.sort(key=lambda r: (r['track'], r['candidate_id'], r['episode']))
    pd.DataFrame(rows).to_csv(root / 'episodes.csv', index=False)
    summarize(root, rows, finalist_ids)
    verify_hashes(ROOT, hashes)
    manifest.update(completed=True, completed_runs=len(rows), completed_at=datetime.now(timezone.utc).isoformat())
    dump(root / 'manifest.json', manifest)
    dump(root / 'status.json', dict(status='COMPLETE_PENDING_INDEPENDENT_RELEASE_AUDIT',
                                   completed=len(rows), expected=len(tasks), selected=selected))
    print(json.dumps(dict(selected=selected, runs=len(rows), status='COMPLETE_PENDING_INDEPENDENT_RELEASE_AUDIT')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 4:
        parser.error('Use 1–4 local workers')
    run(args.output.resolve(), args.resume, args.workers)


if __name__ == '__main__':
    main()
