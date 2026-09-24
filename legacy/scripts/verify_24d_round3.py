#!/usr/bin/env python3
"""Independent read-only verification of the third bounded v3 search."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_24d import digest,file_hash,atomic_json,make_registry
from scripts.verify_24d import verify_hash_map,verify_group,same_frame,require_receipt_sealed
from scripts.verify_24d_tuning import check_summary
from scripts.supplement_24d import registry as recent_registry
from scripts.audit_24d import audit_saved_group
from src.yahoo_daily import load_calendar,load_daily


def require(ok,message):
    if not ok:raise ValueError(message)


def read(path):return json.loads(Path(path).read_text())
def csv(path):return pd.read_csv(path,float_precision='round_trip')


def check_measured(rows):
    """A passed row must satisfy the strict local gate, not only its pass flag."""
    require(rows.measured_pass.isin([True,False]).all(),'Invalid measured pass flags')
    require(rows.complete_period.isin([True,False]).all(),'Invalid completion flags')
    require(rows.loc[~rows.complete_period,'episode_return'].isna().all(),'Incomplete episode has terminal return')
    passed=rows[rows.measured_pass]
    if passed.empty:return
    require(passed.complete_period.all(),'Incomplete measured pass')
    require(np.isfinite(passed.episode_return.to_numpy(float)).all(),'Nonfinite accepted return')
    for column in ['measured_hard_breach_days','no_valid_plan_days','unfilled_orders','simulated_warning_days',
                   'stale_held_price_days','hold_without_envelope_days','execution_price_bound_breaches','raw_rule_breach_days']:
        require(column in rows and passed[column].eq(0).all(),'Nonzero or missing strict gate: '+column)
    require(passed.requested_sessions.eq(24).all() and passed.observed_sessions.eq(24).all(),'Incomplete accepted session count')
    require(not bool(passed.disqualified.any()),'Disqualified measured pass')


def ordered(summary,parameters):
    """Re-sort independently; never trust producer order or baseline-distance values."""
    from src.strategy_24d import build_config
    baseline=build_config()['full_tuning_params']
    result=summary.copy()
    result['baseline_distance']=[sum(v!=baseline[k] for k,v in parameters[c].items()) for c in result.candidate_id]
    fields=['compliance_pass_rate','valid_episode_rate','median_24d_return','p25_24d_return','p10_24d_return',
            'median_mdd','mean_24d_return','median_turnover','baseline_distance','candidate_id']
    return result.sort_values(fields,ascending=[False,False,False,False,False,True,False,True,True,True],na_position='last').reset_index(drop=True)


def check_adoption(freeze,development,validation,stability,candidate_ids=None):
    require(freeze['formal_status']=='BLOCK_SUBMISSION','Official status overclaim')
    require(freeze['prior_holdout_exposure'] is True,'Previously observed holdout described as unseen')
    chosen=freeze.get('candidate_id')
    if chosen is None:
        require(freeze['status']=='NO_ELIGIBLE_CANDIDATE','Null selection has false status')
    else:
        require(freeze['status']=='SELECTED_RESEARCH_ONLY','Invalid adopted status')
        for label,summary in [('development',development),('validation',validation)]:
            row=summary[summary.candidate_id.eq(chosen)]
            require(len(row)==1,'Selected candidate absent from '+label)
            require(row.compliance_pass_rate.eq(1).all() and row.valid_episode_rate.eq(1).all() and row.failed.eq(0).all(),
                    'Ineligible selected candidate in '+label)
    baseline_rows=validation[validation.candidate_id.eq('x0352_daily_baseline')]
    require(len(baseline_rows)==1,'Missing unique validation baseline')
    baseline=baseline_rows.iloc[0]
    if candidate_ids is None:
        candidate_ids=set(validation.candidate_id)-{'x0352_daily_baseline',freeze.get('prior_leader_id')}
    admitted=set(candidate_ids)
    for summary in [development,validation]:
        admitted &= set(summary.loc[summary.compliance_pass_rate.eq(1)&summary.valid_episode_rate.eq(1)&summary.failed.eq(0),'candidate_id'])
    contenders=validation[validation.candidate_id.isin(admitted)&validation.median_24d_return.ge(baseline.median_24d_return)
                          &validation.p25_24d_return.ge(baseline.p25_24d_return)]
    evidence=stability['results']; checked=[]; expected=None
    for candidate in contenders.candidate_id:
        matches=[item for item in evidence if item['candidate_id']==candidate]
        require(len(matches)==1,'Missing or duplicate stability evidence for qualified candidate '+candidate)
        checked.append(candidate)
        require(isinstance(matches[0]['stable'],bool),'Invalid stability decision flag')
        if matches[0]['stable']:
            expected=candidate
            break
    require([item['candidate_id'] for item in evidence]==checked,'Stability evaluations differ from ranked qualified candidates')
    require(chosen==expected,'Final selection omits or differs from first eligible stable candidate')


def verify(output,rebuild=False):
    out=Path(output).resolve();manifest=read(out/'result_manifest.json')
    require(manifest['status']=='COMPLETE','Partial expansion')
    require(manifest['formal_status']=='BLOCK_SUBMISSION','Official certification overclaim')
    verify_hash_map(out,manifest['files'])
    run=read(out/'study_manifest.json'); expansion=read(out/'expansion_manifest.json');guard=expansion['guard']
    verify_hash_map(ROOT,run['guard']['source_sha256']);verify_hash_map(ROOT,guard['implementation'])
    require(digest(run['guard'])==run['guard_sha256'],'Study guard mismatch')
    require(guard['parent_guard']==run['guard_sha256'],'Expansion parent guard mismatch')
    parent=ROOT/'outputs/24d_expansion';original=ROOT/'outputs/24d_tuning'
    require(file_hash(parent/'result_manifest.json')==guard['parent_result_manifest_sha256'],'Previous result seal changed')
    require(set(guard['selection_source_sha256'])=={'outputs/24d_tuning/candidates.json','outputs/24d_expansion/candidates.json','outputs/24d_expansion/development_ranking.csv'},'Selection inputs differ from development-only protocol')
    verify_hash_map(ROOT,guard['selection_source_sha256'])
    require(file_hash(original/'result_manifest.json')==guard['original_result_manifest_sha256'],'Original result seal changed')
    for name,checksum in guard['selection_source_sha256'].items():
        owner=ROOT/Path(name).parent
        require(owner in [parent,original],'Unexpected selection source')
        source_seal=read(owner/'result_manifest.json')
        require(source_seal['files'].get(Path(name).name)==checksum,'Selection source is not sealed by previous result')
    require(all('holdout' not in p and 'recent' not in p and 'validation' not in p for p in guard['selection_source_sha256']),
            'Nondevelopment previous result used to generate expansion')
    cache=ROOT/'data/yahoo_daily/v3_20260923';calendar=load_calendar(cache);study=run['guard']['study']
    verify_hash_map(cache,run['guard']['artifact_sha256'])
    require(file_hash(cache/'metadata.json')==run['guard']['metadata_sha256'],'Metadata mismatch')
    require(file_hash(cache/'calendar_v2.json')==run['guard']['calendar_amendment_sha256'],'Calendar amendment mismatch')
    require(digest(calendar)==run['guard']['calendar_sha256'],'Calendar mismatch')
    require(study['splits']=={'development':['2010-01-01','2018-12-31'],'validation':['2019-01-01','2022-12-31'],
                            'holdout':['2023-01-01','2024-12-31']},'Wrong chronological split')
    registry=make_registry(calendar,study);same_frame(csv(out/'episode_registry.csv'),registry,['episode_id'],'Registry')
    monthly=registry[registry.kind.eq('monthly')];dev=monthly[monthly.split.eq('development')];val=monthly[monthly.split.eq('validation')]
    pre=pd.concat([dev,val]);screen=dev.iloc[np.linspace(0,len(dev)-1,12).round().astype(int)].reset_index(drop=True)
    bottleneck=dev[dev.start.eq('2015-08-03')];require(len(bottleneck)==1,'Bottleneck outside development')
    same_frame(csv(out/'screen_registry.csv'),screen,['episode_id'],'Screen registry')
    same_frame(csv(out/'bottleneck_registry.csv'),bottleneck,['episode_id'],'Bottleneck registry')
    candidates=read(out/'candidates.json');refs=read(out/'references.json')
    if isinstance(refs,dict):refs=refs['candidates']
    all_specs=candidates+refs;params={s['candidate_id']:s['params'] for s in all_specs}
    from scripts.tune_24d import SPACE,EMA,MACD,spec
    for trial in candidates:
        p=trial['params']
        require(spec(p)==trial,'Candidate identity/parameters differ')
        require(all(p[key] in values for key,values in SPACE.items()),'Candidate outside discrete declared space')
        require((p['ema_fast'],p['ema_slow']) in EMA and (p['macd_fast'],p['macd_slow'],p['macd_signal']) in MACD,'Undeclared EMA/MACD')
    new_ids={s['candidate_id'] for s in candidates};reference_ids={s['candidate_id'] for s in refs}
    old_ids={s['candidate_id'] for folder in [original,parent] for s in read(folder/'candidates.json')}
    require(len(old_ids)==575,'Incorrect prior exclusion population')
    require(len(new_ids)==len(candidates) and not(new_ids & old_ids),'Repeated previous candidate or identifier collision')
    members=csv(out/'phase_memberships.csv')
    require(members.candidate_id.is_unique and set(members.candidate_id)==new_ids,'Membership omits or duplicates candidate')
    require(members.family.value_counts().to_dict()=={'uniform':256,'stratified_entry_replacement':256},'Family count differs from frozen budget')
    from scripts.expand_24d_round3 import generate_candidates
    regenerated,regenerated_refs,regenerated_members=generate_candidates()
    require(candidates==regenerated and refs==regenerated_refs,'Candidate generation is not reproducible from frozen development sources')
    same_frame(members,regenerated_members,['candidate_id'],'Regenerated memberships')
    require(not(new_ids & reference_ids),'Reference counted as new')
    require(len(reference_ids)==2 and 'x0352_daily_baseline' in reference_ids,'Missing baseline or previous leader')
    require(reference_ids<=old_ids,'Reference is not from previous study')
    shortlist=read(out/'shortlists.json');freeze=read(out/'final_selection.json');frozen_at=pd.Timestamp(freeze['frozen_at'])
    require(manifest['frozen_selection_sha256']==file_hash(out/'final_selection.json'),'Frozen selection digest mismatch')
    require(freeze['candidate_count']==len(new_ids) and freeze['previous_candidate_count']==len(old_ids),'Candidate count mismatch')
    require(freeze['prior_leader_id'] in reference_ids,'Incorrect previous reference')
    require(freeze['diagnostic_leader_id'] in new_ids,'New diagnostic leader not from expansion')
    require(set(freeze['selection_input_sha256'])=={'development.csv','validation.csv','shortlists.json','candidates.json','references.json','stability.json'},'Frozen selection inputs incomplete or unexpected')
    verify_hash_map(out,freeze['selection_input_sha256'])
    recent=recent_registry(calendar,'2025-01-01','2026-09-30');recent['split']='recent'
    seasonal=registry[registry.kind.eq('oct_nov')].copy();seasonal['split']='seasonal'
    rolling=recent_registry(calendar,'2025-01-01','2026-09-30','rolling').tail(126).copy();rolling['split']='recent_rolling'
    chosen={freeze['candidate_id']} if freeze['candidate_id'] else set()
    comparisons=reference_ids|{freeze['diagnostic_leader_id']}|chosen
    expected={
        'baseline_reproduction':(['x0352_daily_baseline'],pre),
        'bottleneck':(sorted(new_ids|reference_ids),bottleneck),
        'screen':(sorted(set(shortlist['bottleneck_top12'])|reference_ids),screen),
        'development':(sorted(set(shortlist['screen_top6'])|reference_ids),dev),
        'validation':(sorted(set(shortlist['screen_top6'])|reference_ids),val),
        'holdout':(sorted(comparisons),monthly[monthly.split.eq('holdout')]),
        'recent':(sorted(comparisons),recent),'seasonal':(sorted(comparisons),seasonal),
        'rolling_recent':(sorted({'x0352_daily_baseline',freeze['diagnostic_leader_id']}|chosen),rolling)}
    stability=read(out/'stability.json')
    for s in stability['results']:expected['stability_'+s['candidate_id']]=(sorted(s['neighbors']),pre)
    require({p.name for p in (out/'ledgers').iterdir() if p.is_dir()}==set(expected),'Missing or unexpected ledger phase')
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
            if phase in ['holdout','recent','seasonal','rolling_recent']:
                require(pd.Timestamp(receipt['completed_at'])>=frozen_at,'Evaluation predates freeze')
            else:require(pd.Timestamp(receipt['completed_at'])<=frozen_at,'Selection run completed after freeze')
            if rebuild and phase=='holdout':audit_saved_group(group,rows,{candidate:cfg},ctx,episodes);rebuilt+=len(rows)
            chunks.append(rows);groups+=1;attempts+=len(rows)
        joined=pd.concat(chunks,ignore_index=True);tables[phase]=joined
        same_frame(csv(out/(phase+'.csv')),joined,['candidate_id','episode_id'],'Flattened '+phase)
        check_summary(joined,csv(out/(phase+'_summary.csv')))
    repeated=pd.concat(list(tables.values()),ignore_index=True)
    for identity,rows in repeated.groupby(['candidate_id','episode_id']):
        if len(rows)>1:
            require(all(rows[c].nunique(dropna=False)==1 for c in ['episode_return','episode_max_drawdown',
                'episode_turnover','measured_pass','complete_period']),'Repeated episode changed across phases: '+str(identity))
    for phase in ['bottleneck','screen','development','validation']:
        summary=csv(out/(phase+'_ranking.csv'));check_summary(tables[phase],summary)
        recomputed=ordered(summary,params)
        require(summary.candidate_id.tolist()==recomputed.candidate_id.tolist(),'Incorrect ranking order: '+phase)
    b=ordered(csv(out/'bottleneck_ranking.csv'),params);s=ordered(csv(out/'screen_ranking.csv'),params)
    require(shortlist['bottleneck_top12']==b[b.candidate_id.isin(new_ids)].candidate_id.head(12).tolist(),'Wrong bottleneck promotion')
    require(shortlist['screen_top6']==s[s.candidate_id.isin(new_ids)].candidate_id.head(6).tolist(),'Wrong screen promotion')
    ds=csv(out/'development_ranking.csv');vs=csv(out/'validation_ranking.csv')
    require(freeze['diagnostic_leader_id']==ds[ds.candidate_id.isin(new_ids)].iloc[0].candidate_id,'Diagnostic leader used nondevelopment ranking')
    old_dev=csv(parent/'development_ranking.csv')
    prior_new={s['candidate_id'] for s in read(parent/'candidates.json')}
    require(freeze['prior_leader_id']==old_dev[old_dev.candidate_id.isin(prior_new)].iloc[0].candidate_id,'Prior leader not chosen from development')
    for item in stability['results']:
        center=params[item['candidate_id']]
        expected_neighbors={spec({**center,'momentum_weight':round(center['momentum_weight']+delta,10)})['candidate_id']
            for delta in [-.05,.05] if .4<=center['momentum_weight']+delta<=.95}
        require(set(item['neighbors'])==expected_neighbors,'Incomplete stability neighborhood')
        stable=True
        for split,ranking in [('development',ds),('validation',vs)]:
            center_row=ranking[ranking.candidate_id.eq(item['candidate_id'])].iloc[0]
            neighbors=tables['stability_'+item['candidate_id']]
            for candidate in expected_neighbors:
                rows=neighbors[neighbors.split.eq(split)&neighbors.candidate_id.eq(candidate)]
                accepted=rows.measured_pass & rows.complete_period & rows.episode_return.notna()
                r=rows.loc[accepted,'episode_return']
                stable &= bool(accepted.all() and len(rows)>0 and r.median()>=center_row.median_24d_return-.002
                               and r.quantile(.25)>=center_row.p25_24d_return-.002)
        require(bool(item['stable'])==stable,'Stability decision differs from underlying episodes')
    check_adoption(freeze,ds,vs,stability,new_ids)
    reproduction=read(out/'baseline_reproduction.json');require(reproduction['status']=='PASS','Baseline reproduction failed')
    verify_hash_map(ROOT,reproduction['previous_results_sha256'])
    require(reproduction['episodes']==len(pre),'Baseline proof has wrong episode count')
    old=pd.concat([csv(parent/'development.csv'),csv(parent/'validation.csv')]);old=old[old.candidate_id.eq('x0352_daily_baseline')]
    require(reproduction['columns']==['episode_return','episode_max_drawdown','episode_turnover','measured_pass','complete_period','raw_rule_breach_days','simulated_warning_days'],'Baseline parity columns weakened')
    columns=['episode_id']+reproduction['columns']
    same_frame(tables['baseline_reproduction'][columns],old[columns],['episode_id'],'Baseline reproduction')
    for trial in all_specs:
        cfg=read(out/'configs'/(trial['candidate_id']+'.json'))
        from scripts.run_24d import _candidate_config
        require(cfg==_candidate_config(trial),'Frozen factory config differs from declared candidate')
        require(cfg['full_tuning_params']==trial['params'],'Parameters/config mismatch')
        require(cfg['initial_cash']==1e9 and cfg['commission']==.001425 and cfg['sell_tax']==.003,'Costs/capital changed')
        require(cfg['use_4h'] is False and cfg['execution']=='open_proxy','Execution assumptions changed')
    package=read(out/'competition_24d_candidate.json')
    require(package['selection']==freeze and package['config_sha256']==digest(package['config']),'Frozen package mismatch')
    selected=freeze['candidate_id'] or 'x0352_daily_baseline'
    require(package['config']==read(out/'configs'/(selected+'.json')),'Package is not selected config')
    delivery=out/'delivery_manifest.json'
    if delivery.exists():
        seal=read(delivery);require(seal['result_manifest_sha256']==file_hash(out/'result_manifest.json'),'Delivery/result mismatch')
        verify_hash_map(ROOT,seal['files'])
    return dict(status='PASS',candidate_count=len(candidates),groups=groups,attempts=attempts,holdout_ledgers_rebuilt=rebuilt,
        development_episodes=len(dev),validation_episodes=len(val),decision=freeze['status'],formal_status='BLOCK_SUBMISSION',
        result_manifest_sha256=file_hash(out/'result_manifest.json'),verifier_sha256=file_hash(Path(__file__)))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'outputs/24d_round3')
    parser.add_argument('--rebuild',action='store_true');parser.add_argument('--write',action='store_true')
    args=parser.parse_args();result=verify(args.output,args.rebuild)
    if args.write:atomic_json(args.output/'verification.json',result)
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
