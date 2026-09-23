"""Independent all-trial audit of the fixed, already-observed full-v2 study."""
from __future__ import annotations
import argparse,copy,json,sys,time
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor,as_completed
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal,ROUND_FLOOR
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
for p in (ROOT,ROOT/'scripts'):
    if str(p) not in sys.path:sys.path.insert(0,str(p))
from scripts.audit_official_v2 import read,nav_metrics,primary_months
from scripts.audit_tuning_2nd import (check,equal,sha,second_metrics,compare_metrics,ledger_inputs,
 trial_ledger_audit,_trade_arithmetic_audit,_isolated_auditor,legacy_audit_model,
 posttrade_price_envelope)
from scripts.audit_a_deep import normalize_signal_numbers
REPAIR='REPLAN_FUNDED_RULE_REPAIR'
MIXED='BUY_CASH_CAP_CORRECTION_PREFUNDED_MIXED'
TRACKS=('official_ex_post','historical_pit')
AUDIT_CONTEXTS={}


def prefunded(orders,config,cash,holdings=None):
    check(not orders.symbol.duplicated().any(),'Duplicate/same-symbol opposing orders')
    buy=orders[orders.shares.gt(0)];sell=orders[orders.shares.lt(0)]
    if buy.empty or sell.empty:return False
    check(orders.reason.isin([REPAIR,MIXED]).all(),'Unrecognized mixed-order authorization')
    budget=float((buy.shares*buy.sizing_price*config['price_buffer']*(1+config['commission'])).sum())
    check(budget<=cash+.005,'Mixed order depends on same-day sale proceeds')
    if holdings is not None:
        for r in sell.itertuples():check(-r.shares<=holdings.get(r.symbol,0)+1e-6,'Mixed sell exceeds prior inventory')
    return True


def fill_prefunded(fills,lookup,config,cash,holdings):
    if fills.empty or not(fills.shares.gt(0).any() and fills.shares.lt(0).any()):return
    signals=fills.signal_date.unique();check(len(signals)==1,'Mixed plans have different signal days')
    batch=lookup.xs(signals[0],level='signal_date').reset_index()
    prefunded(batch,config,cash,holdings)
    buy=fills[fills.shares.gt(0)]
    check(float((buy.notional+buy.fee+buy.tax).sum())<=cash+.005,'Mixed actual buys use sale proceeds')


full_ledger=_isolated_auditor(legacy_audit_model,[
 ("check(not (todays.shares.gt(0).any() and todays.shares.lt(0).any()), name + ' ' + date + ': simultaneous funding sells/buys')",
  'fill_prefunded(todays, order_lookup, settings, cash, q)'),
 ("order.reason != 'BUY_CASH_CAP_CORRECTION'","order.reason not in ('BUY_CASH_CAP_CORRECTION', REPAIR, MIXED)")],
 dict(fill_prefunded=fill_prefunded,REPAIR=REPAIR,MIXED=MIXED))


def metrics(eq,plan,warnings,cfg):
    m=second_metrics(eq,cfg);m.update(nav_metrics(eq.nav,cfg['initial_cash']))
    active=plan.iloc[:-1]
    m.update(replanning_days=int(active.replanning_triggered.sum()),
      recovered_trade_days=int(active.final_reason.eq(REPAIR).sum()),
      validated_hold_days=int(active.final_reason.str.startswith('HOLD_REVALIDATED').sum()),
      hold_without_envelope_days=int(active.final_reason.eq('HOLD_REVALIDATED_CURRENT_ONLY').sum()),
      no_valid_plan_days=int(active.final_reason.str.startswith('INFEASIBLE').sum()),
      unfilled_orders=int(warnings.issue.str.startswith('UNFILLED').sum()))
    return m


def qualifies(row):
    return row['status']=='COMPLETE' and all(row[k]==0 for k in ('measured_hard_breach_days','no_valid_plan_days','unfilled_orders'))


def winner(rows):
    valid=[r for r in rows if qualifies(r)]
    return min(valid,key=lambda r:(-r['total_return'],r['max_drawdown'],r['turnover_two_way'],r['candidate_id'])) if valid else None


