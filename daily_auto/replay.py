"""Export an audited historical A plan as D-Plan v4; always blocked from submission.

This demonstrates automatic report/order generation without inventing historical
capture times, LLM calls, ETF weights, platform holdings or server receipts.
"""
from pathlib import Path
import argparse
from datetime import datetime
import hashlib
import json
import shutil

import numpy as np
import pandas as pd

from daily_auto.validate import TZ, derive_orders, project_orders, validate_plan, json_safe

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    return pd.read_csv(path, float_precision='round_trip', low_memory=False)


def packet(track, trade_date, study=None):
    study = Path(study or ROOT/'outputs/official_v2_reaudit').resolve()
    folder = study/track
    audit = json.loads((folder/'audit.json').read_text())
    if audit.get('status') != 'PASS' or audit.get('receipt_sha256') != sha(folder/'receipt.json'):
        raise ValueError('Audited complete replay required')
    if audit.get('auditor_sha256') != sha(ROOT/'scripts/audit_official_v2.py'):
        raise ValueError('Auditor changed')
    manifest_path=folder/'manifest.json'
    if audit.get('manifest_sha256') != sha(manifest_path):
        raise ValueError('Source manifest changed')
    manifest=json.loads(manifest_path.read_text())
    for name,digest in {**manifest['hashes'],**audit['documents']['reference_hashes']}.items():
        if sha(ROOT/name)!=digest:
            raise ValueError('Verified source/reference changed: '+name)
    for name,digest in audit['artifact_hashes'].items():
        if sha(folder/name) != digest:
            raise ValueError('Verified replay artifact changed: '+name)
    config_path = folder/'final/A_dplan_guard/config.json'
    model = config_path.parent
    cfg = json.loads(config_path.read_text())
    equity, orders, positions, signals, snapshots = [_read(model/(name+'.csv')) for name in ('equity','orders','holdings','signals','snapshots')]
    if trade_date not in set(equity.date):
        raise ValueError('No audited complete session for '+trade_date)
    candidates = snapshots[snapshots.decision_date.eq(trade_date)]
    if len(candidates) != 1:
        raise ValueError('Need one prior-session plan')
    snap = candidates.iloc[0]; signal_date = str(snap.date)
    orders = orders[orders.signal_date.eq(signal_date)].sort_values('symbol')
    positions = positions[positions.date.eq(signal_date)]
    signals = signals[signals.date.eq(signal_date)].set_index('symbol')
    previous = equity[equity.date.eq(signal_date)]
    book = previous.iloc[0].to_dict() if len(previous) else dict(cash=cfg['initial_cash'],nav=cfg['initial_cash'],dividend_receivable=0.)
    if not previous.empty and signal_date == equity.date.iloc[-1]:
        raise ValueError('Terminal dividend-settled row has no audited next-session plan')
    # Export official four-digit identities; historical aliases remain in the receipt.
    universe_path=ROOT/'data/tuning_2nd/official_universe/processed/universe.csv'
    # Even the historical track's export reads the current official identity map.
    # Bind it to the separately audited official scenario before using aliases.
    official_audit_path=study/'official_ex_post/audit.json'
    official_manifest_path=study/'official_ex_post/manifest.json'
    official_audit=json.loads(official_audit_path.read_text())
    if official_audit.get('status')!='PASS' or official_audit.get('manifest_sha256')!=sha(official_manifest_path):
        raise ValueError('Official identity map has no current audit')
    alias_digest=json.loads(official_manifest_path.read_text())['hashes'][str(universe_path.relative_to(ROOT))]
    if sha(universe_path)!=alias_digest:raise ValueError('Official identity alias changed')
    universe = _read(universe_path)
    official_map = dict(zip(universe.symbol,universe.official_ticker.astype(str)))
    ticker = lambda symbol: official_map.get(symbol, symbol.split('.')[0])
    holdings = {r.symbol:float(r.shares) for r in positions.itertuples()}
    now = datetime.now(TZ).replace(microsecond=0).isoformat()
    ref_path = ROOT/'daily_auto/reference/official_reference.json'
    reference = json.loads(ref_path.read_text())
    code_paths=[config_path,Path(__file__),ROOT/'daily_auto/validate.py',ROOT/'daily_auto/cli.py',
        ROOT/'daily_auto/__init__.py',ROOT/'official_docs/D-Plan.schema.json']
    code_hashes={str(p.relative_to(ROOT)):sha(p) for p in code_paths}
    bundle_sha=hashlib.sha256(json.dumps(code_hashes,sort_keys=True).encode()).hexdigest()
    def evidence(path):
        return dict(known_at=now,authority='other',source_url=Path(path).resolve().as_uri(),sha256=sha(path))
    state = dict(state_version='1.0',data_mode='HISTORICAL_REPLAY',team_id='RESEARCH_ONLY',
        as_of=signal_date,known_at=now,cash=str(book['cash']),nav=str(book['nav']),
        dividend_receivable=str(book['dividend_receivable']),
        holdings=[dict(ticker=ticker(s),shares=int(q) if q.is_integer() else str(q)) for s,q in sorted(holdings.items())],
        closes=[dict(ticker=ticker(s),close=str(row.close),as_of=signal_date,**evidence(model/'signals.csv')) for s,row in signals.iterrows()],
        calendar=dict(trade_date=trade_date,previous_session=signal_date,sessions=[signal_date,trade_date],
            is_trading_day=True,**evidence(model/'equity.csv')),
        whitelist=dict(tickers=[r['ticker'] for r in reference['stocks']],effective_from='2026-07-31',**evidence(ref_path)),
        ledger_provenance=dict(origin='research_shadow',reconciled=False,**evidence(model/'equity.csv')),
        corporate_actions=dict(trade_date=trade_date,status='UNKNOWN',**evidence(model/'config.json')),
        active_share=dict(status='UNKNOWN',method=None,method_confirmation=None,reference_list=None,etfs=[],previous_results=[]))
    sources=[]
    for i,name in enumerate(('signals.csv','equity.csv','orders.csv','holdings.csv','config.json','snapshots.csv'),1):
        sources.append(dict(source_id=f'S{i}',authority='other',url=(model/name).as_uri(),
            name='Historical research replay: '+name,content_as_of=now if name=='config.json' else signal_date+'T13:30:00+08:00',fetched_at=now))
    numeric = pd.to_numeric(signals['return20'],errors='coerce')
    positive = float(numeric.gt(0).mean())
    plan = dict(schema_version='4.0',doc_type='D-Plan',team_id='RESEARCH_ONLY',trade_date=trade_date,
        sources=sources,observations=[dict(obs_id='O1',source_ref=['S1'],
            statement='研究股票池的短期動能正值比例與可入選數量，僅描述已完成前日資料。',
            values=dict(positive_short_momentum_fraction=positive,eligible_names=int(signals.entry_ok.sum()))),
            dict(obs_id='O2',source_ref=['S2','S5'],statement='前日研究帳本的現金與帳面 NAV；首日使用設定中的初始資金，未包含尚未入帳的應收股息。',
                values=dict(cash=float(book['cash']),book_nav=float(book['nav']))),
            dict(obs_id='O3',source_ref=['S3','S6'],statement='凍結計畫的原因代碼為 '+str(snap.plan_reason)+'，委託筆數可由訂單檔核對。',
                values=dict(planned_order_count=len(orders)))],
        market_view=dict(basis_refs=['O1','O2'],logic='固定 A 以股票池價格動能和趨勢進行選股，不另施加宏觀擇時。操作姿態依實際委託淨流向描述，僅為歷史研究回放。',
            regime='neutral',stance='neutral',posture=dict(net_exposure_intent='hold',target_cash_pct_range=[0,.24]),
            counter_evidence='動能訊號可能反轉；此帳本及資料未經主辦方結算或正式收件認證。'),
        inferences=[],decisions=[],no_trade_decisions=[],orders=[],
        agent_metadata=dict(model_provider='other',model_version='deterministic-v2-A-replay',
            run_started_at=now,run_completed_at=now,code_version='sha256:'+bundle_sha[:48]))
    modified=set(orders.symbol)
    names=sorted(set(holdings)|modified)
    for i,symbol in enumerate(names,1):
        row=signals.loc[symbol]
        values={'close':float(row.close),'held_shares':holdings.get(symbol,0.)}
        for key in ('score','return20','return50','ema20','ema50','macd_hist'):
            if key in row and pd.notna(row[key]) and np.isfinite(float(row[key])):values[key]=float(row[key])
        obs=f'O{i+3}'; inf=f'I{i}'
        plan['observations'].append(dict(obs_id=obs,source_ref=['S1','S4'],statement=f'{ticker(symbol)} 的前日價格、動能及研究持股資料；數值來自凍結回放。',values=values))
        odd=bool(holdings.get(symbol,0.)%1000)
        reason=('既有零股部位依保守相容政策續抱，不產生無法符合官方整張目標公式的異動。' if odd and symbol not in modified else
                '依固定 A 的動能訊號、既有持股及現金／權重限制執行原計畫；本輪未重新選參數。')
        plan['inferences'].append(dict(inf_id=inf,premise_refs=[obs,'O2','O3'],logic=reason+' 本日計畫原因：'+str(snap.plan_reason),
            counter_evidence='成交均價尚未知；正式 Active Share 與公司事件處理仍需額外證據。'))
        if symbol in modified:
            order=orders[orders.symbol.eq(symbol)].iloc[0]; held=holdings.get(symbol,0.);target=float(order.target_shares)
            action=('BUY' if held==0 else 'ADD') if order.shares>0 else ('SELL_ALL' if target==0 else 'TRIM')
            plan['decisions'].append(dict(decision_id=f'D{len(plan["decisions"])+1}',ticker=ticker(symbol),action=action,
                target_weight=float(order.target_weight),inference_refs=[inf]))
        else:
            plan['no_trade_decisions'].append(dict(ticker=ticker(symbol),reason_refs=[inf],reason=reason))
    # Derive from decisions, never independently hand-edit the order list.
    plan['orders']=derive_orders(plan,state)['orders']
    expected={ticker(r.symbol):float(r.shares) for r in orders.itertuples()}
    actual={r['ticker']:r['shares']*(1 if r['side']=='BUY' else -1) for r in plan['orders']}
    if actual!=expected:raise ValueError('Exported official formula differs from audited fixed orders')
    projection=project_orders(state,plan['orders']);net=float(projection['net_notional']);nav=float(book['nav'])
    intent='hold' if abs(net)<=.02*nav else 'increase' if net>0 else 'reduce'
    ratio=float(projection['cash_ratio'])
    plan['market_view']['posture']=dict(net_exposure_intent=intent,target_cash_pct_range=[max(0,ratio-.01),min(.249,ratio+.01)])
    filename=f'D-Plan_RESEARCH_ONLY_{trade_date}.json'
    validation=validate_plan(plan,state,checked_at=now,filename=filename)
    if validation['status']=='LOCAL_PREFLIGHT_PASS':
        raise ValueError('Historical replay must never become a live pass')
    return plan,state,validation,dict(mode='HISTORICAL_REPLAY',signal_date=signal_date,trade_date=trade_date,
        source_track=track,source_audit_sha256=sha(folder/'audit.json'),
        code_bundle_sha256=bundle_sha,code_files=code_hashes,
        generated_at=now,source_files={str(p.relative_to(ROOT)):sha(p) for p in
            (model/'signals.csv',model/'orders.csv',model/'holdings.csv',model/'equity.csv',model/'snapshots.csv',config_path,Path(__file__),
             universe_path,ref_path,manifest_path,official_manifest_path,official_audit_path,
             ROOT/'official_docs/D-Plan.schema.json',ROOT/'daily_auto/validate.py')},
        submission_status='BLOCK_SUBMISSION',live_data_capture=False,
        note='Actual generation time retained; no historical timestamp or LLM execution has been fabricated.')


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--track',choices=['historical_pit','official_ex_post'],default='official_ex_post')
    p.add_argument('--date',default='2025-01-02',help='Audited trading session; initial day avoids unresolved corporate fractional-share bookkeeping')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(argv)
    if args.output.exists():raise FileExistsError('Preserve prior packet: '+str(args.output))
    plan,state,validation,receipt=packet(args.track,args.date)
    args.output.mkdir(parents=True)
    files={f'D-Plan_RESEARCH_ONLY_{args.date}.json':plan,'state.json':state,'preflight.json':validation,'receipt.json':receipt}
    for name,obj in files.items():
        (args.output/name).write_text(json.dumps(json_safe(obj),ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    for name,digest in receipt['code_files'].items():
        if sha(ROOT/name)!=digest:raise ValueError('Code changed during export: '+name)
        dest=args.output/'code_snapshot'/name
        dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,dest)
    (args.output/'README.md').write_text('# 歷史 D-Plan 自動產出示範\n\n只供本機流程驗證，禁止正式提交。\n\n'
        '公式與訂單對照已核對；來源是現在讀取的歷史研究檔案，未偽裝為當時已封存的官方資料。'
        'preflight 的 BLOCK 是預期結果，不是主辦方警告。實際上線仍需官方帳本、時點資料、Active Share 與收件介面。\n')
    print(str(args.output.resolve()));print('BLOCK_SUBMISSION: audited historical replay packet, no network submission.')


if __name__=='__main__':main()
