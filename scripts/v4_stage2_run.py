#!/usr/bin/env python3
"""Bounded chronological Stage-2 study, with immutable resumable episode evidence."""
from __future__ import annotations
import argparse, copy, hashlib, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.v4_run_baseline import clean, write_json, sha256


def canonical(value):
    return hashlib.sha256(json.dumps(clean(value),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def event(output, name, payload):
    path=output/'events.jsonl'
    prior=json.loads(path.read_text().splitlines()[-1])['hash'] if path.exists() else None
    row=dict(event=name,time=now(),previous_hash=prior,payload=payload)
    row['hash']=canonical(row)
    with path.open('a') as handle:
        handle.write(json.dumps(row,sort_keys=True)+'\n')


def ensure_feature_cache(output, study):
    """Bind cached representation bytes to the raw data and feature builders.

    A legacy cache lacking a manifest is recomputed once. An existing sealed
    cache with a mismatched input or payload hash fails closed.
    """
    from src.v4_features import build_features
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    feature_path = output / 'features.pkl'
    manifest_path = output / 'features.manifest.json'
    sources = [Path(study['daily']), ROOT / 'src/v4_features.py', ROOT / 'src/strategy_24d.py']
    expected = {str(path.relative_to(ROOT) if path.is_absolute() and path.is_relative_to(ROOT) else path): sha256(path) for path in sources}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get('inputs') != expected:
            raise ValueError('Feature cache source provenance changed; use a new study directory')
        if not feature_path.exists() or sha256(feature_path) != manifest.get('features_sha256'):
            raise ValueError('Feature cache payload hash mismatch')
    else:
        daily = pd.read_parquet(study['daily'])
        daily['date'] = pd.to_datetime(daily.date)
        temporary = output / 'features.pending.pkl'
        build_features(daily).to_pickle(temporary)
        temporary.replace(feature_path)
        write_json(manifest_path, dict(inputs=expected, features_sha256=sha256(feature_path)))
    return feature_path


STATE={}
def initialize(study_path, feature_path):
    from src.strategy_24d import build_features as v3_features
    study=json.loads(Path(study_path).read_text())
    daily=pd.read_parquet(study['daily']); daily['date']=pd.to_datetime(daily.date)
    universe=pd.read_csv(study['universe']); universe['symbol']=universe.yahoo_symbol.astype(str)
    universe['known_at']=universe.attachment_created_at
    config=json.loads(Path('configs/competition_24d_final.json').read_text())
    feature_path = Path(feature_path)
    feature_manifest = json.loads(feature_path.with_name('features.manifest.json').read_text())
    if sha256(feature_path) != feature_manifest['features_sha256']:
        raise ValueError('Worker feature cache payload hash mismatch')
    panel=pd.read_pickle(feature_path)
    execution=pd.read_csv(study['execution_data'],parse_dates=['date'])
    STATE.update(daily=daily, universe=universe, config=config, panel=panel,
        execution=execution, v3=v3_features(daily,config), forecasts={})


def evaluate(task):
    candidate, episodes, output=task
    from src.v4_forecast import WalkForwardForecaster
    from src.v4_stage2_episode import run_episode, normalize_episode_result
    from src.v4_baseline import run_episode as baseline
    forecaster=None
    if candidate['family']=='adaptive' or candidate.get('confidence'):
        weighting=candidate.get('weighting','inverse_error')
        if weighting not in STATE['forecasts']:
            STATE['forecasts'][weighting]=WalkForwardForecaster(STATE['panel'],weighting=weighting)
        forecaster=STATE['forecasts'][weighting]
    records=[]
    for episode in episodes:
        location=Path(output)/'runs'/candidate['id']/episode['episode_id']
        if (location/'complete.json').exists():
            complete=json.loads((location/'complete.json').read_text())
            if complete['candidate_hash']!=canonical(candidate):
                raise ValueError('Attempted reuse with changed candidate')
            for name,digest in complete['hashes'].items():
                if sha256(location/name)!=digest: raise ValueError('Corrupt episode evidence')
            records.append(complete['record']); continue
        if location.exists():
            raise ValueError('Partial episode must be quarantined before retry: '+str(location))
        started=now()
        config=dict(STATE['config'],prior_session_date=episode['prior_session_date'])
        dates=pd.DatetimeIndex(pd.to_datetime([episode['prior_session_date'],*episode['sessions']]))
        execution=STATE['execution'].loc[STATE['execution'].date.isin(dates)]
        if candidate['family']=='v3':
            result=baseline(STATE['daily'],STATE['universe'],config,episode['sessions'],execution,
                features=STATE['v3'],sizing_price_mode='official_close')
        else:
            # Signals use the exact episode's prior cross sections; the forecaster
            # owns its causal full-history model state, independently of this view.
            features=STATE['panel'].loc[STATE['panel'].date.isin(dates)]
            result=run_episode(STATE['daily'],STATE['universe'],config,episode['sessions'],execution,
                features,candidate,forecaster)
        result=normalize_episode_result(result)
        location.mkdir(parents=True)
        for name,table in result.items():
            if isinstance(table,pd.DataFrame): table.to_csv(location/(name+'.csv'),index=False)
        write_json(location/'config.json',result.get('config',config))
        write_json(location/'strategy_config.json',candidate)
        write_json(location/'metrics.json',result['metrics'])
        record=dict(result['metrics'],candidate=candidate['id'],family=candidate['family'],
            episode=episode['episode_id'],split=episode['split'],run_started_at=started,path=str(location))
        hashes={p.name:sha256(p) for p in sorted(location.iterdir()) if p.is_file()}
        write_json(location/'complete.json',dict(candidate_hash=canonical(candidate),hashes=hashes,record=record))
        records.append(clean(record))
    return records


def aggregate(records):
    rows=[]
    for cid,group in pd.DataFrame(records).groupby('candidate'):
        returns=pd.to_numeric(group.episode_return,errors='coerce')
        full=returns.notna().all()
        rows.append(dict(candidate=cid,family=group.family.iloc[0],attempted=len(group),
            complete=int(returns.notna().sum()),canonical=int(group.canonical_status.eq('AVAILABLE').sum()),
            measured=int(group.measured_pass.eq(True).sum()),median=returns.median() if full else -1.,
            mean=returns.mean() if full else -1.,p25=returns.quantile(.25) if full else -1.,
            p10=returns.quantile(.1) if full else -1.,worst=returns.min() if full else -1.,
            positive=float(returns.gt(0).sum()/len(group)),mdd=group.episode_max_drawdown.max(),
            turnover=group.episode_turnover.mean(),cost=group.transaction_cost.mean()))
    return pd.DataFrame(rows).sort_values(['canonical','measured','median','p25','p10','worst','mdd','mean','cost','candidate'],
        ascending=[False]*6+[True,False,True,True]).reset_index(drop=True)


def rank_development(summaries, configs, family):
    selected=summaries.loc[summaries.family.eq(family)].copy()
    if family=='direct':
        lookup={c['id']:c for c in configs}
        selected['utility']=selected.apply(lambda r: r['median']-lookup[r.candidate]['risk_penalty']*r.mdd-lookup[r.candidate]['turnover_penalty']*r.turnover-lookup[r.candidate].get('regularization',.01)*np.square(lookup[r.candidate]['coefficients']).sum(),axis=1)
        selected=selected.sort_values(['canonical','measured','utility','candidate'],ascending=[False,False,False,True])
    return selected


def bind_development_inputs(output, study_path, study):
    """Standalone fitting cannot reuse runs after any data/config/code change."""
    paths = [Path(study_path), Path('configs/competition_24d_final.json'),
             *[Path(study[k]) for k in ('daily', 'calendar', 'universe', 'execution_data', 'episode_registry')],
             Path(study['execution_data']).with_suffix('.manifest.json'),
             *sorted((ROOT / 'src').glob('*.py')), *sorted((ROOT / 'scripts').glob('v4_*.py')),
             *sorted((ROOT / 'config').glob('v4_*search.json'))]
    hashes = {str(p.relative_to(ROOT) if p.is_absolute() and p.is_relative_to(ROOT) else p): sha256(p)
              for p in paths}
    path = Path(output) / 'development_inputs.json'
    if path.exists():
        if json.loads(path.read_text()) != hashes:
            raise ValueError('Standalone development inputs changed; use a new output directory')
    else:
        if (Path(output) / 'runs').exists():
            raise ValueError('Unproven standalone runs exist without input provenance')
        write_json(path, hashes)
    return hashes


def require_official_cache(study, episodes):
    """Acquisition failures stop scientific search before any outcome is viewed."""
    path=Path(study['execution_data'])
    metadata=json.loads(path.with_suffix('.manifest.json').read_text())
    dates={d for e in episodes for d in [e['prior_session_date'],*e['sessions']]}
    expected={(d,m) for d in dates for m in ('TWSE','TPEx')}
    attempts=metadata['attempts']
    actual={(a['date'],a['market']) for a in attempts}
    failed=[a for a in attempts if a['status']!='OK']
    if len(actual)!=len(attempts) or actual!=expected or failed:
        raise ValueError(f'BLOCK_CANONICAL_V4: {len(failed)} failed acquisitions; complete official registry required before tuning')
    from scripts.v4_verify import verify_execution_provenance
    return verify_execution_provenance(path)


def run(study_path, output, workers=4):
    from src.v4_features import build_features
    from src.v4_search import candidates, ablations
    output=Path(output); output.mkdir(parents=True,exist_ok=True)
    study=json.loads(Path(study_path).read_text()); episodes=json.loads(Path(study['episode_registry']).read_text())
    require_official_cache(study,episodes)
    configs=candidates()
    inputs=[Path(study_path),Path('config/v4_stage2_search.json'),Path('configs/competition_24d_final.json'),
        *[Path(study[k]) for k in ['daily','calendar','universe','execution_data','episode_registry']],
        *sorted(Path('src').glob('*.py')), *sorted(Path('scripts').glob('v4_*.py')),
        *sorted(Path('config').glob('v4_*search.json')),
        *sorted(Path('tests').glob('test_v4*.py')), *sorted(Path('docs').glob('v4_*spec.md')),
        Path(study['execution_data']).with_suffix('.manifest.json'),
        Path(study['episode_registry']).parent/'registry_manifest.json']
    input_hashes={str(p.relative_to(ROOT) if p.is_absolute() else p):sha256(p) for p in inputs}
    if (output/'inputs.json').exists():
        if json.loads((output/'inputs.json').read_text())!=input_hashes:
            raise ValueError('Inputs changed; use a new study directory')
    else:
        write_json(output/'inputs.json',input_hashes); write_json(output/'candidates.json',configs)
        write_json(output/'study.json',study)
        write_json(output/'episodes.json',episodes); event(output,'study_start',dict(input_hash=canonical(input_hashes)))
    feature_path=ensure_feature_cache(output, study)
    records=[]
    def batch(pool, cs, es, phase):
        event(output,phase+'_start',dict(candidates=[c['id'] for c in cs],episodes=[e['episode_id'] for e in es]))
        got=[]
        for rows in pool.map(evaluate,[(c,es,str(output)) for c in cs]):
            got.extend(rows)
            print(json.dumps(dict(phase=phase,candidate=rows[0]['candidate'],episodes=len(rows))),flush=True)
        records.extend(got)
        pd.DataFrame(got).to_csv(output/(phase+'.csv'),index=False)
        event(output,phase+'_complete',dict(sha256=sha256(output/(phase+'.csv'))))
        return got
    with ProcessPoolExecutor(max_workers=workers,initializer=initialize,initargs=(str(study_path),str(feature_path))) as pool:
        dev=[e for e in episodes if e['split']=='development']
        val=[e for e in episodes if e['split']=='validation']
        baseline_config=dict(id='A0_V3',family='v3')
        development=batch(pool,configs,dev,'development')
        summaries=aggregate(development); summaries.to_csv(output/'development_summary.csv',index=False)
        shortlist=[]
        for family in ['momentum','adaptive','direct']:
            family_summary=rank_development(summaries,configs,family)
            if family=='direct': family_summary.to_csv(output/'direct_development_utility.csv',index=False)
            ids=family_summary.candidate.head(3)
            shortlist.extend(c for c in configs if c['id'] in set(ids))
        validation=batch(pool,[baseline_config,*shortlist],val,'validation')
        summary=aggregate(validation); summary.to_csv(output/'validation_summary.csv',index=False)
        selected={}
        for family in ['momentum','adaptive','direct']:
            cid=summary.loc[summary.family.eq(family),'candidate'].iloc[0]
            selected[family]=next(c for c in shortlist if c['id']==cid)
        abl=ablations(selected)
        preferred_family=summary.loc[summary.candidate.isin([c['id'] for c in selected.values()]),'family'].iloc[0]
        frozen=dict(selected=selected,preferred_family=preferred_family,candidate_ids={f:c['id'] for f,c in selected.items()},
            evaluation_configs={c['id']:c for c in [baseline_config,*selected.values(),*abl]},config_hashes={f:canonical(c) for f,c in selected.items()},
            validation_hashes={'validation.csv':sha256(output/'validation.csv')},frozen_at=now(),
            decision_rule='100% canonical and measured; zero paired median/P25/P10 deterioration; neighborhood stability; independent audit; no diagnostic selection')
        freeze_path=output/'freeze.json'
        if freeze_path.exists():
            old=json.loads(freeze_path.read_text())
            if old['selected']!=selected: raise ValueError('Frozen selection changed')
        else:
            write_json(freeze_path,frozen); event(output,'freeze',dict(freeze_sha256=sha256(freeze_path)))
        # Ablations are fixed derivatives, not fresh candidates for selection.
        write_json(output/'ablations.json',abl)
        event(output,'ablations_declared',dict(ablations_sha256=sha256(output/'ablations.json')))
        other=[e for e in episodes if e['split'] not in ['development','validation']]
        batch(pool,[baseline_config,*selected.values()],other,'confirmation')
        batch(pool,[baseline_config],dev,'baseline_development')
        batch(pool,abl,episodes,'ablations')
    # Deduplicate candidate/episode reused by fixed ablations, retaining all failures.
    frame=pd.DataFrame(records).drop_duplicates(['candidate','episode'])
    frame.to_csv(output/'all_episodes.csv',index=False)
    attempts=frame[['candidate','episode','split','run_started_at','path']].to_dict('records')
    outputs={str(p.relative_to(output)):sha256(p) for p in sorted(output.rglob('*'))
        if p.is_file() and p.name not in ['manifest.json','verification.json','features.pkl','run.log','status.json']}
    write_json(output/'manifest.json',dict(inputs=input_hashes,outputs=outputs,attempts=attempts,completed_at=now()))
    write_json(output/'status.json',dict(status='EXPERIMENTS_COMPLETE_PENDING_AUDIT',attempts=len(attempts)))


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--study',default='config/v4_stage2_study.json')
    parser.add_argument('--output',default='outputs/v4/stage2'); parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args(); run(args.study,args.output,args.workers)


def run_development_family(family, study_path, output, workers=4):
    """Standalone family development only; held-out dates cannot enter fitting."""
    from src.v4_search import candidates
    from src.v4_features import build_features
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    study=json.loads(Path(study_path).read_text())
    registry=json.loads(Path(study['episode_registry']).read_text())
    require_official_cache(study,registry)
    bind_development_inputs(output, study_path, study)
    episodes=[e for e in registry if e['split']=='development']
    feature_path=ensure_feature_cache(output, study)
    configs=[c for c in candidates() if c['family']==family]
    write_json(output/'candidates.json',configs)
    with ProcessPoolExecutor(max_workers=workers,initializer=initialize,initargs=(str(study_path),str(feature_path))) as pool:
        rows=[r for result in pool.map(evaluate,[(c,episodes,str(output)) for c in configs]) for r in result]
    pd.DataFrame(rows).to_csv(output/'development.csv',index=False)
    summary=aggregate(rows)
    summary.to_csv(output/'development_summary.csv',index=False)
    rank_development(summary,configs,family).to_csv(output/'development_fit_ranking.csv',index=False)
