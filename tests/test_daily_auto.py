"""Offline checker tests. All prices/ledger/evidence metadata here are SYNTHETIC.

These fixtures exercise deterministic rules; they are never submission evidence.
The real supplied reference provides identities only, not invented market facts.
"""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from decimal import Decimal as D
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator, FormatChecker
from daily_auto.cli import main
from daily_auto.validate import (ROOT, SCHEMA, OFFICIAL_REFERENCE, PreflightError,
                                active_share_value, derive_orders, load_json,
                                project_orders, validate_plan)

REFERENCE = load_json(OFFICIAL_REFERENCE)
TICKERS = [r['ticker'] for r in REFERENCE['stocks']]
ETFS = [r['ticker'] for r in REFERENCE['active_etfs']]
CHECK = '2026-10-27T08:00:00+08:00'


def evidence(authority='twse', **values):
    host = 'esun-ai-challenge.tw' if authority == 'organizer' else 'www.twse.com.tw'
    return dict(known_at='2026-10-26T19:30:00+08:00', authority=authority,
                source_url=f'https://{host}/UNIT_TEST_ONLY', sha256='0'*64, **values)


def fixture():
    """20 equal positions, 20% cash, no orders; AS intentionally missing."""
    plan = load_json(ROOT/'official_docs/D-Plan_TEAM_042_2026-10-27.json')
    plan.pop('_NOTE')
    plan['sources'] = [dict(source_id='S1', authority='twse', name='Synthetic unit-test source',
                           url='https://www.twse.com.tw/UNIT_TEST_ONLY',
                           content_as_of='2026-10-26T19:30:00+08:00')]
    plan['observations'] = [dict(obs_id='O1', source_ref=['S1'],
                                statement='Synthetic unit-test observation', values={'close':100})]
    plan['inferences'] = [dict(inf_id='I1', premise_refs=['O1'],
                              logic='Synthetic test logic with enough characters.', conclusion='Keep current position.',counter_evidence=None)]
    plan['market_view'] = dict(basis_refs=['O1'], logic='Synthetic test market logic with enough characters.',
                              regime='neutral', stance='neutral', counter_evidence=None, posture=dict(
                                  net_exposure_intent='hold', target_cash_pct_range=[D('.15'),D('.24')]))
    plan['decisions'] = []; plan['orders'] = []
    plan['no_trade_decisions'] = [dict(ticker=t, reason_refs=['I1'], reason='Unit-test hold') for t in TICKERS[:20]]
    plan['agent_metadata'] = dict(model_provider='other', model_version='UNIT_TEST_ONLY',
                                 run_started_at='2026-10-26T19:40:00+08:00',
                                 run_completed_at='2026-10-27T07:50:00+08:00', code_version='unittest')
    state = dict(state_version='1.0', data_mode='LIVE', team_id=plan['team_id'],
                 as_of='2026-10-26', known_at='2026-10-26T19:30:00+08:00',
                 cash='2000000', nav='10000000', dividend_receivable='0',
                 holdings=[dict(ticker=t, shares=4000) for t in TICKERS[:20]],
                 closes=[dict(ticker=t, close='100', as_of='2026-10-26', **evidence()) for t in TICKERS],
                 calendar=evidence(trade_date='2026-10-27', previous_session='2026-10-26',
                                   sessions=['2026-10-26','2026-10-27'], is_trading_day=True),
                 whitelist=evidence('organizer', tickers=TICKERS.copy(), effective_from='2026-09-18'),
                 ledger_provenance=evidence('organizer', origin='official_settlement', reconciled=True),
                 corporate_actions=evidence(status='NONE_CONFIRMED', trade_date='2026-10-27'),
                 active_share={'status':'UNKNOWN'})
    return plan,state


