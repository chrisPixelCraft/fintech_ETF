"""Resumable paired-universe full tuning with immutable trial receipts."""
from pathlib import Path
import argparse, copy, json, os, platform, shutil, sys, time, traceback
from datetime import datetime, timezone
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from src import official_deep_tuning as full, tuning_a_deep as deep, tuning_2nd
from scripts.prepare_official_deep import apply_axis, canonical
from scripts.run_v2_tuning import dump, sha
from scripts.run_tuning_2nd import dependencies
from scripts.audit_official_v2 import primary_months
from src.backtest import save_result

CONTEXTS={}


def trial_run(track, trial, root):
    out=root/track/'trials'/trial['candidate_id'];out.mkdir(parents=True,exist_ok=True)
    start=time.monotonic();cfg=full.config_for(CONTEXTS[track],trial)
    try:
        result=full.run_model(CONTEXTS[track],cfg)
        for key in ['equity','orders','trades','holdings','warnings','snapshots','plan_audit']:
            result[key].to_csv(out/f'{key}.csv',index=False)
        dump(out/'config.json',result['config'])
        pd.DataFrame(primary_months(result['equity'],cfg['initial_cash'])).to_csv(out/'monthly.csv',index=False)
        row={**result['metrics'],**trial['params'],**{k:v for k,v in trial.items() if k!='params'},
             'track':track,'status':'COMPLETE','elapsed_seconds':time.monotonic()-start}
        row['eligible']=full.eligible(row)
        dump(out/'metrics.json',row)
        dump(out/'receipt.json',{p.name:sha(p) for p in out.iterdir() if p.is_file() and p.name!='receipt.json'})
        return row
    except Exception as exc:
        row=dict(track=track,candidate_id=trial['candidate_id'],status='FAILED',eligible=False,
                 error=str(exc),traceback=traceback.format_exc(),config=cfg)
        dump(out/'failure.json',row);return row


def batch(trials,root,workers):
    results=[];jobs=[]
    for track in CONTEXTS:
        pending=[]
        for trial in trials:
            out=root/track/'trials'/trial['candidate_id']
            if (out/'receipt.json').exists():
                for name,digest in json.loads((out/'receipt.json').read_text()).items():
                    if sha(out/name)!=digest:raise ValueError('Changed trial artifact '+str(out/name))
                if json.loads((out/'config.json').read_text())!=full.config_for(CONTEXTS[track],trial):
                    raise ValueError('Changed resumed trial config')
                results.append(json.loads((out/'metrics.json').read_text()))
            elif out.exists() and any(out.iterdir()):
                raise ValueError('Diagnose incomplete output before recovery: '+str(out))
            else:pending.append(trial);jobs.append((track,trial))
        CONTEXTS[track]['cache'].prewarm(pending)
    with ProcessPoolExecutor(max_workers=workers,mp_context=mp.get_context('fork')) as pool:
        futures={pool.submit(trial_run,t,c,root):(t,c) for t,c in jobs}
        for future in as_completed(futures):
            row=future.result();results.append(row)
            dump(root/'status.json',dict(status='RUNNING',completed_batch=len(results),expected_batch=2*len(trials),
                last_track=row['track'],last_candidate=row['candidate_id'],last_status=row['status'],pid=os.getpid()))
            print(f"{len(results)}/{2*len(trials)} {row['track']} {row['candidate_id']} {row['status']} eligible={row['eligible']} return={row.get('total_return')}",flush=True)
            if row['status']=='FAILED':
                for pending in futures: pending.cancel()
                pool.shutdown(wait=True, cancel_futures=True)
                dump(root/'failed_batch.json',results)
                raise ValueError('Unexpected failure quarantined: ' + row['candidate_id'] + ': ' + row['error'])
    if any(r['status']!='COMPLETE' for r in results):
        dump(root/'failed_batch.json',results);raise ValueError('Failed/partial runs quarantined; no selection')
    return results


