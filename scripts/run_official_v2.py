"""Rerun pinned v2 A plus a literal D-Plan guard; no tuning or live submission."""
from pathlib import Path
import argparse
import copy
import json
import os
import platform
import sys
import time
import traceback
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
import v2_A_best
from src import tuning_2nd
from src.official_v2_review import run_guard
from src.backtest import save_result
from scripts.run_tuning_2nd import dependencies
from scripts.run_v2_tuning import dump, sha, monthly_rows


def run_track(track, output):
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Refusing to overwrite prior evidence: ' + str(output))
    output.mkdir(parents=True, exist_ok=True)
    prior = ROOT / 'outputs/tuning_report_2nd_try' / track
    paths = dependencies(track) + ['src/official_v2_review.py', 'scripts/run_official_v2.py',
        'v2_A_best.py', 'src/v2_second_best.py', 'docs/official_v2_protocol.md']
    paths += [str(p.relative_to(ROOT)) for p in (ROOT / 'official_docs').iterdir() if p.is_file()]
    paths += [str((prior / name).relative_to(ROOT)) for name in ('manifest.json', 'audit.json', 'selection.json')]
    hashes = {p: sha(ROOT / p) for p in dict.fromkeys(paths)}
    manifest = dict(study_id='official_v2_reaudit_20260922', track=track,
        hashes=hashes, outputs_complete=False, expected_sessions=417,
        expected_versions=['A', 'A_dplan_guard', 'v1_matched', '0050'],
        candidate_id=v2_A_best.SCENARIO_SELECTIONS[track], parameter_search=False,
        period='2025-01-01/2026-09-21', formal_submission='BLOCK',
        started_at=datetime.now(timezone.utc).isoformat(), pid=os.getpid(),
        command=sys.argv, python=platform.python_version())
    dump(output / 'manifest.json', manifest)
    try:
        print(track + ': preparing context', flush=True)
        ctx = tuning_2nd.context(track)
        config = v2_A_best.build_config(track)
        results = {}
        for name in manifest['expected_versions']:
            start = time.monotonic()
            dump(output / 'status.json', dict(status='RUNNING', current=name, completed=list(results)))
            if name == 'A':
                result = v2_A_best.run_backtest(ctx, track)
            elif name == 'A_dplan_guard':
                result = run_guard(ctx, config)
            else:
                cfg = copy.deepcopy(ctx['base'])
                cfg.update(strategy_id=name, allocation_mode='full' if name == 'v1_matched' else 'local',
                    ex_post_fixed_universe=track == 'official_ex_post', universe_mode=track)
                result = tuning_2nd.run_model(ctx, name, cfg)
            save_result(result, output / 'final' / name)
            pd.DataFrame(monthly_rows(result['equity'])).to_csv(output / 'final' / name / 'monthly.csv', index=False)
            if name != 'A_dplan_guard':
                for table in ('equity', 'orders', 'trades', 'holdings'):
                    expected = pd.read_csv(prior / 'final' / name / (table + '.csv'), float_precision='round_trip').fillna('')
                    actual = result[table].fillna('')
                    pd.testing.assert_frame_equal(expected, actual, check_dtype=False, check_exact=True)
            results[name] = result
            print(f"{track} {name}: return={result['metrics']['total_return']:.6f} "
                f"NAV_MDD={result['metrics']['max_drawdown']:.6f} "
                f"hard={result['metrics']['measured_hard_breach_days']} elapsed={time.monotonic()-start:.1f}s", flush=True)
        if len({tuple(r['equity'].date) for r in results.values()}) != 1:
            raise ValueError('Calendar mismatch')
        pd.DataFrame([dict(model=n, **r['metrics']) for n, r in results.items()]).to_csv(output / 'comparison.csv', index=False)
        monthly = [dict(model=n, **row) for n, r in results.items() for row in monthly_rows(r['equity'])]
        pd.DataFrame(monthly).to_csv(output / 'monthly_comparison.csv', index=False)
        for p, digest in hashes.items():
            if sha(ROOT / p) != digest:
                raise ValueError('Frozen input changed during run: ' + p)
        manifest['outputs_complete'] = True
        manifest['completed_at'] = datetime.now(timezone.utc).isoformat()
        dump(output / 'manifest.json', manifest)
        files = [p for p in output.rglob('*') if p.is_file() and p.name not in ('receipt.json', 'status.json')]
        dump(output / 'receipt.json', {str(p.relative_to(output)): sha(p) for p in files})
        dump(output / 'status.json', dict(status='COMPLETE_PENDING_INDEPENDENT_AUDIT', completed=list(results)))
    except Exception as exc:
        dump(output / 'failure.json', dict(status='FAILED', error=str(exc), traceback=traceback.format_exc()))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track', choices=['both', *tuning_2nd.TRACKS], default='both')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/official_v2_reaudit')
    args = parser.parse_args()
    for track in tuning_2nd.TRACKS if args.track == 'both' else [args.track]:
        run_track(track, args.output.resolve() / track)
