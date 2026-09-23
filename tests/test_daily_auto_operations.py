"""Synthetic transport/ledger fixtures only; no live credentials/network calls."""
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from daily_auto.operations import OperationsStore, OperationError, FIELDS, TRUSTED, MANUAL
from daily_auto.validate import ROOT, TZ

NOW=datetime(2026,10,27,8,0,tzinfo=TZ)
TEAM='TEAM_042';DAY='2026-10-27'


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=OperationsStore(self.root/'store')
        self.clock=patch('daily_auto.operations._clock',return_value=NOW);self.clock.start();self.addCleanup(self.clock.stop)
        self.env=patch.dict(os.environ,{'UNIT_TEST_ESUN_CREDENTIAL':'TEST_ONLY_NOT_A_REAL_SECRET'});self.env.start();self.addCleanup(self.env.stop)
        self.spec=self.root/'synthetic-spec.txt';self.spec.write_text('SYNTHETIC UNIT TEST CONTRACT ONLY')
        self.plan=json.loads((ROOT/'official_docs/D-Plan_TEAM_042_2026-10-27.json').read_text())
        self.plan['agent_metadata']['run_completed_at']='2026-10-27T07:59:00+08:00'
        self.path=self.root/'D-Plan_TEAM_042_2026-10-27.json';self.save_plan()
        start=date(2026,10,27);days=[(start+timedelta(days=i)).isoformat() for i in range(32) if (start+timedelta(days=i)).weekday()<5]
        self.calendar=self.fetch('calendar',dict(sessions=days,published_at='2026-09-01T00:00:00+08:00'))['id']

    def save_plan(self):self.path.write_text(json.dumps(self.plan,ensure_ascii=False))

    def adapter(self,kind):
        return dict(kind=kind,method='GET',url=f'https://esun-ai-challenge.tw/UNIT_TEST_ONLY/{kind}',
                    contract_status='OFFICIAL_SCHEMA_REVIEWED',fields={k:'/'+k for k in FIELDS[kind]},
                    auth={'type':'bearer_env','env_var':'UNIT_TEST_ESUN_CREDENTIAL'},
                    spec=dict(file=str(self.spec),sha256=hashlib.sha256(self.spec.read_bytes()).hexdigest(),
                              source_url='https://esun-ai-challenge.tw/UNIT_TEST_SPEC',known_at='2026-09-01T00:00:00+08:00'))

    def fetch(self,kind,value):
        with patch('daily_auto.operations._get',return_value=json.dumps(value).encode()) as request:
            result=self.store.fetch_record(self.adapter(kind));self.assertEqual(request.call_count,1)
            return result

    def import_record(self,kind,value):
        p=self.root/'import.json';p.write_text(json.dumps(value));return self.store.import_record(kind,p)

    def receipt(self,**changes):
        value=dict(receipt_id='official-test-receipt',team_id=TEAM,trade_date=DAY,filename=self.path.name,
                   plan_sha256=hashlib.sha256(self.path.read_bytes()).hexdigest(),status='ACCEPTED',
                   received_at=NOW.isoformat())
        value.update(changes);return value

    def verify(self,record,**kw):
        return self.store.verify_receipt(record['id'],self.path,team_id=TEAM,trade_date=DAY,
                                         calendar_record_id=self.calendar,**kw)

    def quota(self,used=0,**changes):
        value=dict(team_id=TEAM,trade_date=DAY,as_of=(NOW-timedelta(seconds=10)).isoformat(),attempts_used=used)
        value.update(changes);return self.fetch('quota',value)['id']

    def reserve(self,quota):
        return self.store.reserve_attempt(self.path,team_id=TEAM,trade_date=DAY,
                                          calendar_record_id=self.calendar,quota_record_id=quota)

    def test_missing_spec_or_credential_does_not_call_network(self):
        cfg=self.adapter('ledger');cfg['contract_status']='UNKNOWN'
        with patch('daily_auto.operations._get') as request,self.assertRaises(OperationError) as cm:self.store.fetch_record(cfg)
        self.assertEqual(cm.exception.code,'API_SPEC_UNKNOWN');request.assert_not_called()
        cfg=self.adapter('ledger');cfg['auth']['env_var']='DOES_NOT_EXIST_REAL_TOKEN'
        with patch('daily_auto.operations._get') as request,self.assertRaises(OperationError) as cm:self.store.fetch_record(cfg)
        self.assertEqual(cm.exception.code,'CREDENTIAL_MISSING');request.assert_not_called()

    def test_adapter_cannot_post_leak_token_in_url_or_redirect_official_domain(self):
        for change,code in [({'method':'POST'},'READ_ONLY_ADAPTER'),
                            ({'url':'https://esun-ai-challenge.tw.evil.invalid/x'},'ENDPOINT_ORIGIN'),
                            ({'url':'https://esun-ai-challenge.tw/x?token=secret'},'SECRET_IN_URL')]:
            cfg=self.adapter('receipt');cfg.update(change)
            self.assertIn(code,self.store.adapter_status(cfg)['blocking_codes'])

    def test_manual_receipt_is_not_trusted_even_when_it_claims_to_be(self):
        value=self.receipt();value['provenance']=TRUSTED;value['trusted']=True
        record=self.import_record('receipt',value);self.assertEqual(record['provenance'],MANUAL)
        result=self.verify(record);self.assertIn('MANUAL_RECEIPT_UNVERIFIED',result['blocking_codes'])
        self.assertEqual(self.store.daily_status(TEAM,DAY,calendar_record_id=self.calendar)['successful_day_count'],0)
        self.assertEqual((self.store.raw_dir/(record['raw_sha']+'.bin')).read_bytes(),json.dumps(value).encode())

    def test_exact_bound_receipt_counts_once_and_replay_rejected(self):
        record=self.fetch('receipt',self.receipt());result=self.verify(record)
        self.assertEqual(result['status'],'VERIFIED_ACCEPTED');self.assertFalse(result['receipt_is_settlement'])
        again=self.verify(record);self.assertIn('RECEIPT_REPLAY',again['blocking_codes'])
        another=self.fetch('receipt',self.receipt());self.assertIn('RECEIPT_REPLAY',self.verify(another)['blocking_codes'])
        self.assertEqual(self.store.daily_status(TEAM,DAY,calendar_record_id=self.calendar)['successful_day_count'],1)

    def test_wrong_bytes_team_date_filename_and_late_receipt_block(self):
        for change,code in [({'plan_sha256':'f'*64},'RECEIPT_PLAN_SHA256_MISMATCH'),
                            ({'team_id':'OTHER'},'RECEIPT_TEAM_ID_MISMATCH'),
                            ({'trade_date':'2026-10-28'},'RECEIPT_TRADE_DATE_MISMATCH'),
                            ({'filename':'other.json'},'RECEIPT_FILENAME_MISMATCH'),
                            ({'received_at':'2026-10-27T08:55:00.000001+08:00'},'RECEIPT_OUTSIDE_OFFICIAL_WINDOW')]:
            with self.subTest(change=change):
                self.assertIn(code,self.verify(self.fetch('receipt',self.receipt(**change)))['blocking_codes'])

    def test_schema_hash_binding_rejects_any_post_submission_edit(self):
        record=self.fetch('receipt',self.receipt());self.plan['market_view']['logic']+=' another character';self.save_plan()
        self.assertIn('RECEIPT_PLAN_SHA256_MISMATCH',self.verify(record)['blocking_codes'])

    def test_http_success_is_not_accepted_and_missing_hash_not_guessed(self):
        for status in ['PENDING','REJECTED']:
            self.assertIn('SERVER_NOT_ACCEPTED',self.verify(self.fetch('receipt',self.receipt(status=status)))['blocking_codes'])
        value=self.receipt();value.pop('plan_sha256')
        with self.assertRaises(OperationError) as cm:self.fetch('receipt',value)
        self.assertEqual(cm.exception.code,'MISSING_OFFICIAL_FIELD')

    def test_exact_ledger_reconciliation_odd_and_fractional_units_preserved(self):
        ledger=dict(ledger_id='settlement-1',team_id=TEAM,as_of='2026-10-23',settled_at='2026-10-23T19:00:00+08:00',
                    settlement_status='SETTLED',cash='120000',nav='300000',dividend_receivable='1234.50',
                    holdings=[dict(ticker='2330',shares='1500.5')])
        local={k:deepcopy(ledger[k]) for k in ('team_id','as_of','cash','nav','dividend_receivable','holdings')}
        trusted=self.fetch('ledger',ledger)
        result=self.store.reconcile_ledger(trusted['id'],local,team_id=TEAM,as_of='2026-10-23')
        self.assertEqual(result['status'],'TRUSTED_RECONCILED');self.assertEqual(result['official_ledger']['holdings'][0]['shares'],'1500.5')
        manual=self.import_record('ledger',ledger)
        self.assertIn('OFFICIAL_LEDGER_ORIGIN_UNVERIFIED',self.store.reconcile_ledger(manual['id'],local,team_id=TEAM,as_of='2026-10-23')['blocking_codes'])
        local['holdings'][0]['shares']='2000';local['cash']='999999'
        result=self.store.reconcile_ledger(trusted['id'],local,team_id=TEAM,as_of='2026-10-23')
        self.assertEqual({r['field'] for r in result['differences']},{'shares','cash'})
        self.assertEqual(trusted['data']['holdings'][0]['shares'],'1500.5')

    def test_quota_requires_recent_server_evidence_and_reservations_are_not_sends(self):
        status=self.store.daily_status(TEAM,DAY,calendar_record_id=self.calendar)
        self.assertIn('OFFICIAL_ATTEMPT_COUNT_UNKNOWN',status['blocking_codes']);self.assertIsNone(status['attempts_remaining'])
        q=self.quota(as_of='2026-10-27T07:58:00+08:00')
        with self.assertRaises(OperationError) as cm:self.reserve(q)
        self.assertEqual(cm.exception.code,'QUOTA_STALE_OR_FUTURE')
        q=self.quota();result=self.reserve(q)
        self.assertEqual(result['status'],'LOCAL_RESERVED_NOT_SUBMITTED');self.assertFalse(result['submission_permission'])
        with self.assertRaises(OperationError) as cm:self.reserve(q)
        self.assertEqual(cm.exception.code,'PLAN_REPLAY')

    def test_25_limit_transactionally_counts_timeouts_and_replacements(self):
        q=self.quota()
        for i in range(25):
            self.plan['agent_metadata']['code_version']=f'test{i}';self.save_plan();self.reserve(q)
        self.plan['agent_metadata']['code_version']='test26';self.save_plan()
        with self.assertRaises(OperationError) as cm:self.reserve(q)
        self.assertEqual(cm.exception.code,'DAILY_ATTEMPT_LIMIT')
        status=self.store.daily_status(TEAM,DAY,calendar_record_id=self.calendar,quota_record_id=q)
        self.assertEqual(status['local_reservations'],25);self.assertEqual(status['attempts_remaining'],0)
        self.assertEqual(status['successful_day_count'],0)

    def test_remote24_plus_one_local_reservation_exhausts_budget(self):
        q=self.quota(24);self.reserve(q);self.plan['agent_metadata']['code_version']='changed';self.save_plan()
        with self.assertRaises(OperationError) as cm:self.reserve(q)
        self.assertEqual(cm.exception.code,'DAILY_ATTEMPT_LIMIT')

    def test_window_uses_taipei_and_holiday_not_weekday_guess(self):
        q=self.quota()
        with patch('daily_auto.operations._clock',return_value=NOW.replace(hour=9)),self.assertRaises(OperationError):self.reserve(q)
        status=self.store.daily_status(TEAM,'2026-10-26',calendar_record_id=self.calendar)
        self.assertIn('NOT_OFFICIAL_SESSION',status['blocking_codes'])
        with patch('daily_auto.operations._clock',return_value=datetime(2026,11,28,9,tzinfo=TZ)):
            status=self.store.daily_status(TEAM,'2026-11-27',calendar_record_id=self.calendar)
            self.assertEqual(status['eligibility'],'NO_LONGER_REACHABLE');self.assertEqual(len(status['missed_dates']),24)

    def test_22_successful_days_are_distinct_trade_dates_not_receipt_count(self):
        data=self.store.record(self.calendar)['data'];days=data['sessions'][:22]
        for day in days:
            stamp=datetime.fromisoformat(day+'T08:00:00+08:00')
            with patch('daily_auto.operations._clock',return_value=stamp):
                self.plan['trade_date']=day;self.plan['agent_metadata']['run_completed_at']=day+'T07:59:00+08:00'
                self.path=self.root/f'D-Plan_{TEAM}_{day}.json';self.save_plan()
                record=self.fetch('receipt',self.receipt(receipt_id='r'+day,trade_date=day,received_at=stamp.isoformat()))
                result=self.store.verify_receipt(record['id'],self.path,team_id=TEAM,trade_date=day,calendar_record_id=self.calendar)
                self.assertEqual(result['status'],'VERIFIED_ACCEPTED')
        with patch('daily_auto.operations._clock',return_value=stamp):
            status=self.store.daily_status(TEAM,days[-1],calendar_record_id=self.calendar)
            self.assertEqual(status['successful_day_count'],22);self.assertEqual(status['eligibility'],'MINIMUM_REACHED')
        status=self.store.daily_status(TEAM,DAY,calendar_record_id=self.calendar)
        self.assertIn('CLOCK_ROLLBACK',status['blocking_codes']);self.assertEqual(status['successful_day_count'],1)

    def test_inline_secret_adapter_field_is_rejected_before_request_or_archive(self):
        for place in ['root','auth']:
            cfg=self.adapter('receipt')
            (cfg if place=='root' else cfg['auth'])['token']='NEVER_ARCHIVE_ME'
            with patch('daily_auto.operations._get') as request,self.assertRaises(OperationError) as cm:self.store.fetch_record(cfg)
            self.assertEqual(cm.exception.code,'ADAPTER_EXTRA_FIELDS');request.assert_not_called()
            for path in self.store.raw_dir.iterdir():self.assertNotIn(b'NEVER_ARCHIVE_ME',path.read_bytes())

    def test_daily_status_rechecks_accepted_receipt_raw_evidence(self):
        record=self.fetch('receipt',self.receipt());self.verify(record)
        (self.store.raw_dir/(record['raw_sha']+'.bin')).write_bytes(b'changed')
        with self.assertRaises(OperationError) as cm:self.store.daily_status(TEAM,DAY,calendar_record_id=self.calendar)
        self.assertEqual(cm.exception.code,'RAW_INTEGRITY')

    def test_ledger_receipt_and_attempt_journal_tampering_is_blocked(self):
        record=self.fetch('receipt',self.receipt());self.verify(record)
        self.assertEqual(self.store.audit()['status'],'PASS_LOCAL_INTEGRITY')
        with sqlite3.connect(self.store.db) as con:con.execute('UPDATE receipts SET plan_sha=?',('f'*64,))
        with self.assertRaises(OperationError) as cm:self.store.daily_status(TEAM,DAY,calendar_record_id=self.calendar)
        self.assertEqual(cm.exception.code,'JOURNAL_INTEGRITY')

    def test_raw_body_change_and_manual_trust_upgrade_are_detected(self):
        record=self.import_record('receipt',self.receipt())
        with sqlite3.connect(self.store.db) as con:con.execute('UPDATE records SET provenance=? WHERE id=?',(TRUSTED,record['id']))
        with self.assertRaises(OperationError) as cm:self.store.record(record['id'])
        self.assertEqual(cm.exception.code,'RECORD_INTEGRITY')
        with sqlite3.connect(self.store.db) as con:con.execute('UPDATE records SET provenance=? WHERE id=?',(MANUAL,record['id']))
        (self.store.raw_dir/(record['raw_sha']+'.bin')).write_bytes(b'changed')
        with self.assertRaises(OperationError) as cm:self.store.record(record['id'])
        self.assertEqual(cm.exception.code,'RAW_INTEGRITY')

    def test_credentials_never_archived(self):
        self.fetch('receipt',self.receipt())
        for path in self.store.root.rglob('*'):
            if path.is_file():self.assertNotIn(b'TEST_ONLY_NOT_A_REAL_SECRET',path.read_bytes())

    def test_accepted_plan_is_exact_readonly_and_not_settlement(self):
        record=self.fetch('receipt',self.receipt());self.verify(record)
        head=self.store.audit()['head_sha256'];sha=hashlib.sha256(self.path.read_bytes()).hexdigest()
        result=self.store.accepted_plan(TEAM,DAY,sha)
        self.assertEqual(result['status'],'VERIFIED_ACCEPTED');self.assertFalse(result['receipt_is_settlement'])
        self.assertEqual(result['record_id'],record['id']);self.assertEqual(self.store.audit()['head_sha256'],head)
        self.assertEqual(self.store.accepted_plan(TEAM,DAY,'f'*64)['status'],'UNKNOWN')
        self.assertEqual(self.store.accepted_plan(TEAM,'2026-10-28',sha)['status'],'UNKNOWN')

    def test_optional_official_marks_and_warning_count_are_retained_not_inferred(self):
        data=dict(ledger_id='l',team_id=TEAM,as_of='2026-10-23',settled_at='2026-10-23T19:00:00+08:00',
                  settlement_status='SETTLED',cash='100',nav='1000',dividend_receivable='0',
                  holdings=[dict(ticker='2330',shares='1500.5',market_value='900')],official_warning_count=2)
        adapter=self.adapter('ledger');adapter['fields']['official_warning_count']='/official_warning_count'
        adapter['holdings_fields']=dict(ticker='/ticker',shares='/shares',market_value='/market_value')
        with patch('daily_auto.operations._get',return_value=json.dumps(data).encode()):record=self.store.fetch_record(adapter)
        self.assertEqual(record['data']['holdings'][0]['market_value'],'900');self.assertEqual(record['data']['official_warning_count'],2)
        del data['holdings'][0]['market_value'];del data['official_warning_count']
        record=self.fetch('ledger',data)
        self.assertNotIn('market_value',record['data']['holdings'][0]);self.assertNotIn('official_warning_count',record['data'])

    def test_later_accepted_different_bytes_supersedes_prior_phase(self):
        first=self.fetch('receipt',self.receipt());self.verify(first)
        sha=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.plan['market_view']['logic']+=' Different reason, same orders.';self.save_plan()
        later=NOW+timedelta(seconds=1)
        with patch('daily_auto.operations._clock',return_value=later):
            self.verify(self.fetch('receipt',self.receipt(receipt_id='new',received_at=later.isoformat())))
            prior=self.store.accepted_plan(TEAM,DAY,sha)
            self.assertIn('ACCEPTED_PLAN_SUPERSEDED',prior['blocking_codes'])
            current=self.store.accepted_plan(TEAM,DAY,hashlib.sha256(self.path.read_bytes()).hexdigest())
            self.assertEqual(current['status'],'VERIFIED_ACCEPTED')
            self.assertEqual(current['official_effective_version'],'UNKNOWN')

    def test_equal_timestamp_distinct_bytes_is_ambiguous(self):
        self.verify(self.fetch('receipt',self.receipt()))
        old=hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.plan['market_view']['logic']+=' Equal timestamp replacement.';self.save_plan()
        self.verify(self.fetch('receipt',self.receipt(receipt_id='same-time-other')))
        for sha in (old,hashlib.sha256(self.path.read_bytes()).hexdigest()):
            result=self.store.accepted_plan(TEAM,DAY,sha)
            self.assertIn('AMBIGUOUS_LATEST_ACCEPTED_PLAN',result['blocking_codes'])


if __name__=='__main__':unittest.main()
