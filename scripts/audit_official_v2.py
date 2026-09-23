"""Independent supplied-document, literal-order and shadow-ledger audit.

A PASS verifies the recorded experiment, including incompatibilities/failures;
it does not certify D-Plan acceptance, official penalties or Active Share.
"""
from __future__ import annotations
import argparse
import copy
import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT/'scripts'):
    if str(path) not in sys.path:sys.path.insert(0,str(path))
from scripts.audit_tuning_2nd import (audit_model, check, equal, sha, exact_replay,
    second_metrics, compare_metrics, trial_trade_audit, trial_ledger_audit, ledger_inputs, monthly_audit)
from scripts.audit_a_deep import normalize_signal_numbers

MODELS=('A','A_dplan_guard','v1_matched','0050')


def read(path):
    return pd.read_csv(path,keep_default_na=False,low_memory=False,float_precision='round_trip')


def derive_order(weight, nav, price, held, lot=1000):
    """Literal primary-guide formula in decimal arithmetic, independently coded."""
    w,n,p,h,l=map(lambda x:Decimal(str(x)),(weight,nav,price,held,lot))
    if min(n,p,l)<=0 or w<0:raise ValueError('Invalid sizing inputs')
    target=(w*n/p/l).to_integral_value(rounding=ROUND_FLOOR)*l
    return target,target-h


def nav_metrics(values, initial):
    wealth=np.r_[float(initial),np.asarray(values,float)]
    change=wealth[1:]/wealth[:-1]-1
    sigma=np.std(change,ddof=1) if len(change)>1 else 0.
    return dict(total_return=float(wealth[-1]/initial-1),
        max_drawdown=float(1-(wealth/np.maximum.accumulate(wealth)).min()),
        annualized_volatility=float(sigma*np.sqrt(252)),
        sharpe_zero_rf=float(change.mean()/sigma*np.sqrt(252)) if sigma else None)


def primary_months(eq, initial):
    rows=[];previous_book=previous_economic=float(initial)
    for month,group in eq.groupby(pd.to_datetime(eq.date).dt.to_period('M')):
        book=nav_metrics(group.nav,previous_book);economic=nav_metrics(group.economic_nav,previous_economic)
        rows.append(dict(month=str(month),sessions=len(group),book_return=book['total_return'],
            book_max_drawdown=book['max_drawdown'],economic_return=economic['total_return'],
            economic_max_drawdown=economic['max_drawdown'],ending_book_nav=float(group.nav.iloc[-1]),
            ending_economic_nav=float(group.economic_nav.iloc[-1]),
            partial_period=bool(str(month)=='2026-09')))
        previous_book=float(group.nav.iloc[-1]);previous_economic=float(group.economic_nav.iloc[-1])
    equal(np.prod([1+r['book_return'] for r in rows]),previous_book/initial,'Book monthly compounding',atol=1e-10)
    equal(np.prod([1+r['economic_return'] for r in rows]),previous_economic/initial,'Economic monthly compounding',atol=1e-10)
    return rows


