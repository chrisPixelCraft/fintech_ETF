"""Run every frozen second-round candidate, retaining ineligible diagnostics."""
from pathlib import Path
import argparse, copy, json, os, platform, shutil, sys, time, traceback
from datetime import datetime, timezone
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import pandas as pd
from src import tuning_2nd as t
from src.backtest import save_result
from scripts.run_v2_tuning import dump, sha, monthly_rows

CTX={}

def trial_run(strategy,trial,guard,output,full=False):
    folder=output/'trials'/strategy/trial['candidate_id']
    cfg=t.config_for(CTX,trial,strategy,guard)
    start=time.monotonic()
    try:
        result=t.run_model(CTX,strategy,cfg)
        m={**result['metrics'],'status':'COMPLETE','strategy':strategy,'candidate_id':trial['candidate_id'],
           'elapsed_seconds':time.monotonic()-start,'guard_ratio':guard,'path':str(folder.relative_to(output))}
        folder.mkdir(parents=True,exist_ok=True)
        if full: save_result(result,folder)
        else:
            for key in ('equity','trades','orders','warnings'):
                result[key].to_csv(folder/f'{key}.csv',index=False)
        dump(folder/'config.json',cfg)
        pd.DataFrame(monthly_rows(result['equity'])).to_csv(folder/'monthly.csv',index=False)
        dump(folder/'metrics.json',m)
        return {**m,**trial['params']}
    except Exception as exc:
        dump(folder/'failure.json',dict(error=str(exc),traceback=traceback.format_exc(),config=cfg))
        return dict(status='FAILED',strategy=strategy,candidate_id=trial['candidate_id'],error=str(exc))


def dependencies(track):
    old=json.loads((ROOT/'outputs/v2_abc_tuning_20260922/tuning_manifest.json').read_text())
    paths=list(old['hashes'])+['src/tuning_2nd.py','src/compliance_planner.py','scripts/run_tuning_2nd.py','config/tuning_2nd_study.json']
    if track=='official_ex_post':
        paths += [str(p.relative_to(ROOT)) for p in (ROOT/'data/tuning_2nd/official_universe/processed').glob('*') if p.is_file()]
    return list(dict.fromkeys(paths))


def final_replays(output,study,summary):
    selections={};results={};comparison=[];monthly=[];curves={}
    for strategy in ['A','B','C']:
        winner=t.choose(summary[summary.strategy.eq(strategy)].to_dict('records'))
        selections[strategy]=dict(status='SELECTED_ZERO_MEASURED_HARD' if winner else 'NO_ELIGIBLE_WINNER',
                                  candidate_id=winner['candidate_id'] if winner else None,
                                  formal_compliance='UNKNOWN_BLOCK_SUBMISSION')
        if winner is None:continue
        trial=next(x for x in study['candidates'] if x['candidate_id']==winner['candidate_id'])
        cfg=t.config_for(CTX,trial,strategy,study['cash_guard_ratio'])
        result=t.run_model(CTX,strategy,cfg)
        eq=pd.read_csv(output/winner['path']/'equity.csv')
        pd.testing.assert_frame_equal(eq.fillna(''),result['equity'].fillna(''),check_dtype=False,rtol=1e-12,atol=1e-5)
        if result['metrics']['measured_hard_breach_days']!=0:raise ValueError('Winner lost eligibility in replay')
        result['metrics']['candidate_id']=winner['candidate_id']
        save_result(result,output/'final'/strategy);results[strategy]=result
    for name in ('v1_matched','0050'):
        cfg=copy.deepcopy(CTX['base'])
        cfg.update(strategy_id=name,allocation_mode='full' if name=='v1_matched' else 'local',
                   ex_post_fixed_universe=CTX['track']=='official_ex_post',universe_mode=CTX['track'])
        result=t.run_model(CTX,name,cfg)
        result['metrics']['candidate_id']='FIXED_BENCHMARK'
        save_result(result,output/'final'/name);results[name]=result
    if len({tuple(r['equity'].date) for r in results.values()})!=1:raise ValueError('Comparison calendar mismatch')
    for name,result in results.items():
        comparison.append(dict(model=name,**result['metrics']))
        monthly += [dict(model=name,**row) for row in monthly_rows(result['equity'])]
        eq=result['equity']
        curves[name]=pd.Series([1e9,*eq.economic_nav],index=['2024-12-31',*eq.date])
    pd.DataFrame(comparison).to_csv(output/'comparison.csv',index=False)
    pd.DataFrame(monthly).to_csv(output/'monthly_comparison.csv',index=False)
    pd.DataFrame(curves).rename_axis('date').to_csv(output/'nav_comparison.csv')
    dump(output/'selection.json',selections)
    return selections


