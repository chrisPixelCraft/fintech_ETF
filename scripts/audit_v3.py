"""Independent v3 factor, intervention, portfolio and reporting verification."""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
for path in (ROOT,ROOT/'scripts'):
    if str(path) not in sys.path:sys.path.insert(0,str(path))
from scripts.audit_tuning_2nd import (audit_model,audit_report_tables,check,
    compare_metrics,equal,exact_replay,ledger_inputs,monthly_audit,second_metrics,
    sha,trial_ledger_audit,trial_trade_audit)
from scripts.audit_a_deep import normalize_signal_numbers

VERSIONS=('A','B','C','D')
MODELS=(*VERSIONS,'v1_matched','0050')
FACTOR_FLOATS=('stock_return','market_return','beta_covariance','beta_market_variance',
    'beta','volatility','ols_market_variance','ols_beta_lagged','ols_alpha_lagged',
    'one_step_residual','residual_sum','residual_std','residual_momentum')
V3_NUMERIC=('audit_base_score','v3_beta','v3_volatility','v3_residual_momentum',
            'v3_defensive_score','v3_residual_percentile','v3_blended_score')


def read(path):
    return pd.read_csv(path,keep_default_na=False,low_memory=False,float_precision='round_trip')


def number(series):
    return pd.to_numeric(series.replace('',np.nan),errors='raise')


def signal_numbers(frame):
    result=normalize_signal_numbers(frame)
    for col in V3_NUMERIC:
        if col in result:result[col]=number(result[col])
    return result


def total_return_prices(daily):
    """Independent old-share event recurrence, retaining missing quote dates."""
    frame=daily.copy();frame['date']=pd.to_datetime(frame.date).dt.normalize()
    calendar=pd.DatetimeIndex(sorted(frame.date.unique()))
    prices={};quotes={}
    for symbol,rows in frame.groupby('symbol',sort=True):
        rows=rows.sort_values('date')
        closes=rows.close.to_numpy(float);splits=rows.split.to_numpy(float)
        cash=rows.dividend.to_numpy(float);values=np.empty(len(rows))
        values[0]=closes[0]
        for i in range(1,len(rows)):
            values[i]=values[i-1]*((closes[i]*splits[i]+cash[i])/closes[i-1])
        observed=rows.volume.to_numpy(float)>0
        index=pd.DatetimeIndex(rows.date)
        quotes[str(symbol)]=pd.Series(observed,index=index).reindex(calendar,fill_value=False)
        prices[str(symbol)]=pd.Series(values,index=index).where(observed).reindex(calendar)
    return calendar,prices,quotes


