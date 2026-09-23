"""Official evidence acquisition and local operation journal; NEVER submits.

Only fetch_record() can create TRANSPORT_VERIFIED records, after an explicit
reviewed endpoint contract and an actual, certificate-verified HTTPS GET.
import_record() always creates MANUAL_UNVERIFIED records, even if their JSON
claims otherwise. There is no public 'trust=True' switch or manual promotion.

Canonical data shapes are documented in official_evidence_status.md. Adapters
map JSON pointers to these shapes; missing/ambiguous fields fail closed. Raw
bytes, adapter/spec snapshots, and journal events are retained. Hashes detect
changed local artifacts; they are NOT signatures against a hostile local owner.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import ssl
import urllib.error
import urllib.request
from urllib.parse import urlparse, parse_qsl
import uuid

from daily_auto.validate import (TZ, SCHEMA, _date, _timestamp, decimal, json_safe,
                                 load_json, PreflightError)
from jsonschema import Draft202012Validator, FormatChecker

CONTEST_START, CONTEST_END = date(2026,10,26), date(2026,11,27)
DAILY_LIMIT, REQUIRED_DAYS, DECLARED_DAYS = 25, 22, 24
KINDS = {'ledger','receipt','quota','calendar'}
TRUSTED, MANUAL = 'TRANSPORT_VERIFIED', 'MANUAL_UNVERIFIED'
FIELDS = {
    'ledger': {'ledger_id','team_id','as_of','settled_at','settlement_status','cash','nav','dividend_receivable','holdings'},
    'receipt': {'receipt_id','team_id','trade_date','filename','plan_sha256','status','received_at'},
    'quota': {'team_id','trade_date','as_of','attempts_used'},
    'calendar': {'sessions','published_at'},
}
TEAM = re.compile(r'^[A-Za-z0-9_-]{1,32}$')
HASH = re.compile(r'^[0-9a-f]{64}$')
MAX_BYTES = 8*1024*1024


class OperationError(PreflightError):
    pass


def _sha(raw):return hashlib.sha256(raw).hexdigest()
def _json(value):return json.dumps(json_safe(value),ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def _clock():return datetime.now(TZ)


def _team(value):
    if not isinstance(value,str) or not TEAM.fullmatch(value):
        raise OperationError('TEAM_FORMAT','An assigned team ID is required')
    return value


def _pointer(value,path):
    if path=='':return value
    if not isinstance(path,str) or not path.startswith('/'):
        raise OperationError('ADAPTER_POINTER','Use explicit JSON pointers, not expressions')
    try:
        for part in path[1:].split('/'):
            part=part.replace('~1','/').replace('~0','~')
            value=value[int(part)] if isinstance(value,list) else value[part]
        return value
    except (KeyError,IndexError,ValueError,TypeError):
        raise OperationError('MISSING_OFFICIAL_FIELD',path) from None


def _official_url(value,kind='ledger'):
    parsed=urlparse(str(value))
    hosts=('esun-ai-challenge.tw',) if kind!='calendar' else ('esun-ai-challenge.tw','twse.com.tw','tpex.org.tw')
    valid=(parsed.scheme=='https' and not parsed.username and not parsed.password
           and parsed.port in (None,443) and not parsed.fragment and parsed.hostname
           and any(parsed.hostname==h or parsed.hostname.endswith('.'+h) for h in hosts))
    if not valid:raise OperationError('ENDPOINT_ORIGIN','Only explicit official HTTPS hosts/port443 are supported')
    if any(re.search('token|secret|key|auth|password|session|cookie',k,re.I) for k,v in parse_qsl(parsed.query)):
        raise OperationError('SECRET_IN_URL','Credentials must never be placed in a URL')
    return value


def _canonical(kind,value,mapping=None):
    if kind not in KINDS:raise OperationError('RECORD_KIND',str(kind))
    if mapping is not None:
        if not isinstance(mapping,dict) or not FIELDS[kind]<=set(mapping):
            raise OperationError('ADAPTER_FIELDS','Explicit mapping required for every canonical field')
        result={name:_pointer(value,path) for name,path in mapping.items()}
    else:
        if not isinstance(value,dict):raise OperationError('RECORD_SHAPE','Expected a JSON object')
        result={k:value[k] for k in FIELDS[kind] if k in value}
        for k in ('attempt_number','client_nonce','official_warning_count'):
            if k in value:result[k]=value[k]
    missing=FIELDS[kind]-set(result)
    if missing:raise OperationError('MISSING_OFFICIAL_FIELD',','.join(sorted(missing)))
    return result


def _validate_record(kind,data):
    """Validate meaning without filling absent values or inferring success."""
    if kind!='calendar':_team(data['team_id'])
    if kind=='ledger':
        if not isinstance(data['ledger_id'],str) or not data['ledger_id']:
            raise OperationError('LEDGER_ID','Nonempty official settlement identity required')
        asof=_date(data['as_of'],'ledger.as_of');settled=_timestamp(data['settled_at'],'settled_at')
        if settled.date()<asof:raise OperationError('LEDGER_TIME','Settlement predates valuation date')
        if data['settlement_status'] not in ('SETTLED','PENDING','FAILED'):
            raise OperationError('SETTLEMENT_STATUS','Map explicit official status; HTTP200 is not settlement')
        for field in ('cash','nav','dividend_receivable'):data[field]=str(decimal(data[field],field))
        _positions(data['holdings'])
        # Preserve fractional/odd corporate-event quantities, do not round.
        positions=[]
        for row in data['holdings']:
            item=dict(ticker=row['ticker'],shares=str(decimal(row['shares'])))
            if 'market_value' in row:
                value=decimal(row['market_value'],'market_value')
                if value<0:raise OperationError('LEDGER_MARKET_VALUE','Official market value cannot be negative')
                item['market_value']=str(value)
            positions.append(item)
        data['holdings']=positions
        if 'official_warning_count' in data:
            value=data['official_warning_count']
            if isinstance(value,bool) or not isinstance(value,int) or value<0:
                raise OperationError('OFFICIAL_WARNING_COUNT','Explicit nonnegative integer required')
    elif kind=='receipt':
        _date(data['trade_date'],'receipt.trade_date');_timestamp(data['received_at'],'received_at')
        if not isinstance(data['receipt_id'],str) or not data['receipt_id']:
            raise OperationError('RECEIPT_ID','Nonempty server receipt identity required')
        if data['status'] not in ('ACCEPTED','REJECTED','PENDING'):
            raise OperationError('RECEIPT_STATUS','Only an explicitly mapped final status is meaningful')
        if not isinstance(data['plan_sha256'],str) or not HASH.fullmatch(data['plan_sha256']):
            raise OperationError('RECEIPT_HASH','Server must bind exact uploaded bytes with SHA256')
        if 'attempt_number' in data:_count(data['attempt_number'])
    elif kind=='quota':
        _date(data['trade_date'],'quota.trade_date');_timestamp(data['as_of'],'quota.as_of');_count(data['attempts_used'])
    else:
        _timestamp(data['published_at'],'calendar.published_at')
        sessions=data['sessions']
        if not isinstance(sessions,list) or not sessions:raise OperationError('CALENDAR_SHAPE','Explicit official sessions required')
        parsed=[_date(d,'calendar.sessions') for d in sessions]
        if parsed!=sorted(set(parsed)):raise OperationError('CALENDAR_ORDER','Sessions must be unique and sorted')
    return data


def _count(value):
    if isinstance(value,bool) or not isinstance(value,int) or not 0<=value<=DAILY_LIMIT:
        raise OperationError('ATTEMPT_COUNT','Official attempt count must be an integer in [0,25]')
    return value


def _positions(rows):
    if not isinstance(rows,list):raise OperationError('LEDGER_POSITIONS','Positions must be an array')
    result={}
    for row in rows:
        ticker=row.get('ticker')
        if not isinstance(ticker,str) or not re.fullmatch(r'\d{4}',ticker) or ticker in result:
            raise OperationError('LEDGER_POSITIONS','Unique four-digit positions required')
        q=decimal(row.get('shares'),'shares')
        if q<=0:raise OperationError('LEDGER_POSITIONS','Positions contain strictly positive actual shares')
        result[ticker]=q
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise OperationError('REDIRECT_BLOCKED','No redirect/auth transfer to another endpoint')


def _get(url,headers):
    opener=urllib.request.build_opener(_NoRedirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    try:
        with opener.open(urllib.request.Request(url,headers=headers,method='GET'),timeout=20) as response:
            if response.status!=200:raise OperationError('HTTP_STATUS',str(response.status))
            if response.geturl()!=url:raise OperationError('REDIRECT_BLOCKED','Response URL changed')
            if 'json' not in response.headers.get('Content-Type','').lower():
                raise OperationError('HTTP_CONTENT_TYPE','Expected documented JSON, not login HTML')
            raw=response.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES:raise OperationError('RESPONSE_TOO_LARGE','Response exceeds8MiB')
            return raw
    except urllib.error.HTTPError as exc:
        raise OperationError('HTTP_STATUS',str(exc.code)) from None
    except (urllib.error.URLError,TimeoutError,OSError):
        # Do not echo exception/header/response bodies that might contain secrets.
        raise OperationError('CONNECTION_FAILED','No authenticated official response acquired') from None


class OperationsStore:
    """Local transactional evidence store. No method sends a submission/email.

    import_record(kind,path,adapter=None): never upgrades manual provenance.
    fetch_record(adapter): one reviewed explicit GET; no endpoint discovery.
    reconcile_ledger(...), verify_receipt(...), reserve_attempt(...), daily_status(...)
    are isolated from strategy generation and never modify a D-Plan or ledger.
    """
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.raw_dir=self.root/'raw';self.raw_dir.mkdir(exist_ok=True,mode=0o700)
        self.db=self.root/'operations.sqlite3'
        with self._db() as con:
            con.executescript('''
            CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY, kind TEXT NOT NULL, raw_sha TEXT NOT NULL,
              provenance TEXT NOT NULL, acquired_at TEXT NOT NULL, data TEXT NOT NULL, metadata TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, team TEXT NOT NULL, trade_date TEXT NOT NULL,
              plan_sha TEXT NOT NULL, filename TEXT NOT NULL, reserved_at TEXT NOT NULL, ordinal INTEGER NOT NULL,
              UNIQUE(team,trade_date,plan_sha), UNIQUE(team,trade_date,ordinal));
            CREATE TABLE IF NOT EXISTS receipts(receipt_id TEXT PRIMARY KEY, record_id TEXT UNIQUE NOT NULL,
              team TEXT NOT NULL, trade_date TEXT NOT NULL, plan_sha TEXT NOT NULL, received_at TEXT NOT NULL,
              outcome TEXT NOT NULL, attempt_id TEXT, UNIQUE(attempt_id));
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, payload TEXT NOT NULL,
              previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL);
            ''')
        self.db.chmod(0o600)

    @contextmanager
    def _db(self):
        con=sqlite3.connect(self.db,timeout=30,isolation_level=None);con.row_factory=sqlite3.Row
        try:
            con.execute('BEGIN IMMEDIATE')
            if con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
                self._check_journal(con)
            yield con;con.commit()
        except Exception:con.rollback();raise
        finally:con.close()

    def _archive(self,raw):
        if not isinstance(raw,bytes) or len(raw)>MAX_BYTES:raise OperationError('RAW_SIZE','Raw bytes required, maximum8MiB')
        sha=_sha(raw);path=self.raw_dir/(sha+'.bin')
        try:
            with path.open('xb') as f:f.write(raw)
            path.chmod(0o600)
        except FileExistsError:
            if path.read_bytes()!=raw:raise OperationError('RAW_INTEGRITY','Archived content hash mismatch')
        return sha

    def _raw(self,sha):
        if not isinstance(sha,str) or not HASH.fullmatch(sha):raise OperationError('RAW_INTEGRITY','Invalid archive hash')
        path=self.raw_dir/(sha+'.bin')
        if not path.is_file() or _sha(path.read_bytes())!=sha:raise OperationError('RAW_INTEGRITY','Archived evidence changed or missing')
        return path

    def _event(self,con,payload):
        row=con.execute('SELECT event_hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
        previous=row[0] if row else '0'*64
        text=_json(dict(at=_clock().isoformat(),**payload));h=_sha((previous+'\n'+text).encode())
        con.execute('INSERT INTO events(payload,previous_hash,event_hash) VALUES(?,?,?)',(text,previous,h))

    def _check_journal(self,con):
        previous='0'*64;payloads=[]
        for row in con.execute('SELECT * FROM events ORDER BY seq'):
            if row['seq']!=len(payloads)+1 or row['previous_hash']!=previous or _sha((previous+'\n'+row['payload']).encode())!=row['event_hash']:
                raise OperationError('JOURNAL_INTEGRITY','Event chain changed or incomplete')
            previous=row['event_hash'];payloads.append(json.loads(row['payload']))
        captures={p['record_id']:p for p in payloads if p['action']=='CAPTURE'}
        if set(captures)!={r[0] for r in con.execute('SELECT id FROM records')}:
            raise OperationError('JOURNAL_INTEGRITY','Evidence inventory differs from capture journal')
        attempts={p['result']['attempt_id']:p['result'] for p in payloads if p['action']=='ATTEMPT_RESERVED'}
        rows=con.execute('SELECT * FROM attempts').fetchall()
        if set(attempts)!={r['id'] for r in rows}:raise OperationError('JOURNAL_INTEGRITY','Attempt inventory changed')
        for r in rows:
            p=attempts[r['id']]
            fields={'team':'team_id','trade_date':'trade_date','plan_sha':'plan_sha256','filename':'filename','ordinal':'local_ordinal','reserved_at':'reserved_at'}
            if any(r[k]!=p[v] for k,v in fields.items()):raise OperationError('JOURNAL_INTEGRITY','Reserved attempt changed')
        accepted={p['result']['receipt_id']:p['result'] for p in payloads
                  if p['action']=='RECEIPT_VERIFY' and p['result']['status']=='VERIFIED_ACCEPTED'}
        rows=con.execute('SELECT * FROM receipts').fetchall()
        if set(accepted)!={r['receipt_id'] for r in rows}:raise OperationError('JOURNAL_INTEGRITY','Accepted receipt inventory changed')
        for r in rows:
            p=accepted[r['receipt_id']]
            fields={'record_id':'record_id','team':'team_id','trade_date':'trade_date','plan_sha':'plan_sha256','received_at':'received_at','outcome':'status','attempt_id':'attempt_id'}
            if any(r[k]!=p[v] for k,v in fields.items()):raise OperationError('JOURNAL_INTEGRITY','Accepted receipt changed')
        return payloads,previous

    def _capture(self,kind,raw,provenance,metadata,adapter):
        raw_sha=self._archive(raw);record_id=uuid.uuid4().hex
        adapter=adapter or {};adapter_sha=self._archive(_json(adapter).encode())
        metadata=dict(metadata,adapter_sha256=adapter_sha)
        value=load_json(self._raw(raw_sha))
        canonical=_canonical(kind,value,adapter.get('fields'))
        if 'holdings_fields' in adapter and kind=='ledger':
            canonical['holdings']=[{k:_pointer(r,p) for k,p in adapter['holdings_fields'].items()} for r in canonical['holdings']]
        for field,mapping in adapter.get('value_maps',{}).items():
            if field not in canonical or str(canonical[field]) not in mapping:
                raise OperationError('UNMAPPED_OFFICIAL_VALUE',field)
            canonical[field]=mapping[str(canonical[field])]
        canonical=_validate_record(kind,canonical)
        now=_clock().isoformat()
        with self._db() as con:
            con.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?)',
                        (record_id,kind,raw_sha,provenance,now,_json(canonical),_json(metadata)))
            self._event(con,dict(action='CAPTURE',record_id=record_id,kind=kind,raw_sha256=raw_sha,
                                 provenance=provenance,acquired_at=now,metadata=metadata,data_sha256=_sha(_json(canonical).encode())))
        return self.record(record_id)

    def import_record(self,kind,raw_path,adapter=None):
        """Copy bytes exactly. A JSON `trusted`/`source_mode` claim has no effect."""
        if adapter is not None and (not isinstance(adapter,dict) or set(adapter)-{'fields','holdings_fields','value_maps'}):
            raise OperationError('ADAPTER_EXTRA_FIELDS','Manual import accepts field mappings only, never credentials')
        return self._capture(kind,Path(raw_path).read_bytes(),MANUAL,
                             dict(imported_filename=Path(raw_path).name,origin_authentication='UNKNOWN'),adapter)

    def adapter_status(self,adapter):
        """Metadata readiness only; READY never means a connection succeeded."""
        blockers=[];kind=adapter.get('kind');spec=adapter.get('spec',{})
        try:
            allowed={'kind','method','url','contract_status','fields','auth','spec','holdings_fields','value_maps'}
            if set(adapter)-allowed or set(adapter.get('auth',{}))-{'type','env_var'}:
                raise OperationError('ADAPTER_EXTRA_FIELDS','Unknown adapter/auth fields could contain credentials')
            if isinstance(spec,dict) and set(spec)-{'file','sha256','source_url','known_at'}:
                raise OperationError('ADAPTER_EXTRA_FIELDS','Unknown specification metadata')
            if kind not in KINDS:raise OperationError('RECORD_KIND',str(kind))
            optional={'attempt_number','client_nonce'} if kind=='receipt' else {'official_warning_count'} if kind=='ledger' else set()
            if set(adapter.get('fields',{}))-(FIELDS[kind]|optional):
                raise OperationError('ADAPTER_EXTRA_FIELDS','Only canonical field mappings are accepted')
            if set(adapter.get('holdings_fields',{}))-{'ticker','shares','market_value'}:
                raise OperationError('ADAPTER_EXTRA_FIELDS','Only ticker/shares/optional market_value holdings mappings are accepted')
            if set(adapter.get('value_maps',{}))-{'status','settlement_status'}:
                raise OperationError('ADAPTER_EXTRA_FIELDS','Only explicit status value maps are accepted')
            _official_url(adapter.get('url'),kind)
            if adapter.get('method')!='GET':raise OperationError('READ_ONLY_ADAPTER','Only GET acquisition is implemented')
            if adapter.get('contract_status')!='OFFICIAL_SCHEMA_REVIEWED':raise OperationError('API_SPEC_UNKNOWN','No reviewed official contract')
            if not FIELDS[kind]<=set(adapter.get('fields',{})):raise OperationError('API_SPEC_UNKNOWN','Missing explicit field mapping')
            if not isinstance(spec,dict) or not all(spec.get(k) for k in ('file','sha256','source_url','known_at')):
                raise OperationError('API_SPEC_UNKNOWN','Archived official specification required')
            _official_url(spec['source_url'],kind);known=_timestamp(spec['known_at'],'spec.known_at')
            if known>_clock():raise OperationError('SPEC_FROM_FUTURE','Specification not yet known')
            path=Path(spec['file'])
            if not path.is_file() or _sha(path.read_bytes())!=spec['sha256']:
                raise OperationError('SPEC_HASH','Specification missing or hash differs')
            auth=adapter.get('auth',{})
            if kind=='calendar' and auth.get('type')=='none':pass
            elif auth.get('type')=='bearer_env' and re.fullmatch(r'[A-Z][A-Z0-9_]{2,100}',str(auth.get('env_var',''))):
                if not os.environ.get(auth['env_var']):raise OperationError('CREDENTIAL_MISSING','Configured credential variable is not set')
            else:raise OperationError('AUTH_SPEC_UNKNOWN','Explicit supported authentication contract required')
        except (OperationError,PreflightError,OSError,ValueError,TypeError) as exc:blockers.append(getattr(exc,'code','ADAPTER_INVALID'))
        return dict(status='BLOCKED' if blockers else 'READY_TO_ATTEMPT_GET',blocking_codes=blockers,
                    connected=False,credential_values_disclosed=False)

    def fetch_record(self,adapter):
        status=self.adapter_status(adapter)
        if status['blocking_codes']:raise OperationError(status['blocking_codes'][0],'Adapter is not ready; no network request made')
        # Never store the token or request headers. Neither a200 nor this fetch
        # makes a pending receipt accepted: its payload status must say so.
        headers={'Accept':'application/json'};auth=adapter['auth']
        if auth['type']=='bearer_env':headers['Authorization']='Bearer '+os.environ[auth['env_var']]
        raw=_get(adapter['url'],headers)
        spec_sha=self._archive(Path(adapter['spec']['file']).read_bytes())
        return self._capture(adapter['kind'],raw,TRUSTED,
                             dict(url=adapter['url'],method='GET',tls_certificate_verified=True,
                                  spec_sha256=spec_sha,origin_authentication='OFFICIAL_HTTPS_TRANSPORT'),adapter)

    def record(self,record_id,con=None):
        if con is None:
            with self._db() as connection:return self.record(record_id,connection)
        row=con.execute('SELECT * FROM records WHERE id=?',(record_id,)).fetchone()
        if row is None:raise OperationError('RECORD_NOT_FOUND',str(record_id))
        result=dict(row);self._raw(result['raw_sha']);result['data']=json.loads(result['data']);result['metadata']=json.loads(result['metadata'])
        for key in ('adapter_sha256','spec_sha256'):
            if key in result['metadata']:self._raw(result['metadata'][key])
        # Match the immutable capture event, so changing DB trust/data alone is
        # caught. Full file-owner forgery remains outside this local boundary.
        captures=[json.loads(x[0]) for x in con.execute('SELECT payload FROM events')]
        matches=[x for x in captures if x.get('action')=='CAPTURE' and x.get('record_id')==record_id]
        expected=dict(kind=result['kind'],raw_sha256=result['raw_sha'],provenance=result['provenance'],
                      acquired_at=result['acquired_at'],metadata=result['metadata'],data_sha256=_sha(_json(result['data']).encode()))
        if len(matches)!=1 or any(matches[0].get(k)!=v for k,v in expected.items()):
            raise OperationError('RECORD_INTEGRITY','Record differs from captured event')
        return result

    def reconcile_ledger(self,record_id,local_state,*,team_id,as_of):
        r=self.record(record_id);actual=r['data'];blockers=[];diffs=[]
        if r['kind']!='ledger':raise OperationError('RECORD_KIND','Expected ledger')
        if actual['team_id']!=_team(team_id) or local_state.get('team_id')!=team_id:blockers.append('LEDGER_TEAM_MISMATCH')
        if actual['as_of']!=as_of or local_state.get('as_of')!=as_of:blockers.append('LEDGER_DATE_MISMATCH')
        _date(as_of,'expected.as_of')
        if actual['settlement_status']!='SETTLED':blockers.append('LEDGER_NOT_SETTLED')
        if _timestamp(actual['settled_at'],'settled_at')>_timestamp(r['acquired_at'],'acquired_at'):blockers.append('LEDGER_FUTURE_SETTLEMENT')
        if r['provenance']!=TRUSTED:blockers.append('OFFICIAL_LEDGER_ORIGIN_UNVERIFIED')
        if decimal(actual['cash'])<0 or decimal(actual['nav'])<=0 or decimal(actual['dividend_receivable'])<0:
            blockers.append('OFFICIAL_LEDGER_FINANCIAL_INVALID')
        left,right=_positions(local_state.get('holdings')),_positions(actual['holdings'])
        for ticker in sorted(set(left)|set(right)):
            if left.get(ticker,Decimal(0))!=right.get(ticker,Decimal(0)):
                diffs.append(dict(field='shares',ticker=ticker,local=str(left.get(ticker,0)),official=str(right.get(ticker,0))))
        for field in ('cash','nav','dividend_receivable'):
            if field not in local_state:blockers.append('LOCAL_LEDGER_FIELD_MISSING');continue
            if abs(decimal(local_state[field])-decimal(actual[field]))>Decimal('.01'):
                diffs.append(dict(field=field,local=str(local_state[field]),official=actual[field]))
        if diffs:blockers.append('LEDGER_RECONCILIATION_MISMATCH')
        result=dict(status='BLOCKED' if blockers else 'TRUSTED_RECONCILED',blocking_codes=sorted(set(blockers)),
                    differences=diffs,record_id=record_id,raw_sha256=r['raw_sha'],provenance=r['provenance'],
                    unit_policy='ACTUAL_SHARES_PRESERVED_NO_ROUNDING',money_tolerance='0.01',
                    local_state_sha256=_sha(_json(local_state).encode()),official_ledger=actual)
        with self._db() as con:self._event(con,dict(action='RECONCILE',result=result))
        return result

    def _calendar(self,record_id,con):
        if record_id is None:raise OperationError('CALENDAR_UNKNOWN','Trusted official session record required')
        r=self.record(record_id,con)
        if r['kind']!='calendar' or r['provenance']!=TRUSTED:
            raise OperationError('CALENDAR_UNVERIFIED','Manually imported calendar is not authenticated')
        if _timestamp(r['data']['published_at'],'published_at')>_timestamp(r['acquired_at'],'acquired_at'):
            raise OperationError('CALENDAR_FROM_FUTURE','Publication after acquisition')
        sessions=[d for d in r['data']['sessions'] if CONTEST_START<=_date(d,'session')<=CONTEST_END]
        if len(sessions)!=DECLARED_DAYS:raise OperationError('CONTEST_CALENDAR_CONFLICT','Expected24 confirmed sessions; changed schedule needs reviewed policy')
        return sessions

    def _plan(self,path,team_id,trade_date):
        raw=Path(path).read_bytes();plan=load_json(path)
        errors=list(Draft202012Validator(load_json(SCHEMA),format_checker=FormatChecker()).iter_errors(plan))
        if errors:raise OperationError('PLAN_SCHEMA','D-Plan does not match supplied official schema')
        if plan['team_id']!=_team(team_id) or plan['trade_date']!=trade_date:
            raise OperationError('PLAN_IDENTITY','Plan does not match expected team/date')
        _date(trade_date,'trade_date');filename=f'D-Plan_{team_id}_{trade_date}.json'
        if Path(path).name!=filename:raise OperationError('PLAN_FILENAME','Wrong official plan filename')
        return plan,raw,filename

    def verify_receipt(self,record_id,plan_path,*,team_id,trade_date,calendar_record_id,attempt_id=None):
        plan,raw,filename=self._plan(plan_path,team_id,trade_date);sha=_sha(raw)
        with self._db() as con:
            r=self.record(record_id,con);data=r['data'];blockers=[]
            if r['kind']!='receipt':raise OperationError('RECORD_KIND','Expected receipt')
            sessions=self._calendar(calendar_record_id,con)
            if trade_date not in sessions:blockers.append('NOT_OFFICIAL_SESSION')
            if r['provenance']!=TRUSTED:blockers.append('MANUAL_RECEIPT_UNVERIFIED')
            for field,expected in [('team_id',team_id),('trade_date',trade_date),('filename',filename),('plan_sha256',sha)]:
                if data[field]!=expected:blockers.append('RECEIPT_'+field.upper()+'_MISMATCH')
            trade=_date(trade_date,'trade_date');received=_timestamp(data['received_at'],'received_at')
            pdf_start=datetime.combine(trade-timedelta(days=1),time(19,30),TZ)
            deadline=datetime.combine(trade,time(8,55),TZ)
            if not pdf_start<=received<=deadline:blockers.append('RECEIPT_OUTSIDE_OFFICIAL_WINDOW')
            if received>_timestamp(r['acquired_at'],'acquired_at'):blockers.append('RECEIPT_TIME_FROM_FUTURE')
            if _timestamp(plan['agent_metadata']['run_completed_at'],'run_completed_at')>received:blockers.append('PLAN_COMPLETED_AFTER_RECEIPT')
            if data['status']!='ACCEPTED':blockers.append('SERVER_NOT_ACCEPTED')
            if con.execute('SELECT 1 FROM receipts WHERE receipt_id=? OR record_id=?',(data['receipt_id'],record_id)).fetchone():
                blockers.append('RECEIPT_REPLAY')
            if attempt_id is not None:
                attempt=con.execute('SELECT * FROM attempts WHERE id=?',(attempt_id,)).fetchone()
                if attempt is None or any(attempt[k]!=v for k,v in [('team',team_id),('trade_date',trade_date),('plan_sha',sha),('filename',filename)]):
                    blockers.append('ATTEMPT_BINDING_MISMATCH')
                elif received<_timestamp(attempt['reserved_at'],'reserved_at'):blockers.append('RECEIPT_BEFORE_ATTEMPT')
                if con.execute('SELECT 1 FROM receipts WHERE attempt_id=?',(attempt_id,)).fetchone():blockers.append('ATTEMPT_RECEIPT_REPLAY')
                if data.get('client_nonce',attempt_id)!=attempt_id:blockers.append('RECEIPT_NONCE_MISMATCH')
            self._archive(raw)
            result=dict(status='BLOCKED' if blockers else 'VERIFIED_ACCEPTED',blocking_codes=sorted(set(blockers)),
                        record_id=record_id,receipt_id=data['receipt_id'],team_id=team_id,trade_date=trade_date,
                        plan_sha256=sha,received_at=data['received_at'],provenance=r['provenance'],
                        receipt_is_settlement=False,attempt_id=attempt_id)
            if not blockers:
                con.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?,?,?)',
                            (data['receipt_id'],record_id,team_id,trade_date,sha,data['received_at'],'VERIFIED_ACCEPTED',attempt_id))
            self._event(con,dict(action='RECEIPT_VERIFY',result=result))
            return result

    def _daily(self,con,team_id,trade_date,calendar_record_id,quota_record_id=None):
        _team(team_id);trade=_date(trade_date,'trade_date');now=_clock();blockers=[]
        sessions=self._calendar(calendar_record_id,con)
        if any(_timestamp(json.loads(r[0])['at'],'journal.at')>now for r in con.execute('SELECT payload FROM events')):
            blockers.append('CLOCK_ROLLBACK')
        if trade_date not in sessions:blockers.append('NOT_OFFICIAL_SESSION')
        start=datetime.combine(trade,time(5),TZ);deadline=datetime.combine(trade,time(8,55),TZ)
        window='BEFORE_LOCAL_WINDOW' if now<start else 'AFTER_DEADLINE' if now>deadline else 'LOCAL_WINDOW_OPEN'
        rows=con.execute('SELECT * FROM attempts WHERE team=? AND trade_date=?',(team_id,trade_date)).fetchall()
        local_count=len(rows);used=None
        if quota_record_id is None:blockers.append('OFFICIAL_ATTEMPT_COUNT_UNKNOWN')
        else:
            q=self.record(quota_record_id,con);d=q['data']
            if q['kind']!='quota' or q['provenance']!=TRUSTED:blockers.append('OFFICIAL_ATTEMPT_COUNT_UNVERIFIED')
            elif d['team_id']!=team_id or d['trade_date']!=trade_date:blockers.append('QUOTA_IDENTITY_MISMATCH')
            else:
                stamp=_timestamp(d['as_of'],'quota.as_of');acquired=_timestamp(q['acquired_at'],'acquired_at')
                if stamp>acquired or now<acquired or (now-stamp).total_seconds()>60:blockers.append('QUOTA_STALE_OR_FUTURE')
                # Never release a reservation: failed/timeout requests may have
                # reached the server. Count all post-snapshot local reservations.
                after=sum(_timestamp(a['reserved_at'],'reserved_at')>=stamp for a in rows)
                used=max(local_count,d['attempts_used']+after)
                if used>=DAILY_LIMIT:blockers.append('DAILY_ATTEMPT_LIMIT')
        if local_count>=DAILY_LIMIT:blockers.append('DAILY_ATTEMPT_LIMIT')
        receipts=con.execute('SELECT record_id,trade_date,plan_sha,received_at FROM receipts WHERE team=?',(team_id,)).fetchall()
        for receipt in receipts:
            self._raw(receipt['plan_sha']);self.record(receipt['record_id'],con)
        successful=sorted({r['trade_date'] for r in receipts if r['trade_date'] in sessions and _timestamp(r['received_at'],'received_at')<=now})
        remaining=[d for d in sessions if datetime.combine(_date(d,'session'),time(8,55),TZ)>=now and d not in successful]
        passed=[d for d in sessions if datetime.combine(_date(d,'session'),time(8,55),TZ)<now]
        missed=[d for d in passed if d not in successful]
        if window!='LOCAL_WINDOW_OPEN':blockers.append(window)
        return dict(team_id=team_id,trade_date=trade_date,checked_at=now.isoformat(),local_window=window,
                    official_window_start=datetime.combine(trade-timedelta(days=1),time(19,30),TZ).isoformat(),
                    deadline=deadline.isoformat(),official_window_conflict='PDF19:30_vs_SCHEMA05:00_LOCAL_USES05:00',
                    local_reservations=local_count,conservative_attempts_used=used,
                    attempts_remaining=None if used is None else max(0,DAILY_LIMIT-used),
                    successful_dates=successful,successful_day_count=len(successful),required_successful_days=REQUIRED_DAYS,
                    total_official_sessions=len(sessions),missed_dates=missed,
                    eligibility='MINIMUM_REACHED' if len(successful)>=REQUIRED_DAYS else 'NO_LONGER_REACHABLE' if len(successful)+len(remaining)<REQUIRED_DAYS else 'NOT_YET_REACHED',
                    blocking_codes=sorted(set(blockers)),can_reserve=not blockers,
                    submission_implemented=False,remaining_count_scope='LOCAL_RESERVATIONS_PLUS_RECENT_SERVER_SNAPSHOT_NOT_GLOBAL_ATOMIC_QUOTA')

    def daily_status(self,team_id,trade_date,*,calendar_record_id,quota_record_id=None):
        with self._db() as con:return self._daily(con,team_id,trade_date,calendar_record_id,quota_record_id)

    def accepted_plan(self,team_id,trade_date,plan_sha256):
        """Read-only exact accepted-byte lookup; not settlement/compliance proof.

        Missing records fail closed. An earlier acceptance of different bytes
        does not unlock a later phase. This method does not append an event.
        """
        _team(team_id);_date(trade_date,'trade_date')
        if not isinstance(plan_sha256,str) or not HASH.fullmatch(plan_sha256):
            raise OperationError('PLAN_HASH','Use the bare64-character SHA256 of exact submitted bytes')
        base=dict(team_id=team_id,trade_date=trade_date,plan_sha256=plan_sha256,
                  version_scope='LATEST_LOCALLY_VERIFIED_RECEIPTS_ONLY',official_effective_version='UNKNOWN')
        with self._db() as con:
            rows=con.execute('SELECT * FROM receipts WHERE team=? AND trade_date=?',(team_id,trade_date)).fetchall()
            records={}
            for r in rows:
                record=self.record(r['record_id'],con);self._raw(r['plan_sha']);records[r['record_id']]=record
                if _timestamp(record['acquired_at'],'acquired_at')>_clock():
                    return dict(status='UNKNOWN',blocking_codes=['CLOCK_ROLLBACK'],**base)
                if record['provenance']!=TRUSTED or record['data']['status']!='ACCEPTED':
                    raise OperationError('RECEIPT_INTEGRITY','Accepted receipt no longer matches trusted record')
            if not rows or not any(r['plan_sha']==plan_sha256 for r in rows):
                return dict(status='UNKNOWN',blocking_codes=['EXACT_ACCEPTED_PLAN_NOT_FOUND'],**base)
            latest=max(_timestamp(r['received_at'],'received_at') for r in rows)
            newest=[r for r in rows if _timestamp(r['received_at'],'received_at')==latest]
            if len({r['plan_sha'] for r in newest})!=1:
                return dict(status='UNKNOWN',blocking_codes=['AMBIGUOUS_LATEST_ACCEPTED_PLAN'],**base)
            r=newest[-1]
            if r['plan_sha']!=plan_sha256:
                return dict(status='UNKNOWN',blocking_codes=['ACCEPTED_PLAN_SUPERSEDED'],**base)
            record=records[r['record_id']]
            return dict(status='VERIFIED_ACCEPTED',blocking_codes=[],team_id=team_id,trade_date=trade_date,
                        plan_sha256=plan_sha256,receipt_id=r['receipt_id'],record_id=r['record_id'],
                        received_at=r['received_at'],provenance=record['provenance'],record=record,
                        receipt_is_settlement=False,receipt_is_compliance=False,
                        version_scope='LATEST_LOCALLY_VERIFIED_RECEIPTS_ONLY',official_effective_version='UNKNOWN')

    def reserve_attempt(self,plan_path,*,team_id,trade_date,calendar_record_id,quota_record_id):
        """Reserve one LOCAL attempt before a separately authorized sender.

        This is not submission permission: separate validation/AS/ledger gates
        must also pass. A crash/timeout consumes its reservation; no retry loop.
        """
        plan,raw,filename=self._plan(plan_path,team_id,trade_date)
        if _timestamp(plan['agent_metadata']['run_completed_at'],'run_completed_at')>_clock():
            raise OperationError('PLAN_FROM_FUTURE','Plan is not yet completed')
        sha=self._archive(raw)
        with self._db() as con:
            status=self._daily(con,team_id,trade_date,calendar_record_id,quota_record_id)
            if status['blocking_codes']:raise OperationError(status['blocking_codes'][0],'Local operation reservation blocked')
            if con.execute('SELECT 1 FROM attempts WHERE team=? AND trade_date=? AND plan_sha=?',(team_id,trade_date,sha)).fetchone():
                raise OperationError('PLAN_REPLAY','Exact same bytes already reserved; reconcile the first attempt instead of blindly retrying')
            attempt_id=uuid.uuid4().hex;ordinal=status['local_reservations']+1
            reserved_at=_clock().isoformat()
            con.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?)',(attempt_id,team_id,trade_date,sha,filename,reserved_at,ordinal))
            result=dict(status='LOCAL_RESERVED_NOT_SUBMITTED',attempt_id=attempt_id,team_id=team_id,
                        trade_date=trade_date,plan_sha256=sha,filename=filename,local_ordinal=ordinal,
                        reserved_at=reserved_at,submission_permission=False)
            self._event(con,dict(action='ATTEMPT_RESERVED',result=result))
            return result

    def audit(self):
        """Detect archive/event/record changes. Not a hostile-owner signature."""
        with self._db() as con:
            previous='0'*64;count=0
            for row in con.execute('SELECT * FROM events ORDER BY seq'):
                if row['seq']!=count+1 or row['previous_hash']!=previous or _sha((previous+'\n'+row['payload']).encode())!=row['event_hash']:
                    raise OperationError('JOURNAL_INTEGRITY','Event chain changed or incomplete')
                previous=row['event_hash'];count+=1
            records=[r[0] for r in con.execute('SELECT id FROM records')]
            for record_id in records:self.record(record_id,con)
            return dict(status='PASS_LOCAL_INTEGRITY',event_count=count,record_count=len(records),head_sha256=previous,
                        authentication_scope='HTTPS_ACQUISITION_OR_MANUAL_LABEL_NOT_OFFICIAL_DIGITAL_SIGNATURE')
