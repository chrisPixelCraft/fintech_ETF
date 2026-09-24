#!/usr/bin/env python3
"""Bounded staged tuning. Research diagnostics never bypass the adoption gate."""
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
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_24d as engine
from scripts.supplement_24d import registry as supplemental_registry
from src.strategy_24d import build_config

OUTPUT = ROOT/'outputs/24d_tuning'
STUDY = ROOT/'config/24d_tuning_study.json'
SPACE = dict(target_count=[20,22,25,28,30], max_replacements_per_day=[0,1,2,4],
    return_short=[3,5,7,10,15,20], return_long=[15,20,30,40,50,60],
    replacement_margin=[0.,.025,.05,.10,.20,.30],
    momentum_weight=[.40,.50,.55,.65,.75,.85,.95], long_return_fraction=[0.,.10,.20,.35,.50,.70],
    one_day_chase_return=[.03,.04,.055,.07,.085,.10], volatility_spike_ratio=[1.5,2.,2.5,3.,4.],
    volume_low=[.2,.5,.8,1.], volume_high=[2.,2.5,3.,5.,8.], cash_guard_ratio=[.08,.10,.12,.15,.18])
EMA = [(5,15),(5,20),(8,21),(10,30),(15,40),(20,50)]
MACD = [(5,13,4),(6,13,4),(8,21,5),(12,26,9),(16,35,9)]
BASE = build_config()['full_tuning_params']
RANK = ['compliance_pass_rate','valid_episode_rate','median_24d_return','p25_24d_return',
        'p10_24d_return','median_mdd','mean_24d_return','median_turnover','baseline_distance','candidate_id']
ASC = [False,False,False,False,False,True,False,True,True,True]


def spec(params=None):
    params = {**BASE, **(params or {})}
    if not params['max_replacements_per_day']:
        params['replacement_margin'] = BASE['replacement_margin']
    config = build_config(params)
    for key, values in SPACE.items():
        if not min(values) <= params[key] <= max(values):
            raise ValueError('Outside declared bound: '+key)
    if params['return_short'] >= params['return_long']:
        raise ValueError('Return horizons out of order')
    identifier = 'x0352_daily_baseline' if params == BASE else 'tune_'+engine.digest(params)[:12]
    return dict(candidate_id=identifier, params=params, source='v3_tuning')


def unique(specs):
    return list({s['candidate_id']:s for s in specs}.values())


def rank(rows, specs):
    result = engine.aggregate(rows)
    lookup = {s['candidate_id']:s for s in specs}
    result['baseline_distance'] = [sum(v != BASE[k] for k,v in lookup[c]['params'].items()) for c in result.candidate_id]
    return result.sort_values(RANK, ascending=ASC, na_position='last').reset_index(drop=True)


def eligible(summary):
    return summary.loc[summary.compliance_pass_rate.eq(1.) & summary.valid_episode_rate.eq(1.) & summary.failed.eq(0)]


def screen_registry(development):
    indices = np.linspace(0, len(development)-1, 12).round().astype(int)
    return development.iloc[indices].reset_index(drop=True)