def reference_factors(daily,parameters):
    """Direct valid-observation loops plus NumPy least squares, not producer code."""
    p=parameters;calendar,prices,quotes=total_return_prices(daily)
    market=prices[p['benchmark_symbol']]
    mr=market/market.shift(1)-1
    frames=[]
    for symbol,price in prices.items():
        sr=price/price.shift(1)-1
        paired=np.flatnonzero(np.isfinite(sr.to_numpy()) & np.isfinite(mr.to_numpy()))
        x=mr.iloc[paired].to_numpy(float);y=sr.iloc[paired].to_numpy(float)
        frame=pd.DataFrame(index=calendar,columns=FACTOR_FLOATS,dtype=float)
        frame['stock_return']=sr;frame['market_return']=mr
        for prefix in ('beta','volatility','ols','residual'):
            frame[prefix+'_obs_count']=0
            frame[prefix+'_span_sessions']=0
            frame[prefix+'_window_start']=pd.NaT
            frame[prefix+'_window_end']=pd.NaT
        frame['paired_observation_number']=0
        residual_values=[];residual_positions=[]
        for k,pos in enumerate(paired):
            date=calendar[pos]
            frame.at[date,'paired_observation_number']=k+1
            for prefix,n,lag in [('beta',p['beta_window'],False),
                                  ('volatility',p['volatility_window'],False),
                                  ('ols',p['ols_window'],True)]:
                stop=k if lag else k+1;start=max(stop-n,0)
                frame.at[date,prefix+'_obs_count']=stop-start
                if stop>start:
                    a,b=paired[start],paired[stop-1]
                    frame.at[date,prefix+'_window_start']=calendar[a]
                    frame.at[date,prefix+'_window_end']=calendar[b]
                    frame.at[date,prefix+'_span_sessions']=b-a+1
            n=p['beta_window']
            if k+1>=n:
                bx=x[k-n+1:k+1];by=y[k-n+1:k+1]
                variance=float(np.var(bx,ddof=1))
                covariance=float(np.dot(bx-bx.mean(),by-by.mean())/(n-1))
                frame.at[date,'beta_market_variance']=variance
                frame.at[date,'beta_covariance']=covariance
                if variance>p['variance_epsilon']:frame.at[date,'beta']=covariance/variance
            n=p['volatility_window']
            if k+1>=n:frame.at[date,'volatility']=np.std(y[k-n+1:k+1],ddof=1)
            n=p['ols_window']
            if k>=n:
                ox=x[k-n:k];oy=y[k-n:k]
                variance=float(np.var(ox,ddof=1))
                frame.at[date,'ols_market_variance']=variance
                if variance>p['variance_epsilon']:
                    alpha,beta=np.linalg.lstsq(np.column_stack([np.ones(n),ox]),oy,rcond=None)[0]
                    residual=y[k]-alpha-beta*x[k]
                    frame.at[date,'ols_alpha_lagged']=alpha
                    frame.at[date,'ols_beta_lagged']=beta
                    frame.at[date,'one_step_residual']=residual
                    residual_values.append(residual);residual_positions.append(pos)
                    count=min(len(residual_values),p['residual_window'])
                    frame.at[date,'residual_obs_count']=count
                    frame.at[date,'residual_window_start']=calendar[residual_positions[-count]]
                    frame.at[date,'residual_window_end']=date
                    frame.at[date,'residual_span_sessions']=pos-residual_positions[-count]+1
                    if count==p['residual_window']:
                        window=np.array(residual_values[-count:]);std=window.std(ddof=1)
                        frame.at[date,'residual_sum']=window.sum();frame.at[date,'residual_std']=std
                        if std>p['residual_std_floor']:
                            frame.at[date,'residual_momentum']=window.sum()/(np.sqrt(count)*std)
        frame['date']=calendar;frame['symbol']=symbol
        frame['total_return_price']=price;frame['quote_present']=quotes[symbol]
        frame['current_pair_valid']=np.isfinite(sr)&np.isfinite(mr)
        frame['beta_valid']=np.isfinite(frame.beta)&frame.current_pair_valid
        frame['volatility_valid']=np.isfinite(frame.volatility)&frame.current_pair_valid
        frame['residual_valid']=np.isfinite(frame.residual_momentum)&frame.current_pair_valid
        frame['common_valid']=frame.beta_valid&frame.volatility_valid&frame.residual_valid
        frames.append(frame.reset_index(drop=True))
    reference=pd.concat(frames,ignore_index=True).sort_values(['date','symbol']).reset_index(drop=True)
    ema=market.dropna().ewm(span=p['regime_ema_span'],adjust=False,min_periods=p['regime_ema_span']).mean().reindex(calendar)
    ret=market/market.shift(p['regime_return_sessions'])-1
    known=np.isfinite(ema)&np.isfinite(ret)&quotes[p['benchmark_symbol']]
    weak=known&market.lt(ema)&ret.lt(0)
    state=pd.DataFrame(dict(date=calendar,total_return_price=market.to_numpy(),
        regime_ema=ema.to_numpy(),regime_return=ret.to_numpy(),market_return=mr.to_numpy(),
        quote_present=quotes[p['benchmark_symbol']].to_numpy(),
        quote_observation_count=quotes[p['benchmark_symbol']].cumsum().to_numpy(),
        regime_known=known.to_numpy(),weak_market=weak.to_numpy(),
        regime=np.where(~known,'UNKNOWN',np.where(weak,'WEAK','NORMAL'))))
    return reference,state


