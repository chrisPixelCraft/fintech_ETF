"""Export deterministic, blocked paper decision packets from an audited historical replay.

This is a historical paper-replay command, not a live-data ingestion service or
exchange submission client. No network orders or artificial future sessions.
"""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import pandas as pd

ROOT=Path(__file__).resolve().parent

def clean(value):
    if isinstance(value,dict):return {str(k):clean(v) for k,v in value.items()}
    if isinstance(value,list):return [clean(v) for v in value]
    if pd.isna(value):return None
    if hasattr(value,'item'):return value.item()
    return value

def encode(value):return json.dumps(clean(value),indent=2,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n'

def run(date, study, output):
    date=str(pd.Timestamp(date).date())
    audit=json.loads((study/'audit.json').read_text())
    if audit['status']!='PASS':raise ValueError('Paper export requires verified replay accounting')
    eq=pd.read_csv(study/'A/equity.csv')
    calendar=sorted(eq.date)
    if date not in calendar:
        raise ValueError('NO_KNOWN_TAIWAN_SESSION: no trading book generated for this date')
    snaps=pd.read_csv(study/'A/snapshots.csv')
    eligible=snaps[snaps.date<date]
    signal_date=eligible.date.max()
    if pd.isna(signal_date):raise ValueError('No prior-close decision')
    cutoff=pd.Timestamp(date).tz_localize('Asia/Taipei')+pd.Timedelta(hours=8,minutes=55)
    files={};snapshot=dict(decision_date=date,signal_date=signal_date,cutoff_at=cutoff.isoformat(),mode='HISTORICAL_PAPER_REPLAY',
        source_study=str(study.relative_to(ROOT)),study_provenance_sha256=hashlib.sha256((study/'provenance.json').read_bytes()).hexdigest(),
        note='Retrospective source snapshot; not an archived historical publication vintage.',versions={})
    lines=[f'# v2 Paper Decision｜{date}','','狀態：`BLOCK_SUBMISSION`。此包是歷史研究回放，尚未取得正式 Active Share 認證。','',
           '| 版本 | 計畫 | 委託筆數 | 目標檔數 |','|---|---|---:|---:|']
    for model in ['A','B','C','D']:
        folder=study/model
        signals=pd.read_csv(folder/'signals.csv');signals=signals[signals.date==signal_date]
        orders=pd.read_csv(folder/'orders.csv');orders=orders[orders.signal_date==signal_date]
        holdings=pd.read_csv(folder/'holdings.csv');holdings=holdings[holdings.date==signal_date]
        model_snaps=pd.read_csv(folder/'snapshots.csv');record=model_snaps[model_snaps.date==signal_date].iloc[0].to_dict()
        if 'decision_date' in record and str(record['decision_date'])!=date:raise ValueError('Decision-date alignment failed')
        quantities=dict(zip(holdings.symbol,holdings.shares))
        for row in orders.itertuples():quantities[row.symbol]=quantities.get(row.symbol,0)+row.shares
        quantities={s:q for s,q in quantities.items() if q>1e-6}
        prices=dict(zip(signals.symbol,signals.close))
        candidates=signals.sort_values(['score','symbol'],ascending=[False,True]).to_dict('records')
        ledger=pd.read_csv(folder/'equity.csv');ledger=ledger[ledger.date==signal_date]
        known_state=ledger.iloc[0].to_dict() if len(ledger) else {'cash':1e9,'nav':1e9,'dividend_receivable':0,'holdings':0}
        snapshot['versions'][model]=dict(signal_rows=candidates,previous_ledger=known_state,previous_holdings=holdings.to_dict('records'),decision_metadata=json.loads(record['metadata']))
        orders_data=dict(version=model,date=date,signal_date=signal_date,cutoff_at=cutoff.isoformat(),status='PAPER_ONLY',orders=orders.to_dict('records'))
        files[f'{model}_candidate.json']=encode(dict(version=model,target_holdings=[dict(symbol=s,shares=q,known_close=prices.get(s)) for s,q in sorted(quantities.items())],ranking=candidates))
        files[f'{model}_orders.json']=encode(orders_data)
        files[f'{model}_compliance.json']=encode(dict(version=model,rule_status='BLOCK_SUBMISSION',reasons=['HISTORICAL_POOL_NOT_OFFICIAL_WHITELIST','ACTIVE_SHARE_UNKNOWN'],plan_reason=record['plan_reason'],decision_cutoff=cutoff.isoformat(),order_book_sha256=hashlib.sha256(files[f'{model}_orders.json'].encode()).hexdigest()))
        lines.append(f"| {model} | {record['plan_reason']} | {len(orders)} | {len(quantities)} |")
    files['snapshot.json']=encode(snapshot)
    files['comparison.md']='\n'.join(lines)+'\n'
    output.mkdir(parents=True,exist_ok=True)
    for name,body in files.items():
        path=output/name
        if path.exists() and path.read_text()!=body:raise FileExistsError('Immutable paper snapshot differs: '+str(path))
    for name,body in files.items():
        path=output/name
        if not path.exists():path.write_text(body)
    print(str(output));print('1 snapshot; 4 candidates; 4 order books; 4 compliance results; 1 comparison. BLOCK_SUBMISSION.')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--date',required=True);p.add_argument('--mode',choices=['paper'],default='paper')
    p.add_argument('--study',default='outputs/backtest_v2_2025_to_now');p.add_argument('--output');args=p.parse_args()
    run(args.date,ROOT/args.study,ROOT/(args.output or f'outputs/paper_daily/{args.date}'))
