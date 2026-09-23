"""Independent offline checks of generated replay packets and their trust boundary."""
from contextlib import redirect_stdout
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
import hashlib
import io
import json
import socket
import tempfile
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker

from daily_auto import replay
from daily_auto.validate import TZ, load_json, validate_plan, derive_orders, PreflightError

ROOT=Path(__file__).resolve().parents[1]


def encode(plan):
    # This is deliberately the standard JSON reader used by a receiver, not
    # the producer's custom decimal/string serialization helper.
    return json.loads(json.dumps(plan,ensure_ascii=False,allow_nan=False))


def independent_orders(plan,state):
    nav=Decimal(str(state['nav']))
    close={r['ticker']:Decimal(str(r['close'])) for r in state['closes']}
    held={r['ticker']:Decimal(str(r['shares'])) for r in state['holdings']}
    expected=[]
    for decision in plan['decisions']:
        symbol=decision['ticker'];weight=Decimal(str(decision['target_weight']))
        target=(weight*nav/close[symbol]/1000).to_integral_value(rounding=ROUND_FLOOR)*1000
        quantity=target-held.get(symbol,Decimal(0))
        expected.append(dict(ticker=symbol,side='BUY' if quantity>0 else 'SELL',
            shares=int(abs(quantity)),decision_ref=decision['decision_id']))
    return expected


class GeneratedReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before=datetime.now(TZ).replace(microsecond=0)
        with patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden')):
            cls.initial=replay.packet('official_ex_post','2025-01-02')
            cls.retained=replay.packet('official_ex_post','2025-01-03')
        cls.after=datetime.now(TZ)

    def test_receiver_sees_schema_numbers_not_numeric_strings(self):
        validator=Draft202012Validator(load_json(ROOT/'official_docs/D-Plan.schema.json'),format_checker=FormatChecker())
        for packet in (self.initial,self.retained):
            plan=encode(packet[0]);self.assertEqual(list(validator.iter_errors(plan)),[])
            for decision in plan['decisions']:
                self.assertIsInstance(decision['target_weight'],(float,int))
                self.assertNotIsInstance(decision['target_weight'],bool)
            for observation in plan['observations']:
                for value in observation['values'].values():self.assertIsInstance(value,(float,int))
            for order in plan['orders']:
                self.assertIs(type(order['shares']),int);self.assertEqual(order['shares']%1000,0)

    def test_json_roundtrip_official_formula_uses_actual_prior_holdings(self):
        for plan,state,validation,receipt in (self.initial,self.retained):
            self.assertEqual(encode(plan)['orders'],independent_orders(encode(plan),state))
            self.assertEqual(validation['derived_orders']['orders'],plan['orders'])
            self.assertTrue(all(r['shares']>0 for r in plan['orders']))

    def test_every_prior_holding_and_reference_is_covered(self):
        plan,state,_,_=self.retained
        holds={r['ticker'] for r in state['holdings']};self.assertGreaterEqual(len(holds),20)
        changed=[r['ticker'] for r in plan['decisions']];unchanged=[r['ticker'] for r in plan['no_trade_decisions']]
        self.assertFalse(set(changed)&set(unchanged));self.assertTrue(holds<=set(changed)|set(unchanged))
        self.assertTrue(set(unchanged)<=holds)
        self.assertEqual(len(changed)+len(unchanged),len(set(changed+unchanged)))
        sources={r['source_id'] for r in plan['sources']};observations={r['obs_id'] for r in plan['observations']}
        inferences={r['inf_id'] for r in plan['inferences']};decisions={r['decision_id'] for r in plan['decisions']}
        for r in plan['observations']:self.assertTrue(set(r['source_ref'])<=sources)
        for r in plan['inferences']:self.assertTrue(set(r['premise_refs'])<=observations)
        for r in plan['decisions']:self.assertTrue(set(r['inference_refs'])<=inferences)
        for r in plan['no_trade_decisions']:self.assertTrue(set(r['reason_refs'])<=inferences)
        for r in plan['orders']:self.assertIn(r['decision_ref'],decisions)
        for rows,key,prefix in [('observations','obs_id','O'),('inferences','inf_id','I'),('decisions','decision_id','D')]:
            self.assertEqual([r[key] for r in plan[rows]],[prefix+str(i+1) for i in range(len(plan[rows]))])

    def test_observed_holdings_and_initial_capital_cite_their_actual_files(self):
        plan,_,_,_=self.initial
        sources={r['source_id']:r['url'] for r in plan['sources']}
        for observation in plan['observations']:
            urls=[sources[ref] for ref in observation['source_ref']]
            if 'held_shares' in observation['values']:
                self.assertTrue(any(url.endswith('/holdings.csv') for url in urls))
            if 'book_nav' in observation['values']:
                self.assertTrue(any(url.endswith('/config.json') for url in urls))

    def test_fractional_corporate_shares_are_never_silently_rounded(self):
        plan,state,_,_=self.retained
        state=json.loads(json.dumps(state));state['holdings'][0]['shares']='2150.1234'
        with self.assertRaises(PreflightError) as context:derive_orders(plan,state)
        self.assertEqual(context.exception.code,'HOLDINGS_SHARES')
        self.assertEqual(state['holdings'][0]['shares'],'2150.1234')

    def test_real_generation_clock_and_no_fabricated_provider(self):
        for plan,state,_,receipt in (self.initial,self.retained):
            meta=plan['agent_metadata']
            start=datetime.fromisoformat(meta['run_started_at']);end=datetime.fromisoformat(meta['run_completed_at'])
            self.assertLessEqual(self.before,start);self.assertLessEqual(start,end);self.assertLessEqual(end,self.after)
            self.assertEqual(meta['model_provider'],'other');self.assertIn('deterministic',meta['model_version'])
            self.assertFalse(receipt['live_data_capture'])
            for source in plan['sources']:
                self.assertEqual(source['authority'],'other');self.assertTrue(source['url'].startswith('file:'))
                self.assertLessEqual(datetime.fromisoformat(source['content_as_of']),end)
                if not source['url'].endswith('/config.json'):
                    self.assertLess(datetime.fromisoformat(source['content_as_of']),start)
                self.assertGreaterEqual(datetime.fromisoformat(source['fetched_at']),self.before)
            self.assertEqual(state['ledger_provenance']['origin'],'research_shadow')
            self.assertFalse(state['ledger_provenance']['reconciled'])

    def test_schema_success_cannot_become_submission_green(self):
        for plan,state,validation,receipt in (self.initial,self.retained):
            self.assertEqual(validation['status'],'BLOCK')
            self.assertEqual(validation['submission_status'],'BLOCK_SUBMISSION')
            self.assertEqual(receipt['submission_status'],'BLOCK_SUBMISSION')
            self.assertEqual(validation['formal_certification'],'NOT_PROVIDED')
            self.assertIn('NON_LIVE_STATE',validation['blocking_codes'])
            self.assertIn('ACTIVE_SHARE_UNKNOWN',validation['blocking_codes'])
            self.assertIn('CORPORATE_ACTION_STATE_UNKNOWN',validation['blocking_codes'])
            for code in ('SCHEMA','OFFICIAL_DERIVED_ORDERS','PRIOR_HOLDING_COVERAGE','REFERENCE_CHAIN'):
                self.assertFalse(any(r['status']=='FAIL' for r in validation['checks'] if r['code']==code),code)

    def test_serialized_plan_tamper_detected_without_network(self):
        plan,state,_,_=self.initial;plan=encode(plan);plan['orders'][0]['shares']+=1000
        with patch.object(socket.socket,'connect',side_effect=AssertionError('Network forbidden')):
            report=validate_plan(plan,state,checked_at=datetime.now(TZ))
        self.assertIn('OFFICIAL_DERIVED_ORDERS',report['blocking_codes'])
        self.assertEqual(report['submission_status'],'BLOCK_SUBMISSION')

    def test_generator_rejects_unverified_source_mapping_and_reference(self):
        original=replay.sha
        paths=[ROOT/'data/tuning_2nd/official_universe/processed/universe.csv',
               ROOT/'daily_auto/reference/official_reference.json']
        for changed in paths:
            def altered(path):return '0'*64 if Path(path).resolve()==changed.resolve() else original(path)
            with self.subTest(changed=changed.name),patch.object(replay,'sha',side_effect=altered):
                with self.assertRaises(ValueError):replay.packet('official_ex_post','2025-01-02')

    def test_metadata_version_covers_code_and_inputs(self):
        plan,_,_,receipt=self.initial
        files=receipt['source_files']
        for name in ['daily_auto/replay.py','daily_auto/validate.py','official_docs/D-Plan.schema.json',
                     'daily_auto/reference/official_reference.json',
                     'data/tuning_2nd/official_universe/processed/universe.csv']:
            self.assertIn(name,files)
            self.assertEqual(files[name],hashlib.sha256((ROOT/name).read_bytes()).hexdigest())
        config=ROOT/'outputs/official_v2_reaudit/official_ex_post/final/A_dplan_guard/config.json'
        self.assertNotEqual(plan['agent_metadata']['code_version'],'sha256:'+hashlib.sha256(config.read_bytes()).hexdigest()[:32])

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            output=Path(td)/'existing';output.mkdir();marker=output/'keep';marker.write_text('prior evidence')
            with self.assertRaises(FileExistsError):replay.main(['--output',str(output)])
            self.assertEqual(marker.read_text(),'prior evidence')

    def test_cli_write_retains_json_number_types(self):
        with tempfile.TemporaryDirectory() as td:
            output=Path(td)/'packet'
            with patch.object(replay,'packet',return_value=self.initial),redirect_stdout(io.StringIO()):
                replay.main(['--date','2025-01-02','--output',str(output)])
            plan=json.loads((output/'D-Plan_RESEARCH_ONLY_2025-01-02.json').read_text())
            self.assertEqual(plan['orders'],independent_orders(plan,self.initial[1]))
            self.assertTrue(all(isinstance(d['target_weight'],(float,int)) for d in plan['decisions']))
            self.assertEqual(json.loads((output/'preflight.json').read_text())['submission_status'],'BLOCK_SUBMISSION')
            receipt=json.loads((output/'receipt.json').read_text())
            for name,digest in receipt['code_files'].items():
                archived=output/'code_snapshot'/name
                self.assertTrue(archived.is_file(),name)
                self.assertEqual(hashlib.sha256(archived.read_bytes()).hexdigest(),digest)
            expected=hashlib.sha256(json.dumps(receipt['code_files'],sort_keys=True).encode()).hexdigest()
            self.assertEqual(receipt['code_bundle_sha256'],expected)
            self.assertEqual(plan['agent_metadata']['code_version'],'sha256:'+expected[:48])


if __name__=='__main__':unittest.main()