def compare_features(observed,expected,market,expected_market):
    a=observed.copy();a['date']=pd.to_datetime(a.date)
    b=expected.copy();b['date']=pd.to_datetime(b.date)
    a=a.sort_values(['date','symbol']).reset_index(drop=True)
    b=b.sort_values(['date','symbol']).reset_index(drop=True)
    pd.testing.assert_frame_equal(a[['date','symbol']],b[['date','symbol']],check_exact=True)
    errors={}
    for col in (*FACTOR_FLOATS,'total_return_price'):
        av=number(a[col]).to_numpy(float);bv=b[col].to_numpy(float)
        check(np.array_equal(np.isnan(av),np.isnan(bv)),'Factor missingness differs: '+col)
        check(np.allclose(av,bv,equal_nan=True,atol=1e-9,rtol=1e-8),'Independently reconstructed factor mismatch: '+col)
        errors[col]=float(np.nanmax(np.abs(av-bv))) if np.isfinite(av).any() else 0.
    for col in ('quote_present','current_pair_valid','beta_valid','volatility_valid','residual_valid','common_valid','paired_observation_number'):
        check(np.array_equal(a[col],b[col]),'Factor observation/validity mismatch: '+col)
    for prefix in ('beta','volatility','ols','residual'):
        for suffix in ('obs_count','span_sessions'):
            col=prefix+'_'+suffix
            check(np.array_equal(a[col].to_numpy(int),b[col].to_numpy(int)),'Window metadata mismatch: '+col)
        for suffix in ('window_start','window_end'):
            col=prefix+'_'+suffix
            av=pd.to_datetime(a[col].replace('',pd.NaT));bv=pd.to_datetime(b[col])
            pd.testing.assert_series_equal(av,bv,check_names=False)
    current=pd.to_datetime(a.available_through.replace('',pd.NaT))
    check((current[a.current_pair_valid]==a.loc[a.current_pair_valid,'date']).all(),'Factor availability date mismatch')
    check(current[~a.current_pair_valid].isna().all(),'Invalid factor claims current availability')
    ols_end=pd.to_datetime(a.ols_window_end.replace('',pd.NaT))
    fit=number(a.ols_beta_lagged).notna()
    check((ols_end[fit]<a.loc[fit,'date']).all(),'OLS uses contemporaneous/future fit observations')
    m=market.copy();m['date']=pd.to_datetime(m.date)
    n=expected_market
    for col in ('date','quote_present','quote_observation_count','regime_known','weak_market','regime'):
        pd.testing.assert_series_equal(m[col],n[col],check_names=False,check_dtype=False)
    for col in ('total_return_price','regime_ema','regime_return','market_return'):
        check(np.allclose(number(m[col]),n[col],equal_nan=True,atol=1e-9,rtol=1e-10),'Market-state reconstruction mismatch: '+col)
    return dict(rows=len(a),market_sessions=len(m),maximum_factor_errors=errors)


def expected_ranks(factors):
    valid=factors.common_valid.astype(bool)
    f=factors[valid]
    beta=number(f.beta);vol=number(f.volatility);res=number(f.residual_momentum)
    b=((-beta).rank(method='average',pct=True)+(-vol).rank(method='average',pct=True))/2
    c=res.rank(method='average',pct=True)
    d=(b.rank(method='average',pct=True)+c)/2
    return {'B':b,'C':c,'D':d},valid