def phase_specs(phase, parents):
    p = parents[0]['params']
    values = []
    if phase == 'A':
        values = [{**BASE,'target_count':n,'max_replacements_per_day':r} for n,r in itertools.product(SPACE['target_count'],SPACE['max_replacements_per_day'])]
    elif phase == 'B':
        values += [{**p,'return_short':a,'return_long':b} for a,b in itertools.product(SPACE['return_short'],SPACE['return_long']) if a < b]
        values += [{**p,'ema_fast':a,'ema_slow':b} for a,b in EMA]
        values += [{**p,'macd_fast':a,'macd_slow':b,'macd_signal':c} for a,b,c in MACD]
        for parent in parents[1:5]:
            for i,(a,b) in enumerate(EMA):
                f,s,g=MACD[i%len(MACD)]
                values.append({**parent['params'],'return_short':SPACE['return_short'][i],
                    'return_long':SPACE['return_long'][i], 'ema_fast':a,'ema_slow':b,
                    'macd_fast':f,'macd_slow':s,'macd_signal':g})
    elif phase == 'C':
        values = [{**p,'max_replacements_per_day':r,'replacement_margin':m} for r,m in itertools.product(SPACE['max_replacements_per_day'],SPACE['replacement_margin'])]
    elif phase == 'D':
        values = [{**p,'momentum_weight':m,'long_return_fraction':q} for m,q in itertools.product(SPACE['momentum_weight'],SPACE['long_return_fraction'])]
    elif phase == 'E':
        for key in ['one_day_chase_return','volatility_spike_ratio','volume_low','volume_high','cash_guard_ratio']:
            values += [{**p,key:v} for v in SPACE[key]]
    elif phase == 'F':
        for parent in parents[:5]:
            p=parent['params']
            neighbors=[]
            for key in ['return_short','return_long','momentum_weight']:
                ordered=sorted(SPACE[key],key=lambda x:(abs(x-p[key]),x))
                neighbors.append(ordered[:2])
            values += [{**p,'return_short':a,'return_long':b,'momentum_weight':m}
                for a,b,m in itertools.product(*neighbors) if a<b]
    elif phase == 'G':
        rng=np.random.default_rng(24092026)
        while len(unique([spec(v) for v in values])) < 48:
            q={**BASE,**{k:vs[int(rng.integers(len(vs)))] for k,vs in SPACE.items()}}
            if q['return_short'] >= q['return_long']:
                continue
            q['ema_fast'],q['ema_slow']=EMA[int(rng.integers(len(EMA)))]; q['macd_fast'],q['macd_slow'],q['macd_signal']=MACD[int(rng.integers(len(MACD)))]
            values.append(q)
    else:
        raise ValueError('Unknown phase')
    return unique([spec(v) for v in values])


def context(output=OUTPUT):
    ctx=engine.make_context(output, ROOT/'data/yahoo_daily/v3_20260923', STUDY)
    files=[Path(__file__),ROOT/'docs/v3_tuning.md', ROOT/'scripts/supplement_24d.py']
    guard=dict(parent_guard=ctx['guard_sha256'], implementation={str(p.relative_to(ROOT)):engine.file_hash(p) for p in files})
    path=ctx['output']/'tuning_manifest.json'
    if path.exists():
        if json.loads(path.read_text())['guard'] != guard:
            raise ValueError('Tuning implementation changed; use isolated output')
    else:
        engine.atomic_json(path,dict(created_at=engine.utc_now(),guard=guard, protocol=ctx['study']['tuning_protocol']))
    ctx['guard_sha256']=engine.digest(guard)
    return ctx


def run(ctx, specs, episodes, phase, workers):
    engine.atomic_json(ctx['output']/'status.json',dict(status='RUNNING',phase=phase,updated_at=engine.utc_now(),pid=os.getpid()))
    return engine.run_groups(specs,episodes,phase,ctx,workers)


def reproduce(ctx, monthly, workers):
    pre=monthly[monthly.split.isin(['development','validation'])]
    baseline=run(ctx,[spec()],pre,'baseline_reproduction',workers)
    old=pd.read_csv(ROOT/'outputs/24d_diagnostics/monthly_all_candidates.csv',float_precision='round_trip')
    old=old[(old.candidate_id=='x0352_daily_baseline') & old.episode_id.isin(pre.episode_id)].set_index('episode_id')
    new=baseline.set_index('episode_id').loc[old.index]
    if len(new)!=len(pre) or len(old)!=len(pre):
        raise ValueError('Incomplete baseline reproduction')
    columns=['episode_return','episode_max_drawdown','episode_turnover','measured_pass','complete_period','raw_rule_breach_days','simulated_warning_days']
    for column in columns:
        pd.testing.assert_series_equal(new[column],old[column],check_dtype=False,check_names=False,rtol=1e-12,atol=1e-12)
    engine.atomic_json(ctx['output']/'baseline_reproduction.json',dict(status='PASS',episodes=len(pre),columns=columns,
        previous_results_sha256=engine.file_hash(ROOT/'outputs/24d_diagnostics/monthly_all_candidates.csv'),
        evaluation_scope='FULL_NEW_DEVELOPMENT_AND_VALIDATION; holdout excluded before freeze'))
    return baseline