def decide(plan, state, changes):
    """changes: ticker, action, target decimal; generated orders still test exact formula."""
    plan['decisions']=[dict(decision_id=f'D{i+1}', ticker=t, action=a, target_weight=D(w),
                            inference_refs=['I1']) for i,(t,a,w) in enumerate(changes)]
    changed={t for t,a,w in changes}
    plan['no_trade_decisions']=[r for r in plan['no_trade_decisions'] if r['ticker'] not in changed]
    plan['orders']=derive_orders(plan,state)['orders']


def statuses(report, code):
    return [r['status'] for r in report['checks'] if r['code']==code]


class FormulaTests(unittest.TestCase):
    def test_official_positive_example_exact_2000_and_108000(self):
        plan=load_json(ROOT/'official_docs/D-Plan_TEAM_042_2026-10-27.json')
        state=dict(nav='102000000', holdings=[dict(ticker='2330',shares=17000)],
                   closes=[dict(ticker='2330',close='1480'),dict(ticker='2891',close='42.5')])
        self.assertEqual(derive_orders(plan,state)['orders'],
                         [{k:o[k] for k in ('ticker','side','shares','decision_ref')} for o in plan['orders']])

    def test_decimal_floor_boundary_and_midpoint(self):
        state=dict(nav='10000000',holdings=[],closes=[dict(ticker='2330',close='100')])
        plan={'decisions':[dict(decision_id='D1',ticker='2330',action='BUY',target_weight=D('.05'))]}
        self.assertEqual(derive_orders(plan,state)['orders'][0]['shares'],5000)
        plan['decisions'][0]['target_weight']=D('.04999999999999999999999999999999999999999999')
        self.assertEqual(derive_orders(plan,state)['orders'][0]['shares'],4000)
        plan['decisions'][0]['target_weight']=D('.055')
        self.assertEqual(derive_orders(plan,state)['orders'][0]['shares'],5000)

    def test_odd_lot_delta_is_rejected_not_repaired(self):
        state=dict(nav='10000000',holdings=[dict(ticker='2330',shares=1500)],closes=[dict(ticker='2330',close='100')])
        for action,weight in [('SELL_ALL','0'),('TRIM','.01'),('ADD','.02')]:
            with self.subTest(action=action), self.assertRaises(PreflightError) as cm:
                derive_orders({'decisions':[dict(decision_id='D1',ticker='2330',action=action,target_weight=weight)]},state)
            self.assertEqual(cm.exception.code,'ODD_LOT_DERIVATION')

    def test_action_consistency_and_zero_change(self):
        plan,state=fixture()
        for action,weight,code in [('BUY','.05','ACTION_INCONSISTENT'),
                                   ('SELL_ALL','.01','ACTION_INCONSISTENT'),
                                   ('TRIM','0','ACTION_INCONSISTENT'),
                                   ('ADD','.04','ZERO_DELTA_DECISION')]:
            plan['decisions']=[dict(decision_id='D1',ticker=TICKERS[0],action=action,target_weight=weight)]
            with self.subTest(action=action,weight=weight), self.assertRaises(PreflightError) as cm:
                derive_orders(plan,state)
            self.assertEqual(cm.exception.code,code)

    def test_percentage_fees_tax_and_receivable_not_cash(self):
        state=dict(cash='100000',nav='300000',dividend_receivable='99999999',
                   holdings=[dict(ticker='2330',shares=2000)],
                   closes=[dict(ticker='2330',close='100'),dict(ticker='2317',close='100')])
        result=project_orders(state,[dict(ticker='2330',side='SELL',shares=1000),dict(ticker='2317',side='BUY',shares=1000)])
        self.assertEqual(result['cash'],D('99415'))
        self.assertEqual(result['nav'],D('299415'))
        self.assertEqual(result['commission'],D('285'))
        self.assertEqual(result['sell_tax'],D('300'))
        with self.assertRaises(PreflightError) as cm:
            project_orders(state,[dict(ticker='2330',side='SELL',shares=3000)])
        self.assertEqual(cm.exception.code,'OVERSELL')

    def test_strict_json_parse_rejects_duplicates_nan_and_preserves_decimals(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'input.json'
            for text,code in [('{"x":1,"x":2}','DUPLICATE_JSON_KEY'),('{"x":NaN}','NONFINITE_JSON')]:
                p.write_text(text)
                with self.assertRaises(PreflightError) as cm:load_json(p)
                self.assertEqual(cm.exception.code,code)
            p.write_text('{"x":0.10000000000000000000000000001}')
            self.assertEqual(load_json(p)['x'],D('.10000000000000000000000000001'))


class SemanticTests(unittest.TestCase):
    def check(self,plan,state,**kw):return validate_plan(plan,state,checked_at=CHECK,**kw)

    def test_structurally_valid_hold_does_not_fake_active_share(self):
        plan,state=fixture();report=self.check(plan,state)
        self.assertEqual(report['blocking_codes'],['ACTIVE_SHARE_UNKNOWN'])
        self.assertEqual(report['projection']['cash_ratio'],'0.2')
        self.assertEqual(report['submission_status'],'BLOCK_SUBMISSION')
        self.assertEqual(report['formal_certification'],'NOT_PROVIDED')
        self.assertEqual(statuses(report,'SCHEMA'),['PASS'])

    def test_both_supplied_examples_schema_valid_but_negative_semantics_block(self):
        validator=Draft202012Validator(load_json(SCHEMA),format_checker=FormatChecker())
        for name in ('042','043'):
            p=load_json(ROOT/f'official_docs/D-Plan_TEAM_{name}_2026-10-27.json')
            self.assertEqual(list(validator.iter_errors(p)),[])
        p=load_json(ROOT/'official_docs/D-Plan_TEAM_043_2026-10-27.json')
        _,s=fixture();s['team_id']=p['team_id'];s['nav']='102000000';s['cash']='16000000'
        s['holdings']=[dict(ticker='2330',shares=17000),dict(ticker='2317',shares=280000),dict(ticker='2412',shares=280000)]
        for row in s['closes']:
            if row['ticker']=='2330':row['close']='1480'
            if row['ticker']=='2891':row['close']='42.5'
            if row['ticker']=='2454':row['close']='1250'
        r=validate_plan(p,s,checked_at='2026-10-27T08:50:00+08:00')
        for code in ('REFERENCE_CHAIN','OFFICIAL_DERIVED_ORDERS','POSTURE_NET_FLOW'):
            self.assertIn('FAIL',statuses(r,code),code)

    def test_sequential_id_and_reference_chain_not_only_regex(self):
        p,s=fixture();p['sources'][0]['source_id']='S01'
        r=self.check(p,s)
        self.assertIn('FAIL',statuses(r,'SEQUENTIAL_IDS'))
        self.assertIn('FAIL',statuses(r,'REFERENCE_CHAIN'))
        p,s=fixture();p['inferences'][0]['premise_refs']=['O9']
        self.assertIn('FAIL',statuses(self.check(p,s),'REFERENCE_CHAIN'))

    def test_coverage_duplicates_phantom_and_unknown_field(self):
        p,s=fixture();p['no_trade_decisions'].pop()
        self.assertIn('FAIL',statuses(self.check(p,s),'PRIOR_HOLDING_COVERAGE'))
        p,s=fixture();p['no_trade_decisions'].append(deepcopy(p['no_trade_decisions'][0]))
        self.assertIn('FAIL',statuses(self.check(p,s),'DECISION_TICKER_UNIQUENESS'))
        p,s=fixture();p['no_trade_decisions'].append(dict(ticker=TICKERS[30],reason_refs=['I1']))
        self.assertIn('FAIL',statuses(self.check(p,s),'PRIOR_HOLDING_COVERAGE'))
        p,s=fixture();p['constraints_pass']=True
        self.assertIn('FAIL',statuses(self.check(p,s),'SCHEMA'))

    def test_orders_cannot_override_formula_or_use_nonlots(self):
        p,s=fixture();decide(p,s,[(TICKERS[1],'ADD','.055')]);p['orders'][0]['shares']=2000
        self.assertIn('FAIL',statuses(self.check(p,s),'OFFICIAL_DERIVED_ORDERS'))
        p['orders'][0]['shares']=1500
        self.assertIn('FAIL',statuses(self.check(p,s),'SCHEMA'))

    def test_posture_flow_tolerance_and_fee_cash_range(self):
        for weight,expect in [('.065','PASS'),('.075','FAIL')]:
            p,s=fixture();decide(p,s,[(TICKERS[1],'ADD',weight)])
            self.assertEqual(statuses(self.check(p,s),'POSTURE_NET_FLOW'),[expect])
        p,s=fixture();decide(p,s,[(TICKERS[1],'ADD','.055')])
        p['market_view']['posture']['target_cash_pct_range']=[D('.19'),D('.20')]
        r=self.check(p,s)
        self.assertLess(D(r['projection']['cash_ratio']),D('.19'))
        self.assertEqual(statuses(r,'POSTURE_CASH_ESTIMATE'),['FAIL'])
        p['market_view']['posture']['net_exposure_intent']='reduce'
        self.assertEqual(statuses(self.check(p,s),'POSTURE_NET_FLOW'),['FAIL'])

    def test_funding_claim_requires_actual_cash_gap(self):
        p,s=fixture();decide(p,s,[(TICKERS[1],'TRIM','.035'),(TICKERS[2],'ADD','.055')])
        p['decisions'][0]['funding_for']=['D2']
        p['market_view']['posture']['target_cash_pct_range']=[D('.195'),D('.205')]
        self.assertEqual(statuses(self.check(p,s),'FUNDING_DEFICIT_EXISTS'),['PASS'])
        p['market_view']['posture']['target_cash_pct_range']=[D('.15'),D('.24')]
        self.assertEqual(statuses(self.check(p,s),'FUNDING_DEFICIT_EXISTS'),['FAIL'])
        p['decisions'][0]['funding_for']=['D1']
        self.assertEqual(statuses(self.check(p,s),'FUNDING_REFERENCES'),['FAIL'])

    def test_strict_cash_negative_cash_count_and_fee_cap(self):
        p,s=fixture();s['nav']='8000000'
        for h in s['holdings']:h['shares']=3000
        self.assertIn('FAIL',statuses(self.check(p,s),'PROJECTED_CASH_HARD_LIMIT'))
        p,s=fixture();s['holdings'].pop();s['nav']='9600000';p['no_trade_decisions'].pop()
        self.assertEqual(statuses(self.check(p,s),'PROJECTED_HOLDING_COUNT'),['FAIL'])
        p,s=fixture();decide(p,s,[(TICKERS[1],'ADD','.10')])
        self.assertIn('FAIL',statuses(self.check(p,s),'ACTIVE_CAP_BREACH'))
        p,s=fixture();s['cash']='0';s['nav']='8000000';decide(p,s,[(TICKERS[1],'ADD','.075')])
        self.assertIn('FAIL',statuses(self.check(p,s),'PROJECTED_CASH_HARD_LIMIT'))

    def test_passive_grace_requires_history_and_sixth_day_blocks(self):
        for age,code in [(None,'PASSIVE_CAP_HISTORY_UNKNOWN'),(4,'PASSIVE_CAP_GRACE'),(5,'PASSIVE_CAP_OVERDUE')]:
            p,s=fixture();s['holdings'][1]['shares']=12000;s['nav']='10800000'
            if age is not None:s['holdings'][1]['passive_cap_days']=age
            self.assertTrue(statuses(self.check(p,s),code),code)

    def test_odd_holding_no_trade_is_preserved_and_flagged(self):
        p,s=fixture();s['holdings'][0]['shares']=4500;s['nav']='10050000'
        r=self.check(p,s)
        self.assertEqual(r['projection']['holdings'][TICKERS[0]],'4500')
        self.assertEqual(statuses(r,'EXISTING_ODD_LOT_POLICY'),['UNKNOWN'])

    def test_calendar_actual_dates_and_future_source(self):
        p,s=fixture();s['calendar']['sessions']=['2026-10-26','2026-10-28']
        self.assertEqual(statuses(self.check(p,s),'TRADING_CALENDAR'),['FAIL'])
        p,s=fixture();p['sources'][0]['content_as_of']='2026-10-27T08:01:00+08:00'
        self.assertIn('FAIL',statuses(self.check(p,s),'SOURCE_TIME_LOCK'))
        p,s=fixture();s['closes'][0]['known_at']='2026-10-28T00:00:00+08:00'
        self.assertIn('FAIL',statuses(self.check(p,s),'EVIDENCE_KNOWN_AT'))
        p,s=fixture();p['trade_date']='2026-02-30'
        self.assertIn('FAIL',statuses(self.check(p,s),'INVALID_DATE'))
        p,s=fixture();p['agent_metadata']['run_completed_at']='2026-10-27T07:50:00Z'
        self.assertIn('FAIL',statuses(self.check(p,s),'SCHEMA'))

    def test_generation_previous_evening_allowed_but_local_submission_window_conservative(self):
        p,s=fixture();r=self.check(p,s)
        self.assertEqual(statuses(r,'AGENT_TIME_ORDER'),['PASS'])
        self.assertEqual(statuses(r,'CONSERVATIVE_SUBMISSION_WINDOW'),['PASS'])
        p['agent_metadata']['run_completed_at']='2026-10-27T04:50:00+08:00'
        r=validate_plan(p,s,checked_at='2026-10-27T04:59:00+08:00')
        self.assertEqual(statuses(r,'CONSERVATIVE_SUBMISSION_WINDOW'),['FAIL'])
        self.assertIn('not declared an official violation',next(x['message'] for x in r['checks'] if x['code']=='CONSERVATIVE_SUBMISSION_WINDOW'))

    def test_exact_whitelist_not_any_150_and_no_backdated_effective(self):
        p,s=fixture();s['whitelist']['tickers'][-1]='9999'
        self.assertEqual(statuses(self.check(p,s),'OFFICIAL_150_COVERAGE'),['FAIL'])
        p,s=fixture();s['whitelist']['effective_from']='2026-10-28'
        self.assertEqual(statuses(self.check(p,s),'WHITELIST_EFFECTIVE'),['FAIL'])

    def test_local_evidence_hash_and_unsettled_dividend_book_nav(self):
        p,s=fixture()
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'evidence.txt';path.write_text('UNIT TEST ONLY')
            s['ledger_provenance']['evidence_file']=path.name
            self.assertIn('FAIL',statuses(self.check(p,s,evidence_root=td),'EVIDENCE_FILE_HASH'))
            s['ledger_provenance']['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertIn('PASS',statuses(self.check(p,s,evidence_root=td),'EVIDENCE_FILE_HASH'))
        s['dividend_receivable']='500000';s['nav']='10500000'
        self.assertEqual(statuses(self.check(p,s),'LEDGER_NAV_RECONCILIATION'),['FAIL'])


class ActiveShareTests(unittest.TestCase):
    def as_block(self):
        return dict(status='AVAILABLE',method='TOP10_UNION_FULL_NAV',
            method_confirmation=evidence('organizer',status='OFFICIAL_CONFIRMED',
                                         method='TOP10_UNION_FULL_NAV',tie_break='ticker'),
            reference_list=evidence('organizer',etf_ids=ETFS.copy()),
            etfs=[evidence(etf_id=t,as_of='2026-10-26',holdings=[dict(ticker=f'F{i}',weight='.05') for i in range(10)]) for t in ETFS],
            previous_results=[evidence('organizer',etf_id=t,as_of='2026-10-26',value='.3') for t in ETFS])

    def test_top10_union_uses_full_nav_not_renormalized(self):
        self.assertEqual(active_share_value({'A':'.2','B':'.1'},{'A':'.1','C':'.1'}),D('.15'))
        self.assertEqual(active_share_value({str(i):'.04' for i in range(20)},{f'F{i}':'.05' for i in range(10)}),D('.45'))

    def test_unknown_method_cannot_claim_pass(self):
        p,s=fixture();s['active_share']={'status':'PASS'}
        self.assertIn('ACTIVE_SHARE_UNKNOWN',validate_plan(p,s,checked_at=CHECK)['blocking_codes'])
        s['active_share']=self.as_block();s['active_share']['method_confirmation']=None
        self.assertIn('ACTIVE_SHARE_METHOD_UNRESOLVED',validate_plan(p,s,checked_at=CHECK)['blocking_codes'])

    def test_all_30_exact_official_ids_and_temporal_records_required(self):
        p,s=fixture();s['active_share']=self.as_block()
        r=validate_plan(p,s,checked_at=CHECK)
        self.assertEqual(r['active_share']['status'],'PASS_LOCAL_ESTIMATE')
        self.assertEqual(r['status'],'LOCAL_PREFLIGHT_PASS')
        self.assertEqual(r['submission_status'],'BLOCK_SUBMISSION')
        s['active_share']['reference_list']['etf_ids'][-1]='00900A'
        self.assertIn('ACTIVE_SHARE_COVERAGE',validate_plan(p,s,checked_at=CHECK)['blocking_codes'])
        s['active_share']=self.as_block();s['active_share']['etfs'][0]['known_at']='2026-10-27T09:00:00+08:00'
        self.assertIn('ACTIVE_SHARE_RECORD_UNKNOWN',validate_plan(p,s,checked_at=CHECK)['blocking_codes'])

    def test_two_day_threshold_and_missing_actual_history(self):
        p,s=fixture();s['active_share']=self.as_block()
        matching=sorted(TICKERS[:20])[:10]
        for etf in s['active_share']['etfs']:
            etf['holdings']=[dict(ticker=t,weight='.04') for t in matching]
        for prev in s['active_share']['previous_results']:prev['value']='.1'
        r=validate_plan(p,s,checked_at=CHECK)
        self.assertIn('ACTIVE_SHARE_PROJECTED_MINIMUM',r['blocking_codes'])
        self.assertIn('ACTIVE_SHARE_TWO_DAY_RISK',r['blocking_codes'])
        s['active_share']=self.as_block();s['active_share']['previous_results'].pop()
        self.assertIn('ACTIVE_SHARE_HISTORY_UNKNOWN',validate_plan(p,s,checked_at=CHECK)['blocking_codes'])


class CLITests(unittest.TestCase):
    def test_replay_outputs_block_and_hashes_and_preserves_existing_file(self):
        # json_safe would turn schema numbers into strings, so use official
        # standard JSON then keep synthetic state monetary fields as strings.
        p,s=fixture()
        def encode(value):return float(value) if isinstance(value,D) else value
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);plan=root/'D-Plan_TEAM_042_2026-10-27.json';state=root/'state.json';out=root/'report.json'
            plan.write_text(json.dumps(p,default=encode));state.write_text(json.dumps(s))
            args=['validate','--plan',str(plan),'--state',str(state),'--output',str(out),
                  '--mode','replay','--checked-at',CHECK]
            with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                self.assertEqual(main(args),2)
                report=json.loads(out.read_text());saved=out.read_bytes()
                self.assertIn('NON_LIVE_STATE',report['blocking_codes'])
                self.assertEqual(len(report['input_hashes']),6)
                self.assertEqual(main(args),3)
                self.assertEqual(saved,out.read_bytes())

    def test_live_cannot_inject_historical_receipt_clock(self):
        with tempfile.TemporaryDirectory() as td,redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as cm:
            main(['validate','--plan','missing','--state','missing','--output',str(Path(td)/'out'),
                  '--checked-at',CHECK])
        self.assertEqual(cm.exception.code,2)


if __name__=='__main__':unittest.main()