def audit_interventions(output,factors,market,parameters):
    factor=factors.copy();factor['date']=pd.to_datetime(factor.date).dt.strftime('%Y-%m-%d')
    factor=factor.set_index(['date','symbol'])
    regime=market.assign(date=pd.to_datetime(market.date).dt.strftime('%Y-%m-%d')).set_index('date').regime
    baseline=signal_numbers(read(output/'final/A/signals.csv')).set_index(['date','symbol']).sort_index()
    audits={}
    for version in VERSIONS:
        saved=signal_numbers(read(output/'final'/version/'signals.csv'))
        indexed=saved.set_index(['date','symbol']).sort_index()
        check(indexed.index.equals(baseline.index),'Variant changes available rows: '+version)
        for name in ('entry_ok','exit'):
            pd.testing.assert_series_equal(indexed[name],baseline[name],check_exact=True)
            pd.testing.assert_series_equal(indexed['audit_base_'+name],baseline[name],check_exact=True,check_names=False)
        for col in ('audit_base_score',):
            check(np.allclose(indexed[col],baseline.score,equal_nan=True,atol=1e-13,rtol=0),'Baseline score changed: '+version)
        snapshots=read(output/'final'/version/'snapshots.csv').set_index('date')
        statuses={};changed=0;applied_days=0;changed_days=0;invalid_rows_reranked=0
        for day,group in saved.groupby('date',sort=True):
            g=group.set_index('symbol');f=factor.xs(day).reindex(g.index)
            ranks,valid=expected_ranks(f)
            count=int(valid.sum());r=regime.loc[day]
            status=('BASELINE_A' if version=='A' else
                    'BASELINE_FALLBACK_MARKET_UNKNOWN' if r=='UNKNOWN' else
                    'NORMAL_MARKET_BASELINE' if r!='WEAK' else
                    'BASELINE_FALLBACK_INSUFFICIENT_COMMON_VALID' if count<parameters['minimum_common_valid'] else
                    'WEAK_MARKET_RERANKED')
            statuses[status]=statuses.get(status,0)+1
            applied=status=='WEAK_MARKET_RERANKED'
            expected=ranks[version].reindex(g.index).fillna(0.) if applied else g.audit_base_score
            check(np.allclose(g.score,expected,equal_nan=True,atol=1e-13,rtol=0),version+': incorrect transformed score on '+day)
            check(g.v3_rank_applied.eq(applied).all() and g.v3_regime.eq(r).all(),'Incorrect intervention flag')
            check(np.array_equal(g.v3_factor_valid,valid),'Changed common factor coverage')
            for name in ('beta','volatility','residual_momentum'):
                check(np.allclose(g['v3_'+name],number(f[name]),equal_nan=True,atol=1e-13,rtol=0),'Signal/factor export mismatch')
            for column,scores in [('v3_defensive_score',ranks['B']),('v3_residual_percentile',ranks['C']),('v3_blended_score',ranks['D'])]:
                check(np.allclose(g[column],scores.reindex(g.index),equal_nan=True,atol=1e-13,rtol=0),'Incorrect diagnostic percentile')
            changed_mask=~np.isclose(g.score,g.audit_base_score,rtol=0,atol=1e-14,equal_nan=True)
            check(np.array_equal(g.audit_score_changed,changed_mask),'Wrong changed-score diagnosis')
            meta=json.loads(snapshots.loc[day,'metadata'])
            check(meta['version']==version and meta['status']==status and meta['signal_date']==day,'Wrong callback provenance')
            check(meta['parameters']==parameters and meta['common_valid_count']==count,'Callback parameter/coverage drift')
            check(meta['rank_universe']=='ALL_RANKED_COMMON_VALID_NOT_ENTRY_FILTERED','Entry-filtered ranking confounder')
            changed+=int(changed_mask.sum());changed_days+=int(changed_mask.any())
            applied_days+=int(applied);invalid_rows_reranked+=int((~valid).sum()) if applied else 0
        audits[version]=dict(signal_days=saved.date.nunique(),applied_weak_signal_days=applied_days,
            score_changed_days=changed_days,score_changed_rows=changed,
            invalid_factor_rows_scored_zero_on_applied_days=invalid_rows_reranked,status_counts=statuses)
    return audits


