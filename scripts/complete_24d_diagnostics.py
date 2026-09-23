#!/usr/bin/env python3
"""Complete all fixed-set diagnostics after selection, without changing adoption.

Reuse sealed evidence, evaluate missing monthly cells, and run one continuous
cash-reset book per fixed set. No result here may enter candidate selection.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import json
import multiprocessing
import os
from pathlib import Path
import sys
import time

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))

from scripts.run_24d import (TABLES, atomic_frame, atomic_json, digest, file_hash, run_groups, utc_now)
from scripts.supplement_24d import (LABEL, context as supplement_context, enrich_group,
    extended_summary, registry, run_long_horizon)
from scripts.audit_24d import _audit_ledger

_CTX=None


def sealed_artifacts(folder,seal_name):
    folder=Path(folder);seal=json.loads((folder/seal_name).read_text())
    for name,checksum in seal['output_sha256'].items():
        if file_hash(folder/name)!=checksum:
            raise ValueError('Sealed artifact differs: '+str(folder/name))
    return seal


def wait_dependencies(main,supplement,output):
    waiting=[main/'run_manifest.json',supplement/'supplement_audit.json']
    while any(not path.exists() for path in waiting):
        for folder,manifest in [(main,'study_manifest.json'),(supplement,'supplement_manifest.json')]:
            record=json.loads((folder/manifest).read_text())
            pid=record.get('pid')
            if pid and not (folder/('run_manifest.json' if folder==main else 'supplement_audit.json')).exists():
                try:
                    os.kill(pid,0)
                except ProcessLookupError:
                    raise RuntimeError('Dependency exited before final seal: '+str(folder)) from None
        atomic_json(output/'status.json',dict(status='WAITING_FOR_SEALED_INPUTS',
            updated_at=utc_now(),pid=os.getpid(),missing=[str(p) for p in waiting if not p.exists()]))
        time.sleep(30)


def canonical_rows(rows):
    rows=rows.copy()
    if 'source_episode_id' not in rows:
        rows['source_episode_id']=rows.episode_id
    if 'source_phase' not in rows:
        rows['source_phase']=rows.phase
    rows['episode_id']='monthly_'+rows.start.astype(str)
    rows['kind']='monthly'
    rows['analysis_status']=LABEL
    rows['execution_model']='DAILY_OPEN_RESEARCH_PROXY'
    return rows


def missing_groups(episodes,rows,specs):
    """Group identical missing date sets; never double-count reused observations."""
    if rows.duplicated(['candidate_id','episode_id']).any():
        raise ValueError('Duplicate reused candidate/monthly cell')
    expected=set(episodes.episode_id)
    groups={}
    for spec in specs:
        subset=rows[rows.candidate_id.eq(spec['candidate_id'])]
        if not set(subset.episode_id)<=expected:
            raise ValueError('Reused episode is outside the fixed monthly registry')
        absent=tuple(episodes.loc[~episodes.episode_id.isin(subset.episode_id),'episode_id'])
        if absent:
            groups.setdefault(absent,[]).append(spec)
    return [(members,episodes[episodes.episode_id.isin(ids)].copy()) for ids,members in groups.items()]


def _initialize(ctx):
    global _CTX
    _CTX=ctx


def continuous_job(spec,config):
    ctx=_CTX;candidate=spec['candidate_id'];folder=ctx['output']/'continuous'/candidate
    expected=digest(dict(input_guard=ctx['guard_sha256'],candidate_id=candidate,config=config))
    receipt=folder/'receipt.json'
    if receipt.exists():
        record=json.loads(receipt.read_text())
        if record['input_guard']!=expected:
            raise ValueError('Continuous diagnostic guard changed')
        for name,checksum in record['artifact_sha256'].items():
            if file_hash(folder/name)!=checksum:
                raise ValueError('Continuous artifact changed: '+str(folder/name))
        return json.loads((folder/'row.json').read_text())
    dates=[d for d in ctx['session_dates'] if d>='2010-01-01']
    config={**config,'prior_session_date':ctx['session_dates'][ctx['session_dates'].index(dates[0])-1]}
    atomic_json(folder/'status.json',dict(status='RUNNING',updated_at=utc_now(),pid=os.getpid()))
    result=run_long_horizon(ctx['daily'],ctx['universe'],config,dates)
    if result['equity'].empty:
        raise RuntimeError('Continuous diagnostic has no accounting evidence: '+str(result['metrics']))
    audit=_audit_ledger(result,ctx)
    for name in TABLES:
        atomic_frame(folder/(name+'.parquet'),result[name])
    atomic_json(folder/'config.json',result['config'])
    atomic_json(folder/'metrics.json',result['metrics'])
    # Reconstruct the serialized book independently, not just its in-memory producer.
    reread={name:pd.read_parquet(folder/(name+'.parquet')) for name in TABLES}
    reread.update(config=json.loads((folder/'config.json').read_text()),
                  metrics=json.loads((folder/'metrics.json').read_text()))
    _audit_ledger(reread,ctx)
    atomic_json(folder/'audit.json',audit)
    m=result['metrics'];complete=bool(m['complete_period'])
    row=dict(candidate_id=candidate,source=spec['source'],requested_start=dates[0],requested_end=dates[-1],
        requested_sessions=len(dates),observed_sessions=m['observed_sessions'],
        observed_start=m['start'],observed_end=m['end'],complete_period=complete,
        disqualified=bool(m['disqualified']),measured_pass=bool(m['measured_pass']),
        long_horizon_return=m['episode_return'] if complete else None,
        forensic_partial_return=m['forensic_partial_return'] if not complete else None,
        final_economic_nav=float(result['equity'].economic_nav.iloc[-1]),
        simulated_warning_days=m['simulated_warning_days'],analysis_status=LABEL,
        execution_model='DAILY_OPEN_RESEARCH_PROXY',source_group=str(folder),
        return_interpretation='FULL_INTERVAL' if complete else 'DISQUALIFIED_PARTIAL_NOT_LONG_HORIZON_RETURN')
    atomic_json(folder/'row.json',row)
    files=[name+'.parquet' for name in TABLES]+['config.json','metrics.json','audit.json','row.json']
    atomic_json(receipt,dict(completed_at=utc_now(),input_guard=expected,
        artifact_sha256={name:file_hash(folder/name) for name in files}))
    atomic_json(folder/'status.json',dict(status='COMPLETE',completed_at=utc_now(),pid=os.getpid()))
    return row


def prepare(main,supplement,output):
    main,supplement,output=Path(main).resolve(),Path(supplement).resolve(),Path(output).resolve()
    if output in [main,supplement] or main in output.parents or supplement in output.parents:
        raise ValueError('Family diagnostics require a separate output directory')
    output.mkdir(parents=True,exist_ok=True)
    implementation={str(p.relative_to(ROOT)):file_hash(p) for p in [
        Path(__file__).resolve(),ROOT/'tests/test_complete_24d_diagnostics.py']}
    intent=dict(implementation_sha256=implementation,main=str(main),supplement=str(supplement),
        analysis_status=LABEL,adoption_allowed=False)
    path=output/'launch_manifest.json'
    if path.exists():
        previous=json.loads(path.read_text())
        if previous['intent']!=intent:
            raise ValueError('Family diagnostic launch source changed')
    else:
        atomic_json(path,dict(started_at=utc_now(),pid=os.getpid(),command=sys.argv,intent=intent))
    wait_dependencies(main,supplement,output)
    # The source loaded when the process started must still match its launch seal.
    for relative,checksum in implementation.items():
        if file_hash(ROOT/relative)!=checksum:
            raise ValueError('Family diagnostic source changed while waiting')
    sealed_artifacts(main,'run_manifest.json')
    sealed_artifacts(supplement,'supplement_audit.json')
    ctx=supplement_context(main,supplement)
    configs={}
    registered=pd.read_csv(main/'candidates.csv')
    for candidate in registered.candidate_id:
        configs[candidate]=json.loads((main/'configs'/(candidate+'.json')).read_text())
    for folder,candidate in [(main,'simple_momentum_reference'),(supplement,'diagnostic_replacement_2')]:
        configs[candidate]=json.loads((folder/'configs'/(candidate+'.json')).read_text())
    specs=[]
    for candidate,config in configs.items():
        spec=dict(candidate_id=candidate,params=config['full_tuning_params'],source='PRE_FREEZE_SEARCHED_PARAMETER_SET')
        if config.get('research_reference'):
            spec.update(source='SANITY_REFERENCE',research_reference=config['research_reference'])
        elif candidate=='diagnostic_replacement_2':
            spec['source']='POST_FREEZE_REPLACEMENT_DIAGNOSTIC'
        specs.append(spec)
    guard=dict(implementation_sha256=implementation,main_run_manifest_sha256=file_hash(main/'run_manifest.json'),
        supplement_audit_sha256=file_hash(supplement/'supplement_audit.json'),
        frozen_selection_sha256=file_hash(main/'final_selection.json'),configs=configs,analysis_status=LABEL)
    manifest=output/'diagnostics_manifest.json'
    if manifest.exists() and json.loads(manifest.read_text())['guard_sha256']!=digest(guard):
        raise ValueError('Family diagnostic inputs changed')
    if not manifest.exists():
        atomic_json(manifest,dict(created_at=utc_now(),guard_sha256=digest(guard),guard=guard,
            candidate_count=len(specs),adoption_allowed=False))
    if (output/'diagnostics_audit.json').exists():
        sealed_artifacts(output,'diagnostics_audit.json')
    ctx.update(output=output,guard_sha256=digest(guard),configs=configs,specs=specs,supplement=supplement)
    ctx['study']={**ctx['study'],'max_workers':4}
    return ctx


def execute(ctx,workers=2):
    workers=max(1,min(int(workers),4));out=ctx['output'];specs=ctx['specs']
    episodes=registry(ctx['session_dates'])
    atomic_frame(out/'monthly_registry.csv',episodes)
    enriched=pd.read_csv(ctx['supplement']/'enriched_episodes.csv',float_precision='round_trip')
    main=enriched.source_study.eq('original')
    keep=(main & enriched.phase.isin(['development_coarse','walk_forward_later','recent_stress','sanity_monthly']))
    keep|=enriched.source_study.eq('supplement') & enriched.phase.isin(['replacement_development','replacement_validation'])
    reused=canonical_rows(enriched[keep & enriched.candidate_id.isin([s['candidate_id'] for s in specs])])
    # The sanity reference has no coarse/WF rows; searched sets have no sanity rows.
    groups=missing_groups(episodes,reused,specs)
    combined=[reused]
    for index,(members,missing) in enumerate(groups,1):
        phase=f'remaining_monthly_{index:03d}'
        run_groups(members,missing,phase,ctx,workers)
        for spec in members:
            rows=enrich_group(out/'ledgers'/phase/spec['candidate_id'])
            rows['source_study']='family_diagnostics'
            combined.append(canonical_rows(rows))
    rows=pd.concat(combined,ignore_index=True).sort_values(['candidate_id','start']).reset_index(drop=True)
    if rows.duplicated(['candidate_id','episode_id']).any() or len(rows)!=len(specs)*200:
        raise ValueError('Incomplete or duplicated fixed-set monthly denominator')
    expected=episodes.set_index('episode_id')
    for candidate,group in rows.groupby('candidate_id'):
        if set(group.episode_id)!=set(expected.index):
            raise ValueError('Missing monthly cells for '+candidate)
        for row in group.itertuples():
            if row.start!=expected.at[row.episode_id,'start'] or row.end!=expected.at[row.episode_id,'end']:
                raise ValueError('Reused episode interval differs from registry')
    rows['source_root']=rows.source_study.map({'original':str(ctx['main']),
        'supplement':str(ctx['supplement']),'family_diagnostics':str(out)})
    atomic_frame(out/'monthly_all_candidates.csv',rows)
    atomic_frame(out/'full_period_summary.csv',extended_summary(rows))
    atomic_frame(out/'candidate_scopes.csv',pd.DataFrame([dict(candidate_id=s['candidate_id'],source=s['source']) for s in specs]))
    long_rows=[]
    if workers==1:
        _initialize(ctx)
        for spec in specs:
            long_rows.append(continuous_job(spec,ctx['configs'][spec['candidate_id']]))
    else:
        with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('fork'),
                initializer=_initialize,initargs=(ctx,)) as pool:
            futures={pool.submit(continuous_job,spec,ctx['configs'][spec['candidate_id']]):spec['candidate_id'] for spec in specs}
            for future in as_completed(futures):
                long_rows.append(future.result())
                print(json.dumps(dict(event='continuous_complete',candidate_id=futures[future])),flush=True)
    atomic_frame(out/'long_horizon.csv',pd.DataFrame(long_rows).sort_values('candidate_id'))
    atomic_json(out/'diagnostics_audit.json',dict(completed_at=utc_now(),analysis_status=LABEL,
        main_run_manifest_sha256=file_hash(ctx['main']/'run_manifest.json'),
        supplement_audit_sha256=file_hash(ctx['supplement']/'supplement_audit.json'),
        monthly_attempts=len(rows),monthly_per_candidate=200,long_horizon_attempts=len(long_rows),
        candidate_count=len(specs),parameter_set_count=sum(s['source']!='SANITY_REFERENCE' for s in specs),
        sanity_reference_count=sum(s['source']=='SANITY_REFERENCE' for s in specs),
        output_sha256={str(path.relative_to(out)):file_hash(path) for path in sorted(out.rglob('*'))
            if path.is_file() and path.name not in ['diagnostics_audit.json','status.json']
            and path.suffix!='.log' and '.tmp.' not in path.name},
        source_accuracy_verified=False,active_share_status='ACTIVE_SHARE_NOT_VERIFIED',
        ready_status='BLOCK_READY',adoption_allowed=False))
    atomic_json(out/'status.json',dict(status='COMPLETE',completed_at=utc_now(),pid=os.getpid()))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main',type=Path,default=ROOT/'outputs/24d')
    parser.add_argument('--supplement',type=Path,default=ROOT/'outputs/24d_supplement')
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/24d_diagnostics')
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    execute(prepare(args.main,args.supplement,args.output),args.workers)


if __name__=='__main__':
    main()