def local_candidates(study,rows):
    eligible=[r for r in rows if r['track']=='official_ex_post' and full.eligible(r)]
    parents=[]
    for mode in ['strict','coverage_only']:
        pool=sorted([r for r in eligible if r['four_hour_mode']==mode],key=lambda r:(-r['total_return'],r['max_drawdown'],r['candidate_id']))
        if pool:
            selected=pool[:2]+[min(pool[:10],key=lambda r:(r['max_drawdown'],-r['total_return']))]
            for r in selected:
                if r['candidate_id'] not in {p['candidate_id'] for p in parents}:parents.append(r)
    if not parents:raise ValueError('NO_ELIGIBLE_WINNER: redesign feasibility before performance search')
    rng=np.random.default_rng(study['local_policy']['seed']);seen={canonical(t['params']) for t in study['candidates']}
    output=[];attempt=0
    while len(output)<study['local_candidates']:
        if attempt>10000:raise ValueError('Local uniqueness exhausted')
        parent=parents[len(output)%len(parents)];p={k:parent[k] for k in full.FIELDS}
        axes=rng.choice(list(study['spaces']),size=int(rng.choice([1,2,3])),replace=False)
        for axis in axes:
            choices=study['spaces'][axis]
            keys={'return_pair':['return_short','return_long'],'ema_pair':['ema_fast','ema_slow'],
                  'macd_tuple':['macd_fast','macd_slow','macd_signal']}
            old=[p[k] for k in keys[axis]] if axis in keys else p[axis]
            if old in choices:
                i=choices.index(old);choices=[v for j,v in enumerate(choices) if abs(j-i)==1]
            else:choices=[v for v in choices if v!=old]
            if choices:p=apply_axis(p,axis,choices[int(rng.integers(len(choices)))])
        attempt+=1;key=canonical(p)
        if key in seen:continue
        seen.add(key);output.append(dict(candidate_id=f'l{len(output):04d}',params=p,phase='local',varied='local_joint',
            parent=parent['candidate_id'],search_seed=study['local_policy']['seed']))
    return output


def finalize(root,rows,trials):
    selected=full.choose([r for r in rows if r['track']=='official_ex_post'])
    if selected is None:raise ValueError('NO_ELIGIBLE_WINNER')
    trial=next(t for t in trials if t['candidate_id']==selected['candidate_id'])
    dump(root/'selection.json',dict(candidate_id=trial['candidate_id'],params=trial['params'],
        selected_on='official_ex_post',scope='EX_POST_DEVELOPMENT',formal_submission='BLOCK_IF_UNKNOWN',
        rule='zero measured hard/no valid plan/unfilled, then book return/MDD/turnover',
        historical_pit='same parameters; no independent cherry-pick'))
    comparisons=[];months=[]
    for track,ctx in CONTEXTS.items():
        result=full.run_model(ctx,full.config_for(ctx,trial))
        old=root/track/'trials'/trial['candidate_id']
        for key in ['equity','orders','trades','holdings','plan_audit']:
            pd.testing.assert_frame_equal(result[key].fillna(''),pd.read_csv(old/f'{key}.csv',float_precision='round_trip').fillna(''),
                                          check_dtype=False,rtol=1e-12,atol=1e-5)
        outputs={'full_tuned_v2':result}
        for model in ['v1_matched','0050']:
            cfg=copy.deepcopy(ctx['base']);cfg.update(strategy_id=model,allocation_mode='full' if model=='v1_matched' else 'local',
                ex_post_fixed_universe=track=='official_ex_post',universe_mode=track)
            # Controls use the frozen original feature cache, never candidate indicators.
            control_ctx=tuning_2nd.context(track)
            outputs[model]=tuning_2nd.run_model(control_ctx,model,cfg)
            for key in ['equity','orders','trades','holdings']:
                expected=pd.read_csv(ROOT/f'outputs/official_v2_reaudit/{track}/final/{model}/{key}.csv',float_precision='round_trip').fillna('')
                pd.testing.assert_frame_equal(outputs[model][key].fillna(''),expected,check_dtype=False,check_exact=True)
        for model,res in outputs.items():
            dest=root/track/'final'/model;save_result(res,dest)
            if 'plan_audit' in res:res['plan_audit'].to_csv(dest/'plan_audit.csv',index=False)
            comparisons.append(dict(track=track,model=model,**res['metrics']))
            months += [dict(track=track,model=model,**m) for m in primary_months(res['equity'],1e9)]
        pd.DataFrame({m:r['equity'].set_index('date').nav for m,r in outputs.items()}).to_csv(root/track/'nav.csv')
    pd.DataFrame(comparisons).to_csv(root/'comparison.csv',index=False)
    pd.DataFrame(months).to_csv(root/'monthly.csv',index=False)


