"""Run frozen A/B/C/D and matched controls; never choose a historical winner for LIVE."""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import platform
import shutil
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
from src.backtest import aggregate_four_hour, save_result
from src.backtest_v2 import run_v2, run_buy_hold_v2
from src.sector_gate import SectorGate


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def analytics(results, output, config):
    initial=config['initial_cash']; summaries=[]; monthly=[]; annual=[]; blocks=[]; comparison={}
    classification=results['B']['signals'][['date','symbol','sector_id']].drop_duplicates(['date','symbol'])
    exposures=[]
    for model,result in results.items():
        eq=result['equity'].copy();eq['date']=pd.to_datetime(eq.date)
        summary={'model':model,**result['metrics']}
        summary['calendar_cagr']=(eq.economic_nav.iloc[-1]/initial)**(365.25/(eq.date.iloc[-1]-pd.Timestamp(config['start'])+pd.Timedelta(days=1)).days)-1
        summary['average_stock_exposure']=float((1-eq.cash_ratio).mean())
        summary['cash_ratio_max']=float(eq.cash_ratio.max());summary['cash_ratio_min']=float(eq.cash_ratio.min())
        summary['holdings_min']=int(eq.holdings.min());summary['holdings_max']=int(eq.holdings.max())
        summary['negative_cash_days']=int(eq.cash.lt(-1e-6).sum())
        holdings=result['holdings']; summary['max_single_weight']=float(holdings.weight.max()) if len(holdings) else 0
        tagged=holdings.merge(classification,on=['date','symbol'],how='left')
        tagged['sector_id']=tagged.sector_id.fillna('UNKNOWN')
        sector_weights=tagged.groupby(['date','sector_id']).weight.sum().reset_index()
        sector_weights['model']=model;exposures.append(sector_weights)
        risk=[]
        for day,g in sector_weights.groupby('date'):
            known=g[g.sector_id.ne('UNKNOWN')]
            coverage=known.weight.sum()
            risk.append(dict(coverage=coverage,hhi=float(((known.weight/coverage)**2).sum()) if coverage>0 else np.nan,max_sector=float(known.weight.max()) if len(known) else np.nan))
        risk=pd.DataFrame(risk)
        summary['mean_classified_nav_weight']=float(risk.coverage.mean()) if len(risk) else 0
        summary['known_sector_hhi_mean']=float(risk.hhi.mean()) if len(risk) else np.nan
        summary['max_known_sector_weight']=float(risk.max_sector.max()) if len(risk) else np.nan
        summaries.append(summary)
        for freq,target,key in [('M',monthly,'month'),('Y',annual,'year')]:
            prev=initial
            for period,frame in eq.groupby(eq.date.dt.to_period(freq)):
                nav=np.r_[prev,frame.economic_nav.to_numpy(float)]
                target.append(dict(model=model,**{key:str(period)},sessions=len(frame),through=str(frame.date.iloc[-1].date()),
                                   economic_return=float(nav[-1]/nav[0]-1),economic_max_drawdown=float(-(nav/np.maximum.accumulate(nav)-1).min())))
                prev=nav[-1]
        prev=initial
        for block,start in enumerate(range(0,len(eq),24),1):
            frame=eq.iloc[start:start+24];nav=np.r_[prev,frame.economic_nav.to_numpy(float)]
            blocks.append(dict(model=model,block=block,start=str(frame.date.iloc[0].date()),end=str(frame.date.iloc[-1].date()),sessions=len(frame),complete=len(frame)==24,return_net=nav[-1]/nav[0]-1,mdd=float(-(nav/np.maximum.accumulate(nav)-1).min())))
            prev=nav[-1]
        comparison[model]=pd.Series(np.r_[initial,eq.economic_nav.to_numpy()],index=[pd.Timestamp(config['start'])-pd.Timedelta(days=1),*eq.date])
    for name,rows in [('summary',summaries),('monthly_comparison',monthly),('annual_comparison',annual),('blocks_24_sessions',blocks)]:
        pd.DataFrame(rows).to_csv(output/f'{name}.csv',index=False)
    pd.DataFrame(comparison).rename_axis('date').to_csv(output/'economic_nav_comparison.csv')
    pd.concat(exposures,ignore_index=True).to_csv(output/'sector_exposure.csv',index=False)


