#!/usr/bin/env python3
"""Third bounded daily-parameter search; previous sealed studies remain immutable."""
from __future__ import annotations
import argparse
import itertools
import json
import os
from pathlib import Path
import sys
import traceback
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import run_24d as engine
from scripts.tune_24d import BASE,SPACE,EMA,MACD,spec,unique,rank,eligible,screen_registry
from scripts.supplement_24d import registry as recent_registry

STUDY=ROOT/'config/24d_round3_study.json'
PARENT=ROOT/'outputs/24d_expansion'
ORIGINAL=ROOT/'outputs/24d_tuning'
OUTPUT=ROOT/'outputs/24d_round3'


def generate_candidates():
    previous=[]
    for directory in [ORIGINAL,PARENT]:
        previous.extend(json.loads((directory/'candidates.json').read_text()))
    lookup={p['candidate_id']:p for p in previous}
    if len(lookup)!=575:
        raise ValueError('Expected exactly 575 distinct prior candidate IDs')
    ordered=pd.read_csv(PARENT/'development_ranking.csv')
    prior_ids={p['candidate_id'] for p in json.loads((PARENT/'candidates.json').read_text())}
    leader=ordered[ordered.candidate_id.isin(prior_ids)].candidate_id.iloc[0]
    seen=set(lookup); candidates=[]; memberships=[]
    rng=np.random.default_rng(2409202603)
    cells=list(itertools.product(SPACE['target_count'],SPACE['max_replacements_per_day']))
    for family in ['uniform','stratified_entry_replacement']:
        count=0
        while count<256:
            params={**BASE,**{key:values[int(rng.integers(len(values)))] for key,values in SPACE.items()}}
            if params['return_short']>=params['return_long']:continue
            params['ema_fast'],params['ema_slow']=EMA[int(rng.integers(len(EMA)))]
            params['macd_fast'],params['macd_slow'],params['macd_signal']=MACD[int(rng.integers(len(MACD)))]
            if family=='stratified_entry_replacement':
                params['target_count'],params['max_replacements_per_day']=cells[count%len(cells)]
                # Alternate entry thresholds within each structural cell; every
                # declared level is visited, with random remaining parameters.
                cycle=count//len(cells)
                for offset,key in enumerate(['volume_low','volume_high','one_day_chase_return','volatility_spike_ratio','cash_guard_ratio']):
                    values=SPACE[key]
                    params[key]=values[(cycle+count%len(cells)+offset)%len(values)]
            trial=spec(params)
            if trial['candidate_id'] in seen:continue
            seen.add(trial['candidate_id']);candidates.append(trial)
            memberships.append(dict(candidate_id=trial['candidate_id'],family=family,parent_id=None,
                target_count=params['target_count'],max_replacements_per_day=params['max_replacements_per_day']));count+=1
    references=unique([spec(),lookup[leader]])
    return candidates,references,pd.DataFrame(memberships)


def context(output):
    ctx=engine.make_context(output,ROOT/'data/yahoo_daily/v3_20260923',STUDY)
    files=[Path(__file__),ROOT/'scripts/tune_24d.py',ROOT/'scripts/supplement_24d.py',ROOT/'docs/v3_tuning.md']
    inputs=[ORIGINAL/'candidates.json',PARENT/'candidates.json',PARENT/'development_ranking.csv']
    guard=dict(parent_guard=ctx['guard_sha256'],implementation={str(p.relative_to(ROOT)):engine.file_hash(p) for p in files},
        parent_result_manifest_sha256=engine.file_hash(PARENT/'result_manifest.json'),
        original_result_manifest_sha256=engine.file_hash(ORIGINAL/'result_manifest.json'),
        selection_source_sha256={str(p.relative_to(ROOT)):engine.file_hash(p) for p in inputs})
    path=ctx['output']/'expansion_manifest.json'
    if path.exists():
        if json.loads(path.read_text())['guard']!=guard:raise ValueError('Expansion guard changed; refuse stale resume')
    else:engine.atomic_json(path,dict(created_at=engine.utc_now(),guard=guard,protocol=ctx['study']))
    ctx['guard_sha256']=engine.digest(guard)
    return ctx


def run(ctx,configs,episodes,phase,workers):
    engine.atomic_json(ctx['output']/'status.json',dict(status='RUNNING',phase=phase,pid=os.getpid(),updated_at=engine.utc_now()))
    return engine.run_groups(configs,episodes,phase,ctx,workers)


