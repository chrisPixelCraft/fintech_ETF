"""Run fixed A/B/C/D weak-market interventions; preserve every variant."""
from pathlib import Path
import argparse
import json
import os
import platform
import shutil
import sys
import time
import traceback
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from src import v3_study as v3
from src.backtest import save_result
from scripts.run_tuning_2nd import dependencies
from scripts.run_v2_tuning import dump, sha, monthly_rows


def diagnostics(results):
    rows = []
    reference = results['A']['holdings']
    base = {str(d): group.set_index('symbol').shares.to_dict()
            for d, group in reference.groupby('date')}
    for model in v3.VERSIONS:
        result = results[model]
        sig = result['signals']
        holdings = result['holdings']
        changed_days = sum(group.set_index('symbol').shares.to_dict() != base[str(d)]
                           for d, group in holdings.groupby('date'))
        rows.append(dict(model=model, signal_days=sig.date.nunique(),
            score_changed_days=sig.loc[sig.audit_score_changed, 'date'].nunique(),
            score_changed_rows=int(sig.audit_score_changed.sum()),
            holdings_differ_from_A_days=changed_days,
            trade_days=result['equity'].traded_notional.gt(0).sum(),
            trade_count=len(result['trades'])))
    return pd.DataFrame(rows)


def run_track(track, output, study):
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Preserve prior results; choose an empty output: ' + str(output))
    output.mkdir(parents=True, exist_ok=True)
    paths = dependencies(track) + ['src/v3_study.py', 'src/v3_signals.py',
        'scripts/run_v3_study.py', 'config/v3_weak_market.json', 'v2_A_best.py',
        'src/v2_second_best.py', 'docs/AI_CUP_Trading_Agent_v3.md', 'docs/v3_protocol.md']
    prior = ROOT / 'outputs/tuning_report_2nd_try' / track
    paths += [str((prior / name).relative_to(ROOT)) for name in ('audit.json', 'manifest.json', 'selection.json')]
    # Include exact immutable control artifacts, not only a summary of them.
    for model in ('A', 'v1_matched', '0050'):
        paths += [str(p.relative_to(ROOT)) for p in (prior / 'final' / model).iterdir() if p.is_file()]
    hashes = {name: sha(ROOT / name) for name in dict.fromkeys(paths)}
    manifest = dict(study_id=study['study_id'], track=track, study=study, hashes=hashes,
        started_at=datetime.now(timezone.utc).isoformat(), pid=os.getpid(),
        command=sys.argv, python=platform.python_version(), expected_versions=list(v3.VERSIONS),
        expected_sessions=417, outputs_complete=False,
        formal_submission=study['formal_submission'], selection_scope='FIXED_DEVELOPMENT_COMPARISON')
    dump(output / 'manifest.json', manifest)
    for name in paths:
        dst = output / 'input_snapshot' / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dst)
    try:
        print(track + ': preparing frozen context', flush=True)
        ctx = v3.context(track, study)
        ctx['v3_signals'].features.to_csv(output / 'features.csv', index=False)
        ctx['v3_signals'].market_state.to_csv(output / 'market_state.csv', index=False)
        v3.instrument_series(ctx['daily']).rename_axis('date').to_csv(output / 'instrument_0050.csv')
        results = {}
        for version in v3.VERSIONS:
            start = time.monotonic()
            dump(output / 'status.json', dict(status='RUNNING', current=version, completed=list(results)))
            result = v3.run_model(ctx, version, study)
            if version == 'A':
                v3.assert_baseline_replay(result, track)
                dump(output / 'baseline_replay.json', dict(status='PASS', exact=True,
                    candidate=v3.BASELINES[track], tables=['equity', 'orders', 'trades', 'holdings']))
            save_result(result, output / 'final' / version)
            pd.DataFrame(monthly_rows(result['equity'])).to_csv(output / 'final' / version / 'monthly.csv', index=False)
            results[version] = result
            print(f"{track} {version}: COMPLETE return={result['metrics']['economic_total_return']:.6f} "
                  f"hard={result['metrics']['measured_hard_breach_days']} elapsed={time.monotonic()-start:.1f}s", flush=True)
        for name in ('v1_matched', '0050'):
            src, dest = prior / 'final' / name, output / 'final' / name
            shutil.copytree(src, dest)
            results[name] = dict(equity=pd.read_csv(dest / 'equity.csv', float_precision='round_trip'),
                                 metrics=json.loads((dest / 'metrics.json').read_text()))
        if len({tuple(r['equity'].date) for r in results.values()}) != 1:
            raise ValueError('Comparison calendars differ')
        pd.DataFrame([dict(model=k, **r['metrics']) for k, r in results.items()]).to_csv(output / 'comparison.csv', index=False)
        monthly, weak = v3.evaluation_tables(results, ctx['daily'])
        monthly.to_csv(output / 'monthly_comparison.csv', index=False)
        weak.to_csv(output / 'weak_month_summary.csv', index=False)
        monthly[monthly.model.eq('A')][['month', 'instrument_return', 'weak_month', 'partial_month']].to_csv(output / 'evaluation_only_month_labels.csv', index=False)
        diagnostics(results).to_csv(output / 'intervention_diagnostics.csv', index=False)
        curves = {name: pd.Series([1e9, *r['equity'].economic_nav], index=['2024-12-31', *r['equity'].date])
                  for name, r in results.items()}
        pd.DataFrame(curves).rename_axis('date').to_csv(output / 'nav_comparison.csv')
        for name, digest in hashes.items():
            if sha(ROOT / name) != digest:
                raise ValueError('Source changed during execution: ' + name)
        manifest['outputs_complete'] = True
        manifest['completed_at'] = datetime.now(timezone.utc).isoformat()
        dump(output / 'manifest.json', manifest)
        files = [p for p in output.rglob('*') if p.is_file() and 'input_snapshot' not in p.parts
                 and p.name not in ('receipt.json', 'status.json')]
        dump(output / 'receipt.json', {str(p.relative_to(output)): sha(p) for p in files})
        dump(output / 'status.json', dict(status='COMPLETE_PENDING_INDEPENDENT_AUDIT', completed=list(results)))
    except Exception as exc:
        dump(output / 'failure.json', dict(status='FAILED', error=str(exc), traceback=traceback.format_exc()))
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track', choices=['both', *v3.second.TRACKS], default='both')
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/v3_weak_market')
    args = parser.parse_args(argv)
    study = v3.load_study()
    for track in study['tracks'] if args.track == 'both' else [args.track]:
        run_track(track, args.output.resolve() / track, study)


if __name__ == '__main__':
    main()