def source_context(track):
    if track=='official_ex_post':
        folder=ROOT/'data/tuning_2nd/official_universe/processed';daily=pd.read_csv(folder/'daily.csv',low_memory=False);universe=read(folder/'universe.csv')
        check(json.loads((folder/'readiness.json').read_text())['status']=='DATA_READY','Official data not ready')
    else:
        daily=pd.read_csv(ROOT/'data/v2/market_daily.csv',low_memory=False);universe=read(ROOT/'data/extended/processed/universe_20241231.csv')
    check(len(universe)==universe.symbol.nunique()==150,'Expected150 identities')
    known=universe.known_at if 'known_at' in universe else universe.known_at_assumption
    check(bool(pd.to_datetime(known,utc=True).le(pd.Timestamp('2025-01-02T08:55+08:00')).all())==(track=='historical_pit'),'False PIT membership dates')
    daily.date=pd.to_datetime(daily.date)
    calendar=[pd.Timestamp(x) for x in sorted(daily.date.unique()) if pd.Timestamp('2025-01-01')<=x<=pd.Timestamp('2026-09-21')]
    check(len(calendar)==417,'Calendar is not417 sessions')
    execution=daily[['date','symbol','turnover','execution_volume']].copy();execution.date=execution.date.dt.strftime('%Y-%m-%d')
    market=daily.copy();market.date=market.date.dt.strftime('%Y-%m-%d');market=market.set_index(['date','symbol'])
    clock=sorted(execution.date.unique());following=dict(zip(clock[:-1],clock[1:]))
    return dict(daily=daily,universe=universe,calendar=calendar,execution=execution,market=market,following=following,
      inputs=ledger_inputs(daily,calendar,universe))


def orders_and_hold(folder,eq,cfg,ctx,plan):
    orders=read(folder/'orders.csv');held=read(folder/'holdings.csv');fills=read(folder/'trades.csv')
    holdings={(r.date,r.symbol):float(r.shares) for r in held.itertuples()}
    held_by_day={d:dict(zip(g.symbol,g.shares)) for d,g in held.groupby('date')}
    cash=eq.set_index('date').cash.to_dict();nav=eq.set_index('date').nav.to_dict()
    date_set=set(eq.date);market=ctx['market'];fillkeys=set(zip(fills.date,fills.symbol))
    mixed=0;rounding=0;unfilled=0
    for day,batch in orders.groupby('signal_date'):
        if ctx['following'].get(day) not in date_set:continue
        q=held_by_day.get(day,{})
        is_mixed=prefunded(batch,cfg,cash.get(day,cfg['initial_cash']),q)
        mixed+=int(is_mixed)
        if is_mixed:
            buys=fills[fills.date.eq(ctx['following'][day])&fills.shares.gt(0)]
            check(float((buys.notional+buys.fee+buys.tax).sum())<=cash.get(day,cfg['initial_cash'])+.005,'Actual mixed buy not prefunded')
    for r in orders.itertuples():
        q=holdings.get((r.signal_date,r.symbol),0.)
        equal(r.target_shares,q+r.shares,'Target shares',atol=1e-6)
        q_lot=round(q/1000)*1000
        check(abs(q-q_lot)<=1e-6,'Real odd holding was traded')
        target_lot=round(float(r.target_shares)/1000)*1000
        check(abs(float(r.target_shares)-target_lot)<=1e-6,'Target is genuinely non-board-lot')
        rounding+=int(float(r.target_shares)!=target_lot or q!=q_lot)
        w,n,p=map(lambda x:Decimal(str(x)),(r.target_weight,r.signal_nav,r.sizing_price))
        decoded=int((w*n/p/1000).to_integral_value(rounding=ROUND_FLOOR))*1000
        check(decoded==target_lot and abs(decoded-q-r.shares)<=1e-6,'Decimal formula mismatch')
        check(np.floor(r.target_weight*r.signal_nav/r.sizing_price/1000)*1000==target_lot,'Float formula mismatch')
        equal(r.sizing_price,float(market.at[(r.signal_date,r.symbol),'close']),'Prior-close sizing',atol=1e-8)
        if r.signal_date!=eq.date.iloc[-1]:equal(r.signal_nav,nav.get(r.signal_date,cfg['initial_cash']),'Prior NAV',atol=.005)
        check(r.target_weight<= (cfg['tsmc_max_weight'] if r.symbol.startswith('2330.') else cfg['max_weight'])+1e-12,'Declared cap exceeded')
        day=ctx['following'].get(r.signal_date)
        if day not in date_set:continue
        source=market.loc[(day,r.symbol)] if (day,r.symbol) in market.index else None
        hasprice=source is not None and np.isfinite(source.turnover) and np.isfinite(source.execution_volume) and source.turnover>0 and source.execution_volume>0
        expected=bool(hasprice and q*float(source.split)+r.shares>=-1e-6)
        check(((day,r.symbol) in fillkeys)==expected,'Fill completeness mismatch')
        unfilled+=int(not expected)
    # Same causal session state, independently reconstructed current/envelope HOLD checks.
    grouped={d:g for d,g in held.groupby('date')}
    for r in plan.iloc[:-1].itertuples():
        if not str(r.final_reason).startswith('HOLD_REVALIDATED'):continue
        pos=grouped.get(r.date,held.iloc[:0]);qs=dict(zip(pos.symbol,pos.shares))
        check(cfg['min_count']<=len(qs)<=cfg['max_count'],'Invalid HOLD count')
        check(all((r.date,s) in market.index for s in qs),'Validated HOLD missing a current quote')
        prices={s:float(market.at[(r.date,s),'close']) for s in qs};c=cash.get(r.date,cfg['initial_cash'])
        book=c+sum(qs[s]*prices[s] for s in qs)
        check(c>=0 and c/book<.25,'Invalid nominal HOLD cash')
        check(all(qs[s]*prices[s]/book <= (cfg['tsmc_max_weight'] if s.startswith('2330.') else cfg['max_weight'])+1e-10 for s in qs),'Invalid nominal HOLD cap')
        check(r.nominal_failures=='','HOLD has hidden nominal failure')
        env=posttrade_price_envelope(qs,c,prices,{},cfg)
        envelope_ok=env['cash_min']>=-1e-6 and env['cash_ratio_max']<.25-1e-12 and all(w <= (cfg['tsmc_max_weight'] if s.startswith('2330.') else cfg['max_weight'])+1e-10 for s,w in env['max_weights'].items())
        if r.final_reason=='HOLD_REVALIDATED_ENVELOPE':check(envelope_ok and r.hold_envelope_failures=='','False envelope HOLD')
        if r.final_reason=='HOLD_REVALIDATED_CURRENT_ONLY':check(not envelope_ok and r.hold_envelope_failures!='','False CURRENT_ONLY status')
    return dict(orders=len(orders),mixed_prefunded_days=mixed,submicroshare_rounding_rows=rounding,unfilled=unfilled)


