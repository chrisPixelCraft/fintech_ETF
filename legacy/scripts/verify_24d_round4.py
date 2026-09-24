#!/usr/bin/env python3
"""Independently verify fourth-round required-episode pruning and raw evidence."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_24d import digest,file_hash,atomic_json,make_registry,_candidate_config
from scripts.verify_24d import verify_hash_map,verify_group,same_frame,require_receipt_sealed
from scripts.verify_24d_tuning import check_summary
from scripts.verify_24d_round3 import require,read,csv,check_measured,ordered
from scripts.audit_24d import audit_saved_group
from src.yahoo_daily import load_calendar,load_daily

PRIOR=['24d_tuning','24d_expansion','24d_round3']
VARIABLES={'return_short','return_long','momentum_weight','long_return_fraction',
           'max_replacements_per_day','replacement_margin'}


def check_population(candidates,refs,members):
    from scripts.tune_24d import SPACE,EMA,MACD,spec
    lookup={p['candidate_id']:p for name in PRIOR for p in read(ROOT/'outputs'/name/'candidates.json')}
    require(len(lookup)==1087,'Incorrect prior exclusion population')
    ids={p['candidate_id'] for p in candidates}
    require(len(ids)==len(candidates)==384 and not ids.intersection(lookup),'Duplicated or wrong new population')
    require(members.candidate_id.is_unique and set(members.candidate_id)==ids,'Incomplete memberships')
    parents=[]
    for name in PRIOR:
        folder=ROOT/'outputs'/name
        own={p['candidate_id'] for p in read(folder/'candidates.json')}
        ranking=csv(folder/'development_ranking.csv')
        parents.append(ranking.loc[ranking.candidate_id.isin(own),'candidate_id'].iloc[0])
    require(len(set(parents))==3,'Parents are not distinct')
    require(set(members.parent_id)==set(parents),'Parents not chosen from own development evidence')
    require(members.groupby('parent_id').size().to_dict()==dict.fromkeys(parents,128),'Unbalanced parent budget')
    require(len(members.family.unique())==3 and sorted(members.family.value_counts())==[128]*3,'Incorrect family budget')
    refids={p['candidate_id'] for p in refs}
    require(len(refids)==len(refs)==4 and refids==set(parents)|{'x0352_daily_baseline'},'Incorrect references')
    byid=members.set_index('candidate_id')
    for trial in candidates:
        p=trial['params'];parent=lookup[byid.loc[trial['candidate_id'],'parent_id']]['params']
        require(spec(p)==trial,'Wrong candidate identity')
        require(set(p)==set(parent),'Parameter keys changed')
        require({k for k in p if p[k]!=parent[k]}<=VARIABLES,'Unapproved parameter changed')
        require(p['return_short']<p['return_long'],'Invalid return horizons')
        require(all(p[k] in values for k,values in SPACE.items()),'Outside original search bounds')
        require((p['ema_fast'],p['ema_slow']) in EMA and (p['macd_fast'],p['macd_slow'],p['macd_signal']) in MACD,'Outside indicator bounds')
    for ref in refs:require(ref==lookup[ref['candidate_id']],'Reference parameters changed')
    return ids,refids


def check_pruning(freeze,hard,new_ids):
    require(hard.candidate_id.is_unique,'Duplicate hard-case candidate')
    rows=hard[hard.candidate_id.isin(new_ids)]
    require(set(rows.candidate_id)==set(new_ids),'Missing required episode')
    check_measured(rows)
    require(not rows.measured_pass.any(),'Passing new candidate incorrectly pruned')
    require(freeze['candidate_id'] is None and freeze['status']=='NO_ELIGIBLE_CANDIDATE','False adopted candidate')
    require(freeze['stop_reason']=='ALL_NEW_FAILED_REQUIRED_DEVELOPMENT_EPISODE','Incorrect early-stop reason')
    require(freeze['downstream_evaluation']=='NOT_RUN','Unmeasured downstream status')
    require(freeze['formal_status']=='BLOCK_SUBMISSION','Official status overclaim')


def verify(output,rebuild=False):
    out=Path(output).resolve();manifest=read(out/'result_manifest.json')
    require(manifest['status']=='COMPLETE' and manifest['formal_status']=='BLOCK_SUBMISSION','Incomplete or official overclaim')
    require(not (out/'failure.json').exists(),'Unresolved engineering failure')
    verify_hash_map(out,manifest['files'])
    run=read(out/'study_manifest.json');expansion=read(out/'expansion_manifest.json');guard=expansion['guard']
    verify_hash_map(ROOT,run['guard']['source_sha256']);verify_hash_map(ROOT,guard['implementation'])
    require(digest(run['guard'])==run['guard_sha256'] and guard['parent_guard']==run['guard_sha256'],'Study guard mismatch')
    prior_guard=read(ROOT/'outputs/24d_round3/study_manifest.json')['guard']
    current_fixed={k:v for k,v in run['guard']['source_sha256'].items() if k!='config/24d_round4_study.json'}
    prior_fixed={k:v for k,v in prior_guard['source_sha256'].items() if k!='config/24d_round3_study.json'}
    require(current_fixed==prior_fixed,'Trading engine, rules, universe or fixed sources changed')
    expected_sources={f'outputs/{name}/{file}' for name in PRIOR for file in ['candidates.json','development_ranking.csv']}
    require(set(guard['selection_source_sha256'])==expected_sources,'Unexpected nondevelopment selection inputs')
    verify_hash_map(ROOT,guard['selection_source_sha256'])
    require(set(guard['parent_result_manifest_sha256'])=={f'outputs/{n}' for n in PRIOR},'Incorrect parent seal population')
    for name,checksum in guard['parent_result_manifest_sha256'].items():
        require(file_hash(ROOT/name/'result_manifest.json')==checksum,'Parent result seal changed')
    # Parent result seals bind the precise development sources used for local search.
    for name in PRIOR:
        parent=ROOT/'outputs'/name;seal=read(parent/'result_manifest.json')
        for file in ['candidates.json','development_ranking.csv']:
            require(seal['files'][file]==guard['selection_source_sha256'][f'outputs/{name}/{file}'],'Unsealed parent input')
    cache=ROOT/'data/yahoo_daily/v3_20260923';calendar=load_calendar(cache);study=run['guard']['study']
    verify_hash_map(cache,run['guard']['artifact_sha256'])
    require(file_hash(cache/'metadata.json')==run['guard']['metadata_sha256'],'Metadata mismatch')
    require(file_hash(cache/'calendar_v2.json')==run['guard']['calendar_amendment_sha256'],'Calendar amendment mismatch')
    require(digest(calendar)==run['guard']['calendar_sha256'],'Calendar mismatch')
    require(study['splits']=={'development':['2010-01-01','2018-12-31'],'validation':['2019-01-01','2022-12-31'],'holdout':['2023-01-01','2024-12-31']},'Split changed')
    registry=make_registry(calendar,study);same_frame(csv(out/'episode_registry.csv'),registry,['episode_id'],'Registry')
    monthly=registry[registry.kind.eq('monthly')];dev=monthly[monthly.split.eq('development')];val=monthly[monthly.split.eq('validation')]
    pre=pd.concat([dev,val]);hard_registry=dev[dev.start.eq('2015-08-03')]
    require(len(dev)==107 and len(val)==47 and len(hard_registry)==1,'Episode coverage changed')
    same_frame(csv(out/'bottleneck_registry.csv'),hard_registry,['episode_id'],'Bottleneck registry')
    candidates=read(out/'candidates.json');refs=read(out/'references.json');members=csv(out/'phase_memberships.csv')
    ids,refids=check_population(candidates,refs,members)
    from scripts.expand_24d_round4 import generate_candidates
    regenerated,regenerated_refs,regenerated_members=generate_candidates()
    require(candidates==regenerated and refs==regenerated_refs,'Generation is not reproducible')
    same_frame(members,regenerated_members,['candidate_id'],'Regenerated memberships')
    freeze=read(out/'final_selection.json');frozen_at=pd.Timestamp(freeze['frozen_at'])
    require(freeze['candidate_count']==384 and freeze['previous_candidate_count']==1087,'Frozen candidate count wrong')
    require(freeze['prior_holdout_exposure'] is True,'Earlier holdout exposure hidden')
    require(manifest['frozen_selection_sha256']==file_hash(out/'final_selection.json'),'Freeze hash mismatch')
    verify_hash_map(out,freeze['selection_input_sha256'])
    require(set(freeze['selection_input_sha256'])=={'baseline_reproduction.csv','bottleneck.csv','candidates.json','references.json','phase_memberships.csv'},'Unexpected pruning inputs')
    require(set(freeze['reference_ids'])==refids and freeze['hard_pass_ids']==[],'Incorrect frozen coverage')
    require(freeze['stop_reason']=='ALL_NEW_FAILED_REQUIRED_DEVELOPMENT_EPISODE','Full-survivor branch needs dedicated independent audit')
    expected={'baseline_reproduction':(['x0352_daily_baseline'],pre),'bottleneck':(sorted(ids|refids),hard_registry)}
    require({p.name for p in (out/'ledgers').iterdir() if p.is_dir()}==set(expected),'Unexpected or missing phase')
    require(not any((out/(p+'.csv')).exists() for p in ['development','validation','screen','holdout','recent','seasonal','rolling_recent']),'Unexpected downstream output after pruning')
    ctx=None
    if rebuild:
        universe=csv(ROOT/'data/reference/universe_competition_20260731.csv');universe['symbol']=universe.yahoo_symbol;universe['known_at']=universe.attachment_created_at
        ctx=dict(daily=load_daily(cache),universe=universe,session_dates=calendar,study=study)
    attempts=groups=rebuilt=0;tables={}
    for phase,(identifiers,episodes) in expected.items():
        folder=out/'ledgers'/phase
        require(sorted(p.name for p in folder.iterdir() if p.is_dir())==identifiers,'Candidate coverage differs: '+phase)
        chunks=[]
        for candidate in identifiers:
            group=folder/candidate;receipt=read(group/'receipt.json');relative=str((group/'receipt.json').relative_to(out))
            require_receipt_sealed(relative,receipt,manifest['files'])
            rows,cfg=verify_group(out,relative,receipt,episodes,candidate,phase,digest(guard));check_measured(rows)
            require(pd.Timestamp(receipt['completed_at'])<=frozen_at,'Pruning predates measured evidence')
            if rebuild:audit_saved_group(group,rows,{candidate:cfg},ctx,episodes);rebuilt+=len(rows)
            chunks.append(rows);groups+=1;attempts+=len(rows)
        joined=pd.concat(chunks,ignore_index=True);tables[phase]=joined
        same_frame(csv(out/(phase+'.csv')),joined,['candidate_id','episode_id'],'Flattened '+phase)
        check_summary(joined,csv(out/(phase+'_summary.csv')))
    check_pruning(freeze,tables['bottleneck'],ids)
    parameters={p['candidate_id']:p['params'] for p in candidates+refs}
    ranking=csv(out/'bottleneck_ranking.csv');check_summary(tables['bottleneck'],ranking)
    require(ranking.candidate_id.tolist()==ordered(ranking,parameters).candidate_id.tolist(),'Wrong hard-case ranking')
    reproduction=read(out/'baseline_reproduction.json')
    require(reproduction['status']=='PASS' and reproduction['episodes']==154,'Baseline reproduction failed')
    verify_hash_map(ROOT,reproduction['previous_results_sha256'])
    columns=['episode_return','episode_max_drawdown','episode_turnover','measured_pass','complete_period','raw_rule_breach_days','simulated_warning_days']
    require(reproduction['columns']==columns,'Weakened baseline parity')
    old=pd.concat([csv(ROOT/'outputs/24d_round3'/f'{p}.csv') for p in ['development','validation']]);old=old[old.candidate_id.eq('x0352_daily_baseline')]
    same_frame(tables['baseline_reproduction'][['episode_id']+columns],old[['episode_id']+columns],['episode_id'],'Baseline parity')
    duplicate=tables['bottleneck'][tables['bottleneck'].candidate_id.eq('x0352_daily_baseline')]
    baseline=tables['baseline_reproduction'][tables['baseline_reproduction'].episode_id.eq('monthly_2015-08-03')]
    same_frame(duplicate[['episode_id']+columns],baseline[['episode_id']+columns],['episode_id'],'Repeated baseline')
    for trial in candidates+refs:
        cfg=read(out/'configs'/(trial['candidate_id']+'.json'))
        require(cfg==_candidate_config(trial),'Factory config mismatch')
        require(cfg['initial_cash']==1e9 and cfg['commission']==.001425 and cfg['sell_tax']==.003,'Capital/cost changed')
        require(cfg['use_4h'] is False and cfg['execution']=='open_proxy','Execution assumption changed')
    package=read(out/'competition_24d_candidate.json')
    require(package['selection']==freeze and package['config_sha256']==digest(package['config']),'Frozen package mismatch')
    require(package['config']==read(out/'configs/x0352_daily_baseline.json'),'Pruned study falsely replaces baseline')
    if (out/'delivery_manifest.json').exists():
        seal=read(out/'delivery_manifest.json');require(seal['result_manifest_sha256']==file_hash(out/'result_manifest.json'),'Delivery/result mismatch');verify_hash_map(ROOT,seal['files'])
    return dict(status='PASS',candidate_count=384,groups=groups,attempts=attempts,ledgers_rebuilt=rebuilt,
        development_episodes=107,validation_episodes=47,new_downstream_evaluations=0,decision=freeze['status'],formal_status='BLOCK_SUBMISSION',
        result_manifest_sha256=file_hash(out/'result_manifest.json'),verifier_sha256=file_hash(Path(__file__)))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=ROOT/'outputs/24d_round4')
    p.add_argument('--rebuild',action='store_true');p.add_argument('--write',action='store_true');args=p.parse_args()
    result=verify(args.output,args.rebuild)
    if args.write:atomic_json(args.output/'verification.json',result)
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
