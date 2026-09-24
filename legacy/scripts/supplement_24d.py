#!/usr/bin/env python3
"""Additive, post-freeze diagnostics; never selects or adopts a new strategy.

The original study, configuration and selection remain immutable. This module
adds missing recent windows, replacement=2, a 2025 walk-forward observation,
and continuous-book/ledger-derived diagnostics under a separate hash seal.
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_24d import (TABLES, aggregate, atomic_frame, atomic_json, clean, digest,
    file_hash, load_study, rank_candidates, run_groups, utc_now)
from scripts.audit_24d import _audit_ledger, verify_hashes
from src import strategy_24d
from src.yahoo_daily import load_calendar, load_daily, load_metadata

LABEL = 'POST_FREEZE_DIAGNOSTIC_NEVER_SELECTION_INPUT'


def registry(calendar, start='2010-01-01', end='2026-08-31', kind='monthly'):
    dates = sorted(set(str(pd.Timestamp(d).date()) for d in calendar))
    indices = {d: i for i, d in enumerate(dates)}
    eligible = [d for d in dates if start <= d <= end and indices[d] > 0 and indices[d]+24 <= len(dates)]
    if kind == 'monthly':
        first = {}
        for day in dates:
            first.setdefault(day[:7], day)
        eligible = [d for d in eligible if first[d[:7]] == d]
    elif kind != 'rolling':
        raise ValueError('Unsupported diagnostic episode kind')
    return pd.DataFrame([dict(episode_id=kind+'_'+day, kind=kind, start=day,
        end=dates[indices[day]+23], prior_session_date=dates[indices[day]-1], session_count=24,
        split='post_freeze_diagnostic', analysis_status=LABEL) for day in eligible])


def context(main, output):
    main, output = Path(main).resolve(), Path(output).resolve()
    if output == main or main in output.parents:
        raise ValueError('Supplement must have a separate output root')
    manifest = json.loads((main/'study_manifest.json').read_text())
    selection = json.loads((main/'final_selection.json').read_text())
    verify_hashes(ROOT, manifest['guard']['source_sha256'])
    cache = ROOT/'data/yahoo_daily'/Path(manifest['cache']).name
    verify_hashes(ROOT, {selection['candidate_config_path']: selection['candidate_config_sha256']})
    metadata = load_metadata(cache)
    calendar = load_calendar(cache)
    if digest(calendar) != manifest['guard']['calendar_sha256']:
        raise ValueError('Calendar differs from the original frozen study')
    if file_hash(cache/'metadata.json') != manifest['guard']['metadata_sha256']:
        raise ValueError('Raw snapshot metadata changed')
    daily = load_daily(cache)
    universe = pd.read_csv(ROOT/'data/reference/universe_competition_20260731.csv')
    universe['symbol'] = universe.yahoo_symbol
    universe['known_at'] = universe.attachment_created_at
    study = load_study()
    study = {**study, 'max_workers': 2}
    implementation = {str(p.relative_to(ROOT)): file_hash(p) for p in [
        Path(__file__).resolve(), ROOT/'tests/test_24d_supplement.py']}
    guard = dict(parent_guard_sha256=manifest['guard_sha256'],
        selection_sha256=file_hash(main/'final_selection.json'), implementation_sha256=implementation,
        calendar_sha256=digest(calendar), raw_artifact_sha256=metadata['artifact_sha256'],
        analysis_status=LABEL, adoption_allowed=False,
        requested_scope=dict(monthly_end='2026-08-31', rolling_end='2026-08-31',
            replacement=2, walk_forward_test_year=2025, continuous_book=True))
    output.mkdir(parents=True, exist_ok=True)
    seal = output/'supplement_manifest.json'
    if seal.exists():
        if json.loads(seal.read_text())['guard_sha256'] != digest(guard):
            raise ValueError('Supplement input/source seal changed; refusing stale resume')
    else:
        atomic_json(seal, dict(created_at=utc_now(), guard_sha256=digest(guard), guard=guard,
            parent_output=str(main), parent_pid=manifest['pid'], launch_command=sys.argv,
            pid=os.getpid(), ready_status='BLOCK_READY'))
    final_seal=output/'supplement_audit.json'
    if final_seal.exists():
        sealed=json.loads(final_seal.read_text())
        for name,checksum in sealed['output_sha256'].items():
            if file_hash(output/name)!=checksum:
                raise ValueError('Completed supplement artifact changed: '+name)
    return dict(main=main, output=output, daily=daily, universe=universe,
        session_dates=calendar, study=study, selection=selection,
        guard_sha256=digest(guard), parent_pid=manifest['pid'])


def spec_from_config(main, candidate):
    config = json.loads((Path(main)/'configs'/(candidate+'.json')).read_text())
    spec = dict(candidate_id=candidate, params=config['full_tuning_params'], source='frozen_reference')
    if config.get('research_reference'):
        spec['research_reference'] = config['research_reference']
    return spec


def wait_for(path, ctx):
    path = Path(path)
    while not path.exists():
        try:
            os.kill(ctx['parent_pid'], 0)
        except ProcessLookupError:
            raise RuntimeError('Parent stopped before producing required artifact: '+str(path)) from None
        atomic_json(ctx['output']/'status.json', dict(status='WAITING_FOR_PARENT',
            updated_at=utc_now(), waiting_for=str(path), pid=os.getpid()))
        time.sleep(30)
    return path


def run_long_horizon(daily, universe, config, dates, features=None):
    """Only interval-length guards differ; frozen rollback/disqualification stays."""
    source = inspect.getsource(strategy_24d.run_episode)
    changes = {
        'if len(dates) != 24 or not dates.is_unique or not dates.is_monotonic_increasing:':
            'if len(dates) < 1 or not dates.is_unique or not dates.is_monotonic_increasing:',
        'requested_sessions=24, observed_sessions=len(eq),':
            'requested_sessions=len(dates), observed_sessions=len(eq),',
    }
    for before, after in changes.items():
        if source.count(before) != 1:
            raise RuntimeError('Long-horizon length adapter seam changed')
        source = source.replace(before, after)
    namespace = dict(vars(strategy_24d))
    exec(compile(source, '<post-freeze-continuous-book-diagnostic>', 'exec'), namespace)
    return namespace['run_episode'](daily, universe, config, dates, features)


def cold_start_metrics(equity, compliance=None):
    out = {}
    for day in [1, 3, 5]:
        available = len(equity) >= day
        out[f'day_{day}_invested_fraction'] = float(1-equity.iloc[day-1].cash_ratio) if available else None
        out[f'day_{day}_holding_count'] = int(equity.iloc[day-1].holdings) if available else None
    if equity.empty:
        out.update(days_to_valid_portfolio=None, valid_portfolio_reached=False)
        return out
    valid = (equity.holdings.between(20, 30) & equity.cash_ratio.ge(0) & equity.cash_ratio.lt(.25)
        & equity.violations.fillna('').eq('') & equity.stale_count.eq(0) & equity.odd_residual_names.eq(0))
    if compliance is not None and len(compliance):
        flags = compliance.set_index('date').warning_today
        valid &= ~equity.date.map(flags).fillna(True).astype(bool)
    reached = np.flatnonzero(valid.to_numpy())
    out.update(days_to_valid_portfolio=int(reached[0]+1) if len(reached) else None,
               valid_portfolio_reached=bool(len(reached)))
    return out


def requested_split(start, end):
    for name, first, last in [('development', '2010-01-01','2018-12-31'),
        ('validation','2019-01-01','2022-12-31'),('holdout','2023-01-01','2024-12-31'),
        ('recent','2025-01-01','2026-09-30')]:
        if first <= start <= end <= last:
            return name
    return 'purged'


def enrich_group(folder):
    folder = Path(folder)
    receipt = json.loads((folder/'receipt.json').read_text())
    for filename, checksum in receipt['artifact_sha256'].items():
        if file_hash(folder/filename) != checksum:
            raise ValueError('Unverified group artifact: '+str(folder/filename))
    rows = pd.read_csv(folder/'metrics.csv', float_precision='round_trip')
    equity = pd.read_parquet(folder/'equity.parquet')
    compliance = pd.read_parquet(folder/'compliance_daily.parquet')
    positions = {key: frame for key, frame in equity.groupby('episode_id', sort=False)}
    checks = {key: frame for key, frame in compliance.groupby('episode_id', sort=False)}
    enriched = []
    for row in rows.to_dict('records'):
        empty = equity.iloc[:0]
        cold = cold_start_metrics(positions.get(row['episode_id'], empty), checks.get(row['episode_id']))
        enriched.append(dict(row, **cold, execution_model='DAILY_OPEN_RESEARCH_PROXY',
            starting_cash=1e9,
            terminal_NAV=row.get('final_economic_nav', row.get('final_nav')) if row.get('complete_period') else None,
            forensic_partial_NAV=row.get('final_economic_nav', row.get('final_nav')) if not row.get('complete_period') else None,
            requested_spec_split=requested_split(row['start'],row['end']),
            split_interpretation='DESCRIPTIVE_REPARTITION_ONLY_ORIGINAL_SELECTION_EXPOSURE_UNCHANGED',
            source_group=str(folder), analysis_status=LABEL))
    return pd.DataFrame(enriched)


def extended_summary(rows):
    base = aggregate(rows).set_index('candidate_id')
    for candidate, group in rows.groupby('candidate_id'):
        valid = group[group.measured_pass.fillna(False).astype(bool) & group.complete_period.fillna(False).astype(bool)]
        returns = valid.episode_return.dropna().astype(float).sort_values()
        n = len(returns)
        k = min(int(np.ceil(n*.05)), max(0,(n-1)//2))
        cold = group.days_to_valid_portfolio.dropna() if 'days_to_valid_portfolio' in group else pd.Series(dtype=float)
        extras = dict(best_return=float(returns.max()) if n else None,
            p90_MDD=float(valid.episode_max_drawdown.quantile(.9)) if n else None,
            median_trade_count=float(valid.trades.median()) if n and 'trades' in valid else None,
            valid_episode_count=n, mean_return_excluding_top_5pct=float(returns.iloc[:n-k].mean()) if n else None,
            median_return_without_tail_extremes=float(returns.iloc[k:n-k].median()) if n else None,
            trimmed_each_tail_count=k, median_days_to_valid_portfolio=float(cold.median()) if len(cold) else None,
            valid_portfolio_reached_count=len(cold), valid_portfolio_unreached_count=len(group)-len(cold),
            analysis_status=LABEL)
        for day in [1,3,5]:
            for suffix in ['invested_fraction','holding_count']:
                key=f'day_{day}_{suffix}'
                values=group[key].dropna() if key in group else pd.Series(dtype=float)
                extras[f'median_{key}']=float(values.median()) if len(values) else None
                extras[f'observed_{key}_count']=len(values)
        for key,value in extras.items():
            base.loc[candidate,key]=value
    return base.reset_index()


def enrich_all(ctx):
    frames=[]
    for root, label in [(ctx['main'],'original'),(ctx['output'],'supplement')]:
        for receipt in sorted((root/'ledgers').glob('*/*/receipt.json')):
            rows=enrich_group(receipt.parent)
            rows['source_study']=label
            frames.append(rows)
    rows=pd.concat(frames,ignore_index=True)
    atomic_frame(ctx['output']/'enriched_episodes.csv',rows)
    summaries=[]
    for (study,phase),group in rows.groupby(['source_study','phase'],sort=True):
        summary=extended_summary(group)
        summary['source_study']=study;summary['phase']=phase
        summaries.append(summary)
    atomic_frame(ctx['output']/'extended_summary.csv',pd.concat(summaries,ignore_index=True))
    # Main comparison is already fixed; these additions are evaluation only.
    selected=ctx['selection']['comparison_candidate_ids']
    main=rows[rows.source_study.eq('original')]
    monthly=main[main.phase.isin(['monthly_comparison','recent_stress']) & main.candidate_id.isin(selected)]
    monthly=monthly[monthly.start.le('2026-08-31')].copy()
    if monthly.duplicated(['candidate_id','start']).any():
        raise ValueError('Duplicate monthly primary denominator')
    atomic_frame(ctx['output']/'monthly_comparison_2010_2026.csv',monthly)
    for candidate,group in monthly.groupby('candidate_id'):
        if len(group)!=200:
            raise ValueError(f'Expected 200 complete monthly starts for {candidate}, got {len(group)}')
    rolling=rows[(rows.phase.eq('rolling') | rows.phase.str.startswith('rolling_')) & rows.candidate_id.isin(selected)]
    if rolling.duplicated(['candidate_id','start']).any():
        raise ValueError('Duplicate rolling starts')
    expected=len(registry(ctx['session_dates'],kind='rolling'))
    if any(len(group)!=expected for _,group in rolling.groupby('candidate_id')):
        raise ValueError('Incomplete extended rolling denominator')
    atomic_frame(ctx['output']/'rolling_comparison_2010_2026.csv',rolling)
    descriptive=[]
    for split,group in monthly.groupby('requested_spec_split'):
        summary=extended_summary(group);summary['requested_spec_split']=split
        summary['selection_provenance']='POST_FREEZE_DESCRIPTIVE_ONLY_2019_WAS_DEVELOPMENT'
        descriptive.append(summary)
    atomic_frame(ctx['output']/'requested_split_diagnostics.csv',pd.concat(descriptive,ignore_index=True))
    return rows


def execute(ctx, workers=2):
    out,main,calendar=ctx['output'],ctx['main'],ctx['session_dates']
    parent_registry=pd.read_csv(main/'episodes.csv')
    monthly=parent_registry[parent_registry.kind.eq('monthly')]
    replacement=dict(candidate_id='diagnostic_replacement_2',params={'max_replacements_per_day':2},source=LABEL)
    design=dict(created_at=utc_now(),analysis_status=LABEL,adoption_allowed=False,
        replacement_2_config=strategy_24d.build_config(replacement['params']),
        parent_selection_sha256=file_hash(main/'final_selection.json'),
        recent_rolling_start='2025-01-01',recent_rolling_end='2026-08-31',
        walk_forward_family='ORIGINAL_PREDECLARED_COARSE_ONLY',long_horizon='CONTINUOUS_SINGLE_BOOK_STOP_ON_DISQUALIFICATION')
    design_path=out/'diagnostic_design.json'
    if design_path.exists():
        recorded=json.loads(design_path.read_text())
        if {k:v for k,v in recorded.items() if k!='created_at'} != {k:v for k,v in design.items() if k!='created_at'}:
            raise ValueError('Immutable diagnostic design changed')
    else:
        atomic_json(design_path,design)
    for split in ['development','validation']:
        episodes=monthly[monthly.split.eq(split)].copy();episodes['analysis_status']=LABEL
        run_groups([replacement],episodes,'replacement_'+split,ctx,workers)
    comparison=[spec_from_config(main,candidate) for candidate in ctx['selection']['comparison_candidate_ids']]
    recent_rolling=registry(calendar,'2025-01-01','2026-08-31','rolling')
    atomic_frame(out/'rolling_episodes_2025_2026.csv',recent_rolling)
    run_groups(comparison,recent_rolling,'rolling',ctx,workers)
    # A continuous ledger is never approximated by compounding reset episodes.
    baseline=ctx['study']['baseline_id'];config=json.loads((main/'configs'/(baseline+'.json')).read_text())
    dates=[day for day in calendar if day>='2010-01-01']
    config['prior_session_date']=calendar[calendar.index(dates[0])-1]
    longpath=out/'long_horizon'
    if (longpath/'receipt.json').exists():
        receipt=json.loads((longpath/'receipt.json').read_text())
        if receipt['input_guard'] != ctx['guard_sha256']:
            raise ValueError('Long-horizon input guard changed')
        for name,checksum in receipt['artifact_sha256'].items():
            if file_hash(longpath/name) != checksum:
                raise ValueError('Long-horizon artifact changed: '+name)
    else:
        result=run_long_horizon(ctx['daily'],ctx['universe'],config,dates)
        if result['equity'].empty:
            raise RuntimeError('Long-horizon diagnostic failed before any ledger evidence: '+str(result['metrics']))
        audited=_audit_ledger(result,ctx)
        metrics=dict(result['metrics']);complete=metrics['complete_period']
        metrics.update(long_horizon_return=metrics['episode_return'] if complete else None,
            requested_start=dates[0],requested_end=dates[-1],analysis_status=LABEL,
            return_interpretation='FULL_INTERVAL' if complete else 'DISQUALIFIED_PARTIAL_NOT_LONG_HORIZON_RETURN')
        for name in TABLES:
            atomic_frame(longpath/(name+'.parquet'),result[name])
        atomic_json(longpath/'metrics.json',metrics);atomic_json(longpath/'audit.json',audited)
        atomic_json(longpath/'config.json',result['config'])
        atomic_json(longpath/'receipt.json',dict(completed_at=utc_now(),input_guard=ctx['guard_sha256'],
            artifact_sha256={p.name:file_hash(p) for p in longpath.iterdir() if p.is_file()}))
    wait_for(main/'walk_forward_family.csv',ctx)
    training=pd.read_csv(main/'walk_forward_family.csv',float_precision='round_trip')
    training=training[training.end.lt('2025-01-01')]
    ranks=rank_candidates(aggregate(training));eligible=ranks[ranks.compliance_pass_rate.eq(1.)]
    chosen=str(eligible.iloc[0].candidate_id) if len(eligible) else baseline
    test=registry(calendar,'2025-01-01','2025-12-31','monthly')
    test=test[test.end.le('2025-12-31')]
    decision=dict(candidate_id=chosen,training_start='2010-01-01',training_end='2024-12-31',
        test_year=2025,selection_rule='ORIGINAL_FROZEN_RANKING_100PCT_GATE',analysis_status=LABEL,
        decision='ELIGIBLE_WALK_FORWARD' if len(eligible) else 'NO_ELIGIBLE_BASELINE_DIAGNOSTIC',
        training_artifact_sha256=file_hash(main/'walk_forward_family.csv'))
    atomic_json(out/'walk_forward_2025_decision.json',decision)
    run_groups([spec_from_config(main,chosen)],test,'walk_forward_2025',ctx,workers)
    wait_for(main/'run_manifest.json',ctx)
    enriched=enrich_all(ctx)
    # Final provenance includes immutable parent artifacts and every supplement group.
    receipts={str(p.relative_to(out)):json.loads(p.read_text()) for p in sorted((out/'ledgers').glob('*/*/receipt.json'))}
    atomic_json(out/'frozen_config_metadata.json',dict(
        candidate_id=ctx['selection']['candidate_id'],selection_sha256=file_hash(main/'final_selection.json'),
        actual_data_cutoff=calendar[-1],source_calendar_sha256=digest(calendar),
        template_start_end_fields_are_inactive_for_episode_runs=True,analysis_status=LABEL))
    atomic_json(out/'supplement_audit.json',dict(completed_at=utc_now(),status='PASS_INTERNAL_ACCOUNTING',
        parent_run_manifest_sha256=file_hash(main/'run_manifest.json'),group_receipts=receipts,
        long_horizon_receipt=json.loads((out/'long_horizon/receipt.json').read_text()),
        enriched_rows=len(enriched),source_accuracy_verified=False,active_share_status='ACTIVE_SHARE_NOT_VERIFIED',
        ready_status='BLOCK_READY',analysis_status=LABEL,
        output_sha256={str(path.relative_to(out)):file_hash(path) for path in sorted(out.rglob('*'))
            if path.is_file() and path.name not in ['supplement_audit.json','status.json']
            and path.suffix != '.log' and '.tmp.' not in path.name}))
    atomic_json(out/'status.json',dict(status='COMPLETE',completed_at=utc_now(),pid=os.getpid()))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main',type=Path,default=ROOT/'outputs/24d')
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/24d_supplement')
    parser.add_argument('--workers',type=int,default=2)
    args=parser.parse_args()
    execute(context(args.main,args.output),max(1,min(args.workers,2)))


if __name__=='__main__':
    main()