def audit_evaluation(output,frames,daily,metrics,interventions):
    calendar,prices,_=total_return_prices(daily)
    instrument=prices['0050.TW'].ffill()
    saved_index=read(output/'instrument_0050.csv')
    check(pd.DatetimeIndex(pd.to_datetime(saved_index.date)).equals(calendar),'Instrument calendar differs')
    check(np.allclose(number(saved_index.instrument_total_return_index),instrument,equal_nan=True,rtol=1e-10,atol=1e-8),'Instrument corporate-action adjustment differs')
    monthly=read(output/'monthly_comparison.csv');labels=read(output/'evaluation_only_month_labels.csv')
    observed_months=sorted(monthly.month.unique());expected={};month_labels={}
    for model,eq in frames.items():
        previous=1e9
        for month,group in eq.groupby(pd.to_datetime(eq.date).dt.to_period('M')):
            key=str(month);ret=float(group.economic_nav.iloc[-1]/previous-1)
            expected[(model,key)]=ret;previous=float(group.economic_nav.iloc[-1])
            if model=='A':
                first,last=pd.Timestamp(group.date.iloc[0]),pd.Timestamp(group.date.iloc[-1])
                before=instrument.index[instrument.index<first][-1]
                month_labels[key]=float(instrument.loc[last]/instrument.loc[before]-1)
    check(len(monthly)==len(expected) and not monthly.duplicated(['model','month']).any(),'Incorrect monthly table membership')
    for row in monthly.itertuples():
        ret=expected[(row.model,row.month)];underlying=month_labels[row.month]
        for value,target in [(row.economic_return,ret),(row.instrument_return,underlying),
                (row.excess_vs_0050,ret-expected[('0050',row.month)]),
                (row.excess_vs_instrument,ret-underlying),(row.difference_vs_A,ret-expected[('A',row.month)])]:
            equal(value,target,'Evaluation return/difference mismatch',atol=1e-10)
        check(bool(row.weak_month)==(underlying<0),'Future-month label mismatch')
        check(bool(row.partial_month)==(row.month=='2026-09'),'Partial last month hidden')
    check(set(labels.month)==set(month_labels) and len(labels)==len(month_labels),'Missing evaluation-only labels')
    for row in labels.itertuples():
        equal(row.instrument_return,month_labels[row.month],'Evaluation-only label source',atol=1e-10)
        check(bool(row.weak_month)==(month_labels[row.month]<0),'Wrong evaluation-only label')
    summary=read(output/'weak_month_summary.csv').set_index('model')
    check(set(summary.index)==set(frames),'Wrong weak-month model set')
    weak=[m for m in observed_months if month_labels[m]<0]
    strong=[m for m in observed_months if month_labels[m]>=0]
    base_positive=sum(expected[('A',m)]>0 for m in weak)
    base_wins=sum(expected[('A',m)]>expected[('0050',m)] for m in weak)
    reconstructed={}
    for name in frames:
        wr=np.array([expected[(name,m)] for m in weak]);sr=np.array([expected[(name,m)] for m in strong])
        delta=np.array([expected[(name,m)]-expected[('A',m)] for m in weak])
        excess=np.array([expected[(name,m)]-expected[('0050',m)] for m in weak])
        raw=dict(weak_months=len(weak),strong_months=len(strong),weak_mean_return=float(wr.mean()),
            weak_mean_excess_0050=float(excess.mean()),
            weak_mean_excess_instrument=float(np.mean([expected[(name,m)]-month_labels[m] for m in weak])),
            weak_mean_difference_A=float(delta.mean()),weak_positive_months=int((wr>0).sum()),
            weak_outperform_0050_months=int((excess>0).sum()),weak_worst_month=float(wr.min()),
            strong_mean_excess_0050=float(np.mean([expected[(name,m)]-expected[('0050',m)] for m in strong])),
            strong_mean_difference_A=float(np.mean([expected[(name,m)]-expected[('A',m)] for m in strong])),
            full_economic_return=metrics[name]['economic_total_return'],full_economic_mdd=metrics[name]['economic_max_drawdown'],
            measured_hard_breach_days=metrics[name]['measured_hard_breach_days'],costs=metrics[name]['costs'],turnover=metrics[name]['turnover_two_way'])
        for field,value in raw.items():equal(summary.loc[name,field],value,name+': weak summary '+field,atol=1e-8)
        met=bool(name in ('B','C','D') and raw['measured_hard_breach_days']==0
                 and raw['weak_mean_difference_A']>0 and raw['weak_positive_months']>=base_positive
                 and raw['weak_outperform_0050_months']>=base_wins
                 and raw['full_economic_mdd']<=metrics['A']['economic_max_drawdown']+1e-12)
        check(bool(summary.loc[name,'development_objective_met'])==met,'Incorrect development objective label')
        check(summary.loc[name,'adoption']=='HOLD','Development result improperly promoted')
        reconstructed[name]={**raw,'development_objective_met':met}
    diagnostics=read(output/'intervention_diagnostics.csv').set_index('model')
    base=read(output/'final/A/holdings.csv')
    base_days={str(d):g.set_index('symbol').shares.to_dict() for d,g in base.groupby('date')}
    for name in VERSIONS:
        holdings=read(output/'final'/name/'holdings.csv')
        own={str(d):g.set_index('symbol').shares.to_dict() for d,g in holdings.groupby('date')}
        count=sum(own.get(d,{})!=base_days.get(d,{}) for d in frames[name].date)
        for col in ('signal_days','score_changed_days','score_changed_rows'):
            equal(diagnostics.loc[name,col],interventions[name][col],'Intervention count '+col,atol=0)
        equal(diagnostics.loc[name,'holdings_differ_from_A_days'],count,'Different holdings days',atol=0)
    return dict(weak_months=weak,strong_months=strong,partial_month='2026-09',summaries=reconstructed)


