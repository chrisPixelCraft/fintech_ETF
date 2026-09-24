import sys, json, time, copy
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[4]))
import numpy as np, pandas as pd
from src.v4_features import build_features, history_at
from src.v4_forecast import WalkForwardForecaster
from src.v4_strategy import V4Strategy
R='/Users/chrishsieh/Documents/dev/fintech_ETF/'
daily=pd.read_parquet(R+'data/yahoo_daily/v3_20260923/nominal_daily.parquet'); daily['date']=pd.to_datetime(daily.date)
episodes=json.load(open(R+'outputs/v4/stage2_data/episodes.json'))
sel=json.load(open(R+'outputs/v4/stage2_verified/freeze.json'))['selected']
ex=pd.read_csv(R+'outputs/v4/stage2_verified/../stage2_data/execution_data.csv',parse_dates=['date']) if False else None
t0=time.time(); base=build_features(daily); print('features built', time.time()-t0, flush=True)
EP=sys.argv[1] if len(sys.argv)>1 else 'development_2015_07'
K=int(sys.argv[2]) if len(sys.argv)>2 else 10
ep=next(e for e in episodes if e['episode_id']==EP)
sessions=pd.to_datetime(ep['sessions']); t=sessions[K]; tm1=sessions[K-1]
print('episode',EP,'t',t.date(),'t-1',tm1.date())

def corrupt(d, cutoff, seed, mode):
    rng=np.random.default_rng(seed); d=d.copy(); m=d.date>=cutoff; n=int(m.sum())
    if mode=='random':
        for c in ['open','high','low','close']:
            d.loc[m,c]=d.loc[m,c]*rng.uniform(.3,3,n)
        d.loc[m,'volume']=d.loc[m,'volume']*rng.uniform(0,10,n)
        d.loc[m,'split']=np.where(rng.random(n)<.05, rng.choice([.5,2.,1.1],n), 1.)
        d.loc[m,'dividend']=np.where(rng.random(n)<.05, rng.uniform(0,20,n), 0.)
        d.loc[m,'quality_flags']=np.where(rng.random(n)<.02,'ACTION_NEUTRAL_RETURN_GT_30PCT','')
        d.loc[m,'valid_for_research']=rng.random(n)>.05
    elif mode=='shuffle':
        # permute symbol labels within each future date
        idx=d.index[m]
        sub=d.loc[m].copy()
        sub['symbol']=sub.groupby('date').symbol.transform(lambda s: rng.permutation(s.values))
        d.loc[idx,'symbol']=sub.symbol.values
        d=d.sort_values(['date','symbol']).reset_index(drop=True)
    elif mode=='drop':
        keep=~m | (rng.random(len(d))>.5)
        d=d.loc[keep].reset_index(drop=True)
    return d

def targets(panel, forecasters, holdings_seed=None):
    dates=pd.DatetimeIndex(pd.to_datetime([ep['prior_session_date'],*ep['sessions']]))
    feats=panel.loc[panel.date.isin(dates)]
    # D-1 official close proxy: original nominal close at t-1 (unperturbed by construction)
    prev=daily.loc[daily.date.eq(tm1)].set_index('symbol').close
    out={}
    remaining=len(sessions)-K
    cfgs=dict(sel)
    cfgs['adaptive_de0']=dict(sel['adaptive'],optimizer='de',seed=0)
    cfgs['adaptive_remaining']=dict(sel['adaptive'],horizon='remaining',remaining_horizon=True)
    cfgs['momentum_regime_cont']=dict(sel['momentum'],regime=True,confidence=True,optimizer='continuous')
    for name,cfg in cfgs.items():
        fc=forecasters[cfg.get('weighting','inverse_error')] if (cfg['family']=='adaptive' or cfg.get('confidence')) else None
        s=V4Strategy(cfg,fc)
        st=dict(holdings={},cash=1e9,nav=1e9,previous_close=prev)
        tgt=s.generate_target(t,feats,st,dict(remaining_sessions=remaining))
        # second state with holdings: use first plan's shares
        hold={k:int(v) for k,v in tgt.target_shares.items() if v>0} if len(tgt) else {}
        val=float(sum(q*prev[k] for k,q in hold.items()))
        st2=dict(holdings=hold,cash=max(1e9-val,0),nav=1e9,previous_close=prev)
        tgt2=s.generate_target(t,feats,st2,dict(remaining_sessions=remaining))
        out[name]=(tgt,tgt2)
    preds={}
    for h in (1,5,10,20,remaining,24):
        preds[h]=forecasters['inverse_error'].predict(t,h)
    preds['rank_ic20']=forecasters['rank_ic'].predict(t,20)
    return out,preds

def fcs(panel): return {w:WalkForwardForecaster(panel,weighting=w) for w in ('inverse_error','rank_ic')}
t0=time.time(); B=targets(base,fcs(base)); print('base targets',time.time()-t0,flush=True)
for name,(a,b) in B[0].items(): print(' ',name,a.attrs.get('status'),len(a),'| with holdings',b.attrs.get('status'),len(b))

def compare(A,Bx,label):
    ok=True
    for name in A[0]:
        for i in range(2):
            x,y=A[0][name][i],Bx[0][name][i]
            try:
                pd.testing.assert_frame_equal(x,y,check_exact=True)
                assert json.dumps(x.attrs,sort_keys=True,default=str)==json.dumps(y.attrs,sort_keys=True,default=str)
            except AssertionError as e:
                ok=False; print('  DIFF',label,name,i,str(e)[:200])
    for h in A[1]:
        try:
            pd.testing.assert_frame_equal(A[1][h],Bx[1][h],check_exact=True); assert A[1][h].attrs==Bx[1][h].attrs
        except AssertionError as e:
            ok=False; print('  DIFF pred',label,h,str(e)[:200])
    print(label,'BIT-IDENTICAL' if ok else 'CHANGED',flush=True)
    return ok
res={}
for mode,cut,seed in [('random',t,1),('shuffle',t,2),('drop',t,3),('random',tm1,4)]:
    d=corrupt(daily,cut,seed,mode); p=build_features(d)
    label=f'{mode}_from_{cut.date()}'
    res[label]=compare(B,targets(p,fcs(p)),label)
json.dump(dict(episode=EP,t=str(t.date()),results=res),open(str(__import__('pathlib').Path(__file__).resolve().parent)+f'/perturb_{EP}_{K}.json','w'),indent=1)