def execute(output=OUTPUT, workers=4):
    ctx=context(output); out=ctx['output']
    registry=engine.make_registry(ctx['session_dates'],ctx['study'])
    monthly=registry[registry.kind.eq('monthly')].copy()
    engine.atomic_frame(out/'episode_registry.csv',registry)
    dev=monthly[monthly.split.eq('development')]; val=monthly[monthly.split.eq('validation')]
    baseline=reproduce(ctx,monthly,workers)
    screen=screen_registry(dev); engine.atomic_frame(out/'screen_registry.csv',screen)
    library={spec()['candidate_id']:spec()}; memberships=[]
    screen_rows=[]; parents=[spec()]
    for phase in 'ABCDEFG':
        if phase=='F':
            staged=pd.concat(screen_rows,ignore_index=True).drop_duplicates(['candidate_id','episode_id'])
            ids=rank(staged,list(library.values())).candidate_id.head(5)
            parents=[library[c] for c in ids]
        candidates=phase_specs(phase,parents)
        library.update({s['candidate_id']:s for s in candidates})
        memberships.extend(dict(phase=phase,candidate_id=s['candidate_id']) for s in candidates)
        rows=run(ctx,candidates,screen,'screen_'+phase,workers)
        screen_rows.append(rows)
        ordered=rank(rows,candidates)
        engine.atomic_frame(out/f'phase_{phase}_ranking.csv',ordered)
        parents=[library[c] for c in ordered.candidate_id.head(5 if phase=='A' else 1)]
        engine.atomic_json(out/'candidates.json',list(library.values()))
        engine.atomic_frame(out/'phase_memberships.csv',pd.DataFrame(memberships))
    screens=pd.concat(screen_rows,ignore_index=True).drop_duplicates(['candidate_id','episode_id'])
    summary=rank(screens,list(library.values()))
    engine.atomic_frame(out/'screen_summary.csv',summary)
    membership=pd.DataFrame(memberships)
    methods={'staged':membership[membership.phase.isin(list('ABCDE'))].candidate_id.unique(),
        'local':membership[membership.phase.eq('F')].candidate_id.unique(),
        'global':membership[membership.phase.eq('G')].candidate_id.unique()}
    leaders={k:summary[summary.candidate_id.isin(ids)].iloc[0].candidate_id for k,ids in methods.items()}
    shortlist=list(dict.fromkeys([*leaders.values(),*summary.candidate_id.tolist()]))[:ctx['study']['validation_shortlist']]
    specs=unique([spec()]+[library[c] for c in shortlist])
    engine.atomic_json(out/'shortlist.json',dict(created_at=engine.utc_now(),candidate_ids=[s['candidate_id'] for s in specs],screen_diagnostic_leaders=leaders,
        reason='Common screening ranking; forced method representation; ineligible leaders remain diagnostics, never adopted.'))
    development=run(ctx,specs,dev,'development',workers)
    validation=run(ctx,specs,val,'validation',workers)
    ds=rank(development,specs); vs=rank(validation,specs)
    engine.atomic_frame(out/'development_ranking.csv',ds);engine.atomic_frame(out/'validation_ranking.csv',vs)
    for method,ids in methods.items():
        pool=ds[ds.candidate_id.isin(ids)]
        leaders[method]=pool.iloc[0].candidate_id if len(pool) else None
    admitted=set(eligible(ds).candidate_id)&set(eligible(vs).candidate_id)
    contenders=vs[vs.candidate_id.isin(admitted)]
    baseline_val=vs[vs.candidate_id.eq(spec()['candidate_id'])].iloc[0]
    selected=None; stability=[]
    for row in contenders.itertuples():
        if row.median_24d_return < baseline_val.median_24d_return or row.p25_24d_return < baseline_val.p25_24d_return:
            continue
        center=library[row.candidate_id]
        neighbors=unique([spec({**center['params'],'momentum_weight':round(center['params']['momentum_weight']+delta,10)})
            for delta in [-.05,.05] if .4<=center['params']['momentum_weight']+delta<=.95])
        nr=run(ctx,neighbors,pd.concat([dev,val]),'stability_'+row.candidate_id,workers)
        stable=True
        for split, ref in [('development',ds),('validation',vs)]:
            ns=rank(nr[nr.split.eq(split)],neighbors); cr=ref[ref.candidate_id.eq(row.candidate_id)].iloc[0]
            stable &= len(eligible(ns))==len(neighbors) and bool((ns.median_24d_return>=cr.median_24d_return-.002).all() and (ns.p25_24d_return>=cr.p25_24d_return-.002).all())
        stability.append(dict(candidate_id=row.candidate_id,stable=bool(stable),neighbors=[s['candidate_id'] for s in neighbors]))
        if stable:
            selected=row.candidate_id;break
    engine.atomic_json(out/'stability.json',dict(results=stability,status='NO_ELIGIBLE_CANDIDATE' if not len(contenders) else 'EVALUATED'))
    frozen_specs=unique([spec()]+[library[c] for c in leaders.values() if c]+([library[selected]] if selected else []))
    freeze=dict(status='SELECTED_RESEARCH_ONLY' if selected else 'NO_ELIGIBLE_CANDIDATE',formal_status='BLOCK_SUBMISSION',
        candidate_id=selected,reference_id=spec()['candidate_id'],diagnostic_method_leaders=leaders,
        candidate_count=len(library),training_period=ctx['study']['splits']['development'],validation_period=ctx['study']['splits']['validation'],
        objective='24D measured-feasible median / P25 / P10 / MDD / mean / turnover',
        code_commit=json.loads((out/'study_manifest.json').read_text())['git_head'],
        implementation_sha256=json.loads((out/'tuning_manifest.json').read_text())['guard']['implementation'],
        selection_input_sha256={p.name:engine.file_hash(p) for p in [out/'development.csv',out/'validation.csv',out/'stability.json']},
        prior_holdout_exposure=True,stop_reason='NO_ELIGIBLE_CANDIDATE' if not selected else 'BOUNDED_STUDY_COMPLETE_STABLE_PLATEAU')
    freeze_path=out/'final_selection.json'
    if freeze_path.exists():
        saved=json.loads(freeze_path.read_text()); expected={k:v for k,v in saved.items() if k!='frozen_at'}
        if expected!=engine.clean(freeze):raise ValueError('Frozen decision changed')
        freeze=saved
    else:
        freeze['frozen_at']=engine.utc_now();engine.atomic_json(freeze_path,freeze)
    # Immutable run-local copy. Canonical reference is unchanged if no eligible replacement exists.
    cfg=engine._candidate_config(library[selected] if selected else spec())
    engine.atomic_json(out/'competition_24d_candidate.json',dict(config=cfg,selection=freeze,config_sha256=engine.digest(cfg)))
    holdout=run(ctx,frozen_specs,monthly[monthly.split.eq('holdout')],'holdout',workers)
    recent=supplemental_registry(ctx['session_dates'],'2025-01-01','2026-09-30');recent['split']='recent'
    run(ctx,frozen_specs,recent,'recent',workers)
    seasonal=registry[registry.kind.eq('oct_nov')].copy();seasonal['split']='seasonal'
    run(ctx,frozen_specs,seasonal,'seasonal',workers)
    rolling=supplemental_registry(ctx['session_dates'],'2025-01-01','2026-09-30','rolling').tail(126).copy();rolling['split']='recent_rolling'
    run(ctx,unique([spec()]+([library[selected]] if selected else [])),rolling,'rolling_recent',workers)
    paths=[p for p in out.rglob('*') if p.is_file() and p.name not in ['run.log','status.json','result_manifest.json','verification.json']]
    engine.atomic_json(out/'result_manifest.json',dict(status='COMPLETE',frozen_selection_sha256=engine.file_hash(freeze_path),
        formal_status='BLOCK_SUBMISSION',files={str(p.relative_to(out)):engine.file_hash(p) for p in sorted(paths)}))
    engine.atomic_json(out/'status.json',dict(status='COMPLETE',updated_at=engine.utc_now(),pid=os.getpid(),decision=freeze['status']))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUTPUT);parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    try:execute(args.output,args.workers)
    except Exception:
        engine.atomic_json(args.output/'failure.json',dict(status='ENGINEERING_FAILURE',traceback=traceback.format_exc(),at=engine.utc_now()))
        raise
if __name__=='__main__':main()
