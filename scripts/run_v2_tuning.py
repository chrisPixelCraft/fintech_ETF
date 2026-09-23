"""Frozen, matched A/B/C tuning with a continuous-ledger retrospective walk-forward."""
from pathlib import Path
import argparse, copy, hashlib, json, os, platform, shutil, sys, time, traceback
from datetime import datetime, timezone
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from src.backtest import aggregate_four_hour, save_result, score_candidates
from src.tuning_features import FeatureCache, IsolatedEngine, normalized_config

CTX={}
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def dump(p,value):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    temp=p.with_suffix(p.suffix+'.tmp');temp.write_text(json.dumps(value,indent=2,ensure_ascii=False,default=str)+'\n');temp.replace(p)

def trial_config(base, study, trial, strategy):
    c=copy.deepcopy(base);p=trial['params']
    for name in ['target_count','replacement_margin','max_replacements_per_day','volatility_spike_ratio','one_day_chase_return','volume_low','volume_high','four_hour_mode']:c[name]=p[name]
    c['score_weights']=dict(zip(['return20','return50','volume','macd','trend','long_trend'],study['score_profiles'][p['score_profile']]))
    c['feature_presets']={k:p[k] for k in ['returns','ema','macd']}
    c['strategy_id']=strategy+'_'+trial['candidate_id'];c['tuning_candidate_id']=trial['candidate_id'];c['tuning_params']=p
    c['study_policy']={'parameter_search':True,'historical_period_is_development':True,'official_live_submission':'BLOCK_IF_UNKNOWN'}
    return normalized_config(c)

def period_metrics(eq,initial=1e9):
    eq=eq.copy().fillna('');nav=np.r_[initial,eq.economic_nav.to_numpy(float)]
    r=nav[1:]/nav[:-1]-1
    hard=eq.cash_ratio.astype(float).ge(.25)|eq.active_cap_breaches.ne('')|eq.overdue_passive_caps.ne('')
    return dict(economic_total_return=float(nav[-1]/initial-1),economic_max_drawdown=float(-(nav/np.maximum.accumulate(nav)-1).min()),
                annualized_volatility=float(np.std(r,ddof=1)*np.sqrt(252)) if len(r)>1 else 0,
                sharpe_zero_rf=float(np.mean(r)/np.std(r,ddof=1)*np.sqrt(252)) if len(r)>1 and np.std(r,ddof=1)>0 else 0,
                hard_breach_days=int(hard.sum()),infeasible_executed_days=int(eq.executed_plan.astype(str).str.startswith('INFEASIBLE').sum()),
                raw_rule_breach_days=int(eq.violations.ne('').sum()),negative_cash_days=int(eq.cash.astype(float).lt(-1e-6).sum()),
                trade_days=int(eq.traded_notional.astype(float).gt(0).sum()),costs=float(eq.costs.astype(float).sum()),
                turnover_two_way=float(eq.turnover.astype(float).sum()),sessions=len(eq),final_economic_nav=float(nav[-1]))

def monthly_rows(eq,initial=1e9):
    rows=[];previous=initial
    for month,group in eq.groupby(pd.to_datetime(eq.date).dt.to_period('M')):
        s=period_metrics(group,previous);rows.append({'month':str(month),'economic_return':s['economic_total_return'],**s});previous=group.economic_nav.iloc[-1]
    return rows

def setup_context():
    from src.tuning_signals import TuningSignals
    daily=pd.read_csv(ROOT/'data/v2/market_daily.csv');hourly=pd.read_csv(ROOT/'data/extended/processed/hourly_canonical.csv')
    uni=pd.read_csv(ROOT/'data/extended/processed/universe_20241231.csv');uni['known_at']=uni.known_at_assumption
    bars=aggregate_four_hour(hourly)
    cache=FeatureCache(daily,bars)
    base=json.loads((ROOT/'config/strategy_v2.json').read_text());study=json.loads((ROOT/'config/v2_tuning_study.json').read_text())
    signals=TuningSignals(calendar_path=ROOT/'data/v2/market_daily.csv',history_path=ROOT/'data/sector/industry_history.csv',index_path=ROOT/'data/sector/sector_index_daily.csv')
    # Per-date market state is independent of trial weights. Cache only causal data.
    dates=sorted(d for d in daily.date.unique() if '2024-12-31'<=d<=base['end'])
    signals.prewarm(dates,sorted(uni.symbol))
    CTX.update(daily=daily,bars=bars,universe=uni,cache=cache,signals=signals,base=base,study=study)