def position_audit(folder,ctx):
    trades=read(folder/'trades.csv');held=read(folder/'holdings.csv');inp=ctx['inputs']
    change=trades.pivot(index='date',columns='symbol',values='shares').reindex(index=inp['dates'],columns=inp['symbols']).fillna(0).to_numpy(float)
    split=np.cumprod(inp['split'],axis=0);expected=split*np.cumsum(change/split,axis=0);expected[np.abs(expected)<1e-6]=0
    actual=held.pivot(index='date',columns='symbol',values='shares').reindex(index=inp['dates'],columns=inp['symbols']).fillna(0).to_numpy(float)
    check(np.array_equal(actual>1e-6,expected>1e-6),'Position identities differ from action/trade ledger')
    check(np.allclose(actual,expected,rtol=1e-12,atol=1e-6),'Position quantities differ from action/trade ledger')
    return float(np.abs(actual-expected).max())


def trial_audit(folder,trial,ctx):
    for name,digest in json.loads((folder/'receipt.json').read_text()).items():check(sha(folder/name)==digest,'Trial receipt mismatch')
    check(not(folder/'failure.json').exists(),'Failed trial contamination')
    cfg=json.loads((folder/'config.json').read_text());check(cfg['full_tuning_params']==trial['params'],'Trial parameters changed')
    from src.official_deep_tuning import config_for
    check(cfg==config_for({'base':json.loads((ROOT/'config/strategy_v2.json').read_text()),'track':ctx['track']},trial),'Generated configuration differs from declared trial')
    eq=read(folder/'equity.csv');plan=read(folder/'plan_audit.csv');warnings=read(folder/'warnings.csv')
    check(eq.date.tolist()==ctx['inputs']['dates'],'Missing trial sessions')
    check(len(plan)==418 and plan.date.iloc[1:].tolist()==eq.date.tolist(),'Wrong plan timing/calendar')
    check(plan.final_reason.iloc[:-1].tolist()==eq.executed_plan.tolist(),'Plan not executed next session')
    check(eq.submission_status.eq('BLOCK_SUBMISSION').all() and eq.active_share_status.str.startswith('UNKNOWN').all(),'False formal readiness')
    _trade_arithmetic_audit(folder/'trades.csv',eq,ctx['execution'],cfg)
    ledger_error=trial_ledger_audit(folder/'trades.csv',eq,ctx['inputs'],cfg)
    pos_error=position_audit(folder,ctx)
    contract=orders_and_hold(folder,eq,cfg,ctx,plan)
    m=metrics(eq,plan,warnings,cfg);saved=json.loads((folder/'metrics.json').read_text())
    compare_metrics(saved,m,str(folder),require=('total_return','max_drawdown','measured_hard_breach_days','no_valid_plan_days','unfilled_orders'))
    equal(contract['unfilled'],m['unfilled_orders'],'Unfilled diagnostic count',atol=0)
    expected_month=pd.DataFrame(primary_months(eq,cfg['initial_cash']));actual_month=read(folder/'monthly.csv')
    pd.testing.assert_frame_equal(actual_month,expected_month,check_dtype=False,atol=1e-9,rtol=1e-12)
    eligible=qualifies(dict(status='COMPLETE',**m));check(saved['eligible']==eligible,'Incorrect eligibility')
    return dict(**m,status='COMPLETE',eligible=eligible,candidate_id=trial['candidate_id'],track=ctx['track'],
        ledger_error=ledger_error,position_error=pos_error,contract=contract)


