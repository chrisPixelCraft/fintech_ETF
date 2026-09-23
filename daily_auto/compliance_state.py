"""Recompute passive-cap ages from authenticated settled books, never user ages.

Conservative price-only inference requires unchanged actual shares AND cash across
consecutive official sessions. A transaction/event ambiguity is not granted a
passive grace period. Official warnings are never inferred from local failures.
"""
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from daily_auto.operations import OperationsStore, TRUSTED, OperationError
from daily_auto.validate import TZ, decimal, _timestamp


class HistoryUnknown(ValueError):
    pass


def _book(data):
    cash=decimal(data['cash']);nav=decimal(data['nav'])
    if cash<0 or nav<=0:raise HistoryUnknown('INVALID_OFFICIAL_BOOK')
    quantities={};values={}
    for row in data['holdings']:
        ticker=row['ticker']
        if ticker in quantities:raise HistoryUnknown('DUPLICATE_OFFICIAL_HOLDING')
        quantities[ticker]=decimal(row['shares'])
        if 'market_value' not in row:raise HistoryUnknown('OFFICIAL_MARKET_VALUES_MISSING')
        values[ticker]=decimal(row['market_value'])
        if quantities[ticker]<=0 or values[ticker]<=0:raise HistoryUnknown('INVALID_OFFICIAL_VALUATION')
    if abs(cash+sum(values.values(),Decimal(0))-nav)>Decimal('.01'):
        raise HistoryUnknown('OFFICIAL_VALUATION_NAV_MISMATCH')
    return cash,quantities,{t:v/nav for t,v in values.items()}


def _derive_ages(books,sessions,as_of):
    """Pure arithmetic seam; caller must verify transport, identity and dates."""
    if as_of not in sessions:raise HistoryUnknown('ASOF_NOT_OFFICIAL_SESSION')
    index={d:i for i,d in enumerate(sessions)}
    ages={};previous=None;details=[]
    for date in sorted(d for d in books if d<=as_of and d in index):
        cash,quantities,weights=_book(books[date]);next_ages={}
        contiguous=previous is not None and index[date]==index[previous[0]]+1
        price_only=contiguous and cash==previous[1] and quantities==previous[2]
        for ticker,weight in weights.items():
            cap=Decimal('.25') if ticker=='2330' else Decimal('.10')
            if weight<=cap:next_ages[ticker]=0
            elif price_only and ages.get(ticker) is not None:
                next_ages[ticker]=ages[ticker]+1
            else:next_ages[ticker]=None
        ages=next_ages;previous=(date,cash,quantities)
        details.append(dict(as_of=date,price_only_transition=bool(price_only),ages=ages.copy()))
    if not previous or previous[0]!=as_of:raise HistoryUnknown('CURRENT_SETTLED_BOOK_MISSING')
    unresolved=[t for t,n in ages.items() if n is None]
    return ages,unresolved,details