def transform_for(strategy,config):
    p=config['tuning_params']
    if strategy=='A':return None
    return CTX['signals'].make_callback(strategy,dict(target_count=config['target_count'],min_count=config['min_count'],sector_top_fraction=p['sector_top_fraction'],
            sector_short_weight=p['sector_short_weight'],sector_fallback_mode=p['sector_fallback_mode'],c_alpha=p['c_alpha']))

def run_candidate(strategy,trial,output):
    started=time.monotonic();folder=output/'trials'/strategy/trial['candidate_id']
    cfg=trial_config(CTX['base'],CTX['study'],trial,strategy)
    try:
        result=IsolatedEngine(CTX['cache']).run_v2(CTX['daily'],CTX['universe'],cfg,CTX['bars'],signal_transform=transform_for(strategy,cfg))
        if len(result['holdings'].query("symbol == '2888.TW' and date >= '2025-07-24'")):raise ValueError('Unsupported multi-security merger held')
        eq=result['equity'];metrics={**result['metrics'],**period_metrics(eq)}
        metrics.update(status='COMPLETE',strategy=strategy,candidate_id=trial['candidate_id'],design=trial['design'],varied=trial['varied'],elapsed_seconds=time.monotonic()-started)
        if not np.isfinite(eq.economic_nav.to_numpy()).all():raise ValueError('Nonfinite accounting')
        folder.mkdir(parents=True,exist_ok=True);eq.to_csv(folder/'equity.csv',index=False)
        result['trades'].to_csv(folder/'trades.csv',index=False);result['orders'].to_csv(folder/'orders.csv',index=False)
        dump(folder/'config.json',cfg);dump(folder/'metrics.json',metrics)
        pd.DataFrame(monthly_rows(eq)).to_csv(folder/'monthly.csv',index=False)
        if trial['candidate_id']=='p000':save_result(result,output/'baseline_replay'/strategy)
        return {**metrics,**trial['params'],'path':str(folder.relative_to(output))}
    except Exception as e:
        dump(folder/'failure.json',dict(error=str(e),traceback=traceback.format_exc(),config=cfg))
        return dict(strategy=strategy,candidate_id=trial['candidate_id'],status='FAILED',error=str(e),**trial['params'],path=str(folder.relative_to(output)))

def ranking(m):
    return (m['negative_cash_days']>0,m['hard_breach_days'],m['infeasible_executed_days'],-m['economic_total_return'],m['economic_max_drawdown'],m['candidate_id'])

def selection(output,summary):
    schedules=[];expost={};cutoff_scores=[]
    calendar=sorted(CTX['daily'].date.unique())
    for strategy in ['A','B','C']:
        rows=summary[(summary.strategy==strategy)&summary.status.eq('COMPLETE')].to_dict('records')
        valid=[r for r in rows if r['negative_cash_days']==0]
        if not valid:raise ValueError('No valid full-history candidate')
        winner=min(valid,key=ranking);expost[strategy]=winner['candidate_id']
        for cutoff in CTX['study']['cutoffs']:
            scores=[]
            for row in rows:
                eq=pd.read_csv(output/row['path']/'equity.csv');eq=eq[eq.date<=cutoff]
                m={**period_metrics(eq),'candidate_id':row['candidate_id'],'strategy':strategy,'selection_cutoff':cutoff}
                scores.append(m);cutoff_scores.append(m)
            valid=[r for r in scores if r['negative_cash_days']==0]
            if not valid:raise ValueError('No valid prefix candidate')
            selected=min(valid,key=ranking);trade=next(d for d in calendar if d>cutoff)
            schedules.append(dict(strategy=strategy,selection_cutoff=cutoff,effective_signal_date=cutoff,effective_trade_date=trade,candidate_id=selected['candidate_id'],selection_scope='PAST_ONLY_DEVELOPMENT_REPLAY'))
    pd.DataFrame(schedules).to_csv(output/'schedule.csv',index=False)
    pd.DataFrame(cutoff_scores).to_csv(output/'selection_scores.csv',index=False)
    return schedules,expost

