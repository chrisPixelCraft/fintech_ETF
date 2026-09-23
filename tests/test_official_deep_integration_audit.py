"""Independent edge checks for fixed-release daily integration; no network."""
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import timedelta

import pandas as pd

from tests import test_full_tuned_entry as raw_contract
from tests.test_daily_auto_full_tuned import state_fixture, config_fixture, build, START
from daily_auto.full_tuned import (_state_from_operations, PacketBuildError,
                                   resolve_planner_context, write_packet)
from daily_auto.operations import OperationsStore, TRUSTED, MANUAL


class IndependentRawAvailability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw_contract.RawSignalContract.setUpClass()
        cls.raw = raw_contract.RawSignalContract()

    @classmethod
    def tearDownClass(cls):
        raw_contract.RawSignalContract.tearDownClass()

    def test_daily_close_cannot_be_known_before_session_close(self):
        manifest = self.raw.manifest()
        manifest['sources'][0]['content_as_of'] = '2026-09-21T13:29:59+08:00'
        with self.assertRaises(ValueError):
            self.raw.prepare(manifest=manifest)

    def test_hourly_start_stamp_is_not_completion_stamp(self):
        manifest = self.raw.manifest()
        manifest['sources'][1]['content_as_of'] = '2026-09-21T12:59:59+08:00'
        with self.assertRaises(ValueError):
            self.raw.prepare(manifest=manifest)

    def test_exact_completion_boundaries_are_available(self):
        manifest = self.raw.manifest()
        manifest['sources'][0]['content_as_of'] = '2026-09-21T13:30:00+08:00'
        manifest['sources'][1]['content_as_of'] = '2026-09-21T13:00:00+08:00'
        self.assertEqual(len(self.raw.prepare(manifest=manifest)['frame']), 150)

    def test_next_session_hourly_data_cannot_change_prior_signals(self):
        expected = self.raw.prepare()['frame'].sort_values('symbol').reset_index(drop=True)
        extra = self.raw.hourly[self.raw.hourly.timestamp.str.startswith('2026-09-21')].copy()
        extra.timestamp = extra.timestamp.str.replace('2026-09-21', '2026-09-22')
        for field in ('open', 'high', 'low', 'close', 'volume'):
            extra[field] *= 1000
        path = self.raw.path / 'independent_future_hourly.csv'
        pd.concat([self.raw.hourly, extra]).to_csv(path, index=False)
        actual = self.raw.prepare(hourly=path, manifest=self.raw.manifest(hourly=path))['frame']
        pd.testing.assert_frame_equal(expected, actual.sort_values('symbol').reset_index(drop=True),
                                      check_exact=True)

    def test_equivalent_timezone_strings_cannot_duplicate_one_bar(self):
        duplicate = self.raw.hourly.iloc[[0]].copy()
        duplicate['timestamp'] = pd.to_datetime(duplicate.timestamp, utc=True).dt.strftime('%Y-%m-%dT%H:%M:%SZ')
        path = self.raw.path / 'independent_duplicate_instant.csv'
        pd.concat([self.raw.hourly, duplicate]).to_csv(path, index=False)
        with self.assertRaisesRegex(ValueError, '[Dd]uplicate'):
            self.raw.prepare(hourly=path, manifest=self.raw.manifest(hourly=path))

    def test_changed_reference_is_not_trusted_by_recording_its_new_hash(self):
        import v2_offcial_best_deep_tuning as entry
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'daily_auto/reference/official_reference.json'
            path.parent.mkdir(parents=True)
            reference = json.loads((entry.ROOT / 'daily_auto/reference/official_reference.json').read_text())
            reference['stocks'][0]['ticker'] = '9999'
            path.write_text(json.dumps(reference))
            with patch.object(entry, 'ROOT', root), self.assertRaisesRegex(ValueError, 'reference changed'):
                entry.verified_reference()

    def test_unused_session_tail_cannot_change_frozen_four_hour_signal(self):
        expected = self.raw.prepare()['frame'].sort_values('symbol').reset_index(drop=True)
        tail = self.raw.hourly[self.raw.hourly.timestamp.str.startswith('2026-09-21T12:')].copy()
        tail.timestamp = tail.timestamp.str.replace('T12:00:', 'T13:30:')
        for column in ('open', 'high', 'low', 'close', 'volume'):
            tail[column] *= 10000
        path = self.raw.path / 'independent_unused_tail.csv'
        pd.concat([self.raw.hourly, tail]).to_csv(path, index=False)
        prepared = self.raw.prepare(hourly=path, manifest=self.raw.manifest(hourly=path))
        actual = prepared['frame'].sort_values('symbol').reset_index(drop=True)
        pd.testing.assert_frame_equal(expected, actual, check_exact=True)
        source = next(item for item in prepared['sources'] if item['role'] == 'hourly')
        self.assertEqual(source['excluded_outside_four_hour_window_rows'], len(tail))


