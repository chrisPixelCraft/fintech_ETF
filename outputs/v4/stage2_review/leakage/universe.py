import sys, json
sys.path.insert(0,'/Users/chrishsieh/Documents/dev/fintech_ETF')
import numpy as np, pandas as pd
R='/Users/chrishsieh/Documents/dev/fintech_ETF/'
d=pd.read_parquet(R+'data/yahoo_daily/v3_20260923/nominal_daily.parquet'); d['date']=pd.to_datetime(d.date)
u=pd.read_csv(R+'data/reference/universe_competition_20260731.csv'); U=set(u.yahoo_symbol)
print('universe',len(U),'daily symbols',d.symbol.nunique(),'extra in daily',set(d.symbol)-U,'missing',U-set(d.symbol))
first=d.loc[d.valid_price].groupby('symbol').date.min()
print('first valid date quantiles'); print(first.loc[list(U & set(first.index))].describe())
eps=json.load(open(R+'outputs/v4/stage2_data/episodes.json'))
rows=[]
dd=d.loc[d.symbol.isin(U)]
cnt=dd.loc[dd.valid_price].groupby('symbol').cumcount()+1  # approx valid sessions
dd=dd.assign(nvalid=cnt)
for e in eps:
    p=pd.Timestamp(e['prior_session_date']); x=dd.loc[dd.date.eq(p)]
    rows.append(dict(ep=e['episode_id'],split=e['split'],prior=str(p.date()),rows=len(x),valid=int(x.valid_price.sum()),ready200=int((x.nvalid>=200).sum()),listed_after=int((first.reindex(list(U))>p).sum())))
r=pd.DataFrame(rows); print(r.to_string())
# price limit artifacts: |action-neutral return| beyond daily limit, not flagged
g=d.sort_values(['symbol','date']).copy()
g['lim']=np.where(g.date<pd.Timestamp('2015-06-01'),.07,.10)
a=g.action_neutral_return
bad=(a.abs()>g.lim+.005)&a.notna()&~g.quality_flags.fillna('').str.contains('GT_30')
print('rows beyond price limit, unflagged:',int(bad.sum()),'symbols',g.loc[bad,'symbol'].nunique())
print(g.loc[bad,['date','symbol','close','split','dividend','action_neutral_return']].sort_values('action_neutral_return').head(15).to_string())
print(g.loc[bad].groupby(g.loc[bad,'date'].dt.year).size().to_string())
