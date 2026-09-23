"""Resumable A-only study: frozen exploration, recorded local refinement, replays."""
from pathlib import Path
import argparse, copy, json, os, platform, shutil, sys, time, traceback
from datetime import datetime, timezone
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from src import tuning_a_deep as deep
from scripts.run_v2_tuning import dump, sha, monthly_rows
from scripts.run_tuning_2nd import dependencies as old_dependencies
from scripts.prepare_a_deep import apply_axis, canonical
from src.backtest import save_result

CTX={}


def trial_run(trial, output, full=False):
    folder=output/'trials'/trial['candidate_id'];folder.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();config=deep.config_for(CTX,trial)
    try:
        result=deep.run_model(CTX,config)
        if full:save_result(result,folder)
        else:
            for key in ['equity','orders','trades','warnings']:
                result[key].to_csv(folder/f'{key}.csv',index=False)
        dump(folder/'config.json',result['config'])
        pd.DataFrame(monthly_rows(result['equity'])).to_csv(folder/'monthly.csv',index=False)
        metrics=dict(result['metrics'],status='COMPLETE')
        metrics.update(candidate_id=trial['candidate_id'],phase=trial['phase'],varied=trial['varied'],
            anchor=trial.get('anchor'),search_seed=trial.get('search_seed'),pair_id=trial.get('pair_id'),
            parent=trial.get('parent'),elapsed_seconds=time.monotonic()-started,path=str(folder.relative_to(output)))
        dump(folder/'metrics.json',metrics)
        dump(folder/'receipt.json',{p.name:sha(p) for p in folder.iterdir() if p.is_file() and p.name!='receipt.json'})
        return {**metrics,**trial['params']}
    except Exception as exc:
        failure=dict(status='FAILED',candidate_id=trial['candidate_id'],phase=trial['phase'],
            error=str(exc),traceback=traceback.format_exc(),config=config)
        dump(folder/'failure.json',failure)
        return {**failure,**trial['params']}


def run_batch(trials,output,workers,resume):
    rows=[];pending=[]
    for trial in trials:
        folder=output/'trials'/trial['candidate_id']
        if resume and (folder/'receipt.json').exists() and not (folder/'failure.json').exists():
            for name,digest in json.loads((folder/'receipt.json').read_text()).items():
                if sha(folder/name)!=digest:raise ValueError('Resume artifact changed: '+str(folder/name))
            saved=json.loads((folder/'config.json').read_text())
            if saved!=deep.config_for(CTX,trial):
                # Engine _settings supplies only already-declared defaults.
                raise ValueError('Resume configuration differs: '+trial['candidate_id'])
            rows.append({**json.loads((folder/'metrics.json').read_text()),**trial['params']})
        elif folder.exists() and any(folder.iterdir()):
            raise ValueError('Incomplete/failed output requires diagnosis before explicit recovery: '+str(folder))
        else:pending.append(trial)
    CTX['cache'].prewarm(pending)
    with ProcessPoolExecutor(max_workers=workers,mp_context=mp.get_context('fork')) as pool:
        jobs={pool.submit(trial_run,trial,output):trial for trial in pending}
        for future in as_completed(jobs):
            row=future.result();rows.append(row)
            dump(output/'status.json',dict(status='RUNNING',batch_completed=len(rows),batch_expected=len(trials),last=row['candidate_id'],last_status=row['status']))
            print(f"{len(rows)}/{len(trials)} {row['candidate_id']} {row['status']} hard={row.get('measured_hard_breach_days')} return={row.get('economic_total_return')}",flush=True)
    if any(r['status']!='COMPLETE' for r in rows):
        dump(output/'failed_batch.json',rows)
        raise ValueError('Failed trials quarantined; do not select partial study')
    return rows


