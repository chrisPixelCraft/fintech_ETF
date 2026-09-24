#!/usr/bin/env python3
"""Read-only verifier: receipts, denominators, ranking, freeze and ledger replay."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_24d import digest, file_hash, make_registry, atomic_json
from scripts.supplement_24d import registry as recent_registry
from scripts.verify_24d import verify_group, verify_hash_map, same_frame
from scripts.audit_24d import audit_saved_group
from src.yahoo_daily import load_calendar,load_daily


def require(ok,message):
    if not ok: raise ValueError(message)


def read(path):return json.loads(Path(path).read_text())
def csv(path):return pd.read_csv(path,float_precision='round_trip')


def check_summary(rows,summary):
    require(set(summary.candidate_id)==set(rows.candidate_id),'Summary candidate omission')
    require(summary.candidate_id.is_unique,'Duplicate summary candidate')
    for row in summary.itertuples():
        group=rows[rows.candidate_id.eq(row.candidate_id)]
        accepted=group.measured_pass & group.complete_period & group.episode_return.notna()
        r=group.loc[accepted,'episode_return'].to_numpy(float)
        expected=dict(attempted=len(group),passed=int(group.measured_pass.sum()),completed=int(group.complete_period.sum()),
            failed=int((~accepted).sum()),compliance_pass_rate=group.measured_pass.mean(),valid_episode_rate=group.complete_period.mean(),
            median_24d_return=np.median(r) if len(r) else np.nan,mean_24d_return=np.mean(r) if len(r) else np.nan,
            p25_24d_return=np.quantile(r,.25) if len(r) else np.nan,p10_24d_return=np.quantile(r,.1) if len(r) else np.nan,
            worst_24d_return=np.min(r) if len(r) else np.nan,positive_episode_rate=np.mean(r>0) if len(r) else np.nan,
            median_mdd=group.loc[accepted,'episode_max_drawdown'].median(),worst_mdd=group.loc[accepted,'episode_max_drawdown'].max(),
            median_turnover=group.loc[accepted,'episode_turnover'].median(),median_days_to_20_holdings=group.loc[accepted,'days_to_20_holdings'].median())
        for k,v in expected.items():
            actual=getattr(row,k)
            require((pd.isna(v) and pd.isna(actual)) or np.isclose(v,actual,rtol=1e-11,atol=1e-12),f'Summary mismatch {row.candidate_id}/{k}')


def verify(output, rebuild=False):
    out=Path(output).resolve(); manifest=read(out/'result_manifest.json')
    require(manifest['status']=='COMPLETE','Partial run')
    verify_hash_map(out,manifest['files'])
    run=read(out/'study_manifest.json'); tune=read(out/'tuning_manifest.json')
    verify_hash_map(ROOT,run['guard']['source_sha256']);verify_hash_map(ROOT,tune['guard']['implementation'])
    require(digest(run['guard'])==run['guard_sha256'],'Study input guard')
    require(tune['guard']['parent_guard']==run['guard_sha256'],'Tuning parent guard')
    guard=digest(tune['guard'])
    study=run['guard']['study'];calendar=load_calendar(ROOT/'data/yahoo_daily/v3_20260923')
    cache=ROOT/'data/yahoo_daily/v3_20260923'
    verify_hash_map(cache,run['guard']['artifact_sha256'])
    require(file_hash(cache/'metadata.json')==run['guard']['metadata_sha256'],'Metadata mismatch')
    require(file_hash(cache/'calendar_v2.json')==run['guard']['calendar_amendment_sha256'],'Calendar amendment mismatch')
    require(digest(calendar)==run['guard']['calendar_sha256'],'Calendar mismatch')
    require(study['splits']=={'development':['2010-01-01','2018-12-31'],'validation':['2019-01-01','2022-12-31'],'holdout':['2023-01-01','2024-12-31']},'Wrong split')
    registry=make_registry(calendar,study); monthly=registry[registry.kind.eq('monthly')]
    same_frame(csv(out/'episode_registry.csv'),registry,['episode_id'],'registry')
    dev=monthly[monthly.split.eq('development')]; val=monthly[monthly.split.eq('validation')]
    screen=dev.iloc[np.linspace(0,len(dev)-1,12).round().astype(int)].reset_index(drop=True)
    same_frame(csv(out/'screen_registry.csv'),screen,['episode_id'],'screen')
    recent=recent_registry(calendar,'2025-01-01','2026-09-30');recent['split']='recent'
    seasonal=registry[registry.kind.eq('oct_nov')].copy();seasonal['split']='seasonal'
    rolling=recent_registry(calendar,'2025-01-01','2026-09-30','rolling').tail(126).copy();rolling['split']='recent_rolling'
    registries=dict(baseline_reproduction=monthly[monthly.split.isin(['development','validation'])],development=dev,validation=val,
        holdout=monthly[monthly.split.eq('holdout')],recent=recent,seasonal=seasonal,rolling_recent=rolling)
    registries.update({'screen_'+p:screen for p in 'ABCDEFG'})
    freeze=read(out/'final_selection.json'); frozen_at=pd.Timestamp(freeze['frozen_at'])
    require(manifest['frozen_selection_sha256']==file_hash(out/'final_selection.json'),'Freeze digest')
    require(freeze['formal_status']=='BLOCK_SUBMISSION' and freeze['prior_holdout_exposure'] is True,'Overclaimed official or holdout status')
    verify_hash_map(out,freeze['selection_input_sha256'])
    members=csv(out/'phase_memberships.csv'); shortlist=read(out/'shortlist.json')['candidate_ids']
    expected_candidates={p:sorted(members.loc[members.phase.eq(p[-1]),'candidate_id']) for p in registries if p.startswith('screen_')}
    expected_candidates.update(development=sorted(shortlist),validation=sorted(shortlist),baseline_reproduction=['x0352_daily_baseline'])
    diagnostics=set(c for c in freeze['diagnostic_method_leaders'].values() if c)|{'x0352_daily_baseline'}
    if freeze['candidate_id']:diagnostics.add(freeze['candidate_id'])
    expected_candidates.update({p:sorted(diagnostics) for p in ['holdout','recent','seasonal']})
    expected_candidates['rolling_recent']=sorted({'x0352_daily_baseline'}|({freeze['candidate_id']} if freeze['candidate_id'] else set()))
    groups=[]; rebuild_count=0;ctx=None
    if rebuild:
        universe=csv(ROOT/'data/reference/universe_competition_20260731.csv');universe['symbol']=universe.yahoo_symbol;universe['known_at']=universe.attachment_created_at
        ctx=dict(daily=load_daily(ROOT/'data/yahoo_daily/v3_20260923'),universe=universe,session_dates=calendar,study=study)
    for phase_path in sorted((out/'ledgers').iterdir()):
        phase=phase_path.name
        if phase.startswith('stability_'):
            episodes=pd.concat([dev,val]); require(phase in ['stability_'+x['candidate_id'] for x in read(out/'stability.json')['results']],'Undeclared stability run')
        else:
            require(phase in registries,'Unexpected phase '+phase);episodes=registries[phase]
            actual=sorted(p.name for p in phase_path.iterdir() if p.is_dir())
            require(actual==expected_candidates[phase],'Group coverage differs: '+phase)
        pieces=[]
        for folder in sorted(phase_path.iterdir()):
            receipt=read(folder/'receipt.json');relative=str((folder/'receipt.json').relative_to(out))
            require(relative in manifest['files'],'Unsealed receipt')
            rows,cfg=verify_group(out,relative,receipt,episodes,folder.name,phase,guard)
            require(not rows.loc[~rows.complete_period,'episode_return'].notna().any(),'Incomplete episode has terminal return')
            if phase in ['holdout','recent','seasonal','rolling_recent']:
                require(pd.Timestamp(receipt['completed_at'])>=frozen_at,'Post-freeze evaluation predates freeze')
            else:require(pd.Timestamp(receipt['completed_at'])<=frozen_at,'Selection data completed after freeze')
            if rebuild and phase=='holdout':
                audit_saved_group(folder,rows,{folder.name:cfg},ctx,episodes);rebuild_count+=len(rows)
            pieces.append(rows);groups.append(dict(phase=phase,candidate_id=folder.name,episodes=len(rows)))
        joined=pd.concat(pieces,ignore_index=True)
        same_frame(csv(out/(phase+'.csv')),joined,['candidate_id','episode_id'],phase+' flattened results')
        check_summary(joined,csv(out/(phase+'_summary.csv')))
    for phase in registries:
        require((out/'ledgers'/phase).is_dir(),'Missing required phase: '+phase)
    # Repeated candidate/episode cells must reproduce exactly across phases.
    screens=pd.concat([csv(out/('screen_'+p+'.csv')) for p in 'ABCDEFG'],ignore_index=True)
    check_columns=['episode_return','episode_max_drawdown','episode_turnover','measured_pass','complete_period']
    for identity,group in screens.groupby(['candidate_id','episode_id']):
        if len(group)>1:
            require(all(group[c].nunique(dropna=False)==1 for c in check_columns),'Non-deterministic repeated cell: '+str(identity))
    screen_unique=screens.drop_duplicates(['candidate_id','episode_id'])
    check_summary(screen_unique,csv(out/'screen_summary.csv'))
    for phase in 'ABCDEFG':
        check_summary(csv(out/('screen_'+phase+'.csv')),csv(out/('phase_'+phase+'_ranking.csv')))
    ds=csv(out/'development_ranking.csv');vs=csv(out/'validation_ranking.csv')
    for phase,s in [('development',ds),('validation',vs)]:check_summary(csv(out/(phase+'.csv')),s)
    admissible=set(ds.loc[ds.compliance_pass_rate.eq(1)&ds.valid_episode_rate.eq(1)&ds.failed.eq(0),'candidate_id']) & set(vs.loc[vs.compliance_pass_rate.eq(1)&vs.valid_episode_rate.eq(1)&vs.failed.eq(0),'candidate_id'])
    if freeze['candidate_id']:
        require(freeze['candidate_id'] in admissible,'Ineligible final selection')
        stability=read(out/'stability.json')['results'];require(any(x['candidate_id']==freeze['candidate_id'] and x['stable'] for x in stability),'Unstable candidate adopted')
    else:require(freeze['status']=='NO_ELIGIBLE_CANDIDATE','False adoption')
    candidates=read(out/'candidates.json')
    require(len(candidates)==freeze['candidate_count'],'Candidate count differs')
    require(len({x['candidate_id'] for x in candidates})==len(candidates),'Candidate identity collision')
    for trial in candidates:
        cfg=read(out/'configs'/(trial['candidate_id']+'.json'))
        require(cfg['full_tuning_params']==trial['params'],'Registry/config parameter mismatch')
        require(cfg['initial_cash']==1_000_000_000 and cfg['commission']==.001425 and cfg['sell_tax']==.003,'Financial assumptions changed')
        require(cfg['use_4h'] is False and cfg['execution']=='open_proxy','Execution/data frequency changed')
    chosen=freeze['candidate_id'] or 'x0352_daily_baseline'
    package=read(out/'competition_24d_candidate.json')
    require(package['selection']==freeze,'Frozen package decision mismatch')
    require(package['config']==read(out/'configs'/(chosen+'.json')),'Frozen config mismatch')
    require(package['config_sha256']==digest(package['config']),'Frozen config digest mismatch')
    reproduction=read(out/'baseline_reproduction.json')
    require(reproduction['status']=='PASS','Baseline failed')
    previous=ROOT/'outputs/24d_diagnostics/monthly_all_candidates.csv'
    require(file_hash(previous)==reproduction['previous_results_sha256'],'Previous baseline source changed')
    reference=csv(previous)
    reference=reference[reference.candidate_id.eq('x0352_daily_baseline') & reference.episode_id.isin(registries['baseline_reproduction'].episode_id)]
    columns=['episode_id']+reproduction['columns']
    same_frame(csv(out/'baseline_reproduction.csv')[columns],reference[columns],['episode_id'],'Independent baseline reproduction check')
    delivery_path=out/'delivery_manifest.json'
    if delivery_path.exists():
        delivery=read(delivery_path)
        required={'README.md','docs/README.md','docs/v3_tuning.md','scripts/tune_24d.py','scripts/verify_24d_tuning.py',
            'scripts/report_24d_tuning.py','tests/test_24d_tuning.py','reports/24d_tuning.md',
            'reports/24d_tuning_comparison.csv','reports/24d_tuning_cold_start.csv'}
        require(required<=set(delivery['files']),'Delivery omits required artifacts')
        require(delivery['result_manifest_sha256']==file_hash(out/'result_manifest.json'),'Delivery/run mismatch')
        verify_hash_map(ROOT,delivery['files'])
    return dict(status='PASS',groups=len(groups),attempts=sum(x['episodes'] for x in groups),
        holdout_ledgers_rebuilt=rebuild_count,development_episodes=len(dev),validation_episodes=len(val),holdout_episodes=len(registries['holdout']),
        candidate_count=len(read(out/'candidates.json')),decision=freeze['status'],formal_status='BLOCK_SUBMISSION',
        result_manifest_sha256=file_hash(out/'result_manifest.json'),verifier_sha256=file_hash(Path(__file__)))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=ROOT/'outputs/24d_tuning');p.add_argument('--rebuild',action='store_true');p.add_argument('--write',action='store_true')
    a=p.parse_args();result=verify(a.output,a.rebuild)
    if a.write:atomic_json(a.output/'verification.json',result)
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
