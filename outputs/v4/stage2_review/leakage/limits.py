import numpy as np, pandas as pd
R='/Users/chrishsieh/Documents/dev/fintech_ETF/'
d=pd.read_parquet(R+'data/yahoo_daily/v3_20260923/nominal_daily.parquet'); d['date']=pd.to_datetime(d.date)
cal=pd.DatetimeIndex(sorted(d.date.unique())); pos=pd.Series(np.arange(len(cal)),index=cal)
g=d.loc[d.valid_price].sort_values(['symbol','date']).copy()
g['p']=g.date.map(pos); g['gap']=g.groupby('symbol').p.diff()
g['first']=g.groupby('symbol').cumcount()
g['lim']=np.where(g.date<pd.Timestamp('2015-06-01'),.07,.10)
g['r']=(g.close*g.split+g.dividend)/g.groupby('symbol').close.shift()-1
bad=(g.r.abs()>g.lim+.006)&g.r.notna()&g.gap.eq(1)&g['first'].gt(5)&g.r.abs().le(.30)
print('adjacent-session unflagged moves beyond price limit (post-IPO):',int(bad.sum()),'symbols',g.loc[bad,'symbol'].nunique())
print('by |r| band:',pd.cut(g.loc[bad,'r'].abs(),[0,.12,.15,.2,.3]).value_counts().sort_index().to_dict())
print(g.loc[bad].sort_values('r').head(12)[['date','symbol','close','r']].to_string())
print(g.loc[bad].groupby(g.loc[bad,'date'].dt.year).size().to_dict())
# rows with gap>1
print('rows following a gap:',int((g.gap>1).sum()))