def physical_prefix(output,track,study):
    from src import v3_study as v3
    from src.v3_signals import WeakMarketSignals
    from src.tuning_features import FeatureCache
    cutoff='2025-12-31' if track=='historical_pit' else '2026-06-30'
    ctx=v3.context(track,study)
    ctx['daily']=ctx['daily'][pd.to_datetime(ctx['daily'].date).le(pd.Timestamp(cutoff))].copy()
    ctx['bars']=ctx['bars'][pd.to_datetime(ctx['bars'].date).le(pd.Timestamp(cutoff))].copy()
    ctx['cache']=FeatureCache(ctx['daily'],ctx['bars'])
    ctx['v3_signals']=WeakMarketSignals(ctx['daily'],study['signal_parameters'])
    ctx['v3_base']['end']=cutoff
    results={}
    for version in ('B','C','D'):
        result=v3.run_model(ctx,version,study);folder=output/'final'/version
        for table,datecol in [('orders','signal_date'),('trades','date'),('signals','date')]:
            reference=read(folder/f'{table}.csv');reference=reference[reference[datecol].le(cutoff)].reset_index(drop=True)
            actual=result[table].fillna('').reset_index(drop=True)
            if table=='signals':reference=signal_numbers(reference);actual=signal_numbers(actual)
            pd.testing.assert_frame_equal(actual,reference,check_dtype=False,rtol=1e-12,atol=1e-5)
            columns=[datecol,'symbol']+(['shares'] if 'shares' in actual else ['entry_ok','exit'])
            pd.testing.assert_frame_equal(actual[columns],reference[columns],check_dtype=False,check_exact=True)
        reference=read(folder/'equity.csv');reference=reference[reference.date.le(cutoff)].reset_index(drop=True)
        actual=result['equity'].fillna('')
        fields=['date','economic_nav','holdings','fees','taxes','costs','traded_notional']
        pd.testing.assert_frame_equal(actual[fields],reference[fields],check_dtype=False,rtol=1e-12,atol=1e-5)
        pd.testing.assert_frame_equal(actual[actual.date.lt(cutoff)],reference[reference.date.lt(cutoff)],check_dtype=False,rtol=1e-12,atol=1e-5)
        stored=read(folder/'holdings.csv');stored=stored[stored.date.le(cutoff)].reset_index(drop=True)
        pd.testing.assert_frame_equal(result['holdings'][['date','symbol','shares']],stored[['date','symbol','shares']],check_dtype=False,check_exact=True)
        results[version]=dict(status='PASS',sessions=len(actual),trades=len(result['trades']),
            max_economic_nav_error=float(np.abs(actual.economic_nav-reference.economic_nav).max()))
    return dict(status='PASS',cutoff=cutoff,versions=results,
        reason='Keep all fixed-universe identities; official pool includes an April-2026 IPO.' if track=='official_ex_post' else 'End-of-2025 physical prefix',
        interpretation='TEMPORAL_INVARIANCE_NOT_UNSEEN_VALIDATION')