def resolve_passive_cap_days(*,state,operations_root=None,ledger_id=None,calendar_record_id=None):
    """Return (trusted ages, report, archived evidence paths) without networking.

    Missing records/official valuation fields produce UNKNOWN, never age zero.
    The CLI must remove hand-entered ages before applying a trusted result.
    """
    report=dict(status='UNKNOWN',blocking_codes=[],official_warning_count=None,
                official_warning_status='UNKNOWN',warning_disqualification=None,
                policy='CONSECUTIVE_OFFICIAL_SESSIONS_CONSERVATIVE_PRICE_ONLY_INFERENCE',
                official_cause_certification='UNKNOWN',
                grace_days=5,first_overdue_day=6,overdue_tickers=[])
    paths=[]
    try:
        if not all([operations_root,ledger_id,calendar_record_id]):
            raise HistoryUnknown('OFFICIAL_CAP_HISTORY_INPUTS_MISSING')
        store=OperationsStore(operations_root);now=datetime.now(TZ)
        current=store.record(ledger_id);calendar=store.record(calendar_record_id)
        if current['kind']!='ledger' or current['provenance']!=TRUSTED:
            raise HistoryUnknown('OFFICIAL_LEDGER_ORIGIN_UNVERIFIED')
        data=current['data'];team=state['team_id'];asof=state['as_of']
        if data['team_id']!=team or data['as_of']!=asof or data['settlement_status']!='SETTLED':
            raise HistoryUnknown('CURRENT_LEDGER_IDENTITY_OR_SETTLEMENT')
        acquired=_timestamp(current['acquired_at'],'acquired_at')
        if acquired>now or _timestamp(data['settled_at'],'settled_at')>acquired:
            raise HistoryUnknown('CURRENT_LEDGER_TIME_INVALID')
        if calendar['kind']!='calendar' or calendar['provenance']!=TRUSTED:
            raise HistoryUnknown('OFFICIAL_CALENDAR_UNVERIFIED')
        calendar_acquired=_timestamp(calendar['acquired_at'],'acquired_at')
        if calendar_acquired>now or _timestamp(calendar['data']['published_at'],'published_at')>calendar_acquired:
            raise HistoryUnknown('OFFICIAL_CALENDAR_PUBLICATION_TIME')
        sessions=calendar['data']['sessions']
        if sessions!=sorted(set(sessions)):raise HistoryUnknown('OFFICIAL_CALENDAR_ORDER')
        warning=data.get('official_warning_count')
        if warning is not None:
            if isinstance(warning,bool) or not isinstance(warning,int) or warning<0:
                raise HistoryUnknown('OFFICIAL_WARNING_COUNT_INVALID')
            report.update(official_warning_count=warning,official_warning_status='OFFICIAL_REPORTED',
                          warning_disqualification=warning>=3)
        if any(decimal(data[k])!=decimal(state[k]) for k in ['cash','nav','dividend_receivable']):
            raise HistoryUnknown('CURRENT_LEDGER_DIFFERS_FROM_STATE')
        q=lambda rows:{r['ticker']:decimal(r['shares']) for r in rows}
        if q(data['holdings'])!=q(state['holdings']):raise HistoryUnknown('CURRENT_HOLDINGS_DIFFER')
        with store._db() as con:
            ids=[row[0] for row in con.execute("SELECT id FROM records WHERE kind='ledger'")]
        books={};record_ids=[]
        for rid in ids:
            record=store.record(rid);d=record['data']
            if record['provenance']!=TRUSTED or d['team_id']!=team or d['as_of']>asof or d['settlement_status']!='SETTLED':continue
            acquired=_timestamp(record['acquired_at'],'acquired_at')
            if acquired>now or _timestamp(d['settled_at'],'settled_at')>acquired:
                raise HistoryUnknown('OFFICIAL_HISTORY_TIME_INVALID')
            if d['as_of'] in books and books[d['as_of']]!=d:
                raise HistoryUnknown('AMBIGUOUS_OFFICIAL_SETTLEMENT_REVISION')
            books[d['as_of']]=d;record_ids.append(rid);paths.append(store._raw(record['raw_sha']))
        paths.extend([store.db,store._raw(calendar['raw_sha'])])
        ages,unresolved,details=_derive_ages(books,sessions,asof)
        report.update(ledger_record_ids=record_ids,calendar_record_id=calendar_record_id,
                      daily_history=details,unresolved_tickers=unresolved,
                      overdue_tickers=[t for t,n in ages.items() if n is not None and n>=6])
        if unresolved:raise HistoryUnknown('PASSIVE_CAUSE_OR_CONSECUTIVE_HISTORY_UNKNOWN')
        report['status']='TRUSTED_RESTORED'
        return ages,report,tuple(dict.fromkeys(paths))
    except (HistoryUnknown,OperationError,KeyError,TypeError,ValueError,OSError) as exc:
        report['blocking_codes']=[getattr(exc,'code',str(exc))]
        return {},report,tuple(dict.fromkeys(paths))