def order_contract(folder,daily,calendar,strict=False):
    orders=read(folder/'orders.csv');positions=read(folder/'holdings.csv');eq=read(folder/'equity.csv')
    fills=read(folder/'trades.csv');config=json.loads((folder/'config.json').read_text())
    holdings={(r.date,r.symbol):float(r.shares) for r in positions.itertuples()}
    nav=eq.set_index('date').nav.to_dict();quote=daily.copy();quote['date']=pd.to_datetime(quote.date).dt.strftime('%Y-%m-%d')
    market=quote.set_index(['date','symbol'])
    all_dates=sorted(quote.date.unique());following=dict(zip(all_dates[:-1],all_dates[1:]))
    fill_keys=set(zip(fills.date,fills.symbol));details=[];event_orders=[];missing=[];float_mismatches=0
    for r in orders.itertuples():
        key=(r.signal_date,r.symbol);held=holdings.get(key,0.)
        book=nav.get(r.signal_date,config['initial_cash'])
        # The terminal plan is constructed before the terminal dividend payout.
        if r.signal_date==eq.date.iloc[-1]:book=float(r.signal_nav)
        equal(r.signal_nav,book,'Signal-day book NAV',atol=.005)
        equal(r.sizing_price,float(market.at[key,'close']),'Signal-day exchange close',atol=1e-9)
        equal(r.target_shares,held+r.shares,'Fixed target shares',atol=1e-7)
        target,derived=derive_order(r.target_weight,r.signal_nav,r.sizing_price,held)
        cap=config['tsmc_max_weight'] if r.symbol.startswith('2330.') else config['max_weight']
        check(0<=r.target_weight<=cap+1e-12,'Declared weight outside cap')
        odd=abs(held/1000-round(held/1000))>1e-10
        mismatch=abs(float(derived)-r.shares)>1e-6
        float_target=np.floor(r.target_weight*r.signal_nav/r.sizing_price/1000)*1000
        float_mismatch=abs(float_target-held-r.shares)>1e-6
        float_mismatches+=int(float_mismatch)
        row=dict(signal_date=r.signal_date,symbol=r.symbol,held_shares=held,
            planned_shares=float(r.shares),declared_weight=float(r.target_weight),
            formula_target=float(target),formula_order=float(derived),odd_held=odd,mismatch=mismatch,float_mismatch=bool(float_mismatch))
        if mismatch or odd:details.append(row)
        if strict:check(not odd and not mismatch and not float_mismatch,'Guard literal formula failure: '+str(row))
        day=following.get(r.signal_date)
        if day not in set(eq.date):continue
        source=market.loc[(day,r.symbol)] if (day,r.symbol) in market.index else None
        valid=source is not None and np.isfinite(source.execution_volume) and source.execution_volume>0 and np.isfinite(source.turnover) and source.turnover>0
        if source is not None and float(source.split)!=1:
            event_orders.append(dict(**row,execution_date=day,split=float(source.split),
                actual_post_action_shares_before_order=held*float(source.split)))
        expected=bool(valid and held*(float(source.split) if source is not None else 1)+r.shares>=-1e-6)
        check(((day,r.symbol) in fill_keys)==expected,'Missing/unexpected complete fill: '+str((day,r.symbol)))
        if not expected:missing.append(dict(date=day,symbol=r.symbol,reason='NO_PRICE' if not valid else 'ACTION_ADJUSTED_INSUFFICIENT_SHARES'))
    return dict(orders=len(orders),arithmetic_policy='DECIMAL_OF_SERIALIZED_NUMBERS; canonical close and NAV; server arithmetic unknown',
        float_floor_mismatch_rows=float_mismatches,mismatch_rows=sum(r['mismatch'] for r in details),
        odd_held_order_rows=sum(r['odd_held'] for r in details),
        mismatch_days=len({r['signal_date'] for r in details if r['mismatch']}),
        split_execution_orders=event_orders,unfilled=missing,
        details=details,status='PASS_LITERAL_FORMULA' if not details else 'INCOMPATIBLE_WITH_LITERAL_FORMULA')


