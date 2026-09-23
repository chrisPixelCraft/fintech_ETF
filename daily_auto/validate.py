"""Offline D-Plan schema and conservative prior-close preflight.

Public API:
  load_json(path): reject duplicate keys / NaN; preserve JSON decimals.
  derive_orders(plan, state): exact Decimal formula, no lot rounding repairs.
  validate_plan(plan, state, checked_at=..., filename=..., evidence_root=...)

The state contract is demonstrated by examples/state.template.json. Monetary
inputs accept decimal strings; holdings shares must be nonnegative integers.
Every official evidence record uses known_at, authority, source_url and sha256.
An optional evidence_file is checked against sha256 relative to evidence_root.
This checker validates supplied provenance metadata/hashes, not remote origin,
future VWAP settlement, factual prose, strategy/theme alignment or official
server acceptance. A local check never issues formal certification.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR, localcontext
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT/'official_docs/D-Plan.schema.json'
OFFICIAL_REFERENCE = ROOT/'daily_auto/reference/official_reference.json'
TZ = timezone(timedelta(hours=8))
FEE, TAX, LOT = Decimal('0.001425'), Decimal('0.003'), Decimal(1000)
ZERO, ONE = Decimal(0), Decimal(1)
SHA = re.compile(r'^(?:sha256:)?[0-9a-f]{64}$')
TICKER = re.compile(r'^\d{4}$')
OFFICIAL_DOMAINS = {'twse':('twse.com.tw',), 'tpex':('tpex.org.tw',),
                    'taifex':('taifex.com.tw',), 'mops':('twse.com.tw', 'tpex.org.tw'),
                    'organizer':('esun-ai-challenge.tw',)}


class PreflightError(ValueError):
    def __init__(self, code, message):
        super().__init__(message); self.code = code


def decimal(value, name='number'):
    if isinstance(value, bool) or value is None:
        raise PreflightError('INVALID_NUMBER', f'{name} must be an explicit finite decimal')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise PreflightError('INVALID_NUMBER', f'{name} is not a decimal') from None
    if not result.is_finite():
        raise PreflightError('INVALID_NUMBER', f'{name} must be finite')
    return result


def load_json(path):
    def unique(pairs):
        result = {}
        for key,value in pairs:
            if key in result: raise PreflightError('DUPLICATE_JSON_KEY', key)
            result[key] = value
        return result
    def reject(value):
        raise PreflightError('NONFINITE_JSON', value)
    return json.loads(Path(path).read_text(), parse_float=Decimal,
                      parse_constant=reject, object_pairs_hook=unique)


def json_safe(value):
    if isinstance(value, Decimal): return format(value, 'f')
    if isinstance(value, (date, datetime)): return value.isoformat()
    if isinstance(value, dict): return {str(k):json_safe(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)): return [json_safe(v) for v in value]
    return value


def _date(value, label):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value): raise ValueError()
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise PreflightError('INVALID_DATE', label + ' is not a real YYYY-MM-DD date') from None


def _timestamp(value, label):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?\+08:00', value): raise ValueError()
        result = datetime.fromisoformat(value)
        if result.utcoffset() != timedelta(hours=8): raise ValueError()
        return result
    except (TypeError, ValueError):
        raise PreflightError('INVALID_TAIPEI_TIMESTAMP', label + ' requires a real +08:00 timestamp') from None


def _maps(state):
    holdings, closes = {}, {}
    for row in state.get('holdings', []):
        ticker = row.get('ticker')
        if not isinstance(ticker,str) or not TICKER.fullmatch(ticker) or ticker in holdings:
            raise PreflightError('HOLDINGS_IDENTITY', 'Holdings tickers must be unique four-digit strings')
        shares = decimal(row.get('shares'), 'holdings.shares')
        if shares <= 0 or shares != shares.to_integral_value():
            raise PreflightError('HOLDINGS_SHARES', 'Prior holdings must be positive integral shares; do not round corporate events')
        holdings[ticker] = shares
    for row in state.get('closes', []):
        ticker = row.get('ticker')
        if not isinstance(ticker,str) or not TICKER.fullmatch(ticker) or ticker in closes:
            raise PreflightError('CLOSE_IDENTITY', 'Closing prices must have unique four-digit tickers')
        price = decimal(row.get('close'), 'close')
        if price <= 0: raise PreflightError('CLOSE_VALUE', 'Closing prices must be positive')
        closes[ticker] = price
    return holdings, closes


def derive_orders(plan, state):
    """Official floor(weight*NAV/close/1000)*1000 minus ACTUAL held shares.

    Returns core orders in decision order and human-auditable derivations.
    Odd holdings are preserved. A non-lot delta raises rather than rounding it.
    """
    holdings, closes = _maps(state)
    nav = decimal(state.get('nav'), 'nav')
    if nav <= 0: raise PreflightError('NAV_VALUE','NAV must be positive')
    orders, derivations = [], []
    seen = set()
    with localcontext() as ctx:
        ctx.prec = 60
        for item in plan.get('decisions', []):
            ticker, action = item['ticker'], item['action']
            if ticker in seen: raise PreflightError('DUPLICATE_DECISION_TICKER', ticker)
            seen.add(ticker)
            if ticker not in closes: raise PreflightError('MISSING_CLOSE', ticker)
            weight = decimal(item['target_weight'], 'target_weight')
            if weight < 0 or weight > Decimal('.25'): raise PreflightError('TARGET_WEIGHT_RANGE',ticker)
            held = holdings.get(ticker,ZERO)
            target = (weight*nav/closes[ticker]/LOT).to_integral_value(rounding=ROUND_FLOOR)*LOT
            delta = target-held
            if delta == 0: raise PreflightError('ZERO_DELTA_DECISION', ticker + ': use no_trade_decisions')
            valid = ((action=='BUY' and held==0 and delta>0)
                     or (action=='ADD' and held>0 and delta>0)
                     or (action=='TRIM' and held>0 and delta<0 and target>0)
                     or (action=='SELL_ALL' and held>0 and weight==0 and target==0))
            if not valid: raise PreflightError('ACTION_INCONSISTENT', f'{ticker} {action}: held={held},target={target}')
            if delta < -held: raise PreflightError('OVERSELL', ticker)
            if abs(delta)%LOT != 0:
                raise PreflightError('ODD_LOT_DERIVATION', f'{ticker}: delta={delta}; must not round the authoritative holding')
            orders.append(dict(ticker=ticker,side='BUY' if delta>0 else 'SELL',shares=int(abs(delta)),decision_ref=item['decision_id']))
            derivations.append(dict(ticker=ticker,decision_ref=item['decision_id'],target_weight=weight,
                previous_nav=nav,previous_close=closes[ticker],held_shares=int(held),
                target_shares=int(target),signed_delta=int(delta)))
    return dict(orders=orders,derivations=json_safe(derivations))


def project_orders(state, orders):
    """Prior-close estimate with exact unrounded percentage fees, not T-day VWAP."""
    holdings, closes = _maps(state)
    cash = decimal(state.get('cash'),'cash'); buys=sells=fees=taxes=ZERO
    projected = holdings.copy()
    for order in orders:
        ticker=order['ticker'];q=decimal(order['shares'],'shares')
        if ticker not in closes: raise PreflightError('MISSING_CLOSE',ticker)
        if q<=0 or q%LOT: raise PreflightError('ORDER_LOT',ticker)
        gross=q*closes[ticker];fees+=gross*FEE
        if order['side']=='BUY':
            buys+=gross;cash-=gross*(ONE+FEE);projected[ticker]=projected.get(ticker,ZERO)+q
        elif order['side']=='SELL':
            if q>projected.get(ticker,ZERO):raise PreflightError('OVERSELL',ticker)
            sells+=gross;taxes+=gross*TAX;cash+=gross*(ONE-FEE-TAX);projected[ticker]-=q
        else: raise PreflightError('ORDER_SIDE',ticker)
    projected={t:q for t,q in projected.items() if q>0}
    missing=set(projected)-set(closes)
    if missing:raise PreflightError('MISSING_CLOSE',','.join(sorted(missing)))
    stock_value=sum((q*closes[t] for t,q in projected.items()),ZERO)
    nav=cash+stock_value
    if nav<=0:raise PreflightError('PROJECTED_NAV_NONPOSITIVE','Post-order estimate has nonpositive NAV')
    weights={t:q*closes[t]/nav for t,q in projected.items()}
    return dict(cash=cash,nav=nav,cash_ratio=cash/nav,stock_value=stock_value,
        holdings=projected,weights=weights,buy_notional=buys,sell_notional=sells,
        net_notional=buys-sells,commission=fees,sell_tax=taxes,fees_and_tax=fees+taxes,
        valuation='T_MINUS_1_CLOSE_ESTIMATE_NOT_ACTUAL_FILL')


def active_share_value(portfolio_weights, benchmark_weights):
    """Diagnostic TOP10 union using full-NAV weights; no implicit normalization."""
    def top(weights):
        values={str(t):decimal(w) for t,w in weights.items()}
        if any(w<0 or w>1 for w in values.values()) or sum(values.values(),ZERO)>1+Decimal('1e-12'):
            raise PreflightError('ACTIVE_SHARE_WEIGHTS','Full-NAV weights must be between 0 and 1 and sum <=1')
        return dict(sorted(values.items(),key=lambda x:(-x[1],x[0]))[:10])
    left,right=top(portfolio_weights),top(benchmark_weights)
    return sum((abs(left.get(t,ZERO)-right.get(t,ZERO)) for t in set(left)|set(right)),ZERO)/2


class _Checks:
    def __init__(self, checked_at, evidence_root):
        self.rows=[];self.checked_at=checked_at;self.evidence_root=Path(evidence_root)

    def add(self, code, status, message, **details):
        self.rows.append(dict(code=code,status=status,message=message,**json_safe(details)))

    def condition(self, condition, code, message, **details):
        self.add(code,'PASS' if condition else 'FAIL',message,**details)
        return bool(condition)

    def evidence(self, record, label, cutoff, allowed=None):
        if not isinstance(record,dict):
            self.add('MISSING_EVIDENCE','UNKNOWN',label);return False
        required=('known_at','authority','source_url','sha256')
        if any(record.get(k) is None for k in required):
            self.add('MISSING_EVIDENCE','UNKNOWN',label+' needs known_at/authority/source_url/sha256');return False
        valid=True
        try:known=_timestamp(record['known_at'],label+'.known_at')
        except PreflightError as exc:self.add(exc.code,'FAIL',str(exc));return False
        valid &= self.condition(known<=cutoff,'EVIDENCE_KNOWN_AT',label+' must be known by decision completion')
        valid &= self.condition(isinstance(record['sha256'],str) and bool(SHA.fullmatch(record['sha256'])),
                                'EVIDENCE_HASH_FORMAT',label)
        authority=record['authority'];url=urlparse(str(record['source_url']))
        hosts=OFFICIAL_DOMAINS.get(authority,())
        official=url.scheme=='https' and bool(url.hostname) and any(url.hostname==h or url.hostname.endswith('.'+h) for h in hosts)
        if allowed is not None:
            valid &= self.condition(authority in allowed and official,'OFFICIAL_EVIDENCE_ORIGIN',label)
        elif not url.scheme or not url.netloc:
            valid &= self.condition(False,'EVIDENCE_URL',label)
        if record.get('evidence_file'):
            path=Path(record['evidence_file'])
            if not path.is_absolute():path=self.evidence_root/path
            if not path.is_file():self.add('EVIDENCE_FILE_MISSING','UNKNOWN',str(path));return False
            actual=hashlib.sha256(path.read_bytes()).hexdigest()
            valid &= self.condition(actual==str(record['sha256']).removeprefix('sha256:'),'EVIDENCE_FILE_HASH',label)
        return bool(valid)


def _references(plan, checks):
    layers=[('sources','source_id','S'),('observations','obs_id','O'),('inferences','inf_id','I'),('decisions','decision_id','D')]
    sets={}
    for name,key,prefix in layers:
        actual=[row[key] for row in plan[name]]
        checks.condition(actual==[prefix+str(i+1) for i in range(len(actual))],
                         'SEQUENTIAL_IDS',name+' must start at 1 with no gaps/duplicates/leading zeros')
        sets[name]=set(actual)
    for row in plan['observations']:
        checks.condition(set(row['source_ref'])<=sets['sources'],'REFERENCE_CHAIN',row['obs_id']+' source_ref')
    for row in plan['inferences']:
        checks.condition(set(row['premise_refs'])<=sets['observations'],'REFERENCE_CHAIN',row['inf_id']+' premise_refs')
    checks.condition(set(plan['market_view']['basis_refs'])<=sets['observations'],'REFERENCE_CHAIN','market_view.basis_refs')
    for row in plan['decisions']:
        checks.condition(set(row['inference_refs'])<=sets['inferences'],'REFERENCE_CHAIN',row['decision_id']+' inference_refs')
    for row in plan['no_trade_decisions']:
        checks.condition(set(row['reason_refs'])<=sets['inferences'],'REFERENCE_CHAIN',row['ticker']+' reason_refs')
    decisions={r['decision_id']:r for r in plan['decisions']}
    for row in plan['orders']:
        ref=decisions.get(row['decision_ref'])
        checks.condition(ref is not None and ref['ticker']==row['ticker'],'REFERENCE_CHAIN','order '+row['decision_ref'])
    for row in plan['decisions']:
        if 'funding_for' in row:
            valid=row['action'] in ('TRIM','SELL_ALL') and all(r in decisions and decisions[r]['action'] in ('BUY','ADD') for r in row['funding_for'])
            checks.condition(valid,'FUNDING_REFERENCES',row['decision_id'])


def _active_share(state, projection, checks, cutoff, official_etfs):
    block=state.get('active_share')
    if not isinstance(block,dict) or block.get('status')!='AVAILABLE':
        checks.add('ACTIVE_SHARE_UNKNOWN','UNKNOWN','Complete time-stamped ETF holdings and an official method clarification are required')
        return dict(status='UNKNOWN',comparisons=[])
    method=block.get('method_confirmation')
    method_valid=(isinstance(method,dict) and method.get('method')=='TOP10_UNION_FULL_NAV'
                  and method.get('tie_break')=='ticker' and method.get('status')=='OFFICIAL_CONFIRMED')
    method_valid=checks.evidence(method,'active_share.method_confirmation',cutoff,{'organizer'}) and method_valid
    if not method_valid or block.get('method')!='TOP10_UNION_FULL_NAV':
        checks.add('ACTIVE_SHARE_METHOD_UNRESOLVED','UNKNOWN','No inference about normalization, union, tie handling or benchmark timing is allowed')
        return dict(status='UNKNOWN',comparisons=[])
    reference=block.get('reference_list')
    reference_valid=checks.evidence(reference,'active_share.reference_list',cutoff,{'twse','organizer'})
    ids=reference.get('etf_ids',[]) if isinstance(reference,dict) else []
    etfs=block.get('etfs',[])
    if (not reference_valid or len(ids)!=30 or len(set(ids))!=30 or set(ids)!=official_etfs
            or {r.get('etf_id') for r in etfs}!=set(ids) or len(etfs)!=30):
        checks.add('ACTIVE_SHARE_COVERAGE','UNKNOWN','Need every one of the 30 official ETF reference identities exactly once')
        return dict(status='UNKNOWN',comparisons=[])
    comparisons=[];valid=True
    previous_rows=block.get('previous_results',[])
    previous={r.get('etf_id'):r for r in previous_rows}
    if len(previous)!=len(previous_rows):
        checks.add('ACTIVE_SHARE_HISTORY_DUPLICATE','FAIL','Previous ETF results must be unique')
        valid=False
    for record in etfs:
        name=str(record['etf_id'])
        ready=checks.evidence(record,'ETF '+name,cutoff,{'twse','tpex','mops','organizer'})
        # Issuer-site snapshots require separately established authority; this
        # bounded implementation cannot turn arbitrary fininst URLs into proof.
        ready &= record.get('as_of')==state.get('as_of')
        holdings=record.get('holdings',[])
        if len(holdings)!=10 or len({r.get('ticker') for r in holdings})!=10:
            ready=False
        if not ready:
            checks.add('ACTIVE_SHARE_RECORD_UNKNOWN','UNKNOWN','Incomplete/differently dated official ETF top10: '+name)
            valid=False;continue
        weights={r['ticker']:decimal(r['weight']) for r in holdings}
        value=active_share_value(projection['weights'],weights)
        prev=previous.get(name)
        prior_valid=(isinstance(prev,dict) and prev.get('as_of')==state.get('as_of'))
        prior_valid=checks.evidence(prev,'previous Active Share '+name,cutoff,{'organizer'}) and prior_valid
        if not prior_valid:
            checks.add('ACTIVE_SHARE_HISTORY_UNKNOWN','UNKNOWN','Previous actual comparison missing: '+name);valid=False
        prior_value=decimal(prev['value']) if prior_valid else None
        if prior_value is not None and not ZERO<=prior_value<=ONE:
            checks.add('ACTIVE_SHARE_HISTORY_VALUE','FAIL',name+' actual comparison must be in [0,1]')
            valid=False
        two_days=prior_value is not None and prior_value<Decimal('.20') and value<Decimal('.20')
        checks.condition(value>=Decimal('.20'),'ACTIVE_SHARE_PROJECTED_MINIMUM',name+' prior-close estimate; local conservative first-day block',value=value)
        if two_days:checks.add('ACTIVE_SHARE_TWO_DAY_RISK','FAIL',name+' prior actual and current projected both <20%')
        comparisons.append(dict(etf_id=name,projected_active_share=value,previous_actual_active_share=prior_value,
                                two_day_projected_risk=two_days))
        valid &= value>=Decimal('.20')
    return dict(status='PASS_LOCAL_ESTIMATE' if valid else 'BLOCKED_OR_UNKNOWN',comparisons=json_safe(comparisons))


def validate_plan(plan, state, *, checked_at=None, filename=None, schema_path=SCHEMA, evidence_root=ROOT):
    """Return JSON-safe checks; never fetch, submit, correct or place an order."""
    if checked_at is None:checked_at=datetime.now(TZ)
    if isinstance(checked_at,str):checked_at=_timestamp(checked_at,'checked_at')
    if not isinstance(checked_at,datetime) or checked_at.utcoffset()!=timedelta(hours=8):
        raise PreflightError('INVALID_CHECK_CLOCK','checked_at must be an aware Taipei timestamp')
    checks=_Checks(checked_at,evidence_root)
    report=dict(checked_at=checked_at.isoformat(),checks=checks.rows,projection=None,derived_orders=None,
                active_share=dict(status='UNKNOWN',comparisons=[]),formal_certification='NOT_PROVIDED',
                provenance_scope='SUPPLIED_METADATA_AND_OPTIONAL_LOCAL_HASHES_NOT_REMOTE_AUTHENTICATION',
                limitations=['T-day VWAP/settlement and actual server receipt time are unknown.',
                             'Factual observations, investment logic, source authenticity and prospectus alignment require separate evidence.',
                             'Fee amounts use exact percentages; undisclosed server rounding/minimum-fee rules are not assumed.'])
    def finish():
        report['blocking_codes']=sorted({r['code'] for r in checks.rows if r['status'] in ('FAIL','UNKNOWN')})
        report['status']='BLOCK' if report['blocking_codes'] else 'LOCAL_PREFLIGHT_PASS'
        report['submission_status']='BLOCK_SUBMISSION'  # no official verifier or submission integration
        report['submission_reason']='Local estimates do not certify actual settlement, evidence prose, authentication or server acceptance.'
        return json_safe(report)
    try:
        schema=load_json(schema_path)
        errors=sorted(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(plan),key=lambda e:str(list(e.absolute_path)))
        for error in errors[:100]:checks.add('SCHEMA','FAIL',error.message,path=list(error.absolute_path))
        if errors:return finish()
        # JSON Schema treats NaN as a number; never let that pass a numeric fact.
        def finite_tree(value):
            if isinstance(value,dict):return all(finite_tree(v) for v in value.values())
            if isinstance(value,list):return all(finite_tree(v) for v in value)
            return not isinstance(value,(float,Decimal)) or math.isfinite(value)
        if not finite_tree(plan):checks.add('NONFINITE_NUMBER','FAIL','All JSON numbers must be finite');return finish()
        checks.add('SCHEMA','PASS','Official D-Plan v4 schema with URI format checking')
        trade=_date(plan['trade_date'],'trade_date')
        start=_timestamp(plan['agent_metadata']['run_started_at'],'run_started_at')
        completed=_timestamp(plan['agent_metadata']['run_completed_at'],'run_completed_at')
        checks.condition(start<=completed<=checked_at,'AGENT_TIME_ORDER','started <= completed <= local check time')
        window_start=datetime.combine(trade,time(5),TZ);window_end=datetime.combine(trade,time(8,55),TZ)
        checks.condition(window_start<=checked_at<=window_end,'CONSERVATIVE_SUBMISSION_WINDOW',
                         'Local submission policy uses trade-date 05:00–08:55 Taipei; before05:00 is not declared an official violation')
        checks.add('WINDOW_DOCUMENT_CONFLICT','WARNING','Schema says 05:00–08:55; PDF allows previous-day19:30–08:55. Generation may occur the previous evening; local submission preflight uses their common window.')
        if filename is not None:
            expected=f'D-Plan_{plan["team_id"]}_{plan["trade_date"]}.json'
            checks.condition(Path(filename).name==expected,'FILENAME_IDENTITY',expected)
        for source in plan['sources']:
            for name in ('content_as_of','published_at','fetched_at'):
                if name in source:
                    stamp=_timestamp(source[name],source['source_id']+'.'+name)
                    checks.condition(stamp<=completed and stamp<=window_end,'SOURCE_TIME_LOCK',source['source_id']+'.'+name)
            if 'published_at' in source and 'fetched_at' in source:
                checks.condition(_timestamp(source['published_at'],'published_at')<=_timestamp(source['fetched_at'],'fetched_at'),
                                 'SOURCE_PUBLICATION_ORDER',source['source_id'])
            if source['authority'] in ('media','vendor','fininst','other') and 'archive_url' not in source:
                checks.add('UNARCHIVED_MUTABLE_SOURCE','WARNING',source['source_id']+' has no external archive; factual content is not certified')
        _references(plan,checks)
        if not isinstance(state,dict) or state.get('state_version')!='1.0':
            checks.add('STATE_MISSING','UNKNOWN','A real state_version=1.0 prior ledger is required');return finish()
        checks.condition(state.get('team_id')==plan['team_id'],'TEAM_IDENTITY','D-Plan and ledger team must match')
        mode=state.get('data_mode')
        if mode!='LIVE':checks.add('NON_LIVE_STATE','UNKNOWN','Historical/template state cannot establish live submission readiness',data_mode=mode)
        else:checks.condition(date(2026,10,26)<=trade<=date(2026,11,27),'CONTEST_DATE_RANGE','Official preliminary period')
        previous=_date(state.get('as_of'),'state.as_of')
        checks.condition(previous<trade,'LEDGER_DATE','Prior ledger must precede trade date')
        known=_timestamp(state.get('known_at'),'state.known_at')
        checks.condition(known<=completed,'LEDGER_KNOWN_AT','Prior ledger known by decision completion')
        ledger=state.get('ledger_provenance')
        checks.evidence(ledger,'ledger_provenance',completed,{'organizer'})
        if not isinstance(ledger,dict) or ledger.get('reconciled') is not True or ledger.get('origin') not in ('official_settlement','reconciled_local'):
            checks.add('LEDGER_UNRECONCILED','UNKNOWN','Need actual settled holdings, including failed/missing fills; projected orders are not a ledger')
        checks.add('LEDGER_GUIDE_CONFLICT','WARNING','Guide both says track the book yourself and consult system holdings; require reconciliation instead of assuming all fills.')
        calendar=state.get('calendar')
        checks.evidence(calendar,'calendar',completed,{'twse','tpex','organizer'})
        if not isinstance(calendar,dict):checks.add('CALENDAR_UNKNOWN','UNKNOWN','Missing official session evidence')
        else:
            sessions=calendar.get('sessions',[])
            parsed=[_date(d,'calendar.sessions') for d in sessions]
            valid=(parsed==sorted(set(parsed)) and trade in parsed and previous in parsed
                   and parsed.index(trade)==parsed.index(previous)+1
                   and calendar.get('trade_date')==plan['trade_date']
                   and calendar.get('previous_session')==state['as_of'] and calendar.get('is_trading_day') is True)
            checks.condition(valid,'TRADING_CALENDAR','Trade date must be next verified session after ledger date')
        holdings,closes=_maps(state)
        all_tickers=set(holdings)|{d['ticker'] for d in plan['decisions']}
        for ticker in all_tickers:
            if ticker not in closes:checks.add('MISSING_CLOSE','UNKNOWN',ticker)
        for row in state.get('closes',[]):
            if row['ticker'] in all_tickers:
                checks.condition(row.get('as_of')==state['as_of'],'CLOSE_DATE',row['ticker']+' requires official previous-session close')
                checks.evidence(row,'close '+row['ticker'],completed,{'twse','tpex'})
        whitelist=state.get('whitelist')
        checks.evidence(whitelist,'whitelist',completed,{'organizer','twse','tpex'})
        tickers=whitelist.get('tickers',[]) if isinstance(whitelist,dict) else []
        reference=load_json(OFFICIAL_REFERENCE)
        official_stocks={r['ticker'] for r in reference['stocks']}
        official_etfs={r['ticker'] for r in reference['active_etfs']}
        if len(official_stocks)!=150 or len(official_etfs)!=30:
            raise PreflightError('OFFICIAL_REFERENCE_INVALID','Local official reference must contain exactly150 stocks and30 ETFs')
        report['official_reference_sha256']=hashlib.sha256(OFFICIAL_REFERENCE.read_bytes()).hexdigest()
        valid_list=(len(tickers)==150 and len(set(tickers))==150 and set(tickers)==official_stocks
                    and all(isinstance(t,str) and TICKER.fullmatch(t) for t in tickers))
        checks.condition(valid_list,'OFFICIAL_150_COVERAGE','Exact150 stock identities from supplied official reference required')
        checks.condition(all_tickers<=set(tickers),'WHITELIST','All prior holdings and proposed decisions must be in official150')
        if isinstance(whitelist,dict) and whitelist.get('effective_from'):
            checks.condition(_date(whitelist['effective_from'],'whitelist.effective_from')<=trade,'WHITELIST_EFFECTIVE','Whitelist not future-effective')
        else:checks.add('WHITELIST_EFFECTIVE_UNKNOWN','UNKNOWN','Whitelist effective_from must be explicit')
        action=state.get('corporate_actions')
        checks.evidence(action,'corporate_actions',completed,{'twse','tpex','mops','organizer'})
        if not isinstance(action,dict) or action.get('trade_date')!=plan['trade_date'] or action.get('status')!='NONE_CONFIRMED':
            checks.add('CORPORATE_ACTION_STATE_UNKNOWN','UNKNOWN','Need a dated no-action check, or separate official action/unit reconciliation before this bounded validator can clear the book')
        changed=[d['ticker'] for d in plan['decisions']];unchanged=[d['ticker'] for d in plan['no_trade_decisions']]
        checks.condition(len(changed)==len(set(changed)) and len(unchanged)==len(set(unchanged)) and not(set(changed)&set(unchanged)),
                         'DECISION_TICKER_UNIQUENESS','Each ticker appears in only one decision layer')
        checks.condition(set(holdings)<=set(changed)|set(unchanged) and set(unchanged)<=set(holdings),
                         'PRIOR_HOLDING_COVERAGE','Every actual prior holding has exactly one change/hold explanation; no phantom hold')
        if any(q%LOT for q in holdings.values()):checks.add('EXISTING_ODD_LOT_POLICY','UNKNOWN','Corporate-action odd holdings retained verbatim; formal no-odd-lot holding interpretation unresolved')
        nav=decimal(state.get('nav'),'nav');cash=decimal(state.get('cash'),'cash')
        receivable=decimal(state.get('dividend_receivable','0'),'dividend_receivable')
        checks.condition(cash>=0 and nav>0 and receivable>=0,'PRIOR_LEDGER_VALUES','Cash/NAV/receivable signs')
        if set(holdings)<=set(closes):
            computed=cash+sum((q*closes[t] for t,q in holdings.items()),ZERO)
            checks.condition(abs(computed-nav)<=Decimal('.01'),'LEDGER_NAV_RECONCILIATION',
                             'Book NAV=cash+close-valued stocks; unsettled dividends excluded',computed_nav=computed,declared_nav=nav)
        derived=derive_orders(plan,state);report['derived_orders']=derived
        actual=[{k:o[k] for k in ('ticker','side','shares','decision_ref')} for o in plan['orders']]
        checks.condition(actual==derived['orders'],'OFFICIAL_DERIVED_ORDERS','Canonical order core and sequence must exactly match decisions; explanatory text ignored')
        seen=set()
        for order in actual:
            checks.condition(order['ticker'] not in seen,'ONE_ORDER_PER_TICKER','No same-name intraday buys/sells')
            seen.add(order['ticker'])
            checks.condition(order['side']!='SELL' or decimal(order['shares'])<=holdings.get(order['ticker'],ZERO),
                             'NO_OVERSELL',order['ticker'])
        projection=project_orders(state,actual);report['projection']=json_safe(projection)
        low,high=map(decimal,plan['market_view']['posture']['target_cash_pct_range'])
        checks.condition(low<=high,'POSTURE_CASH_RANGE_ORDER','Cash interval lower <= upper')
        checks.condition(low<=projection['cash_ratio']<=high,'POSTURE_CASH_ESTIMATE','Fee-inclusive prior-close projected cash lies in declared interval')
        intent=plan['market_view']['posture']['net_exposure_intent'];net=projection['net_notional']
        matched=(net>0 if intent=='increase' else net<0 if intent=='reduce' else abs(net)<=Decimal('.02')*nav)
        checks.condition(matched,'POSTURE_NET_FLOW','Declared intent matches previous-close gross net flow',net_notional=net)
        checks.condition(projection['cash']>=0 and ZERO<=projection['cash_ratio']<Decimal('.25'),'PROJECTED_CASH_HARD_LIMIT','Cash >=0 and strictly <25% at prior-close estimate')
        checks.condition(20<=len(projection['holdings'])<=30,'PROJECTED_HOLDING_COUNT','Projected positive positions20–30',count=len(projection['holdings']))
        for d in plan['decisions']:
            cap=Decimal('.25') if d['ticker']=='2330' else Decimal('.10')
            checks.condition(decimal(d['target_weight'])<=cap,'DECLARED_TARGET_CAP',d['ticker'])
        prior_details={h['ticker']:h for h in state.get('holdings',[])}
        buys={o['ticker'] for o in actual if o['side']=='BUY'}
        for ticker,weight in projection['weights'].items():
            cap=Decimal('.25') if ticker=='2330' else Decimal('.10')
            if weight<=cap:continue
            age=prior_details.get(ticker,{}).get('passive_cap_days')
            prior_over=holdings.get(ticker,ZERO)*closes[ticker]/nav>cap
            if ticker in buys:checks.add('ACTIVE_CAP_BREACH','FAIL',ticker,projected_weight=weight)
            elif isinstance(age,int) and not isinstance(age,bool) and age>=1 and prior_over:
                checks.add('PASSIVE_CAP_GRACE' if age+1<=5 else 'PASSIVE_CAP_OVERDUE',
                           'WARNING' if age+1<=5 else 'FAIL',ticker,projected_overweight_day=age+1,projected_weight=weight)
            else:checks.add('PASSIVE_CAP_HISTORY_UNKNOWN','UNKNOWN',ticker+' has no proven passive-overweight history',projected_weight=weight)
        funded={d['decision_id'] for d in plan['decisions'] if 'funding_for' in d}
        if funded:
            without=project_orders(state,[o for o in actual if o['decision_ref'] not in funded])
            checks.condition(without['cash_ratio']<low,'FUNDING_DEFICIT_EXISTS',
                             'Removing all declared funding sales must put estimated cash below posture lower bound',without_funding_cash_ratio=without['cash_ratio'])
        report['active_share']=_active_share(state,projection,checks,completed,official_etfs)
    except PreflightError as exc:checks.add(exc.code,'FAIL',str(exc))
    except (KeyError,TypeError,ValueError,IndexError,AttributeError,OSError) as exc:
        checks.add('MALFORMED_STATE','FAIL',str(exc))
    return finish()
