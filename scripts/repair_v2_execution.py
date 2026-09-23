"""Recover unavailable exchange responses with independently checked FinMind raw money/volume."""
from pathlib import Path
import concurrent.futures as cf
import hashlib
import json
import subprocess
import pandas as pd
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data/v2'
RAW=OUT/'raw_finmind';RAW.mkdir(exist_ok=True)

def fetch(code):
    path=RAW/f'{code}.json'
    url=f'https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice&data_id={code}&start_date=2025-01-01&end_date=2026-09-21'
    if not path.exists():
        raw=subprocess.run(['curl','-fL','--max-time','40','-sS',url],capture_output=True,check=True).stdout
        x=json.loads(raw)
        if x.get('status')!=200:raise ValueError(str(x)[:400])
        path.write_bytes(raw)
    x=json.loads(path.read_bytes())
    return code,x['data'],dict(code=code,url=url,sha256=hashlib.sha256(path.read_bytes()).hexdigest())

def main():
    daily=pd.read_csv(OUT/'market_daily.csv')
    missing=daily[daily.date.ge('2025-01-01')&daily.execution_vwap.isna()&daily.volume.gt(0)&daily.symbol.ne('^TWII')]
    codes=sorted({s.split('.')[0] for s in missing.source_symbol})
    validations=[];sources=[];repairs=[];failures=[]
    daily=daily.set_index(['date','source_symbol'],drop=False)
    with cf.ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(fetch,c):c for c in codes}
        for i,future in enumerate(cf.as_completed(futures),1):
            code=futures[future]
            try: code,rows,source=future.result()
            except Exception as exc:
                failures.append(dict(code=code,error=str(exc)));continue
            sources.append(source)
            for r in rows:
                for suffix in ['.TW','.TWO']:
                    key=(r['date'],code+suffix)
                    if key not in daily.index:continue
                    if not np.isnan(daily.at[key,'execution_vwap']):
                        official=daily.at[key,'execution_vwap'];vendor=r['Trading_money']/r['Trading_Volume'] if r['Trading_Volume'] else np.nan
                        if np.isfinite(vendor):validations.append(dict(date=r['date'],code=code,official_vwap=official,vendor_vwap=vendor,relative_difference=vendor/official-1))
                        continue
                    if r['Trading_money']<=0 or r['Trading_Volume']<=0:continue
                    values=dict(turnover=r['Trading_money'],execution_volume=r['Trading_Volume'],execution_vwap=r['Trading_money']/r['Trading_Volume'],vwap_source=source['url'])
                    for k,v in values.items():daily.at[key,k]=v
                    repairs.append(dict(date=r['date'],symbol=daily.at[key,'symbol'],**values))
            if i%10==0:print(f'{i}/{len(codes)} symbols; repaired {len(repairs)}',flush=True)
    checks=pd.DataFrame(validations);checks.to_csv(OUT/'vendor_vwap_crosscheck.csv',index=False)
    if len(checks) and checks.relative_difference.abs().gt(.001).any():
        # Preserve discrepancies for review, never quietly bless vendor equivalence.
        print('CROSSCHECK discrepancies:',checks[checks.relative_difference.abs().gt(.001)].to_string(index=False),flush=True)
    daily.reset_index(drop=True).to_csv(OUT/'market_daily.csv',index=False)
    pd.DataFrame(repairs).to_csv(OUT/'vendor_execution_repairs.csv',index=False)
    (OUT/'vendor_execution_sources.json').write_text(json.dumps(sources,indent=2))
    audit=dict(repaired=len(repairs),overlap_checks=len(checks),max_relative_difference=float(checks.relative_difference.abs().max()) if len(checks) else None,failures=failures)
    remain=daily[daily.date.ge('2025-01-01')&daily.execution_vwap.isna()&daily.volume.gt(0)&daily.symbol.ne('^TWII')]
    remain.to_csv(OUT/'missing_execution.csv',index=False);audit['missing_positive_volume']=len(remain)
    (OUT/'vendor_execution_audit.json').write_text(json.dumps(audit,indent=2));print(json.dumps(audit,indent=2),flush=True)

if __name__=='__main__':main()