def _trial_worker(job):
    track,trial,folder=job
    return trial_audit(folder,trial,AUDIT_CONTEXTS[track])


def exact_tables(folder,reference,names=('equity','orders','trades','holdings')):
    for name in names:pd.testing.assert_frame_equal(read(folder/f'{name}.csv'),read(reference/f'{name}.csv'),check_dtype=False,check_exact=True)
    return list(names)


def prefix(output,track,trial):
    from src import tuning_a_deep as deep,official_deep_tuning as full
    from src.tuning_features import FeatureCache
    cutoff='2025-12-31' if track=='historical_pit' else '2026-06-30'
    ctx=deep.context(track);ctx['daily']=ctx['daily'][pd.to_datetime(ctx['daily'].date).le(pd.Timestamp(cutoff))].copy()
    ctx['bars']=ctx['bars'][pd.to_datetime(ctx['bars'].date).le(pd.Timestamp(cutoff))].copy()
    ctx['cache']=deep.NumericFeatureCache(FeatureCache(ctx['daily'],ctx['bars']))
    cfg=full.config_for(ctx,trial);cfg['end']=cutoff;actual=full.run_model(ctx,cfg);folder=output/track/'final/full_tuned_v2'
    for table,datecol in [('orders','signal_date'),('trades','date'),('plan_audit','date'),('signals','date')]:
        expected=read(folder/f'{table}.csv');expected=expected[expected[datecol].le(cutoff)].reset_index(drop=True)
        got=actual[table].fillna('').reset_index(drop=True)
        if table=='signals':got=normalize_signal_numbers(got);expected=normalize_signal_numbers(expected)
        pd.testing.assert_frame_equal(got,expected,check_dtype=False,atol=1e-5,rtol=1e-12)
        if 'shares' in got:pd.testing.assert_frame_equal(got[[datecol,'symbol','shares']],expected[[datecol,'symbol','shares']],check_dtype=False,check_exact=True)
    eq=read(folder/'equity.csv');eq=eq[eq.date.le(cutoff)].reset_index(drop=True);got=actual['equity'].fillna('')
    pd.testing.assert_frame_equal(got[got.date.lt(cutoff)],eq[eq.date.lt(cutoff)],check_dtype=False,atol=1e-5,rtol=1e-12)
    check(np.allclose(got.economic_nav,eq.economic_nav,atol=1e-5,rtol=1e-12),'Prefix economic NAV changed')
    h=read(folder/'holdings.csv');h=h[h.date.le(cutoff)].reset_index(drop=True)
    pd.testing.assert_frame_equal(actual['holdings'][['date','symbol','shares']],h[['date','symbol','shares']],check_dtype=False,check_exact=True)
    return dict(status='PASS',cutoff=cutoff,sessions=len(eq),trades=len(actual['trades']),economic_nav_max_error=float(np.abs(got.economic_nav-eq.economic_nav).max()),
      scope='TEMPORAL_INVARIANCE_NOT_UNSEEN_OOS; terminal receivable settlement excluded from book comparison')