def run(args):
    global CTX
    output=ROOT/args.output
    output.mkdir(parents=True,exist_ok=True)
    print('Preparing '+args.track,flush=True);CTX=t.context(args.track);print('Context ready',flush=True)
    if args.pilot:
        jobs=[]
        for s,p in [('A','p005'),('B','p005'),('C','p006')]:
            for guard in [.08,.12,.16]:
                trial=copy.deepcopy(next(x for x in CTX['study']['candidates'] if x['candidate_id']==p))
                trial['candidate_id']=p+'_g'+str(int(guard*100));jobs.append((s,trial,guard))
        for s,p in [('A','p028'),('B','p014'),('C','p033')]:
            trial=copy.deepcopy(next(x for x in CTX['study']['candidates'] if x['candidate_id']==p));jobs.append((s,trial,.12))
        study=None
    else:
        study=json.loads((ROOT/'config/tuning_2nd_study.json').read_text())
        if study['status'] != 'FROZEN':raise ValueError('Formal study must be frozen after correctness pilot')
        manifest_file=output/'manifest.json'
        if manifest_file.exists():
            if not args.resume:raise FileExistsError('Preserve old output; use --resume')
            manifest=json.loads(manifest_file.read_text())
            for p,h in manifest['hashes'].items():
                if sha(ROOT/p)!=h:raise ValueError('Changed frozen input: '+p)
        else:
            hashes={p:sha(ROOT/p) for p in dependencies(args.track)}
            for p in hashes:
                dest=output/'input_snapshot'/p;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/p,dest)
            manifest=dict(track=args.track,study=study,hashes=hashes,expected_trials=len(study['candidates'])*3,
                started_at=datetime.now(timezone.utc).isoformat(),pid=os.getpid(),command=sys.argv,python=platform.python_version(),
                official_compliance='UNKNOWN_BLOCK_SUBMISSION',outputs_complete=False,
                universe_publication_dates=sorted(CTX['universe'].known_at.astype(str).unique()))
            dump(manifest_file,manifest)
        jobs=[(s,trial,study['cash_guard_ratio']) for s in ['A','B','C'] for trial in study['candidates']]
    rows=[];pending=[]
    for s,trial,g in jobs:
        path=output/'trials'/s/trial['candidate_id']/'metrics.json'
        if path.exists() and args.resume and not (path.parent/'failure.json').exists():
            rows.append({**json.loads(path.read_text()),**trial['params']})
        else:pending.append((s,trial,g))
    with ProcessPoolExecutor(max_workers=args.workers,mp_context=mp.get_context('fork')) as pool:
        futures=[pool.submit(trial_run,s,trial,g,output,args.pilot) for s,trial,g in pending]
        for future in as_completed(futures):
            row=future.result();rows.append(row)
            dump(output/'status.json',dict(status='RUNNING',completed=len(rows),expected=len(jobs),last=row['candidate_id']))
            print(f"{len(rows)}/{len(jobs)} {row['strategy']} {row['candidate_id']} {row['status']} hard={row.get('measured_hard_breach_days')} return={row.get('economic_total_return')}",flush=True)
    summary=pd.DataFrame(rows).sort_values(['strategy','candidate_id']);summary.to_csv(output/'trial_summary.csv',index=False)
    if summary.status.ne('COMPLETE').any():raise ValueError('Failed candidates: do not select from partial results')
    if args.pilot:
        dump(output/'status.json',dict(status='PILOT_COMPLETE',completed=len(rows)));return
    selections=final_replays(output,study,summary)
    for p,h in manifest['hashes'].items():
        if sha(ROOT/p)!=h:raise ValueError('Frozen dependency changed: '+p)
    manifest.update(outputs_complete=True,completed_at=datetime.now(timezone.utc).isoformat(),selection=selections)
    dump(output/'manifest.json',manifest)
    dump(output/'status.json',dict(status='COMPLETE_PENDING_AUDIT',completed=len(rows),expected=len(jobs)))
    print('COMPLETE_PENDING_AUDIT',flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--track',choices=t.TRACKS,default='historical_pit');p.add_argument('--output',required=True)
    p.add_argument('--workers',type=int,default=3);p.add_argument('--pilot',action='store_true');p.add_argument('--resume',action='store_true')
    run(p.parse_args())