def document_inventory():
    folder=ROOT/'official_docs';paths=sorted(p for p in folder.iterdir() if p.is_file())
    check(len(paths)==10,'Supplied document inventory changed')
    extracted=ROOT/'daily_auto/reference/extracted'
    inventory=json.loads((extracted.parent/'inventory.json').read_text())
    check(len(inventory)==10,'Incomplete reference inventory')
    for record in inventory:check(sha(ROOT/record['source'])==record['sha256'],'Reference inventory source changed')
    reference=json.loads((extracted.parent/'official_reference.json').read_text())
    etf=next(extracted.glob('*主動型ETF列表*.txt')).read_text()
    etfs=re.findall(r'\b\d{5}A\b',etf);check(len(etfs)==len(set(etfs))==30,'Wrong Active Share ETF list')
    stock=next(extracted.glob('*150檔清單*.txt')).read_text()
    tickers=re.findall(r'(?<!\d)\d{4}(?!\d)',stock)
    check(len(tickers)==len(set(tickers))==150,'Wrong supplied 150-stock list')
    official=read(ROOT/'data/tuning_2nd/official_universe/processed/universe.csv')
    check(set(map(str,official.official_ticker))==set(tickers),'Official scenario differs from supplied PDF')
    check({r['ticker'] for r in reference['stocks']}==set(tickers),'Canonical reference stock parse mismatch')
    check({r['ticker'] for r in reference['active_etfs']}==set(etfs),'Canonical reference ETF parse mismatch')
    return dict(files={str(p.relative_to(ROOT)):sha(p) for p in paths},
        reference_hashes={str(p.relative_to(ROOT)):sha(p) for p in [extracted.parent/'inventory.json',extracted.parent/'official_reference.json',*extracted.glob('*.txt')]},etfs=etfs,
        official_tickers=tickers,official_symbols=official.symbol.tolist(),
        unresolved=['Active Share top-ten normalization, union/cash handling and dated ETF holdings are unspecified or unavailable.',
            'Example TEAM_043 calls C2 mismatch a warning; schema/guide require equality. Enforcement severity is version-ambiguous.',
            'PDF submission opens19:30 previous day; schema says05:00. Deadline08:55 is consistent.',
            'Corporate-action pending-order adjustment and odd-share trade handling are not explicitly specified.',
            'No historical server acceptance, actual holdings records or official warning/rollback history are available.'])


def physical_prefix(output,track):
    from src import tuning_2nd
    from src.official_v2_review import run_guard
    from src.tuning_features import FeatureCache
    cutoff='2025-06-30' if track=='historical_pit' else '2026-06-30'
    ctx=tuning_2nd.context(track)
    ctx['daily']=ctx['daily'][pd.to_datetime(ctx['daily'].date).le(pd.Timestamp(cutoff))].copy()
    ctx['bars']=ctx['bars'][pd.to_datetime(ctx['bars'].date).le(pd.Timestamp(cutoff))].copy()
    ctx['cache']=FeatureCache(ctx['daily'],ctx['bars'])
    cfg=json.loads((output/'final/A/config.json').read_text());cfg['end']=cutoff
    result=run_guard(ctx,cfg);folder=output/'final/A_dplan_guard'
    for table,datecol in [('orders','signal_date'),('trades','date'),('signals','date')]:
        expected=read(folder/f'{table}.csv');expected=expected[expected[datecol].le(cutoff)].reset_index(drop=True)
        actual=result[table].fillna('').reset_index(drop=True)
        if table=='signals':actual=normalize_signal_numbers(actual);expected=normalize_signal_numbers(expected)
        pd.testing.assert_frame_equal(actual,expected,check_dtype=False,atol=1e-5,rtol=1e-12)
        cols=[datecol,'symbol']+(['shares'] if 'shares' in actual else ['entry_ok','exit'])
        pd.testing.assert_frame_equal(actual[cols],expected[cols],check_dtype=False,check_exact=True)
    expected=read(folder/'equity.csv');expected=expected[expected.date.le(cutoff)].reset_index(drop=True)
    actual=result['equity'].fillna('')
    pd.testing.assert_frame_equal(actual[actual.date.lt(cutoff)],expected[expected.date.lt(cutoff)],check_dtype=False,atol=1e-5,rtol=1e-12)
    for column in ['economic_nav','holdings','fees','taxes','costs','traded_notional']:
        check(np.allclose(actual[column],expected[column],atol=1e-5,rtol=1e-12),'Prefix '+column+' mismatch')
    expected_h=read(folder/'holdings.csv');expected_h=expected_h[expected_h.date.le(cutoff)].reset_index(drop=True)
    pd.testing.assert_frame_equal(result['holdings'][['date','symbol','shares']],expected_h[['date','symbol','shares']],check_dtype=False,check_exact=True)
    return dict(status='PASS',cutoff=cutoff,sessions=len(actual),trades=len(result['trades']),
        max_economic_nav_error=float(np.abs(actual.economic_nav-expected.economic_nav).max()),
        scope='TEMPORAL_INVARIANCE_NOT_UNSEEN_OOS',
        terminal_book_difference='Prefix settles receivables at its own end; compare preterminal book NAV and all economic NAV.',
        cutoff_reason='Official latest IPO exists only after April2026; keep150 identities.' if track=='official_ex_post' else 'Physical mid2025 prefix.')