def audit(output):
    manifest=json.loads((output/'manifest.json').read_text());study=manifest['study']
    check(manifest.get('outputs_complete') is True and not(output/'failure.json').exists(),'Study incomplete/failed')
    check(manifest['expected_trials']==manifest['completed_trials']==810,'Expected all810 trials')
    for rel,digest in manifest['hashes'].items():
        check(sha(ROOT/rel)==digest,'Frozen input changed '+rel)
        check(sha(output/'input_snapshot'/rel)==digest,'Input snapshot changed '+rel)
    receipt=json.loads((output/'receipt.json').read_text())
    for rel,digest in receipt.items():check(sha(output/rel)==digest,'Output receipt changed '+rel)
    check(study==json.loads((ROOT/'config/official_deep_study.json').read_text()),'Study mismatch')
    local=json.loads((output/'local_candidates.json').read_text());check(local['exploration_sha256']==sha(output/'exploration.csv'),'Adaptive parent inputs changed')
    trials=study['candidates']+local['candidates'];check(len(trials)==405 and len({t['candidate_id'] for t in trials})==405,'Incomplete unique candidates')
    from scripts.prepare_official_deep import canonical
    check(len({canonical(t['params']) for t in trials})==405,'Effective duplicate candidates')
    summary=read(output/'trials.csv');check(len(summary)==810 and not summary.duplicated(['track','candidate_id']).any(),'Incomplete trial summary')
    check(summary.status.eq('COMPLETE').all(),'Unfinished trial')
    all_rows=[];contexts={};full_stats={};started=time.monotonic()
    for track in TRACKS:
        ctx=source_context(track);ctx['track']=track;contexts[track]=ctx
    AUDIT_CONTEXTS.update(contexts)
    with ProcessPoolExecutor(max_workers=4,mp_context=mp.get_context('fork')) as pool:
        futures=[pool.submit(_trial_worker,(track,trial,output/track/'trials'/trial['candidate_id'])) for track in TRACKS for trial in trials]
        for i,future in enumerate(as_completed(futures),1):
            row=future.result()
            observed=summary[(summary.track==row['track'])&summary.candidate_id.eq(row['candidate_id'])]
            check(len(observed)==1,'Missing trial summary row')
            compare_metrics(observed.iloc[0],{k:v for k,v in row.items() if isinstance(v,(int,float))},'Summary',require=('total_return','max_drawdown','measured_hard_breach_days','no_valid_plan_days','unfilled_orders'))
            check(bool(observed.iloc[0].eligible)==row['eligible'],'Summary eligibility differs')
            all_rows.append(row)
            if i%25==0:print(f'AUDITED {i}/810 elapsed={time.monotonic()-started:.1f}s',flush=True)
    # Reconstruct adaptive membership after independent exploration metrics pass.
    from scripts.run_official_deep import local_candidates
    exploration=read(output/'exploration.csv').to_dict('records')
    recalculated=local_candidates(study,exploration);check(recalculated==local['candidates'],'Adaptive design changed')
    selected=winner([r for r in all_rows if r['track']=='official_ex_post']);check(selected is not None,'No eligible official winner')
    choice=json.loads((output/'selection.json').read_text());check(choice['candidate_id']==selected['candidate_id'],'Wrong official book-NAV winner')
    trial=next(t for t in trials if t['candidate_id']==selected['candidate_id']);check(choice['params']==trial['params'],'Wrong selected parameters')
    check(choice['selected_on']=='official_ex_post' and choice['scope']=='EX_POST_DEVELOPMENT','False selection scope')
    comparison=read(output/'comparison.csv');months=read(output/'monthly.csv');check(len(comparison)==6 and len(months)==126,'Incomplete final report tables')
    prefixes={};replays={}
    for track in TRACKS:
        ctx=contexts[track];base=json.loads((ROOT/'config/strategy_v2.json').read_text());curves=read(output/track/'nav.csv')
        for model in ['full_tuned_v2','v1_matched','0050']:
            folder=output/track/'final'/model;cfg=json.loads((folder/'config.json').read_text());eq=read(folder/'equity.csv')
            _,full_stats[track+'/'+model]=full_ledger(output/track/'final',model,ctx['daily'],ctx['calendar'],base)
            m=second_metrics(eq,cfg);m.update(nav_metrics(eq.nav,cfg['initial_cash']))
            row=comparison[(comparison.track==track)&comparison.model.eq(model)];check(len(row)==1,'Missing comparison')
            compare_metrics(row.iloc[0],m,'Final comparison',require=('total_return','max_drawdown','measured_hard_breach_days'))
            expected=pd.DataFrame(primary_months(eq,cfg['initial_cash']));actual=months[(months.track==track)&months.model.eq(model)].drop(columns=['track','model']).reset_index(drop=True)
            pd.testing.assert_frame_equal(actual,expected,check_dtype=False,atol=1e-9,rtol=1e-12)
            check(curves.date.tolist()==eq.date.tolist(),'Curve calendar mismatch')
            check((np.abs(curves[model]-eq.nav)<=4*np.spacing(np.maximum(np.abs(eq.nav),1))).all(),'Plotted book NAV differs')
            if model=='full_tuned_v2':
                check(cfg['full_tuning_params']==trial['params'],'Different selected parameters across pools')
                replays[track+'/'+model]=exact_tables(folder,output/track/'trials'/trial['candidate_id'],('equity','orders','trades','holdings','plan_audit'))
            else:replays[track+'/'+model]=exact_tables(folder,ROOT/'outputs/official_v2_reaudit'/track/'final'/model)
        prefixes[track]=prefix(output,track,trial)
        print('PREFIX PASS '+track,flush=True)
    (output/'physical_prefix_audit.json').write_text(json.dumps(prefixes,indent=2)+'\n')
    artifacts={rel:sha(output/rel) for rel in receipt}
    artifacts.update({'receipt.json':sha(output/'receipt.json'),'physical_prefix_audit.json':sha(output/'physical_prefix_audit.json')})
    return dict(status='PASS',auditor_sha256=sha(__file__),input_hashes=manifest['hashes'],artifact_hashes=artifacts,
      receipt_sha256=sha(output/'receipt.json'),manifest_sha256=sha(output/'manifest.json'),verified_at=datetime.now(timezone.utc).isoformat(),
      checks=dict(trials=810,sessions_per_trial=417,selected=selected['candidate_id'],
       eligible_by_track={t:sum(r['eligible'] for r in all_rows if r['track']==t) for t in TRACKS},
       max_ledger_error=max(r['ledger_error'] for r in all_rows),max_position_error=max(r['position_error'] for r in all_rows),
       mixed_prefunded_days=sum(r['contract']['mixed_prefunded_days'] for r in all_rows),
       submicroshare_rounding_rows=sum(r['contract']['submicroshare_rounding_rows'] for r in all_rows),
       full_ledger=full_stats,exact_replays=replays,physical_prefix=prefixes),
      formal_submission='BLOCK_UNKNOWN_ACTIVE_SHARE_CORPORATE_AND_PLATFORM',
      limitations=['All417 sessions were previously used for development; no unseen OOS evidence.',
       'Official2026 membership is retrospective; PIT is the SAME selected parameters, not another selected winner.',
       'Zero observed hard/invalid/unfilled does not prove future feasibility, official Active Share or accepted submissions.',
       'CURRENT_ONLY holds meet observed prior-close constraints but lack the next-session envelope guarantee.',
       'Submicroshare float residual normalization is numerical only; genuine corporate odd inventories remain untradeable.',
       'Official warning/rollback/disqualification are not simulated; fixed-share corporate-date order semantics remain assumed.'])


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,default=ROOT/'outputs/full_tuned_v2');args=p.parse_args();out=args.output.resolve()
    try:result=audit(out)
    except Exception as exc:
        result=dict(status='FAIL',error=str(exc),auditor_sha256=sha(__file__),verified_at=datetime.now(timezone.utc).isoformat())
        (out/'audit.json').write_text(json.dumps(result,indent=2)+'\n');raise
    (out/'audit.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('artifact_hashes','input_hashes')},indent=2))
