"""Preserve the frozen signal history; add paired official VWAP numerator/denominator."""
from pathlib import Path
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import subprocess
import threading
import time
import pandas as pd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/v2'
RAW = OUT / 'raw_execution'
LOCK = threading.Lock()

def num(x):
    try: return float(str(x).replace(',', ''))
    except (ValueError, TypeError): return float('nan')

def fetch(item):
    day, market = item
    stamp = day.replace('-', '')
    path = RAW / f'{stamp}_{market}.json'
    url = (f'https://www.twse.com.tw/exchangeReport/MI_INDEX?response=json&date={stamp}&type=ALLBUT0999'
           if market == 'TWSE' else f'https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={day.replace("-", "%2F")}&response=json')
    cached = [path, ROOT / f'data/raw/official/{stamp}_{market}_dated.json', ROOT / f'data/raw/official/{stamp}_{market}.json']
    data = None
    for candidate in cached:
        if candidate.exists():
            try:
                value = json.loads(candidate.read_bytes())
                if value.get('date') == stamp and value.get('tables'):
                    data = value
                    raw = candidate.read_bytes()
                    break
            except (ValueError, OSError): pass
    if data is None:
        last = None
        for attempt in range(2):
            try:
                raw = subprocess.run(['curl','-fL','--max-time','25','-sS',url],capture_output=True,check=True).stdout
                data = json.loads(raw)
                if data.get('date') != stamp or not data.get('tables'): raise ValueError('wrong date or empty tables')
                path.write_bytes(raw)
                break
            except Exception as exc:
                last = str(exc); data = None
                time.sleep(1)
        if data is None: return [], dict(date=day,market=market,error=last)
    manifest = dict(date=day,market=market,url=url,sha256=hashlib.sha256(raw).hexdigest(),retrieved_at=dt.datetime.now(dt.timezone.utc).isoformat())
    with LOCK:
        with (OUT/'execution_sources.jsonl').open('a') as file: file.write(json.dumps(manifest)+'\n')
    records=[]
    if market == 'TWSE':
        table = next(t for t in data['tables'] if '收盤價' in t.get('fields',[]))
        fields=table['fields']
        for r in table['data']:
            records.append(dict(date=day,source_symbol=r[0]+'.TW',execution_volume=num(r[fields.index('成交股數')]),official_turnover=num(r[fields.index('成交金額')]),vwap_source=url))
    else:
        for r in data['tables'][0]['data']:
            records.append(dict(date=day,source_symbol=r[0].strip()+'.TWO',execution_volume=num(r[8]),official_turnover=num(r[9]),vwap_source=url))
    return records, None

def main():
    RAW.mkdir(parents=True,exist_ok=True)
    daily=pd.read_csv(ROOT/'data/extended/processed/daily_canonical.csv')
    dates=sorted(daily.loc[daily.date.between('2025-01-01','2026-09-21'),'date'].unique())
    tasks=[(d,m) for d in dates for m in ['TWSE','TPEx']]
    allrows=[]; failures=[]
    with cf.ThreadPoolExecutor(max_workers=6) as pool:
        for i,(rows,error) in enumerate(pool.map(fetch,tasks),1):
            allrows.extend(rows)
            if error: failures.append(error)
            if i%40==0: print(f'{i}/{len(tasks)} responses; failed {len(failures)}',flush=True)
    official=pd.DataFrame(allrows)
    official=official[official.source_symbol.isin(set(daily.source_symbol))]
    if official.duplicated(['date','source_symbol']).any(): raise ValueError('duplicate quotes')
    official.to_csv(OUT/'official_execution.csv',index=False)
    merged=daily.merge(official,on=['date','source_symbol'],how='left',validate='many_to_one')
    valid=merged.execution_volume.gt(0)&merged.official_turnover.gt(0)
    # Both quantities come from the same official row. Never mix Yahoo volume with exchange money.
    merged.loc[valid,'turnover']=merged.loc[valid,'official_turnover']
    fallback=~valid&merged.turnover.gt(0)&merged.volume.gt(0)
    merged.loc[fallback,'execution_volume']=merged.loc[fallback,'volume']
    merged.loc[fallback,'vwap_source']=merged.loc[fallback,'price_source']
    merged['execution_vwap']=merged.turnover/merged.execution_volume
    merged.to_csv(OUT/'market_daily.csv',index=False)
    live=merged[merged.date.ge('2025-01-01')&merged.symbol.ne('^TWII')]
    missing=live[live.execution_vwap.isna()&live.volume.gt(0)]
    missing.to_csv(OUT/'missing_execution.csv',index=False)
    audit=dict(rows=len(live),available=int(live.execution_vwap.notna().sum()),missing_positive_volume=len(missing),failures=failures,
               original_signal_data_sha256=hashlib.sha256((ROOT/'data/extended/processed/daily_canonical.csv').read_bytes()).hexdigest())
    (OUT/'execution_audit.json').write_text(json.dumps(audit,indent=2))
    print(json.dumps(audit,indent=2),flush=True)

if __name__=='__main__': main()