def audit(output):
    manifest=json.loads((output/'manifest.json').read_text());track=manifest['track']
    check(manifest.get('outputs_complete') is True and not (output/'failure.json').exists(),'Incomplete/failed outputs')
    check(manifest['parameter_search'] is False and manifest['formal_submission']=='BLOCK','Invalid study scope')
    check(manifest['expected_versions']==list(MODELS),'Changed model set')
    check(manifest['candidate_id']=={'historical_pit':'p052','official_ex_post':'p049'}[track],'Retuned A')
    for relative,digest in manifest['hashes'].items():check(sha(ROOT/relative)==digest,'Frozen input changed: '+relative)
    receipt=json.loads((output/'receipt.json').read_text())
    for relative,digest in receipt.items():check(sha(output/relative)==digest,'Artifact changed: '+relative)
    docs=document_inventory()
    if track=='historical_pit':
        daily_path=ROOT/'data/v2/market_daily.csv';universe=read(ROOT/'data/extended/processed/universe_20241231.csv')
        known=universe.known_at_assumption
    else:
        base=ROOT/'data/tuning_2nd/official_universe/processed';daily_path=base/'daily.csv';universe=read(base/'universe.csv');known=universe.known_at
        check(json.loads((base/'readiness.json').read_text())['status']=='DATA_READY','Unready source data')
    check(len(universe)==universe.symbol.nunique()==150,'Incomplete universe')
    check(bool(pd.to_datetime(known,utc=True).le(pd.Timestamp('2025-01-02T08:55:00+08:00')).all())==(track=='historical_pit'),'Backdated universe knowledge')
    daily=pd.read_csv(daily_path);daily['date']=pd.to_datetime(daily.date)
    calendar=[pd.Timestamp(d) for d in sorted(daily.date.unique()) if pd.Timestamp('2025-01-01')<=d<=pd.Timestamp('2026-09-21')]
    check(len(calendar)==manifest['expected_sessions']==417 and str(calendar[0].date())=='2025-01-02','Changed market calendar')
    base=json.loads((ROOT/'config/strategy_v2.json').read_text());inputs=ledger_inputs(daily,calendar,universe)
    execution=daily[['date','symbol','turnover','execution_volume']].copy();execution['date']=execution.date.dt.strftime('%Y-%m-%d')
    prior=ROOT/'outputs/tuning_report_2nd_try'/track
    models={};monthly=[];replays={};errors=[];contracts={};all_details=[]
    comparison=read(output/'comparison.csv').set_index('model');check(set(comparison.index)==set(MODELS),'Missing comparison model')
    pooled_monthly=read(output/'monthly_comparison.csv')
    a_config=json.loads((output/'final/A/config.json').read_text())
    guard_config=json.loads((output/'final/A_dplan_guard/config.json').read_text());guard_config.pop('official_review_policy')
    check(guard_config==a_config,'Guard changed alpha/risk/execution parameters')
    for name in MODELS:
        folder=output/'final'/name;cfg=json.loads((folder/'config.json').read_text());eq=read(folder/'equity.csv')
        _,full=audit_model(output/'final',name,daily,calendar,base)
        metric=second_metrics(eq,cfg);compare_metrics(json.loads((folder/'metrics.json').read_text()),metric,name,require=('economic_total_return','economic_max_drawdown','measured_hard_breach_days'))
        compare_metrics(comparison.loc[name],metric,name+' comparison',require=('economic_total_return','economic_max_drawdown','measured_hard_breach_days'))
        for field,value in nav_metrics(eq.nav,cfg['initial_cash']).items():equal(comparison.loc[name,field],value,name+' primary '+field,atol=1e-8)
        monthly_audit(folder/'monthly.csv',eq,cfg['initial_cash'])
        expected=read(folder/'monthly.csv');actual=pooled_monthly[pooled_monthly.model.eq(name)].drop(columns='model').reset_index(drop=True)
        pd.testing.assert_frame_equal(actual,expected,check_dtype=False,check_exact=True)
        if name!='0050':
            trial_trade_audit(folder/'trades.csv',eq,execution,cfg)
            errors.append(trial_ledger_audit(folder/'trades.csv',eq,inputs,cfg))
        if name!='A_dplan_guard':replays[name]=exact_replay(folder,prior/'final'/name)
        contract=order_contract(folder,daily,calendar,strict=name=='A_dplan_guard')
        for row in contract.pop('details'):all_details.append(dict(model=name,**row))
        contracts[name]=contract
        trades=read(folder/'trades.csv');held=read(folder/'holdings.csv')
        models[name]=dict({**full,**metric},primary_nav=nav_metrics(eq.nav,cfg['initial_cash']),
            held_names_outside_supplied_150=sorted(set(held.symbol)-set(docs['official_symbols'])),
            max_daily_volume_participation=float(trades.volume_participation.max()),
            ending_receivable=float(eq.dividend_receivable.iloc[-1]),
            terminal_distribution=float(eq.dividend_receivable.iloc[-2])+sum(float(r.shares)*float(daily.set_index(['date','symbol']).dividend.get((calendar[-1],r.symbol),0.)) for r in held[held.date.eq(eq.date.iloc[-2])].itertuples()),
            capacity_interpretation='Contest assumes complete VWAP fills; ratio is real-market capacity evidence, not contest rejection.')
        monthly.extend(dict(model=name,**row) for row in primary_months(eq,cfg['initial_cash']))
    a=normalize_signal_numbers(read(output/'final/A/signals.csv'));b=normalize_signal_numbers(read(output/'final/A_dplan_guard/signals.csv'))
    pd.testing.assert_frame_equal(a.drop(columns='plan_reason'),b.drop(columns='plan_reason'),check_dtype=False,check_exact=True)
    pd.DataFrame(monthly).to_csv(output/'primary_nav_monthly.csv',index=False)
    pd.DataFrame(all_details).to_csv(output/'literal_formula_mismatches.csv',index=False)
    prefix=physical_prefix(output,track);(output/'physical_prefix_audit.json').write_text(json.dumps(prefix,indent=2)+'\n')
    artifacts={relative:sha(output/relative) for relative in receipt}
    for file in ('receipt.json','primary_nav_monthly.csv','literal_formula_mismatches.csv','physical_prefix_audit.json'):artifacts[file]=sha(output/file)
    return dict(status='PASS',scope='SHADOW_ACCOUNTING_AND_DOCUMENT_COMPATIBILITY_DIAGNOSTIC_NOT_OFFICIAL_CERTIFICATION',
        verified_at=datetime.now(timezone.utc).isoformat(),track=track,auditor_sha256=sha(__file__),
        receipt_sha256=sha(output/'receipt.json'),manifest_sha256=sha(output/'manifest.json'),
        source_hashes=len(manifest['hashes']),documents=docs,models=models,order_contracts=contracts,
        max_independent_ledger_error=max(errors),exact_replays=replays,unchanged_alpha_signals=True,
        physical_prefix=prefix,artifact_hashes=artifacts,formal_submission='BLOCK',
        limitations=['AS UNKNOWN: 30 named ETFs do not supply dated top-ten weights or resolve normalization.',
            'Original A is a frozen development control; guard is an assumption-labeled compatibility sensitivity, not a new selected alpha.',
            'Guard freezes odd-held names; resulting economic or rule deterioration is reported without retuning.',
            'Pending orders remain fixed shares through corporate actions; supplied docs do not confirm ex-date order rescaling.',
            'Shadow records do not emulate invalid-day rollback, cumulative warnings or disqualification.',
            'All history is previously observed development data; official 2026 membership is an explicit retrospective scenario.',
            'Book NAV and its MDD are primary; economic NAV includes unreinvestable receivables before terminal payout.'])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    output=Path(args.output);output=output if output.is_absolute() else ROOT/output
    try:result=audit(output)
    except Exception as exc:
        result=dict(status='FAIL',error=str(exc),auditor_sha256=sha(__file__),verified_at=datetime.now(timezone.utc).isoformat())
        (output/'audit.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n');raise
    (output/'audit.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('artifact_hashes','documents','models')},indent=2,ensure_ascii=False))