def run(output, end=None):
    config=json.loads((ROOT/'config/strategy_v2.json').read_text())
    if end: config['end']=end
    inputs=[ROOT/p for p in ['data/v2/market_daily.csv','data/extended/processed/hourly_canonical.csv','data/extended/processed/universe_20241231.csv',
                             'data/sector/industry_history.csv','data/sector/sector_index_daily.csv','config/strategy_v2.json']]
    inputs += sorted((ROOT/'data/v2_auxiliary').glob('*.csv'))
    inputs += sorted((ROOT/'data/v2_auxiliary').glob('*.json'))
    inputs += sorted((ROOT/'data/sector').glob('*.json'))
    inputs += sorted((ROOT/'config').glob('v2_auxiliary*.json'))
    for p in inputs:
        if not p.exists(): raise FileNotFoundError(p)
    if (output/'provenance.json').exists(): raise FileExistsError('Existing run preserved; choose a fresh output directory')
    output.mkdir(parents=True,exist_ok=True)
    snap=output/'input_snapshot';snap.mkdir()
    for p in inputs:
        dest=snap/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
    code=[ROOT/'src/backtest.py',ROOT/'src/backtest_v2.py',ROOT/'src/sector_gate.py',ROOT/'src/v2_signals.py',Path(__file__)]
    for p in code:
        dest=snap/p.relative_to(ROOT);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
    provenance=dict(started_at=datetime.now(timezone.utc).isoformat(),command=sys.argv,python=platform.python_version(),
                    hashes={str(p.relative_to(ROOT)):sha(p) for p in inputs+code},config=config,
                    trust='CANDIDATE_PENDING_INDEPENDENT_AUDIT',research_shadow=True,parameter_search=False)
    (output/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    daily=pd.read_csv(inputs[0]);hourly=pd.read_csv(inputs[1]);universe=pd.read_csv(inputs[2])
    if 'known_at' not in universe:universe['known_at']=universe.known_at_assumption
    bars=aggregate_four_hour(hourly)
    calendar=pd.DatetimeIndex(pd.to_datetime(daily.date.unique())).sort_values()
    gate=SectorGate(inputs[3],inputs[4],calendar)
    from src.v2_signals import AuxiliarySignals
    auxiliary=AuxiliarySignals(calendar_path=inputs[0])
    def transform(version):
        def apply(day,ranked):
            ranked,meta=gate(day,ranked)
            if version in ['C','D']:
                ranked,extra=auxiliary.modify(version,day,ranked);meta.update(extra)
            return ranked,meta
        return apply
    results={}
    for name in ['v1_matched','A','B','C','D']:
        print('Running '+name,flush=True)
        settings={**copy.deepcopy(config),'strategy_id':name,'allocation_mode':'full' if name=='v1_matched' else 'local'}
        results[name]=run_v2(daily,universe,settings,bars,signal_transform=transform(name) if name in ['B','C','D'] else None)
        unsupported=results[name]['holdings'].query("symbol == '2888.TW' and date >= '2025-07-24'")
        if len(unsupported):raise ValueError(name+': unsupported multi-security merger held; quarantine this run')
        save_result(results[name],output/name)
        print(name+' '+json.dumps(results[name]['metrics']),flush=True)
    print('Running 0050',flush=True)
    results['0050']=run_buy_hold_v2(daily,copy.deepcopy(config))
    save_result(results['0050'],output/'0050')
    assert len({tuple(x['equity'].date) for x in results.values()})==1,'Calendar mismatch'
    analytics(results,output,config)
    provenance.update(completed_at=datetime.now(timezone.utc).isoformat(),outputs_complete=True)
    changed=[str(p.relative_to(ROOT)) for p in inputs+code if sha(p)!=provenance['hashes'][str(p.relative_to(ROOT))]]
    if changed: raise ValueError('Input or code changed while running: '+str(changed))
    (output/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    print(pd.read_csv(output/'summary.csv')[['model','total_return','economic_max_drawdown']].to_string(index=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',default='outputs/backtest_v2_2025_to_now');p.add_argument('--end');a=p.parse_args()
    run(ROOT/a.output,a.end)