class IndependentLedgerOverlay(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = OperationsStore(self.root / 'ops')
        self.state = state_fixture()
        self.official = dict(ledger_id='independent-fixture', team_id=self.state['team_id'],
            as_of=self.state['as_of'], settled_at='2026-10-26T19:00:00+08:00',
            settlement_status='SETTLED', cash='1900000', nav='9900000',
            dividend_receivable='0', holdings=deepcopy(self.state['holdings']))

    def record(self, value, provenance):
        with patch('daily_auto.operations._clock', return_value=START):
            return self.store._capture('ledger', json.dumps(value).encode(), provenance,
                {'url': 'https://esun-ai-challenge.tw/INDEPENDENT_TEST_ONLY',
                 'origin_authentication': 'OFFICIAL_HTTPS_TRANSPORT'}, None)

    def resolve(self, record):
        return _state_from_operations(self.state, self.root / 'ops', record['id'])[0]

    def test_manual_ledger_cannot_replace_despite_claimed_trust(self):
        self.official.update(provenance=TRUSTED, trusted=True)
        path = self.root / 'manual.json'
        path.write_text(json.dumps(self.official))
        with patch('daily_auto.operations._clock', return_value=START):
            record = self.store.import_record('ledger', path)
        self.assertEqual(record['provenance'], MANUAL)
        result = self.resolve(record)
        self.assertEqual(result['cash'], self.state['cash'])
        self.assertFalse(result['ledger_provenance']['reconciled'])
        self.assertEqual(build(state=result).receipt['submission_status'], 'BLOCK_SUBMISSION')

    def test_wrong_team_and_pending_settlement_cannot_replace(self):
        for change in ({'team_id': 'OTHER_TEAM'}, {'settlement_status': 'PENDING'}):
            with self.subTest(change=change):
                result = self.resolve(self.record({**self.official, **change}, TRUSTED))
                self.assertEqual(result['cash'], self.state['cash'])
                self.assertFalse(result['ledger_provenance']['reconciled'])

    def test_trusted_fractional_entitlement_is_preserved_and_blocks(self):
        self.official['holdings'][0]['shares'] = '4000.25'
        result = self.resolve(self.record(self.official, TRUSTED))
        self.assertEqual(result['holdings'][0]['shares'], '4000.25')
        with self.assertRaises(PacketBuildError) as caught:
            build(state=result)
        self.assertEqual(caught.exception.code, 'FRACTIONAL_HOLDING')

    def test_changed_archive_cannot_replace_and_never_claims_connection(self):
        record = self.record(self.official, TRUSTED)
        (self.store.raw_dir / (record['raw_sha'] + '.bin')).write_bytes(b'changed')
        result = self.resolve(record)
        self.assertEqual(result['cash'], self.state['cash'])
        self.assertFalse(result['ledger_provenance']['reconciled'])
        self.assertFalse(result['operations_reconciliation']['official_platform_connected'])


class IndependentStateMachineRegression(unittest.TestCase):
    def test_existing_book_does_not_accept_user_phase_boolean(self):
        state = state_fixture()
        state['operations_reconciliation'] = {'status': 'TRUSTED_RECONCILED'}
        state['buy_phase'] = True
        state['planner_context'] = {'phase_status': 'TRUSTED_PRIOR', 'buy_phase': True}
        context, report, _ = resolve_planner_context(state=state, config=config_fixture(),
                                                    previous_packet=None)
        self.assertEqual(context['phase_status'], 'UNKNOWN_BLOCKED')
        self.assertIn('PREVIOUS_PACKET_MISSING', report['blocking_codes'])

    def test_unknown_prior_phase_emits_no_orders_even_when_planner_would_trade(self):
        class NeverCalledPlanner:
            def __call__(self, *args, **kwargs):
                raise AssertionError('Unknown phase must block before calling planner')
        packet = build(NeverCalledPlanner(), planner_context={
            'phase_status': 'UNKNOWN_BLOCKED', 'buy_phase': False})
        self.assertEqual(packet.plan['orders'], [])
        self.assertIn('PRIOR_PLANNER_PHASE_UNKNOWN', packet.preflight['blocking_codes'])
        self.assertEqual(packet.receipt['submission_status'], 'BLOCK_SUBMISSION')

    def test_empty_book_with_changed_capital_is_not_bootstrap(self):
        state = state_fixture()
        state.update(holdings=[], cash='1', nav='1', dividend_receivable='0')
        context, report, _ = resolve_planner_context(state=state, config=config_fixture(),
                                                    previous_packet=None)
        self.assertEqual(context['phase_status'], 'UNKNOWN_BLOCKED')
        self.assertIn('BOOTSTRAP_LEDGER_MISMATCH', report['blocking_codes'])

    def test_actual_pinned_book_requires_persistent_buy_phase(self):
        """A real counterexample: the same params/book produce opposite sides."""
        from src.official_deep_tuning import OfficialPlanner
        root = Path(__file__).resolve().parents[1]
        path = root / 'outputs/full_tuned_v2/official_ex_post/final/full_tuned_v2'
        config = json.loads((path / 'config.json').read_text())
        day = '2025-01-06'
        equity = pd.read_csv(path / 'equity.csv', float_precision='round_trip')
        row = equity[equity.date.eq(day)].iloc[0]
        self.assertEqual(row.executed_plan, 'SELL_THEN_WAIT_SETTLEMENT')
        records = pd.read_csv(path / 'holdings.csv', float_precision='round_trip')
        holdings = records[records.date.eq(day)].set_index('symbol').shares.to_dict()
        records = pd.read_csv(path / 'signals.csv', float_precision='round_trip')
        signals = records[records.date.eq(day)].copy()
        lost = OfficialPlanner()(signals, holdings, row.cash, row.nav, config, False)
        kept = OfficialPlanner()(signals, holdings, row.cash, row.nav, config, True)
        self.assertEqual(lost[0], {'4991.TWO': -333000})
        self.assertEqual(kept[0], {'1504.TW': 3000})
        self.assertNotEqual(lost[0], kept[0])


class SellPhaseFixture:
    def __call__(self, rows, holdings, cash, nav, config, *args):
        return {next(iter(holdings)): -1000}, 'SELL_THEN_WAIT_SETTLEMENT', list(holdings)


class IndependentPhaseEvidence(unittest.TestCase):
    """The trusted lookup seam is mocked; raw transport is covered separately."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prior = state_fixture()
        self.prior['ledger_provenance']['operations_record_id'] = 'prior-record'
        self.packet = build(SellPhaseFixture(), state=self.prior)
        self.folder = self.root / 'packet'
        write_packet(self.packet, self.folder)
        self.current = deepcopy(self.prior)
        self.current['as_of'] = self.packet.plan['trade_date']
        self.current['holdings'][0]['shares'] -= 1000
        self.current['operations_reconciliation'] = {'status': 'TRUSTED_RECONCILED'}
        self.raw_sha = hashlib.sha256(json.dumps(self.prior).encode()).hexdigest()
        raw = self.root / 'raw'
        raw.mkdir()
        (raw / (self.raw_sha + '.bin')).write_text(json.dumps(self.prior))
        outer = self

        class AcceptedStore:
            root = outer.root
            calls = 0

            def accepted_plan(self, **identity):
                self.calls += 1
                outer.assertEqual(identity['team_id'], outer.current['team_id'])
                outer.assertEqual(identity['trade_date'], outer.current['as_of'])
                digest = hashlib.sha256((outer.folder / outer.packet.filename).read_bytes()).hexdigest()
                outer.assertEqual(identity['plan_sha256'], digest)
                return dict(status='VERIFIED_ACCEPTED', receipt_id='fixture-only', **identity)

            def reconcile_ledger(self, record_id, previous_state, **identity):
                outer.assertEqual(record_id, 'prior-record')
                outer.assertEqual(previous_state['holdings'], outer.prior['holdings'])
                return {'status': 'TRUSTED_RECONCILED'}

            def record(self, record_id):
                outer.assertEqual(record_id, 'prior-record')
                return {'raw_sha': outer.raw_sha}

        self.store = AcceptedStore()

    def resolve(self):
        return resolve_planner_context(state=self.current, config=config_fixture(),
            previous_packet=self.folder, operations_store=self.store)

    def test_exact_accepted_sell_and_two_books_restore_buy_phase(self):
        context, report, evidence = self.resolve()
        self.assertEqual(context['phase_status'], 'TRUSTED_PRIOR', report)
        self.assertTrue(context['buy_phase'])
        self.assertTrue(all(path.is_file() for path in evidence))

    def test_accepted_plan_without_matching_settled_shares_cannot_restore(self):
        self.current['holdings'][0]['shares'] += 1000
        context, report, _ = self.resolve()
        self.assertEqual(context['phase_status'], 'UNKNOWN_BLOCKED')
        self.assertIn('PREVIOUS_EXECUTION_NOT_RECONCILED', report['blocking_codes'])

    def test_receipt_phase_cannot_override_accepted_plan_reason(self):
        path = self.folder / 'receipt.json'
        receipt = json.loads(path.read_text())
        receipt['planner_result']['reason'] = 'HOLD_REVALIDATED_ENVELOPE'
        path.write_text(json.dumps(receipt))
        context, report, _ = self.resolve()
        self.assertEqual(context['phase_status'], 'UNKNOWN_BLOCKED', report)

    def test_omitting_required_file_from_hashes_blocks_before_trust_lookup(self):
        path = self.folder / 'receipt.json'
        receipt = json.loads(path.read_text())
        del receipt['artifact_hashes']['input_snapshot/state.json']
        path.write_text(json.dumps(receipt))
        context, report, _ = self.resolve()
        self.assertEqual(context['phase_status'], 'UNKNOWN_BLOCKED', report)
        self.assertEqual(self.store.calls, 0)


class IndependentRealOperationsChain(unittest.TestCase):
    def operation_fixture(self):
        from tests import test_daily_auto_operations as fixtures
        case = fixtures.OperationTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def fetch_valued_ledger(self, case, payload):
        adapter = case.adapter('ledger')
        adapter['fields']['official_warning_count'] = '/official_warning_count'
        adapter['holdings_fields'] = dict(ticker='/ticker', shares='/shares', market_value='/market_value')
        with patch('daily_auto.operations._get', return_value=json.dumps(payload).encode()):
            return case.store.fetch_record(adapter)

    def test_real_store_accepted_plan_and_settled_ledger_restore_phase(self):
        case = self.operation_fixture()
        prior = state_fixture()
        payload = dict(ledger_id='phase-prior', team_id=prior['team_id'], as_of=prior['as_of'],
            settled_at='2026-10-26T19:00:00+08:00', settlement_status='SETTLED',
            **{k: prior[k] for k in ('cash', 'nav', 'dividend_receivable', 'holdings')})
        record = case.fetch('ledger', payload)
        prior, _ = _state_from_operations(prior, case.store.root, record['id'])
        packet = build(SellPhaseFixture(), state=prior)
        folder = case.root / 'verified-phase-packet'
        write_packet(packet, folder)
        path = folder / packet.filename
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        receive_time = START + timedelta(minutes=2)
        with patch('daily_auto.operations._clock', return_value=receive_time):
            receipt = case.fetch('receipt', dict(receipt_id='phase-accepted', team_id=prior['team_id'],
                trade_date=packet.plan['trade_date'], filename=packet.filename, plan_sha256=digest,
                status='ACCEPTED', received_at=receive_time.isoformat()))
            verified = case.store.verify_receipt(receipt['id'], path, team_id=prior['team_id'],
                trade_date=packet.plan['trade_date'], calendar_record_id=case.calendar)
            self.assertEqual(verified['status'], 'VERIFIED_ACCEPTED')
        current = deepcopy(prior)
        current['as_of'] = packet.plan['trade_date']
        settled = deepcopy(payload)
        settled.update(ledger_id='phase-current', as_of=current['as_of'],
                       settled_at='2026-10-27T15:00:00+08:00', cash='2099557.5', nav='9999557.5')
        settled['holdings'][0]['shares'] = 3000
        with patch('daily_auto.operations._clock', return_value=START + timedelta(days=1)):
            record = case.fetch('ledger', settled)
            current, _ = _state_from_operations(current, case.store.root, record['id'])
            context, report, paths = resolve_planner_context(state=current, config=config_fixture(),
                previous_packet=folder, operations_store=case.store)
        self.assertEqual(context['phase_status'], 'TRUSTED_PRIOR', report)
        self.assertTrue(context['buy_phase'])
        self.assertTrue(all(path.is_file() for path in paths))

    def test_later_different_accepted_bytes_supersede_old_phase_evidence(self):
        case = self.operation_fixture()
        first_sha = hashlib.sha256(case.path.read_bytes()).hexdigest()
        first = case.fetch('receipt', case.receipt(receipt_id='first-accepted'))
        self.assertEqual(case.verify(first)['status'], 'VERIFIED_ACCEPTED')
        case.plan['observations'][0]['statement'] += ' independent alternative interpretation'
        case.save_plan()
        later = START + timedelta(minutes=1)
        with patch('daily_auto.operations._clock', return_value=later):
            second = case.fetch('receipt', case.receipt(receipt_id='second-accepted', received_at=later.isoformat()))
            self.assertEqual(case.verify(second)['status'], 'VERIFIED_ACCEPTED')
            old = case.store.accepted_plan(case.plan['team_id'], case.plan['trade_date'], first_sha)
        self.assertNotEqual(old['status'], 'VERIFIED_ACCEPTED', old)

    def test_future_calendar_cannot_supply_ages_or_warning_count(self):
        from daily_auto.compliance_state import resolve_passive_cap_days
        case = self.operation_fixture()
        state = state_fixture()
        payload = dict(ledger_id='cap-current', team_id=state['team_id'], as_of=state['as_of'],
            settled_at='2026-10-26T19:00:00+08:00', settlement_status='SETTLED',
            official_warning_count=2,
            **{k: deepcopy(state[k]) for k in ('cash', 'nav', 'dividend_receivable', 'holdings')})
        for row in payload['holdings']:
            row['market_value'] = str(row['shares'] * 100)
        ledger = self.fetch_valued_ledger(case, payload)
        with patch('daily_auto.operations._clock', return_value=START + timedelta(days=1)):
            calendar = case.fetch('calendar', {'sessions': ['2026-10-23', '2026-10-26', '2026-10-27'],
                'published_at': (START + timedelta(days=1)).isoformat()})
        with patch('daily_auto.compliance_state.datetime') as clock:
            clock.now.return_value = START
            ages, report, _ = resolve_passive_cap_days(state=state, operations_root=case.store.root,
                ledger_id=ledger['id'], calendar_record_id=calendar['id'])
        self.assertEqual(ages, {})
        self.assertEqual(report['status'], 'UNKNOWN')
        self.assertIn('OFFICIAL_CALENDAR_PUBLICATION_TIME', report['blocking_codes'])
        self.assertIsNone(report['official_warning_count'])

    def test_authenticated_valuation_ages_remain_cause_inference_not_certification(self):
        from daily_auto.compliance_state import resolve_passive_cap_days
        case = self.operation_fixture()
        state = state_fixture()
        payload = dict(ledger_id='cap-current', team_id=state['team_id'], as_of=state['as_of'],
            settled_at='2026-10-26T19:00:00+08:00', settlement_status='SETTLED',
            official_warning_count=2,
            **{k: deepcopy(state[k]) for k in ('cash', 'nav', 'dividend_receivable', 'holdings')})
        for row in payload['holdings']:
            row['market_value'] = str(row['shares'] * 100)
        ledger = self.fetch_valued_ledger(case, payload)
        calendar = case.fetch('calendar', {'sessions': ['2026-10-23', '2026-10-26', '2026-10-27'],
            'published_at': '2026-09-01T00:00:00+08:00'})
        with patch('daily_auto.compliance_state.datetime') as clock:
            clock.now.return_value = START
            ages, report, _ = resolve_passive_cap_days(state=state, operations_root=case.store.root,
                ledger_id=ledger['id'], calendar_record_id=calendar['id'])
        self.assertEqual(set(ages.values()), {0})
        self.assertEqual(report['status'], 'TRUSTED_RESTORED')
        self.assertEqual(report['official_warning_count'], 2)
        self.assertEqual(report['official_cause_certification'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
