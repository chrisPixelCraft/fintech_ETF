"""Bounded public issuer evidence probe; no competition login or submission.

Exactly one initial GET per listed ETF (three Nomura ETFs use the public
read-only Fund/GetFundList and Fund/GetFundAssets POST queries exposed by the
issuer's downloaded web application). No retries, authentication or security
challenge workarounds. A fetched brochure is NOT counted as dated holdings.
Existing output directories are rejected. Dates and authentication limits are
preserved explicitly; this is not a historical point-in-time AS data source.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]
URLS={
'00982A':'https://www.capitalfund.com.tw/etf/product/detail/399/portfolio',
'00981A':'https://www.ezmoney.com.tw/',
'00983A':'https://www.ctbcinvestments.com/act/202506_CTBCARK/DM.pdf',
'00984A':'https://tw.allianzgi.com/-/media/AllianzGI/AP/Taiwan/pdf/active-etf/00984a-dm',
'00986A':'https://www.tsit.com.tw/ETF/Home/ETFSeriesDetail/00986A',
'00989A':'https://am.jpmorgan.com/tw/zh/asset-management/twetf/investment-ideas/00989a-us-tech-etf/',
'00988A':'https://www.ezmoney.com.tw/events/00988A/00988A-prospectus.pdf',
'00991A':'https://vpx.fhtrust.com.tw/ETF/etf_detail/ETF23',
'00990A':'https://www.yuantafunds.com/myfund/information/1254',
'00987A':'https://www.tsit.com.tw/ETF/Home/ETFSeriesDetail/00987A',
'00992A':'https://www.capitalfund.com.tw/etf/product/detail/500/portfolio',
'00994A':'https://www.fsitc.com.tw/ViewFile.aspx?id=182&path=3',
'00995A':'https://www.ctbcinvestments.com/fund/pdf/ETF_eDM/00995A_DM.pdf',
'00993A':'https://tw.allianzgi.com/',
'00996A':'https://www.megafunds.com.tw/MEGA/etf/etf_product.aspx?id=23',
'00400A':'https://www.cathaysite.com.tw/fund-details/EEA',
'00401A':'https://am.jpmorgan.com/tw/zh/asset-management/twetf/funds/jpmorgan-tw-equity-high-income-etf/',
'00997A':'https://www.capitalfund.com.tw/etf/product/detail/502/portfolio',
'00403A':'https://www.ezmoney.com.tw/events/2026elite50/00403A-tradingguide.pdf',
'00402A':'https://tw.allianzgi.com/',
'00404A':'https://www.abfunds.com.tw/zh-tw/etf/active/equities/abitl-taiwan-momentum-equity-premium-income-50-active-etf.html',
'00405A':'https://www.fubon.com/financialholdings/news/news_1260505_965744.htm',
'00406A':'https://www.ctbcinvestments.com/',
'00407A':'https://www.kgifund.com.tw/Fund/Detail?fundID=J024',
'00408A':'https://www.fsitc.com.tw/FundDetail.aspx?ID=183',
'00410A':'https://sitc.sinopac.com/SinopacEtfs/Etfs/SinglePcf/00410A',
'00409A':'https://www.fhtrust.com.tw/ETF/etf_detail/ETF26',
}
NOMURA='https://www.nomurafunds.com.tw/API/ETFAPI/api/'


def sha(raw):return hashlib.sha256(raw).hexdigest()
def stamp():return datetime.now(timezone.utc).isoformat()
def write_json(path,obj):path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')


def fetch_one(item,out,requested):
    code=item['ticker'];attempts=[];holdings=[]
    row=dict(etf=code,name=item['name'],requested_date=requested,source_date='',source_date_semantics='UNKNOWN',
             status='MISSING_DATED_HOLDINGS',holdings_count=0,source_url='',raw_path='',raw_sha256='',
             observed_at='',known_at_policy='FIRST_OBSERVED_ONLY_NOT_HISTORICAL_PUBLICATION',
             formal_as_status='UNKNOWN',reason='No verified dated stock weights parsed in this bounded attempt')

    def request(url,payload=None,label='page'):
        meta=dict(etf=code,url=url,method='POST_READ_ONLY_QUERY' if payload else 'GET',request_body=payload,retrieved_at=stamp())
        raw=b'';response=None
        try:
            response=requests.request('POST' if payload else 'GET',url,json=payload,timeout=(8,22),allow_redirects=False)
            raw=response.content
            meta.update(http_status=response.status_code,content_type=response.headers.get('Content-Type',''))
            if len(raw)>12*1024*1024:raise ValueError('RESPONSE_TOO_LARGE')
            path=out/'raw'/f'{code}_{label}.bin';path.write_bytes(raw)
            meta.update(raw_path=str(path.relative_to(ROOT)),sha256=sha(raw))
            if response.status_code in (401,403,429):meta['status']='HTTP_BLOCKED_NO_RETRY'
            elif 300<=response.status_code<400:meta['status']='REDIRECT_NOT_FOLLOWED'
            elif response.status_code!=200:meta['status']='HTTP_ERROR'
            else:meta['status']='FETCHED'
        except (requests.RequestException,ValueError) as exc:
            meta.update(status='FETCH_FAILED',error_type=type(exc).__name__)
        attempts.append(meta)
        row.update(source_url=url,raw_path=meta.get('raw_path',''),raw_sha256=meta.get('sha256',''),observed_at=meta['retrieved_at'])
        if meta['status']!='FETCHED':
            row.update(status=meta['status'],reason=meta['status']);return None
        return raw

    def add(ticker,name,weight,quantity='',asof='',semantics='UNKNOWN'):
        weight=float(str(weight).replace('%','').replace(',',''))/100
        if not 0<=weight<=1:raise ValueError('WEIGHT_RANGE')
        holdings.append(dict(etf=code,source_date=asof,source_date_semantics=semantics,
                             ticker=str(ticker),name=name,weight_nav_fraction=weight,shares=quantity,
                             source_url=row['source_url'],raw_sha256=row['raw_sha256'],observed_at=row['observed_at']))

    try:
        if code in ('00980A','00985A','00999A'):
            raw=request(NOMURA+'Fund/GetFundList',dict(Type=2,Keyword=code,No='',FundType=0,PageIndex=1,PageSize=50),'list')
            if raw:
                entries=json.loads(raw).get('Entries',[]);matches=[x for x in entries if x.get('CStockNo')==code]
                if len(matches)!=1:raise ValueError('FUND_ID_NOT_CONFIRMED')
                raw=request(NOMURA+'Fund/GetFundAssets',dict(FundID=matches[0]['CNo'],SearchDate=requested),'assets')
                if raw:
                    response=json.loads(raw)
                    if response.get('StatusCode')!=0:raise ValueError('ISSUER_STATUS_NOT_SUCCESS')
                    entry=response['Entries']
                    if entry['FundID']!=matches[0]['CNo']:raise ValueError('FUND_ID_MISMATCH')
                    data=entry['Data'];asof=data['FundAsset']['NavDate'].replace('/','-')
                    if asof!=requested:raise ValueError('REQUESTED_DATE_NOT_RETURNED')
                    stocks=[t for t in data['Table'] if t['TableTitle']=='股票']
                    for table in stocks:
                        if table['NavDate'].replace('/','-')!=asof:raise ValueError('TABLE_DATE_MISMATCH')
                        if [c['Name'] for c in table['Columns']]!=['股票代號','股票名稱','股數','權重(%)']:
                            raise ValueError('ISSUER_COLUMN_CHANGED')
                        for ticker,name,quantity,weight in table['Rows']:add(ticker,name,weight,quantity,asof,'ISSUER_NAV_DATE')
                    if holdings:row.update(status='AVAILABLE_DATED_EQUITY_WEIGHTS',source_date=asof,source_date_semantics='ISSUER_NAV_DATE',reason='Actual official dated equity table; derivatives/cash excluded; not historical publication proof')
        else:
            raw=request(URLS[code])
            if raw and 'capitalfund.com.tw' in row['source_url']:
                soup=BeautifulSoup(raw,'html.parser');inputs=soup.find_all('input',value=re.compile(r'^\d{4}/\d{2}/\d{2}$'))
                labels=sorted({i['value'].replace('/','-') for i in inputs});asof=labels[0] if len(labels)==1 else ''
                for r in soup.select('.pct-stock-table-tbody .tr.show-for-medium'):
                    names=[x.get_text(strip=True) for x in r.select('.th')];values=[x.get_text(strip=True) for x in r.select('.td')]
                    if len(names)==2 and len(values)>=2:add(names[0],names[1],values[0],values[1],asof,'ISSUER_PAGE_DATE_LABEL_VALUATION_CUTOFF_UNCONFIRMED')
                if holdings:row.update(status='OBSERVED_TOP10_DATE_SEMANTICS_UNCONFIRMED',source_date=asof,source_date_semantics='ISSUER_PAGE_DATE_LABEL_VALUATION_CUTOFF_UNCONFIRMED',reason='SSR exposes first10 stock rows; displayed PCF date is not inferred as prior NAV valuation date')
            elif raw:
                row['reason']='Official response retained; bounded parser did not establish a dated actual stock-weight table; marketing holdings/forecasts not substituted'
    except (ValueError,KeyError,TypeError,IndexError) as exc:
        row.update(status='PARSING_UNCONFIRMED',reason=str(exc));holdings=[]
    if holdings:
        keys=[x['ticker'] for x in holdings]
        if len(set(keys))!=len(keys) or sum(x['weight_nav_fraction'] for x in holdings)>1.01:
            row.update(status='WEIGHT_INTEGRITY_FAILED',reason='Duplicate ticker or stock weights exceed NAV');holdings=[]
    row['holdings_count']=len(holdings)
    return row,holdings,attempts


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--date',default='2026-09-21');a=p.parse_args()
    out=Path(a.output).resolve()
    if out.exists():raise SystemExit('Output already exists; evidence is immutable')
    out.mkdir(parents=True);(out/'raw').mkdir()
    reference=ROOT/'daily_auto/reference/official_reference.json';items=json.loads(reference.read_text())['active_etfs']
    if len(items)!=30 or len({x['ticker'] for x in items})!=30:raise SystemExit('Official30 reference integrity failure')
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda item:fetch_one(item,out,a.date),items))
    coverage=[x[0] for x in results];holdings=[r for x in results for r in x[1]];attempts=[r for x in results for r in x[2]]
    for name,rows in [('coverage',coverage),('holdings_observed',holdings)]:
        if rows:
            with (out/(name+'.csv')).open('w',newline='') as f:
                w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    write_json(out/'request_manifest.json',dict(generated_at=stamp(),reference_sha256=sha(reference.read_bytes()),
               script_sha256=sha(Path(__file__).read_bytes()),requested_date=a.date,attempts=attempts,
               disclosure='Observed current issuer responses only. No competition AS calculation or historical known_at proof.',
               output_hashes={p.name:sha(p.read_bytes()) for p in out.glob('*.csv')}))
    print(json.dumps(dict(rows=len(coverage),holdings=len(holdings),status_counts={s:sum(r['status']==s for r in coverage) for s in sorted({r['status'] for r in coverage})}),ensure_ascii=False))


if __name__=='__main__':main()
