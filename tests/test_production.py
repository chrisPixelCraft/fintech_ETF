"""Production pipeline: state input, Active Share, every engine mode, D-Plan validation."""
import copy
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from competition.data import load_market
from competition.rules import ROOT, load_rules
from production import active_share, dplan, engine
from production.state import SettledBook, initial_book, load_official_holdings, reconcile, settle_day
from production.validate import validate

RULES = load_rules()
STRATEGY = json.loads((ROOT / 'production/strategy.json').read_text())
IDENTITY = dplan.Identity('TEAM_UNSET', 'test')
COLD = tuple(json.loads((ROOT / 'production/cold_start.json').read_text())['symbols'])


class StateTest(unittest.TestCase):
    def test_holdings_file_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            j, c, rows = Path(tmp) / 'h.json', Path(tmp) / 'h.csv', Path(tmp) / 'r.json'
            j.write_text(json.dumps({'2330': 2000, '6488': '1,000'}))
            c.write_text('股票代號,持有股數\n2330,2000\n6488,1000\n')
            rows.write_text(json.dumps({'holdings': [{'stock_id': '2330.TW', 'quantity': 2000},
                                                     {'stock_id': '6488', 'quantity': 1000}]}))
            expected = {'2330.TW': 2000., '6488.TWO': 1000.}
            for path in (j, c, rows):
                self.assertEqual(load_official_holdings(path), expected)
            bad = Path(tmp) / 'bad.json'
            bad.write_text(json.dumps({'9999': 1000}))
            with self.assertRaises(ValueError):
                load_official_holdings(bad)

    def test_reconcile(self):
        self.assertTrue(reconcile({'2330.TW': 1000.}, {'2330.TW': 1000.}).ok)
        r = reconcile({'2330.TW': 1000., '2317.TW': 1000.}, {'2330.TW': 2000., '2454.TW': 1000.})
        self.assertFalse(r.ok)
        self.assertEqual((set(r.only_official), set(r.only_local), set(r.different)),
                         ({'2317.TW'}, {'2454.TW'}, {'2330.TW'}))


class ActiveShareTest(unittest.TestCase):
    def etf(self, weights):
        return active_share.EtfTop10('00981A', '2026-10-20', weights)

    def test_formula_and_normalisation(self):
        portfolio = {f'{1000 + i}': .04 for i in range(25)}
        disjoint = self.etf({f'{2000 + i}': .05 for i in range(10)})
        self.assertAlmostEqual(active_share.active_share(portfolio, disjoint), .5 * (.4 + .5))   # raw < normalised
        same = self.etf({f'{1000 + i}': .04 for i in range(10)})
        self.assertAlmostEqual(active_share.active_share(portfolio, same), 0.)

    def test_near_ties_resolve_toward_overlap(self):
        portfolio = {f'{1000 + i}': .04 for i in range(25)}
        etf = self.etf({f'{1000 + i}': .05 for i in range(15, 25)})           # names ranked 16-25 by ticker
        top = active_share.worst_case_top10(portfolio, etf.weights)
        self.assertEqual(set(top), set(etf.weights))                            # all within the tie band

    def test_status(self):
        w = {f'{1000 + i}': .04 for i in range(25)}
        self.assertEqual(active_share.check(w, None, ['00981A'], '2026-10-27').status, active_share.UNVERIFIED)
        overlap = {'00981A': self.etf({f'{1000 + i}': .04 for i in range(10)})}
        self.assertEqual(active_share.check(w, overlap, ['00981A'], '2026-10-27').status, active_share.INVALID)
        far = {'00981A': self.etf({f'{2000 + i}': .05 for i in range(10)})}
        self.assertEqual(active_share.check(w, far, ['00981A'], '2026-10-27').status, active_share.PASS)
        self.assertEqual(active_share.check(w, far, ['00981A', '00982A'], '2026-10-27').status,
                         active_share.UNVERIFIED)                              # a required ETF is missing
        self.assertEqual(active_share.check(w, far, ['00981A'], '2026-11-20').status, active_share.UNVERIFIED)

    def test_repair_drops_overlap_and_keeps_rules(self):
        scores = pd.Series({f'{1000 + i}.TW': float(100 - i) for i in range(40)})
        etfs = {'00981A': self.etf({f'{1000 + i}': .05 for i in range(10)})}

        def build(sc):
            top = sc.sort_values(ascending=False).index[:25]
            return {s: .038 for s in top}

        weights, dropped = active_share.repair(scores, build, etfs, lambda s: s.split('.')[0])
        tickers = {s.split('.')[0]: w for s, w in weights.items()}
        self.assertGreaterEqual(active_share.active_share(tickers, etfs['00981A']), active_share.INTERNAL_MIN)
        self.assertEqual(len(weights), 25)
        self.assertTrue(dropped and all(s.split('.')[0] in etfs['00981A'].weights for s in dropped))


class EngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = load_market()
        cls.day = pd.Timestamp('2026-06-01')
        cls.prev = cls.market.calendar[cls.market.position(cls.day) - 1]

    def inp(self, book=None, market=None, **extra):
        book = book or initial_book(RULES, str(self.prev.date()))
        return engine.DayInput(trade_date=self.day, prev_date=self.prev, market=market or self.market, book=book,
                               strategy=STRATEGY, rules=RULES, day_index=extra.pop('day_index', 0),
                               sessions_remaining=extra.pop('sessions_remaining', 24), cold_start=COLD, **extra)

    def assert_valid(self, inp, result):
        plan = dplan.build(inp, result, IDENTITY, datetime.now(dplan.TAIPEI))
        errors = validate(plan, holdings=result.holdings, close=result.prev_close, nav=result.nav,
                          cash=inp.book.cash, rules=RULES, filename=dplan.filename(plan), allow_placeholder_team=True)
        self.assertEqual(errors, [], result.mode)
        return plan

    def held_book(self):
        """A settled 25-name book from the normal Day 1."""
        inp = self.inp()
        first = engine.run_day(inp)
        book, _, _, _ = settle_day(inp.book, self.day, first.plan.orders, self.market, RULES)
        return book

    def test_normal_day_one(self):
        inp = self.inp()
        result = engine.run_day(inp)
        self.assertEqual(result.mode, engine.NORMAL)
        self.assertEqual(len(result.plan.orders), 25)
        self.assertEqual(result.active_share['status'], active_share.UNVERIFIED)
        plan = self.assert_valid(inp, result)
        self.assertTrue(all(d['action'] == 'BUY' for d in plan['decisions']))
        self.assertEqual(plan['market_view']['posture']['net_exposure_intent'], 'increase')

    def test_broken_data_on_day_one_uses_cold_start(self):
        close_frame = self.market.close.copy()
        close_frame.loc[self.prev] = np.nan                                  # T-1 closes missing
        broken = self.market.with_frames(close=close_frame)
        inp = self.inp(market=broken)
        close = self.market.close.loc[self.prev]
        result = engine.run_day(inp)
        self.assertEqual(result.mode, engine.EMERGENCY)                      # no price to size anything
        self.assertTrue(any(r.startswith('DATA_GATE') for r in result.reasons))
        # with prices but a failing strategy the frozen list is bought
        ok = engine.fallback_path(self.inp(), self.inp().book, RULES.initial_capital,
                                  close, ['NORMAL_PATH_FAILED'])
        self.assertEqual(ok.mode, engine.COLD_START)
        self.assertEqual(set(ok.plan.orders), set(COLD))
        self.assert_valid(self.inp(), ok)

    def test_state_mismatch_holds(self):
        book = self.held_book()
        later = self.market.calendar[self.market.position(self.day) + 1]
        official = dict(book.holdings)
        official.pop(sorted(official)[0])
        inp = engine.DayInput(trade_date=later, prev_date=self.day, market=self.market, book=book, strategy=STRATEGY,
                              rules=RULES, day_index=1, sessions_remaining=23, official_holdings=official,
                              cold_start=COLD)
        result = engine.run_day(inp)
        self.assertEqual(result.mode, engine.HOLD)
        self.assertIn('STATE_MISMATCH', result.reasons)
        self.assertEqual(result.plan.orders, {})
        self.assertEqual(result.holdings, official)                          # the organizer's book is used
        self.assert_valid(inp, result)

    def test_missing_official_state_when_required(self):
        book = self.held_book()
        later = self.market.calendar[self.market.position(self.day) + 1]
        inp = engine.DayInput(trade_date=later, prev_date=self.day, market=self.market, book=book, strategy=STRATEGY,
                              rules=RULES, day_index=1, sessions_remaining=23, require_official=True, cold_start=COLD)
        result = engine.run_day(inp)
        self.assertEqual((result.mode, result.reasons[0]), (engine.HOLD, 'OFFICIAL_STATE_MISSING'))

    def test_noncompliant_book_gets_minimum_repair(self):
        book = self.held_book()
        keep = dict(sorted(book.holdings.items())[:15])                      # 15 names: count violation
        small = SettledBook(book.date, keep, book.cash, book.receivable, book.marks, {}, 0)
        later = self.market.calendar[self.market.position(self.day) + 1]
        inp = engine.DayInput(trade_date=later, prev_date=self.day, market=self.market, book=small, strategy=STRATEGY,
                              rules=RULES, day_index=1, sessions_remaining=23, cold_start=COLD,
                              require_official=True)                         # forces the fallback path
        result = engine.run_day(inp)
        self.assertEqual(result.mode, engine.REPAIR)
        after = {s: small.holdings.get(s, 0) + result.plan.orders.get(s, 0)
                 for s in set(small.holdings) | set(result.plan.orders)}
        self.assertGreaterEqual(sum(q > 0 for q in after.values()), RULES.min_positions)
        self.assert_valid(inp, result)

    def test_emergency_without_cold_start_list(self):
        inp = engine.DayInput(trade_date=self.day, prev_date=self.prev, market=self.market,
                              book=initial_book(RULES, str(self.prev.date())), strategy=STRATEGY, rules=RULES,
                              day_index=0, sessions_remaining=24, require_official=True)
        self.assertEqual(engine.run_day(inp).mode, engine.EMERGENCY)

    def test_market_data_down(self):
        """No T-1 row: a held book still submits a HOLD D-Plan; Day 1 cannot size and escalates."""
        last = self.market.calendar[-1]
        trade, prev = last + pd.offsets.BDay(2), last + pd.offsets.BDay(1)
        d1, p1 = self.market.calendar[-3], self.market.calendar[-4]
        first = engine.run_day(engine.DayInput(d1, p1, self.market, initial_book(RULES, str(p1.date())), STRATEGY,
                                               RULES, 0, 24))
        book, _, _, _ = settle_day(initial_book(RULES, str(p1.date())), d1, first.plan.orders, self.market, RULES)
        held = engine.DayInput(trade, prev, self.market, book, STRATEGY, RULES, 2, 22, cold_start=COLD,
                               degraded=('MARKET_DATA_FAILED',))
        result = engine.run_day(held)
        self.assertEqual(result.mode, engine.HOLD)
        self.assert_valid(held, result)
        cold = engine.DayInput(trade, prev, self.market, initial_book(RULES, str(last.date())), STRATEGY, RULES, 0, 24,
                               cold_start=COLD, degraded=('MARKET_DATA_FAILED',))
        self.assertEqual(engine.run_day(cold).mode, engine.EMERGENCY)

    def test_active_share_repair_in_normal_path(self):
        normal = engine.run_day(self.inp())
        top = sorted(normal.target, key=lambda s: (-normal.target[s], s))[:10]
        etfs = {'00981A': active_share.EtfTop10('00981A', str(self.day.date()),
                                                {s.split('.')[0]: .05 for s in top})}
        inp = self.inp(etfs=etfs, required_etfs=('00981A',))
        result = engine.run_day(inp)
        self.assertEqual(result.mode, engine.NORMAL)
        self.assertIn(result.active_share['status'], (active_share.PASS, active_share.CAUTION))
        self.assertGreaterEqual(result.active_share['minimum'], active_share.INTERNAL_MIN)
        self.assertTrue(any(r.startswith('ACTIVE_SHARE_REPAIR') for r in result.reasons))


class ValidatorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        market = load_market()
        day = pd.Timestamp('2026-06-01')
        prev = market.calendar[market.position(day) - 1]
        inp = engine.DayInput(trade_date=day, prev_date=prev, market=market, book=initial_book(RULES, str(prev.date())),
                              strategy=STRATEGY, rules=RULES, day_index=0, sessions_remaining=24, cold_start=COLD)
        cls.result = engine.run_day(inp)
        cls.plan = dplan.build(inp, cls.result, IDENTITY, datetime.now(dplan.TAIPEI))
        cls.context = dict(holdings={}, close=cls.result.prev_close, nav=cls.result.nav, cash=RULES.initial_capital,
                           rules=RULES, allow_placeholder_team=True)

    def errors(self, mutate):
        plan = copy.deepcopy(self.plan)
        mutate(plan)
        return validate(plan, **self.context)

    def test_clean_plan(self):
        self.assertEqual(validate(self.plan, **self.context), [])

    def test_detects_each_problem(self):
        cases = {
            'C2': lambda p: p['orders'][0].__setitem__('shares', p['orders'][0]['shares'] + 1000),
            'C1': lambda p: p['decisions'][0].__setitem__('inference_refs', ['I999']),
            'ID_SEQUENCE': lambda p: p['inferences'][1].__setitem__('inf_id', 'I7'),
            'C12': lambda p: p['market_view']['posture'].__setitem__('net_exposure_intent', 'reduce'),
            'ACTION': lambda p: p['decisions'][0].__setitem__('action', 'ADD'),
            'SCHEMA': lambda p: p.__setitem__('extra', 1),
            'ORDER_PAIRING': lambda p: p['orders'].pop(),
            'TEAM_ID': lambda p: None,
        }
        for code, mutate in cases.items():
            with self.subTest(code=code):
                context = dict(self.context, allow_placeholder_team=code != 'TEAM_ID')
                plan = copy.deepcopy(self.plan)
                mutate(plan)
                self.assertTrue(any(e.startswith(code) for e in validate(plan, **context)), code)

    def test_coverage_and_oversell(self):
        held = {s: 1000. for s in list(self.result.plan.orders)[:3]}
        errors = validate(self.plan, **dict(self.context, holdings=held))
        self.assertTrue(any(e.startswith('C2') or e.startswith('ACTION') for e in errors))
        plan = copy.deepcopy(self.plan)
        plan['orders'][0]['side'] = 'SELL'
        self.assertTrue(any(e.startswith('C13') or e.startswith('C2') for e in validate(plan, **self.context)))


if __name__ == '__main__':
    unittest.main()