def execute(output=OUTPUT,workers=4):
    output=Path(output)
    if (output/'failure.json').exists():
        raise RuntimeError('Recorded engineering failure: diagnose and archive it before resume')
    # A finished release is read-only; resume is only for unfinished runs.
    if (output/'result_manifest.json').exists():
        sealed=json.loads((output/'result_manifest.json').read_text())
        for name,checksum in sealed['files'].items():
            if engine.file_hash(output/name)!=checksum:raise ValueError('Finished artifact changed: '+name)
        print('COMPLETE; use the verifier or a separate output directory.',flush=True);return
    ctx=context(output);out=ctx['output']; candidates,references,memberships=generate_candidates()
    library={c['candidate_id']:c for c in candidates+references}
    engine.atomic_json(out/'candidates.json',candidates);engine.atomic_json(out/'references.json',references)
    engine.atomic_frame(out/'phase_memberships.csv',memberships)
    registry=engine.make_registry(ctx['session_dates'],ctx['study']);monthly=registry[registry.kind.eq('monthly')]
    dev=monthly[monthly.split.eq('development')];val=monthly[monthly.split.eq('validation')]
    screen=screen_registry(dev);bottleneck=dev[dev.episode_id.eq('monthly_2015-08-03')]
    assert len(bottleneck)==1 and len(dev)==107 and len(val)==47
    engine.atomic_frame(out/'episode_registry.csv',registry);engine.atomic_frame(out/'screen_registry.csv',screen)
    engine.atomic_frame(out/'bottleneck_registry.csv',bottleneck)
    baseline=run(ctx,[spec()],pd.concat([dev,val]),'baseline_reproduction',workers)
    previous=pd.concat([pd.read_csv(PARENT/(phase+'.csv'),float_precision='round_trip') for phase in ['development','validation']])
    previous=previous[previous.candidate_id.eq('x0352_daily_baseline')].set_index('episode_id').sort_index()
    actual=baseline.set_index('episode_id').sort_index()
    columns=['episode_return','episode_max_drawdown','episode_turnover','measured_pass','complete_period','raw_rule_breach_days','simulated_warning_days']
    pd.testing.assert_frame_equal(actual[columns],previous[columns],check_dtype=False,rtol=1e-12,atol=1e-12)
    engine.atomic_json(out/'baseline_reproduction.json',dict(status='PASS',episodes=len(baseline),columns=columns,
        previous_results_sha256={str((PARENT/(p+'.csv')).relative_to(ROOT)):engine.file_hash(PARENT/(p+'.csv')) for p in ['development','validation']}))
    hard=run(ctx,candidates+references,bottleneck,'bottleneck',workers)
    hardrank=rank(hard,candidates+references);engine.atomic_frame(out/'bottleneck_ranking.csv',hardrank)
    new_ids={c['candidate_id'] for c in candidates}
    screened=hardrank[hardrank.candidate_id.isin(new_ids)].candidate_id.head(12).tolist()
    previous_shortlists=json.loads((out/'shortlists.json').read_text()) if (out/'shortlists.json').exists() else {}
    shortlists=dict(created_at=previous_shortlists.get('created_at',engine.utc_now()),bottleneck_top12=screened)
    engine.atomic_json(out/'shortlists.json',shortlists)
    configs=unique(references+[library[c] for c in screened])
    rows=run(ctx,configs,screen,'screen',workers)
    sr=rank(rows,configs);engine.atomic_frame(out/'screen_ranking.csv',sr)
    shortlist=sr[sr.candidate_id.isin(new_ids)].candidate_id.head(6).tolist()
    shortlists['screen_top6']=shortlist;engine.atomic_json(out/'shortlists.json',shortlists)
    configs=unique(references+[library[c] for c in shortlist])
    development=run(ctx,configs,dev,'development',workers);validation=run(ctx,configs,val,'validation',workers)
    ds=rank(development,configs);vs=rank(validation,configs)
    engine.atomic_frame(out/'development_ranking.csv',ds);engine.atomic_frame(out/'validation_ranking.csv',vs)
    diagnostic=ds[ds.candidate_id.isin(new_ids)].candidate_id.iloc[0]
    admitted=set(eligible(ds).candidate_id)&set(eligible(vs).candidate_id)&new_ids
    baseline_val=vs[vs.candidate_id.eq('x0352_daily_baseline')].iloc[0]
    selected=None;stability=[]
    for row in vs[vs.candidate_id.isin(admitted)].itertuples():
        if row.median_24d_return<baseline_val.median_24d_return or row.p25_24d_return<baseline_val.p25_24d_return:continue
        center=library[row.candidate_id]
        neighbors=unique([spec({**center['params'],'momentum_weight':round(center['params']['momentum_weight']+delta,10)})
            for delta in [-.05,.05] if .4<=center['params']['momentum_weight']+delta<=.95])
        nr=run(ctx,neighbors,pd.concat([dev,val]),'stability_'+row.candidate_id,workers)
        stable=True
        for split,reference in [('development',ds),('validation',vs)]:
            ns=rank(nr[nr.split.eq(split)],neighbors);cr=reference[reference.candidate_id.eq(row.candidate_id)].iloc[0]
            stable &= len(eligible(ns))==len(neighbors) and bool((ns.median_24d_return>=cr.median_24d_return-.002).all() and (ns.p25_24d_return>=cr.p25_24d_return-.002).all())
        stability.append(dict(candidate_id=row.candidate_id,stable=bool(stable),neighbors=[n['candidate_id'] for n in neighbors]))
        if stable:selected=row.candidate_id;break
    engine.atomic_json(out/'stability.json',dict(status='NO_ELIGIBLE_CANDIDATE' if not admitted else 'EVALUATED',results=stability))
    inputs=['development.csv','validation.csv','shortlists.json','candidates.json','references.json','stability.json']
    freeze=dict(status='SELECTED_RESEARCH_ONLY' if selected else 'NO_ELIGIBLE_CANDIDATE',formal_status='BLOCK_SUBMISSION',
        candidate_id=selected,diagnostic_leader_id=diagnostic,prior_leader_id=references[-1]['candidate_id'],
        candidate_count=len(candidates),previous_candidate_count=575,prior_holdout_exposure=True,
        training_period=ctx['study']['splits']['development'],validation_period=ctx['study']['splits']['validation'],
        stop_reason='BOUNDED_STUDY_COMPLETE' if selected else 'NO_ELIGIBLE_CANDIDATE',
        code_commit=json.loads((out/'study_manifest.json').read_text())['git_head'],
        objective=ctx['study']['objective'],selection_input_sha256={name:engine.file_hash(out/name) for name in inputs})
    path=out/'final_selection.json'
    if path.exists():
        saved=json.loads(path.read_text())
        if {k:v for k,v in saved.items() if k!='frozen_at'}!=freeze:raise ValueError('Frozen decision changed')
        freeze=saved
    else:freeze['frozen_at']=engine.utc_now();engine.atomic_json(path,freeze)
    cfg=engine._candidate_config(library[selected] if selected else spec())
    engine.atomic_json(out/'competition_24d_candidate.json',dict(config=cfg,selection=freeze,config_sha256=engine.digest(cfg)))
    frozen=unique(references+[library[diagnostic]]+([library[selected]] if selected else []))
    run(ctx,frozen,monthly[monthly.split.eq('holdout')],'holdout',workers)
    recent=recent_registry(ctx['session_dates'],'2025-01-01','2026-09-30');recent['split']='recent'
    run(ctx,frozen,recent,'recent',workers)
    seasonal=registry[registry.kind.eq('oct_nov')].copy();seasonal['split']='seasonal'
    run(ctx,frozen,seasonal,'seasonal',workers)
    rolling=recent_registry(ctx['session_dates'],'2025-01-01','2026-09-30','rolling').tail(126).copy();rolling['split']='recent_rolling'
    run(ctx,unique([spec(),library[diagnostic]]+([library[selected]] if selected else [])),rolling,'rolling_recent',workers)
    excluded={'run.log','status.json','result_manifest.json','verification.json','delivery_manifest.json'}
    files=sorted(p for p in out.rglob('*') if p.is_file() and p.name not in excluded)
    engine.atomic_json(out/'result_manifest.json',dict(status='COMPLETE',formal_status='BLOCK_SUBMISSION',frozen_selection_sha256=engine.file_hash(path),
        files={str(p.relative_to(out)):engine.file_hash(p) for p in files}))
    engine.atomic_json(out/'status.json',dict(status='COMPLETE',decision=freeze['status'],updated_at=engine.utc_now(),pid=os.getpid()))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=OUTPUT);p.add_argument('--workers',type=int,default=4)
    a=p.parse_args()
    try:execute(a.output,a.workers)
    except Exception:
        if not (a.output/'failure.json').exists():
            engine.atomic_json(a.output/'failure.json',dict(status='ENGINEERING_FAILURE',at=engine.utc_now(),traceback=traceback.format_exc()))
        raise
if __name__=='__main__':main()