def run_walk_forward(strategy,schedules,output):
    base_trial=CTX['study']['candidates'][0];base=trial_config(CTX['base'],CTX['study'],base_trial,strategy)
    lookup={t['candidate_id']:t for t in CTX['study']['candidates']}
    configs={row['effective_signal_date']:trial_config(CTX['base'],CTX['study'],lookup[row['candidate_id']],strategy) for row in schedules if row['strategy']==strategy}
    def resolve(day):
        eligible=[date for date in configs if pd.Timestamp(date)<=pd.Timestamp(day)]
        return configs[max(eligible)] if eligible else base
    adapter=IsolatedEngine(CTX['cache']);engine=adapter.module
    engine.compute_features=lambda daily,c,four_hour: CTX['cache'].frame_for_schedule(configs,base,daily,four_hour)
    original_score=engine.score_candidates
    def scheduled_score(rows,c):
        chosen=resolve(rows.date.iloc[0]);c.update(copy.deepcopy(chosen))
        return original_score(rows,c)
    engine.score_candidates=scheduled_score
    callbacks={cfg['tuning_candidate_id']:transform_for(strategy,cfg) for cfg in [base,*configs.values()]}
    def scheduled_transform(day,ranked):
        cfg=resolve(day);callback=callbacks[cfg['tuning_candidate_id']]
        transformed,meta=callback(day,ranked) if callback else (ranked.copy(),{})
        meta.update(tuning_candidate_id=cfg['tuning_candidate_id'],tuning_config_sha256=hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest())
        return transformed,meta
    result=engine.run_v2(CTX['daily'],CTX['universe'],base,CTX['bars'],signal_transform=scheduled_transform)
    result['config']=base;result['config']['walk_forward_schedule']=configs
    if len(result['holdings'].query("symbol == '2888.TW' and date >= '2025-07-24'")):raise ValueError('Unsupported merger in walk forward')
    save_result(result,output/'walk_forward'/strategy)
    return result

def finish(output,manifest):
    summaries=[]
    for t in manifest['trials']:
        path=output/t['path']
        if (path/'metrics.json').exists():
            metric=json.loads((path/'metrics.json').read_text());cfg=json.loads((path/'config.json').read_text())
            summaries.append({**metric,**cfg['tuning_params'],'path':t['path']})
        else:
            failure=json.loads((path/'failure.json').read_text())
            summaries.append(dict(strategy=t['strategy'],candidate_id=t['candidate_id'],status='FAILED',error=failure['error'],**failure['config']['tuning_params'],path=t['path']))
    summary=pd.DataFrame(summaries);summary.to_csv(output/'trial_summary.csv',index=False)
    if summary.status.eq('FAILED').any():raise ValueError('Incomplete candidate pool: recover failed runs before selection')
    schedules,expost=selection(output,summary)
    comparison=[];monthly=[];curves={}
    for strategy in ['A','B','C']:
        wf=run_walk_forward(strategy,schedules,output)
        trial=next(t for t in CTX['study']['candidates'] if t['candidate_id']==expost[strategy])
        cfg=trial_config(CTX['base'],CTX['study'],trial,strategy)
        best=IsolatedEngine(CTX['cache']).run_v2(CTX['daily'],CTX['universe'],cfg,CTX['bars'],signal_transform=transform_for(strategy,cfg))
        save_result(best,output/'ex_post_best'/strategy)
        for role,eq,ident in [('INCUMBENT',pd.read_csv(output/'baseline_replay'/strategy/'equity.csv'),'p000'),('EX_POST_BEST',best['equity'],expost[strategy]),('WALK_FORWARD',wf['equity'],'schedule')]:
            for scope in ['FULL_DEVELOPMENT','AFTER_SELECTION_START']:
                part=eq if scope=='FULL_DEVELOPMENT' else eq[eq.date>'2025-06-30']
                initial=1e9 if scope=='FULL_DEVELOPMENT' else float(eq[eq.date<='2025-06-30'].economic_nav.iloc[-1])
                comparison.append(dict(strategy=strategy,role=role,candidate_id=ident,scope=scope,**period_metrics(part,initial)))
            monthly.extend(dict(strategy=strategy,role=role,**m) for m in monthly_rows(eq))
            curves[strategy+'_'+role]=pd.Series(eq.economic_nav.to_numpy(),index=eq.date)
    pd.DataFrame(comparison).to_csv(output/'comparison.csv',index=False)
    pd.DataFrame(monthly).to_csv(output/'monthly_comparison.csv',index=False)
    pd.DataFrame(curves).rename_axis('date').to_csv(output/'nav_comparison.csv')
    manifest.update(outputs_complete=True,completed_at=datetime.now(timezone.utc).isoformat(),ex_post_winners=expost)
    for path,expected in manifest['hashes'].items():
        if sha(ROOT/path)!=expected:raise ValueError('Frozen input changed: '+path)
    dump(output/'tuning_manifest.json',manifest)
    dump(output/'status.json',dict(status='COMPLETE_PENDING_AUDIT',completed_trials=len(summary),failed_trials=int(summary.status.eq('FAILED').sum())))

