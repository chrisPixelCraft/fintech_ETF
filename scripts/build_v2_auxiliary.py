"""Normalize cached independent FinMind overnight histories with explicit availability assumptions."""
from pathlib import Path
import hashlib
import json
import sys
from datetime import datetime,timezone
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.fetch_v2_auxiliary import availability

def main():
    out=ROOT/'data/v2_auxiliary';raw=out/'raw';frames=[];sources={};artifacts=[]
    for instrument,symbol in [('NASDAQ','^IXIC'),('SOX','^SOX'),('NVDA','NVDA'),('TSM_ADR','TSM')]:
        path=raw/('finmind_'+symbol.replace('^','')+'.json');body=path.read_bytes();digest=hashlib.sha256(body).hexdigest()
        data=pd.DataFrame(json.loads(body)['data']).sort_values('date')
        url=f'https://api.finmindtrade.com/api/v4/data?dataset=USStockPrice&data_id={symbol}&start_date=2024-01-01&end_date=2026-09-21'
        p=data.Close.astype(float)
        data['return1']=p.pct_change(fill_method=None)
        rows=[]
        for r in data.dropna(subset=['return1']).itertuples():
            stamp,basis=availability(pd.Timestamp(r.date),'us_equity')
            rows.append(dict(instrument=instrument,source_symbol=symbol,session_date=r.date,close=r.Close,adjusted_close=r.Adj_Close,return1=r.return1,
                             available_at=stamp.isoformat(),available_at_basis=basis,source_url=url,source_sha256=digest))
        frames.append(pd.DataFrame(rows));sources[instrument]=dict(status='FETCHED_RESEARCH_SOURCE',rows=len(rows),source_url=url,availability='US 16:30 America/New_York, assumed after completed close',revision_vintage='RETRIEVED_TODAY_NOT_ARCHIVED_RELEASE')
        artifacts.append(dict(path=str(path.relative_to(ROOT)),sha256=digest))
    path=raw/'TaiwanExchangeRate_USD.json';body=path.read_bytes();digest=hashlib.sha256(body).hexdigest()
    data=pd.DataFrame(json.loads(body)['data']).sort_values('date');data['close']=(data.spot_buy+data.spot_sell)/2;data['return1']=data.close.pct_change(fill_method=None)
    url='https://api.finmindtrade.com/api/v4/data?dataset=TaiwanExchangeRate&data_id=USD&start_date=2024-01-01&end_date=2026-09-21'
    rows=[]
    for r in data.dropna(subset=['return1']).itertuples():
        stamp=pd.Timestamp(r.date).tz_localize('Asia/Taipei')+pd.Timedelta(hours=19,minutes=30)
        rows.append(dict(instrument='USD_TWD',source_symbol='USD_BANK_SPOT_MID',session_date=r.date,close=r.close,adjusted_close=r.close,return1=r.return1,
                         available_at=stamp.tz_convert('UTC').isoformat(),available_at_basis='ASSUMED_TAIWAN_BANK_END_OF_DAY_1930',source_url=url,source_sha256=digest))
    frames.append(pd.DataFrame(rows));sources['USD_TWD']=dict(status='FETCHED_RESEARCH_PROXY',rows=len(rows),source_url=url,definition='Bank USD/TWD spot buy/sell midpoint; not global FX close')
    artifacts.append(dict(path=str(path.relative_to(ROOT)),sha256=digest))
    path=raw/'TaiwanFuturesDaily_TX.json';body=path.read_bytes();digest=hashlib.sha256(body).hexdigest()
    data=pd.DataFrame(json.loads(body)['data']);data=data[(data.trading_session=='after_market')&(data.open>0)&(data.close>0)&(data.volume>0)]
    # At 08:55 the entire preceding night is complete; most-active night contract is observable.
    data=data.sort_values(['date','volume','contract_date'],ascending=[True,False,True]).drop_duplicates('date')
    url='https://api.finmindtrade.com/api/v4/data?dataset=TaiwanFuturesDaily&data_id=TX&start_date=2024-01-01&end_date=2026-09-21';rows=[]
    for r in data.itertuples():
        stamp=pd.Timestamp(r.date).tz_localize('Asia/Taipei')+pd.Timedelta(hours=6)
        rows.append(dict(instrument='TAIWAN_FUTURES_NIGHT',source_symbol='TX:'+str(r.contract_date),session_date=r.date,close=r.close,adjusted_close=r.close,return1=r.close/r.open-1,
                         available_at=stamp.tz_convert('UTC').isoformat(),available_at_basis='DERIVED_NIGHT_ENDED_BY_ATTRIBUTION_DATE_0600',source_url=url,source_sha256=digest))
    frames.append(pd.DataFrame(rows));sources['TAIWAN_FUTURES_NIGHT']=dict(status='FETCHED_RESEARCH_SOURCE',rows=len(rows),source_url=url,definition='Completed after_market close/open; highest-volume completed night contract',
        date_semantics_source='https://www.taifex.com.tw/cht/3/futContractsDateAhView',availability='Night attribution day06:00, derived rather than vendor publication timestamp')
    artifacts.append(dict(path=str(path.relative_to(ROOT)),sha256=digest))
    sources['EARNINGS']=dict(status='UNKNOWN',reason='NO_VERIFIED_POINT_IN_TIME_EARNINGS_PUBLICATION_SOURCE',fallback='NEUTRAL')
    fp=out/'institutional_provenance.json'
    if fp.exists():
        prov=json.loads(fp.read_text());sources['INSTITUTIONAL']=dict(status='FETCHED_RESEARCH_SOURCE',provenance_file=str(fp.relative_to(ROOT)),details=prov)
    else:sources['INSTITUTIONAL']=dict(status='UNKNOWN',reason='NO_VERIFIED_FLOW_INPUT',fallback='NEUTRAL')
    pd.concat(frames,ignore_index=True).sort_values(['available_at','instrument']).to_csv(out/'overnight_daily.csv',index=False)
    status=dict(schema_version='1.0',retrieved_at=datetime.now(timezone.utc).isoformat(),sources=sources)
    old=out/'source_status.json'
    if old.exists() and not (raw/'initial_source_failures.json').exists():(raw/'initial_source_failures.json').write_bytes(old.read_bytes())
    old.write_text(json.dumps(status,indent=2,ensure_ascii=False)+'\n')
    (out/'manifest.json').write_text(json.dumps(dict(inputs=artifacts,availability_is_assumed=True,disclaimer='No original historical publication-vintage archive; completed market sessions joined causally.'),indent=2)+'\n')
    print({k:v['status'] for k,v in sources.items()})

if __name__=='__main__':main()