def refinement(study,rows,incumbent_mdd):
    by_mode={}
    for mode in ['strict','coverage_only']:
        eligible=sorted([r for r in rows if r['status']=='COMPLETE' and r['measured_hard_breach_days']==0 and r['four_hour_mode']==mode],
                        key=lambda r:(-r['economic_total_return'],r['economic_max_drawdown'],r['candidate_id']))
        if not eligible:continue
        parents=eligible[:2]
        risk=deep.choose(eligible,incumbent_mdd)
        if risk and risk['candidate_id'] not in {r['candidate_id'] for r in parents}:parents.append(risk)
        by_mode[mode]=parents
    if not by_mode:raise ValueError('NO_ELIGIBLE_WINNER for local refinement')
    policy=study['refinement'];spaces=copy.deepcopy(study['spaces'])
    for key,extra in policy['boundary_extensions'].items():spaces[key]+=extra
    rng=np.random.default_rng(policy['seed']);seen={canonical(t['params']) for t in study['candidates']}
    trials=[];attempt=0;modes=list(by_mode)
    while len(trials)<policy['trials']:
        if attempt>10000:raise ValueError('Refinement uniqueness budget exhausted')
        mode=modes[len(trials)%len(modes)];parents=by_mode[mode]
        parent=parents[(len(trials)//len(modes))%len(parents)]
        params={key:parent[key] for key in deep.FIELDS}
        # Keep integer types native after CSV round trips.
        for key in ['target_count','max_replacements_per_day','return_short','return_long','ema_fast','ema_slow','macd_fast','macd_slow','macd_signal']:
            params[key]=int(params[key])
        axes=rng.choice(list(spaces),size=int(rng.choice(policy['mutations'])),replace=False)
        for axis in axes:
            choices=spaces[axis]
            if axis=='return_pair':current=[params['return_short'],params['return_long']]
            elif axis=='ema_pair':current=[params['ema_fast'],params['ema_slow']]
            elif axis=='macd_tuple':current=[params[k] for k in ['macd_fast','macd_slow','macd_signal']]
            else:current=params[axis]
            nearest=choices.index(current) if current in choices else min(range(len(choices)),key=lambda i:abs(choices[i]-current))
            neighbors=[i for i in range(max(0,nearest-1),min(len(choices),nearest+2)) if choices[i]!=current]
            if neighbors:params=apply_axis(params,axis,choices[int(rng.choice(neighbors))])
        attempt+=1
        key=canonical(params)
        if key in seen:continue
        seen.add(key)
        trials.append(dict(candidate_id=f'r{len(trials):04d}',params=params,phase='local_refinement',
                           varied='local_joint_bundle',parent=parent['candidate_id'],search_seed=policy['seed']))
    return trials


def final_replays(output,study,trials,rows):
    inc_id='a0000' if CTX['track']=='historical_pit' else 'a0001'
    incumbent=next(row for row in rows if row['candidate_id']==inc_id)
    selections={
        'return_winner':deep.choose(rows),
        'risk_controlled':deep.choose(rows,incumbent['economic_max_drawdown']),
        'strict_winner':deep.choose([r for r in rows if r['four_hour_mode']=='strict']),
        'incumbent':incumbent,
    }
    lookup={t['candidate_id']:t for t in trials};results={}
    for name,row in selections.items():
        if row is None:continue
        cfg=deep.config_for(CTX,lookup[row['candidate_id']]);result=deep.run_model(CTX,cfg)
        if result['metrics']['measured_hard_breach_days']!=0:raise ValueError('Selected candidate lost zero-breach eligibility')
        for key in ['equity','trades','orders']:
            expected=pd.read_csv(output/row['path']/f'{key}.csv').fillna('')
            pd.testing.assert_frame_equal(expected,result[key].fillna(''),check_dtype=False,rtol=1e-12,atol=1e-5)
        save_result(result,output/'final'/name);results[name]=result
    previous=ROOT/'outputs/tuning_report_2nd_try'/CTX['track']/'final'
    for name in ['v1_matched','0050']:
        # Immutable independently audited controls, rather than re-tuning them.
        src=previous/name;dest=output/'final'/name
        shutil.copytree(src,dest)
        results[name]=dict(equity=pd.read_csv(dest/'equity.csv'),metrics=json.loads((dest/'metrics.json').read_text()))
    comparison=[];monthly=[];curves={}
    for name,result in results.items():
        eq=result['equity'];comparison.append(dict(model=name,**result['metrics']))
        monthly.extend(dict(model=name,**r) for r in monthly_rows(eq))
        curves[name]=pd.Series([1e9,*eq.economic_nav],index=['2024-12-31',*eq.date])
    pd.DataFrame(comparison).to_csv(output/'comparison.csv',index=False)
    pd.DataFrame(monthly).to_csv(output/'monthly_comparison.csv',index=False)
    pd.DataFrame(curves).rename_axis('date').to_csv(output/'nav_comparison.csv')
    selection={name:dict(candidate_id=None if row is None else row['candidate_id'],
        status='NO_ELIGIBLE_WINNER' if row is None else 'SELECTED_ZERO_MEASURED_HARD',
        scope='EX_POST_DEVELOPMENT',official_submission='BLOCKED_UNKNOWN_ACTIVE_SHARE') for name,row in selections.items()}
    dump(output/'selection.json',selection)


def run(args):
    global CTX
    output=ROOT/args.output
    if output.exists() and any(output.iterdir()) and not args.resume:raise FileExistsError('Preserve existing output')
    output.mkdir(parents=True,exist_ok=True)
    study=json.loads((ROOT/'config/a_deep_study.json').read_text())
    if study['status']!='FROZEN':raise ValueError('Study not frozen')
    print('Preparing '+args.track,flush=True);CTX=deep.context(args.track)
    if args.smoke:
        for trial,original in zip(study['candidates'][:2],['historical_pit','official_ex_post']):
            row=trial_run(trial,output,True)
            if row['status']!='COMPLETE':raise ValueError(row['error'])
            # Each track's own incumbent must exactly reproduce its older implementation.
            if original==args.track:
                for name in ['equity','orders','trades','holdings']:
                    old=pd.read_csv(ROOT/f'outputs/tuning_report_2nd_try/{original}/final/A/{name}.csv').fillna('')
                    new=pd.read_csv(output/'trials'/trial['candidate_id']/f'{name}.csv').fillna('')
                    pd.testing.assert_frame_equal(old,new,check_dtype=False,check_exact=True)
            print(trial['candidate_id']+' SMOKE_PASS',flush=True)
        dump(output/'smoke.json',dict(status='PASS',own_incumbent_exact=True));return
    path=output/'manifest.json'
    if path.exists():
        manifest=json.loads(path.read_text())
        if manifest.get('outputs_complete'):raise ValueError('Completed study preserved; do not resume')
        for p,h in manifest['hashes'].items():
            if sha(ROOT/p)!=h:raise ValueError('Frozen dependency changed: '+p)
    else:
        paths=old_dependencies(args.track)+['src/tuning_a_deep.py','scripts/prepare_a_deep.py','scripts/run_a_deep_tuning.py','config/a_deep_study.json']
        paths += [f'outputs/tuning_report_2nd_try/{args.track}/{p}' for p in ['audit.json','selection.json','comparison.csv']]
        prior_audit=json.loads((ROOT/f'outputs/tuning_report_2nd_try/{args.track}/audit.json').read_text())
        if prior_audit['status']!='PASS':raise ValueError('Prior controls not audited')
        for rel,digest in prior_audit['artifact_hashes'].items():
            if sha(ROOT/f'outputs/tuning_report_2nd_try/{args.track}'/rel)!=digest:raise ValueError('Prior audited artifact changed')
        hashes={p:sha(ROOT/p) for p in dict.fromkeys(paths)}
        for p in hashes:
            dest=output/'input_snapshot'/p;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/p,dest)
        manifest=dict(track=args.track,study=study,hashes=hashes,expected_trials=study['exploration_trials']+64,
            started_at=datetime.now(timezone.utc).isoformat(),pid=os.getpid(),command=sys.argv,
            python=platform.python_version(),outputs_complete=False,
            prior_audit_hash=sha(ROOT/f'outputs/tuning_report_2nd_try/{args.track}/audit.json'),
            official_submission='BLOCKED_UNKNOWN_ACTIVE_SHARE',selection_scope='EX_POST_DEVELOPMENT')
        dump(path,manifest)
    rows=run_batch(study['candidates'],output,args.workers,args.resume)
    pd.DataFrame(rows).sort_values('candidate_id').to_csv(output/'exploration_summary.csv',index=False)
    inc_id='a0000' if args.track=='historical_pit' else 'a0001'
    inc=next(r for r in rows if r['candidate_id']==inc_id)
    local=refinement(study,rows,inc['economic_max_drawdown'])
    refinement_file=output/'refinement_candidates.json'
    frozen_local=dict(exploration_summary_sha256=sha(output/'exploration_summary.csv'),candidates=local,
                      scope='ADAPTIVE_FULL_DEVELOPMENT_HISTORY')
    if refinement_file.exists():
        previous=json.loads(refinement_file.read_text())
        if previous!=frozen_local:raise ValueError('Adaptive candidate provenance mismatch')
    else:dump(refinement_file,frozen_local)
    rows+=run_batch(local,output,args.workers,args.resume)
    pd.DataFrame(rows).sort_values('candidate_id').to_csv(output/'trial_summary.csv',index=False)
    final_replays(output,study,study['candidates']+local,rows)
    for p,h in manifest['hashes'].items():
        if sha(ROOT/p)!=h:raise ValueError('Frozen dependency changed during run: '+p)
    manifest.update(outputs_complete=True,completed_at=datetime.now(timezone.utc).isoformat(),completed_trials=len(rows))
    dump(path,manifest);dump(output/'status.json',dict(status='COMPLETE_PENDING_AUDIT',completed=len(rows),expected=manifest['expected_trials']))
    print('COMPLETE_PENDING_AUDIT',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--track',choices=deep.second.TRACKS,required=True)
    p.add_argument('--output',required=True);p.add_argument('--workers',type=int,default=4)
    p.add_argument('--resume',action='store_true');p.add_argument('--smoke',action='store_true')
    run(p.parse_args())