def run(output,workers=3,resume=False):
    output.mkdir(parents=True,exist_ok=True);manifest_path=output/'tuning_manifest.json'
    if manifest_path.exists() and not resume:raise FileExistsError('Existing study preserved; use --resume for incomplete same study')
    if resume:
        manifest=json.loads(manifest_path.read_text())
        if manifest.get('outputs_complete'):raise ValueError('Already complete')
        for path,expected in manifest['hashes'].items():
            if sha(ROOT/path)!=expected:raise ValueError('Changed resume dependency: '+path)
    else:
        old=json.loads((ROOT/'outputs/backtest_v2_2025_to_now/provenance.json').read_text())
        paths=list(old['hashes'])+['config/v2_tuning_study.json','scripts/prepare_v2_tuning.py','scripts/run_v2_tuning.py','src/tuning_features.py','src/tuning_signals.py']
        hashes={p:sha(ROOT/p) for p in dict.fromkeys(paths)}
        for path in hashes:
            target=output/'input_snapshot'/path;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/path,target)
        study=json.loads((ROOT/'config/v2_tuning_study.json').read_text())
        manifest=dict(study_id=study['study_id'],config=json.loads((ROOT/'config/strategy_v2.json').read_text()),study=study,
            hashes=hashes,started_at=datetime.now(timezone.utc).isoformat(),python=platform.python_version(),pid=os.getpid(),command=sys.argv,
            historical_period_is_development=True,outputs_complete=False,expected_trials=192,
            trials=[dict(strategy=s,candidate_id=t['candidate_id'],path=f"trials/{s}/{t['candidate_id']}") for s in ['A','B','C'] for t in study['candidates']],
            baseline_replay={s:f'baseline_replay/{s}' for s in ['A','B','C']},walk_forward={s:f'walk_forward/{s}' for s in ['A','B','C']},ex_post_best={s:f'ex_post_best/{s}' for s in ['A','B','C']})
        dump(manifest_path,manifest)
    print('Preparing causal shared caches',flush=True);setup_context();print('Caches ready',flush=True)
    candidates={t['candidate_id']:t for t in CTX['study']['candidates']}
    jobs=[t for t in manifest['trials'] if not (output/t['path']/'metrics.json').exists() and not (output/t['path']/'failure.json').exists()]
    completed=192-len(jobs)
    with ProcessPoolExecutor(max_workers=workers,mp_context=mp.get_context('fork')) as pool:
        futures={pool.submit(run_candidate,t['strategy'],candidates[t['candidate_id']],output):t for t in jobs}
        for future in as_completed(futures):
            row=future.result();completed+=1
            dump(output/'status.json',dict(status='RUNNING',completed_trials=completed,total_trials=192,last_strategy=row['strategy'],last_candidate=row['candidate_id'],last_status=row['status']))
            print(f"{completed}/192 {row['strategy']} {row['candidate_id']} {row['status']} elapsed={row.get('elapsed_seconds',0):.1f}s",flush=True)
    print('Selecting past-only schedules and replaying continuous ledgers',flush=True)
    finish(output,manifest)
    print('COMPLETE_PENDING_AUDIT',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',default='outputs/v2_abc_tuning_20260922');p.add_argument('--workers',type=int,default=3);p.add_argument('--resume',action='store_true');a=p.parse_args()
    run(ROOT/a.output,a.workers,a.resume)