def audit(output):
    manifest=json.loads((output/'manifest.json').read_text())
    check(manifest.get('outputs_complete') is True and manifest.get('completed_at'),'Incomplete v3 study')
    check(not (output/'failure.json').exists(),'Failed output contamination')
    track=manifest['track'];study=manifest['study'];snapshot=output/'input_snapshot'
    check(track in ('historical_pit','official_ex_post') and manifest['expected_versions']==list(VERSIONS),'Wrong study versions')
    check(manifest['selection_scope']=='FIXED_DEVELOPMENT_COMPARISON'
          and manifest['formal_submission']=='BLOCKED_UNKNOWN_ACTIVE_SHARE','Unjustified OOS or compliance claim')
    check(study['evaluation']['parameter_search'] is False
          and study['evaluation']['historical_period_is_development'] is True,'Experiment scope changed')
    for relative,expected in manifest['hashes'].items():
        check(sha(ROOT/relative)==expected,'Frozen source changed: '+relative)
        check(sha(snapshot/relative)==expected,'Snapshot mismatch: '+relative)
    check(json.loads((snapshot/'config/v3_weak_market.json').read_text())==study,'Embedded study mismatch')
    receipt=json.loads((output/'receipt.json').read_text())
    for relative,expected in receipt.items():check(sha(output/relative)==expected,'Producer artifact receipt mismatch: '+relative)
    prior=ROOT/'outputs/tuning_report_2nd_try'/track
    previous_audit=json.loads((prior/'audit.json').read_text());check(previous_audit['status']=='PASS','Prior baseline not independently verified')
    for relative,expected in previous_audit['artifact_hashes'].items():
        check(sha(prior/relative)==expected,'Prior verified artifact changed: '+relative)
    if track=='historical_pit':
        market_path=snapshot/'data/v2/market_daily.csv';universe_path=snapshot/'data/extended/processed/universe_20241231.csv'
    else:
        folder=snapshot/'data/tuning_2nd/official_universe/processed'
        market_path=folder/'daily.csv';universe_path=folder/'universe.csv'
        readiness=json.loads((folder/'readiness.json').read_text())
        qa=json.loads((ROOT/'outputs/tuning_report_2nd_try/official_data_audit.json').read_text())
        check(readiness['status']=='DATA_READY' and qa['status']=='PASS'
              and readiness['output_hashes']==qa['verified_output_hashes'],'Official data source gate lost')
    daily=pd.read_csv(market_path,low_memory=False);daily['date']=pd.to_datetime(daily.date)
    universe=read(universe_path);check(len(universe)==150 and universe.symbol.nunique()==150,'Universe differs')
    known=universe['known_at'] if 'known_at' in universe else universe['known_at_assumption']
    dates=pd.to_datetime(known,utc=True);cutoff=pd.Timestamp(study['start']).tz_localize('Asia/Taipei')+pd.Timedelta(hours=8,minutes=55)
    check(bool((dates<=cutoff).all())==(track=='historical_pit'),'Universe knowledge dates misrepresented')
    calendar=[pd.Timestamp(d) for d in sorted(daily.date.unique()) if pd.Timestamp(study['start'])<=d<=pd.Timestamp(study['end'])]
    check(len(calendar)==manifest['expected_sessions']==417,'Changed calendar')
    factors=read(output/'features.csv');market=read(output/'market_state.csv')
    reference,reference_market=reference_factors(daily,study['signal_parameters'])
    feature_audit=compare_features(factors,reference,market,reference_market)
    interventions=audit_interventions(output,factors,market,study['signal_parameters'])
    original=json.loads((prior/'final/A/config.json').read_text())
    original['study_policy'].update(parameter_search=False,selected_from_parameter_search=True,
        selection_scope='EX_POST_DEVELOPMENT',official_live_submission='BLOCK_IF_UNKNOWN')
    base=json.loads((snapshot/'config/strategy_v2.json').read_text())
    inputs=ledger_inputs(daily,calendar,universe)
    execution=daily[['date','symbol','turnover','execution_volume']].copy();execution['date']=execution.date.dt.strftime('%Y-%m-%d')
    frames={};metrics={};full={};errors=[];replays={};capacity={}
    for name in MODELS:
        folder=output/'final'/name;config=json.loads((folder/'config.json').read_text())
        eq=read(folder/'equity.csv');frames[name]=eq
        metrics[name]=second_metrics(eq,config)
        saved=json.loads((folder/'metrics.json').read_text())
        compare_metrics(saved,metrics[name],name+': reported metrics',require=('economic_total_return','economic_max_drawdown','measured_hard_breach_days'))
        if name in VERSIONS:
            comparable=dict(config);comparable.pop('v3');comparable['strategy_id']=original['strategy_id']
            check(comparable==original,name+': frozen A entry/exit/execution/risk configuration changed')
            meta=config['v3']
            check(meta['version']==name and meta['signal_parameters']==study['signal_parameters']
                  and meta['baseline_candidate']==study['baseline_candidates'][track]
                  and meta['parameter_search'] is False,'Wrong v3 fixed metadata')
            trial_trade_audit(folder/'trades.csv',eq,execution,config)
            errors.append(trial_ledger_audit(folder/'trades.csv',eq,inputs,config))
            monthly_audit(folder/'monthly.csv',eq,1e9)
        _,full[name]=audit_model(output/'final',name,daily,calendar,base)
        trades=read(folder/'trades.csv')
        capacity[name]=dict(trades_over_10pct_daily_volume=int(trades.volume_participation.gt(.1).sum()),
            max_daily_volume_participation=float(trades.volume_participation.max()) if len(trades) else 0.)
    replays['A']=exact_replay(output/'final/A',prior/'final/A')
    for name in ('0050','v1_matched'):
        replays[name]=exact_replay(output/'final'/name,prior/'final'/name)
        for path in (prior/'final'/name).iterdir():
            if path.is_file():check(sha(path)==sha(output/'final'/name/path.name),'Copied benchmark artifact changed')
    report_tables=audit_report_tables(output,frames,1e9)
    evaluation=audit_evaluation(output,frames,daily,metrics,interventions)
    prefix=physical_prefix(output,track,study)
    prefix_path=output/'physical_prefix_audit.json';prefix_path.write_text(json.dumps(prefix,indent=2)+'\n')
    artifacts={relative:sha(output/relative) for relative in receipt}
    artifacts['receipt.json']=sha(output/'receipt.json');artifacts[prefix_path.name]=sha(prefix_path)
    return dict(status='PASS',scope='FIXED_DEVELOPMENT_SIGNAL_AND_ACCOUNTING_AUDIT_NOT_FORMAL_CERTIFICATION',
        verified_at=datetime.now(timezone.utc).isoformat(),auditor_sha256=sha(__file__),track=track,
        receipt_sha256=sha(output/'receipt.json'),manifest_sha256=sha(output/'manifest.json'),
        source_hashes=len(manifest['hashes']),source_publication_dates=sorted(known.astype(str).unique()),
        feature_audit=feature_audit,interventions=interventions,full_ledger_audits=full,
        max_independent_ledger_error=max(errors),exact_replays=replays,report_tables=report_tables,
        evaluation=evaluation,physical_prefix=prefix,capacity=capacity,artifact_hashes=artifacts,
        interpretation=['All evaluation months were previously observed; chronological invariance does not create unseen OOS evidence.',
            'Official fixed-pool membership is an explicit later-known retrospective scenario.',
            'Weak-month labels are evaluation-only; decisions use previous-session market observations.',
            'Each version owns a continuous portfolio; holdings can differ during normal markets after weak-day decisions.',
            'Zero observed hard breaches does not resolve Active Share, submission or source-vintage unknowns.',
            'Ideal all-order VWAP fills and no market impact are competition-shadow assumptions, not live capacity evidence.',
            'Every model remains HOLD; a valid audit can include failure of the predeclared development objective.'])


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    output=Path(args.output)
    if not output.is_absolute():output=ROOT/output
    try:result=audit(output)
    except Exception as exc:
        result=dict(status='FAIL',error=str(exc),verified_at=datetime.now(timezone.utc).isoformat(),auditor_sha256=sha(__file__))
        (output/'audit.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
        raise
    (output/'audit.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ('artifact_hashes','full_ledger_audits','feature_audit')},indent=2,ensure_ascii=False))