def main(args):
    root=ROOT/args.output;root.mkdir(parents=True,exist_ok=True)
    manifest_path=root/'manifest.json'
    study=json.loads((ROOT/'config/official_deep_study.json').read_text())
    if manifest_path.exists():
        if not args.resume:raise ValueError('Existing run; explicit --resume required')
        manifest=json.loads(manifest_path.read_text())
        if manifest['outputs_complete']:raise ValueError('Completed study preserved')
        for rel,digest in manifest['hashes'].items():
            if sha(ROOT/rel)!=digest:raise ValueError('Frozen dependency changed '+rel)
    else:
        paths=[]
        for track in study['tracks']:paths+=dependencies(track)
        paths += ['src/tuning_a_deep.py','src/official_v2_review.py','src/official_deep_tuning.py',
            'scripts/prepare_a_deep.py','scripts/prepare_official_deep.py','scripts/run_official_deep.py',
            'scripts/audit_official_v2.py','config/official_deep_study.json','docs/official_deep_protocol.md']
        paths += [str(p.relative_to(ROOT)) for p in (ROOT/'official_docs').iterdir() if p.is_file()]
        hashes={p:sha(ROOT/p) for p in dict.fromkeys(paths)}
        for rel in hashes:
            dest=root/'input_snapshot'/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/rel,dest)
        manifest=dict(study=study,hashes=hashes,started_at=datetime.now(timezone.utc).isoformat(),pid=os.getpid(),
            command=sys.argv,python=platform.python_version(),outputs_complete=False,
            expected_trials=2*(study['exploration_candidates']+study['local_candidates']),trust='CANDIDATE')
        dump(manifest_path,manifest)
    for track in study['tracks']:
        print('CONTEXT '+track,flush=True);CONTEXTS[track]=deep.context(track)
    rows=batch(study['candidates'],root,args.workers)
    pd.DataFrame(rows).sort_values(['track','candidate_id']).to_csv(root/'exploration.csv',index=False)
    local=local_candidates(study,rows)
    proof=dict(exploration_sha256=sha(root/'exploration.csv'),candidates=local)
    localpath=root/'local_candidates.json'
    if localpath.exists():
        if json.loads(localpath.read_text())!=proof:raise ValueError('Adaptive stage changed')
    else:dump(localpath,proof)
    rows += batch(local,root,args.workers)
    pd.DataFrame(rows).sort_values(['track','candidate_id']).to_csv(root/'trials.csv',index=False)
    finalize(root,rows,study['candidates']+local)
    for rel,digest in manifest['hashes'].items():
        if sha(ROOT/rel)!=digest:raise ValueError('Frozen dependency changed during execution '+rel)
    manifest.update(outputs_complete=True,completed_trials=len(rows),completed_at=datetime.now(timezone.utc).isoformat())
    dump(manifest_path,manifest)
    dump(root/'receipt.json',{str(p.relative_to(root)):sha(p) for p in root.rglob('*') if p.is_file()
        and 'input_snapshot' not in p.parts and p.suffix != '.log' and p.name not in ['receipt.json','status.json']})
    dump(root/'status.json',dict(status='COMPLETE_PENDING_INDEPENDENT_AUDIT',completed=len(rows)))
    print('COMPLETE_PENDING_INDEPENDENT_AUDIT',flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',default='outputs/full_tuned_v2');p.add_argument('--workers',type=int,default=6)
    p.add_argument('--resume',action='store_true');args=p.parse_args()
    try:main(args)
    except Exception as exc:
        dump(ROOT/args.output/'failure.json',dict(error=str(exc),traceback=traceback.format_exc()));raise
