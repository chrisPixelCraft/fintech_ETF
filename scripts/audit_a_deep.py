"""Independent source, search, eligibility and accounting audit for A deep tuning.

PASS covers numerical reconstruction and the declared development experiment.
It does not certify contest submission, Active Share or unseen-market accuracy.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
for import_root in (ROOT, ROOT/'scripts'):
    if str(import_root) not in sys.path:
        sys.path.insert(0,str(import_root))
from scripts.audit_tuning_2nd import (audit_model, audit_report_tables, check,
    compare_metrics, equal, exact_replay, ledger_inputs, measured_hard_mask,
    monthly_audit, read_csv, second_metrics, sha, trial_ledger_audit, trial_trade_audit)

FIELDS = ('target_count','replacement_margin','max_replacements_per_day',
          'volatility_spike_ratio','one_day_chase_return','volume_low','volume_high',
          'four_hour_mode','return_short','return_long','ema_fast','ema_slow',
          'macd_fast','macd_slow','macd_signal','momentum_weight','long_return_fraction')
PAIR_FIELDS = {'return_pair':('return_short','return_long'),
               'ema_pair':('ema_fast','ema_slow'),
               'macd_tuple':('macd_fast','macd_slow','macd_signal')}
FIXED = dict(min_count=20,max_count=30,cash_target=0,max_weight=.1,
    tsmc_max_weight=.25,commission=.001425,sell_tax=.003,lot_size=1000,
    price_buffer=1.1,price_lower_buffer=.9,cash_guard_ratio=.12,
    cash_guard_headroom=.02,warmup_sessions=200,execution='vwap',
    dividend_cash_policy='end_of_period',allocation_mode='local',research_shadow=True)
SIGNAL_NUMERIC_COLUMNS = ('open','high','low','close','volume','dividend','split',
    'split_restoration_factor','turnover','execution_volume','official_turnover',
    'execution_vwap','ema20','ema50','ema100','ema200','signal_price',
    'return20','return50','return1','volume_ratio','volatility_ratio','macd_hist',
    'trend','long_trend','score')


def normalize_signal_numbers(frame):
    """Parse only declared numeric signal fields; reject malformed nonblank data."""
    result=frame.copy(deep=True)
    for name in SIGNAL_NUMERIC_COLUMNS:
        if name in result:
            result[name]=pd.to_numeric(result[name].replace('',np.nan),errors='raise')
    return result


def read_prefix_csv(path):
    """Recover the original serialized IEEE float for exact replay checks."""
    return pd.read_csv(path,keep_default_na=False,low_memory=False,float_precision='round_trip')


def weights(params):
    m,q=params['momentum_weight'],params['long_return_fraction']
    return dict(zip(('return20','return50','volume','macd','trend','long_trend'),
        [round(x,12) for x in (m*(1-q),m*q,(1-m)*.4,(1-m)*.2,(1-m)*.2,(1-m)*.2)]))


def effective_key(params):
    out=copy.deepcopy(params)
    out.pop('momentum_weight');out.pop('long_return_fraction')
    out['score_weights']=weights(params)
    if out['max_replacements_per_day']==0:
        out['replacement_margin']=0.
    return json.dumps(out,sort_keys=True,separators=(',',':'))


def choose_independently(rows, max_mdd=None, mode=None):
    """Hard validity first; never replace absent evidence with a weak winner."""
    valid=[]
    for row in rows:
        if row.get('status')!='COMPLETE' or row.get('measured_hard_breach_days')!=0:
            continue
        ret,mdd=row.get('economic_total_return'),row.get('economic_max_drawdown')
        if ret is None or mdd is None or not np.isfinite([ret,mdd]).all():
            continue
        if mode is not None and row.get('four_hour_mode')!=mode:
            continue
        if max_mdd is not None and mdd>max_mdd+1e-12:
            continue
        valid.append(row)
    return min(valid,key=lambda r:(-r['economic_total_return'],r['economic_max_drawdown'],r['candidate_id'])) if valid else None


def check_exploration(study):
    """OFAT labels must represent one actual axis; 4H pairs hold all else fixed."""
    trials=study['candidates']
    anchors={t['anchor']:t['params'] for t in trials if t['phase']=='incumbent'}
    check(set(anchors)=={'anchor_p052','anchor_p049'},'Missing original search anchors')
    for trial in trials:
        p=trial['params']
        if trial['phase']=='incumbent':
            check(trial['varied']=='none','Incumbent is mislabeled')
        elif trial['phase']=='ofat':
            axis=trial['varied'];check(axis in study['ofat'],'Undeclared OFAT axis')
            expected=copy.deepcopy(anchors[trial['anchor']])
            value=[p[k] for k in PAIR_FIELDS[axis]] if axis in PAIR_FIELDS else p[axis]
            check(value in study['ofat'][axis],'OFAT value outside declared range')
            if axis in PAIR_FIELDS:expected.update(zip(PAIR_FIELDS[axis],value))
            else:expected[axis]=value
            check(expected==p,'OFAT changed more than one declared axis')
        elif trial['phase']=='sobol':
            check(trial['search_seed'] in study['search_seeds'],'Unknown Sobol seed')
            for axis,choices in study['spaces'].items():
                value=[p[k] for k in PAIR_FIELDS[axis]] if axis in PAIR_FIELDS else p[axis]
                check(value in choices,'Sobol value outside declared design')
        else:
            raise AssertionError('Unknown exploration phase')


def check_config(config,params,base,track):
    check(set(params)==set(FIELDS),'Unexpected effective A parameters')
    for key,value in FIXED.items():
        check(config.get(key)==value,'Frozen execution/competition field changed: '+key)
    for key in ['start','end','initial_cash','slippage_bps','cash_rebalance_lower','cash_rebalance_upper']:
        check(config[key]==base[key],'Frozen base field changed: '+key)
    check(config['tuning_params']==params and config['deep_feature_params']==params,
          'Recorded numeric tuple differs from declared candidate')
    for key in FIELDS[:8]:
        check(config[key]==params[key],'Numeric parameter not applied: '+key)
    check(config['score_weights']==weights(params),'Score-mixture parameter not applied')
    spec=config['feature_spec']
    for field,keys in [('return_lookbacks',('return_short','return_long')),
                       ('ema_spans',('ema_fast','ema_slow')),
                       ('macd_spans',('macd_fast','macd_slow','macd_signal'))]:
        check(spec[field]==[params[k] for k in keys],'Numeric feature metadata mismatch: '+field)
    check(spec['long_ema_spans']==[100,200] and spec['four_hour_ema']==20
          and spec['four_hour_macd']==[12,26,9] and spec['four_hour_ready_bars']==50,
          'Untuned long trend or 4H calculation changed')
    mode=params['four_hour_mode']
    check(config['use_4h']==(mode=='strict') and config['match_4h_coverage']==(mode=='coverage_only'),
          '4H direction changed coverage readiness')
    check(config['universe_mode']==track and config['ex_post_fixed_universe']==(track=='official_ex_post'),
          'Universe interpretation changed')
    policy=config['study_policy']
    check(policy['historical_period_is_development'] is True
          and policy['selection_scope']=='EX_POST_DEVELOPMENT'
          and policy['official_live_submission']=='BLOCK_IF_UNKNOWN','False experiment/live interpretation')


def independent_refinement(study,rows,incumbent_mdd):
    """Reconstruct the recorded adaptive search from verified exploration rows."""
    parents_by_mode={}
    for mode in ['strict','coverage_only']:
        eligible=sorted((r for r in rows if r['status']=='COMPLETE'
                         and r['measured_hard_breach_days']==0 and r['four_hour_mode']==mode),
            key=lambda r:(-r['economic_total_return'],r['economic_max_drawdown'],r['candidate_id']))
        if not eligible:
            continue
        parents=eligible[:2]
        risk=choose_independently(eligible,incumbent_mdd)
        if risk and risk['candidate_id'] not in {p['candidate_id'] for p in parents}:
            parents.append(risk)
        parents_by_mode[mode]=parents
    check(bool(parents_by_mode),'Adaptive search has no eligible parent')
    policy=study['refinement'];spaces=copy.deepcopy(study['spaces'])
    for axis,values in policy['boundary_extensions'].items():
        spaces[axis].extend(values)
    rng=np.random.default_rng(policy['seed'])
    seen={effective_key(t['params']) for t in study['candidates']}
    result=[];attempts=0;modes=list(parents_by_mode)
    while len(result)<policy['trials']:
        check(attempts<=10000,'Independent refinement exhausted uniqueness budget')
        mode=modes[len(result)%len(modes)];parents=parents_by_mode[mode]
        parent=parents[(len(result)//len(modes))%len(parents)]
        params={k:parent[k] for k in FIELDS}
        for key in ['target_count','max_replacements_per_day','return_short','return_long',
                    'ema_fast','ema_slow','macd_fast','macd_slow','macd_signal']:
            params[key]=int(params[key])
        axes=rng.choice(list(spaces),size=int(rng.choice(policy['mutations'])),replace=False)
        for axis in axes:
            choices=spaces[axis]
            current=[params[k] for k in PAIR_FIELDS[axis]] if axis in PAIR_FIELDS else params[axis]
            index=choices.index(current) if current in choices else min(range(len(choices)),key=lambda i:abs(choices[i]-current))
            neighbors=[i for i in range(max(0,index-1),min(len(choices),index+2)) if choices[i]!=current]
            if not neighbors:
                continue
            value=choices[int(rng.choice(neighbors))]
            if axis in PAIR_FIELDS:
                params.update(zip(PAIR_FIELDS[axis],value))
            else:
                params[axis]=value
        attempts+=1;key=effective_key(params)
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(candidate_id=f'r{len(result):04d}',params=params,phase='local_refinement',
            varied='local_joint_bundle',parent=parent['candidate_id'],search_seed=policy['seed']))
    return result


def physical_prefix_audit(output,track,cutoff='2025-12-31'):
    """Remove actual future observations, rebuild the cache, and replay one winner.

    This tests temporal invariance, not the historical validity of selecting a
    hindsight-tuned winner. End-of-prefix dividend settlement is compared via
    economic NAV, with all earlier book balances checked separately.
    """
    from src import tuning_a_deep as deep
    from src.tuning_features import FeatureCache
    folder=output/'final/return_winner'
    if not folder.exists():
        return dict(status='NOT_APPLICABLE_NO_ELIGIBLE_WINNER')
    ctx=deep.context(track)
    ctx['daily']=ctx['daily'][pd.to_datetime(ctx['daily'].date).le(pd.Timestamp(cutoff))].copy()
    ctx['bars']=ctx['bars'][pd.to_datetime(ctx['bars'].date).le(pd.Timestamp(cutoff))].copy()
    ctx['cache']=deep.NumericFeatureCache(FeatureCache(ctx['daily'],ctx['bars']))
    config=json.loads((folder/'config.json').read_text());config['end']=cutoff
    result=deep.run_model(ctx,config)
    for table,date_column in [('trades','date'),('orders','signal_date'),('signals','date')]:
        reference=read_prefix_csv(folder/f'{table}.csv')
        reference=reference[reference[date_column].le(cutoff)].reset_index(drop=True)
        actual=result[table].fillna('').reset_index(drop=True)
        if table=='signals':
            actual=normalize_signal_numbers(actual)
            reference=normalize_signal_numbers(reference)
        # Portfolio state/indicator CSV serializations may differ by a few ULPs.
        pd.testing.assert_frame_equal(actual,reference,check_dtype=False,rtol=1e-12,atol=1e-5)
        exact_columns=[date_column,'symbol']+(['shares'] if 'shares' in actual else ['entry_ok','exit'])
        pd.testing.assert_frame_equal(actual[exact_columns],reference[exact_columns],check_exact=True,check_dtype=False)
    full=read_prefix_csv(folder/'equity.csv');reference=full[full.date.le(cutoff)].reset_index(drop=True)
    actual=result['equity'].fillna('')
    cols=['date','economic_nav','holdings','fees','taxes','costs','traded_notional']
    pd.testing.assert_frame_equal(actual[cols],reference[cols],check_dtype=False,rtol=1e-12,atol=1e-5)
    pd.testing.assert_frame_equal(actual[actual.date.lt(cutoff)],reference[reference.date.lt(cutoff)],check_dtype=False,rtol=1e-12,atol=1e-5)
    actual_holdings=result['holdings'];stored=read_prefix_csv(folder/'holdings.csv')
    stored=stored[stored.date.le(cutoff)].reset_index(drop=True)
    pd.testing.assert_frame_equal(actual_holdings[['date','symbol','shares']],stored[['date','symbol','shares']],check_exact=True,check_dtype=False)
    return dict(status='PASS',cutoff=cutoff,sessions=len(actual),trades=len(result['trades']),
        cutoff_reason=('Retain all 150 official identities and their frozen history-presence guard; latest listing occurs in April 2026.'
                       if track=='official_ex_post' else 'End-of-2025 physical data cutoff; all historical-pool identities have prior history.'),
        candidate_id=config['tuning_candidate_id'],future_daily_and_four_hour_rows_physically_removed=True,
        max_economic_nav_csv_error=float(np.abs(actual.economic_nav-reference.economic_nav).max()),
        interpretation='TEMPORAL_INVARIANCE_NOT_UNSEEN_OR_PRIOR_KNOWN_SELECTION')


def audit(output):
    manifest=json.loads((output/'manifest.json').read_text())
    check(manifest.get('outputs_complete') is True and manifest.get('completed_at'),'Study is incomplete')
    track=manifest['track'];check(track in ('historical_pit','official_ex_post'),'Unknown track')
    check(manifest['selection_scope']=='EX_POST_DEVELOPMENT'
          and manifest['official_submission']=='BLOCKED_UNKNOWN_ACTIVE_SHARE','False OOS/compliance claim')
    snapshot=output/'input_snapshot'
    for relative,expected in manifest['hashes'].items():
        check(sha(ROOT/relative)==expected,'Frozen source changed: '+relative)
        check(sha(snapshot/relative)==expected,'Source snapshot mismatch: '+relative)
    study=json.loads((snapshot/'config/a_deep_study.json').read_text())
    check(study==manifest['study'] and study['status']=='FROZEN','Declared search differs from frozen snapshot')
    check(study['interpretation']=='EX_POST_DEVELOPMENT_NOT_UNSEEN_TEST','History falsely called unseen')
    check_exploration(study)
    base=json.loads((snapshot/'config/strategy_v2.json').read_text())
    previous=ROOT/'outputs/tuning_report_2nd_try'/track
    prior=json.loads((previous/'audit.json').read_text())
    check(prior['status']=='PASS' and sha(previous/'audit.json')==manifest['prior_audit_hash'],'Prior controls are not trusted')
    for relative,expected in prior['artifact_hashes'].items():
        check(sha(previous/relative)==expected,'Previously audited artifact changed: '+relative)
    if track=='historical_pit':
        market_path=snapshot/'data/v2/market_daily.csv'
        universe_path=snapshot/'data/extended/processed/universe_20241231.csv'
    else:
        folder=snapshot/'data/tuning_2nd/official_universe/processed'
        market_path=folder/'daily.csv';universe_path=folder/'universe.csv'
        readiness=json.loads((folder/'readiness.json').read_text())
        data_audit=json.loads((ROOT/'outputs/tuning_report_2nd_try/official_data_audit.json').read_text())
        check(readiness['status']=='DATA_READY' and data_audit['status']=='PASS'
              and readiness['output_hashes']==data_audit['verified_output_hashes'],'Official source gate lost')
        for name,expected in readiness['output_hashes'].items():
            check(sha(folder/name)==expected,'Official input differs from independently released snapshot')
    daily=pd.read_csv(market_path,low_memory=False);daily['date']=pd.to_datetime(daily.date)
    check(not daily.duplicated(['date','symbol']).any(),'Duplicate market rows')
    universe=read_csv(universe_path)
    check(len(universe)==150 and universe.symbol.nunique()==150,'Wrong fixed universe size')
    known=universe['known_at'] if 'known_at' in universe else universe['known_at_assumption']
    actual_dates=pd.to_datetime(known,utc=True)
    cutoff=pd.Timestamp(base['start']).tz_localize('Asia/Taipei')+pd.Timedelta(hours=8,minutes=55)
    check(bool((actual_dates<=cutoff).all())==(track=='historical_pit'),'Universe publication dates misrepresented')
    calendar=sorted(d for d in daily.date.unique() if pd.Timestamp(base['start'])<=d<=pd.Timestamp(base['end']))
    calendar=[pd.Timestamp(d) for d in calendar]
    expected_dates=[str(d.date()) for d in calendar]
    check(len(expected_dates)==417,'Changed common market calendar')
    inputs=ledger_inputs(daily,calendar,universe)
    market=daily[['date','symbol','turnover','execution_volume']].copy();market['date']=market.date.dt.strftime('%Y-%m-%d')
    refinement=json.loads((output/'refinement_candidates.json').read_text())
    check(refinement['scope']=='ADAPTIVE_FULL_DEVELOPMENT_HISTORY','Adaptive search space falsely called prior-known')
    check(refinement['exploration_summary_sha256']==sha(output/'exploration_summary.csv'),'Changed adaptive parent evidence')
    trials=study['candidates']+refinement['candidates']
    check(len(study['candidates'])==study['exploration_trials'],'Exploration count mismatch')
    check(len(refinement['candidates'])==study['local_trials_per_track'],'Local count mismatch')
    check(len(trials)==manifest['expected_trials']==manifest['completed_trials'],'Incomplete declared search')
    declared={t['candidate_id']:t for t in trials}
    check(len(declared)==len(trials),'Duplicate trial IDs')
    check(len({effective_key(t['params']) for t in trials})==len(trials),'Duplicate effective strategies counted as extra evidence')
    check({p.name for p in (output/'trials').iterdir() if p.is_dir()}==set(declared),'Missing/extraneous trial folders')
    summary=read_csv(output/'trial_summary.csv');exploration=read_csv(output/'exploration_summary.csv')
    check(not summary.candidate_id.duplicated().any() and set(summary.candidate_id)==set(declared)
          and summary.status.eq('COMPLETE').all(),'Incomplete/failing candidate summary')
    exploration_ids={t['candidate_id'] for t in study['candidates']}
    check(not exploration.candidate_id.duplicated().any() and set(exploration.candidate_id)==exploration_ids,'Bad exploration membership')
    summary=summary.set_index('candidate_id');exploration=exploration.set_index('candidate_id')
    errors=[];rows=[];frames={};counts={};artifact_paths=[]
    for cid,trial in sorted(declared.items()):
        folder=output/'trials'/cid
        check(not (folder/'failure.json').exists(),'Failure contamination: '+cid)
        receipt=json.loads((folder/'receipt.json').read_text())
        required={'equity.csv','orders.csv','trades.csv','warnings.csv','config.json','metrics.json','monthly.csv'}
        check(required.issubset(receipt),'Incomplete trial receipt: '+cid)
        for filename,expected in receipt.items():
            check(sha(folder/filename)==expected,'Trial receipt mismatch: '+cid+'/'+filename)
        config=json.loads((folder/'config.json').read_text());check_config(config,trial['params'],base,track)
        check(config['tuning_candidate_id']==cid,'Trial configuration misidentified')
        eq=read_csv(folder/'equity.csv')
        check(eq.date.tolist()==expected_dates,'Incomplete candidate calendar: '+cid)
        check(eq.submission_status.eq('BLOCK_SUBMISSION').all()
              and eq.active_share_status.astype(str).str.startswith('UNKNOWN').all(),'False formal certification')
        counts[cid]=trial_trade_audit(folder/'trades.csv',eq,market,config)
        errors.append(trial_ledger_audit(folder/'trades.csv',eq,inputs,config))
        computed=second_metrics(eq,config)
        metrics=json.loads((folder/'metrics.json').read_text())
        for key in ('candidate_id','phase','varied','anchor','search_seed','pair_id','parent'):
            check(metrics.get(key)==trial.get(key),cid+': trial provenance metadata mismatch: '+key)
        for saved,label in [(metrics,'metrics'),(summary.loc[cid],'summary')]:
            compare_metrics(saved,computed,cid+': '+label,require=('economic_total_return','economic_max_drawdown','measured_hard_breach_days'))
            equal(saved['trades'],counts[cid],cid+': actual trade count',atol=0)
            check(str(saved['observed_rule_eligible']).lower()==str(computed['measured_hard_breach_days']==0).lower(),cid+': wrong eligibility')
            check('UNKNOWN' in saved['official_compliance'] and 'BLOCK' in saved['official_compliance'],cid+': false compliance')
        for key,value in trial['params'].items():
            saved=summary.loc[cid,key]
            if isinstance(value,str):check(saved==value,cid+': wrong summary parameter '+key)
            else:equal(saved,value,cid+': wrong summary parameter '+key,atol=1e-12)
        if cid in exploration_ids:
            compare_metrics(exploration.loc[cid],computed,cid+': exploration',require=('economic_total_return','economic_max_drawdown','measured_hard_breach_days'))
        monthly_audit(folder/'monthly.csv',eq,base['initial_cash'])
        rows.append({**metrics,**trial['params']});frames[cid]=eq
        artifact_paths.append(folder/'receipt.json')
    incumbent_id='a0000' if track=='historical_pit' else 'a0001'
    incumbent=next(r for r in rows if r['candidate_id']==incumbent_id)
    expected_local=independent_refinement(study,[r for r in rows if r['candidate_id'] in exploration_ids],incumbent['economic_max_drawdown'])
    check(expected_local==refinement['candidates'],'Adaptive child/parent or seeded membership mismatch')
    sobol=[t for t in trials if t['phase']=='sobol'];pairs={}
    for trial in sobol:pairs.setdefault(trial['pair_id'],[]).append(trial)
    for pair,items in pairs.items():
        check(len(items)==2 and {x['params']['four_hour_mode'] for x in items}=={'strict','coverage_only'},'Unpaired 4H comparison: '+pair)
        left=copy.deepcopy(items[0]['params']);right=copy.deepcopy(items[1]['params'])
        left.pop('four_hour_mode');right.pop('four_hour_mode')
        check(left==right,'4H pair changes nuisance parameters: '+pair)
    expected_selections=dict(return_winner=choose_independently(rows),
        risk_controlled=choose_independently(rows,incumbent['economic_max_drawdown']),
        strict_winner=choose_independently(rows,mode='strict'),incumbent=incumbent)
    selections=json.loads((output/'selection.json').read_text())
    check(set(selections)==set(expected_selections),'Changed final selection contract')
    final_frames={};full_audits={};replays={};selection_audit={}
    for name,row in expected_selections.items():
        selected=selections[name];expected_id=None if row is None else row['candidate_id']
        check(selected['candidate_id']==expected_id,name+': wrong selected candidate')
        check(selected['scope']=='EX_POST_DEVELOPMENT'
              and selected['official_submission']=='BLOCKED_UNKNOWN_ACTIVE_SHARE','Selected artifact falsely certified')
        if row is None:
            check(selected['status']=='NO_ELIGIBLE_WINNER' and not (output/'final'/name).exists(),'Ineligible fallback labeled best')
            selection_audit[name]=dict(candidate_id=None,status='NO_ELIGIBLE_WINNER');continue
        check(selected['status']=='SELECTED_ZERO_MEASURED_HARD' and row['measured_hard_breach_days']==0,'Ineligible best')
        source=output/'trials'/expected_id;target=output/'final'/name
        for table in ('equity','trades','orders'):
            pd.testing.assert_frame_equal(read_csv(source/f'{table}.csv'),read_csv(target/f'{table}.csv'),check_exact=True,check_dtype=False)
        check(json.loads((target/'config.json').read_text())==json.loads((source/'config.json').read_text()),'Final selected config changed')
        final_frames[name]=read_csv(target/'equity.csv')
        _,full_audits[name]=audit_model(output/'final',name,daily,calendar,base)
        selection_audit[name]=dict(candidate_id=expected_id,economic_total_return=row['economic_total_return'],
            economic_max_drawdown=row['economic_max_drawdown'],measured_hard_breach_days=0,trades=counts[expected_id])
    replays['incumbent']=exact_replay(output/'final/incumbent',previous/'final/A')
    for name in ('v1_matched','0050'):
        target=output/'final'/name
        replays[name]=exact_replay(target,previous/'final'/name)
        for filename in ('config.json','metrics.json'):
            check(sha(target/filename)==sha(previous/'final'/name/filename),'Frozen benchmark metadata changed')
        final_frames[name]=read_csv(target/'equity.csv')
        _,full_audits[name]=audit_model(output/'final',name,daily,calendar,base)
    report=audit_report_tables(output,final_frames,base['initial_cash'])
    # The official retrospective pool includes an April-2026 listing.  Keep
    # all 150 identities and the frozen engine's history-presence guard intact.
    prefix_cutoff='2025-12-31' if track=='historical_pit' else '2026-06-30'
    prefix=physical_prefix_audit(output,track,prefix_cutoff)
    prefix_path=output/'physical_prefix_audit.json'
    prefix_path.write_text(json.dumps(prefix,indent=2,ensure_ascii=False)+'\n')
    artifact_paths.append(prefix_path)
    diagnostics=sorted((output/'audit_diagnostics').glob('*.json'))
    artifact_paths.extend(diagnostics)
    artifact_paths += [output/p for p in ('manifest.json','selection.json','trial_summary.csv',
        'exploration_summary.csv','refinement_candidates.json','comparison.csv','monthly_comparison.csv','nav_comparison.csv')]
    artifact_paths += [output/'final'/name/filename for name in final_frames
        for filename in ('config.json','metrics.json','equity.csv','orders.csv','trades.csv','holdings.csv')]
    return dict(status='PASS',scope='MEASURED_RULES_AND_ACCOUNTING_ONLY_NOT_FORMAL_CONTEST_CERTIFICATION',
        verified_at=datetime.now(timezone.utc).isoformat(),auditor_sha256=sha(__file__),track=track,
        trial_count=len(trials),unique_effective_trials=len(trials),paired_four_hour_comparisons=len(pairs),
        input_and_code_hashes=len(manifest['hashes']),max_all_trial_ledger_error=max(errors),
        zero_hard_eligible=sum(r['measured_hard_breach_days']==0 for r in rows),
        universe_publication_dates=sorted(known.astype(str).unique()),
        selection=selection_audit,full_ledger_audits=full_audits,exact_replays=replays,report_tables=report,
        physical_prefix_audit=prefix,
        audit_recovery=dict(classification='AUDITOR_INPUT_REPRESENTATION_AND_PREFIX_UNIVERSE_BOUNDARY',
            prior_diagnostic_artifacts=[str(p.relative_to(output)) for p in diagnostics],
            correction='Strict known-numeric and round-trip CSV parsing; official prefix after every fixed-pool identity first appears; no frozen result/source modifications') if diagnostics else None,
        artifact_hashes={str(p.relative_to(output)):sha(p) for p in artifact_paths},
        limitations=['All dates were already observed during strategy development; no unseen OOS claim.',
            'Adaptive local candidates and original anchors use full-history evidence; prefix rankings are hindsight-informed diagnostics.',
            'Official ex-post membership cannot establish historical point-in-time performance.',
            'Zero observed hard breaches is separate from unknown Active Share, source vintages and actual submission compliance.',
            'Price bounds, whole-order fills, no market impact and unresolved capacity remain modeling assumptions.',
            'The risk-controlled choice is a separately reported retrospective MDD constraint, not a prospective risk guarantee.',
            'A receives additional search budget while legacy v1 and 0050 are fixed contextual controls.'])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    output=Path(args.output)
    if not output.is_absolute():output=ROOT/output
    try:
        result=audit(output)
    except Exception as exc:
        result=dict(status='FAIL',error=str(exc),verified_at=datetime.now(timezone.utc).isoformat(),auditor_sha256=sha(__file__))
        (output/'audit.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
        raise
    (output/'audit.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('artifact_hashes','full_ledger_audits')},indent=2,ensure_ascii=False))
