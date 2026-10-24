"""Frozen tournament rules, causality, D-Plan validation and production replay harness."""
import json
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
from datetime import datetime
from unittest.mock import patch

import numpy as np
import pandas as pd

from competition.backtest import ExecutionConfig
from competition.data import load_market
from competition.rules import ROOT, load_rules
from production import active_share, dplan, engine, tournament, run_daily
from production.state import initial_book, settle_day, ticker_map
from production.validate import validate
from tests.test_causality import corrupt_after

RULES = load_rules()
CONFIG = json.loads((ROOT / 'production/strategy.json').read_text())
IDENTITY = dplan.Identity('TEAM_UNSET', 'tournament-test')


def recent_market():
    source = ROOT / 'production_runs/preflight_20261026'
    return load_market(yahoo_path=source / 'strategy_retest/corrected_nominal_daily.parquet',
                       official_path=source / 'official_combined.csv', calendar_path=source / 'calendar_v2.json')


def replay_window(market, start, end, forced_switch=False):
    """Engine -> validated D-Plan -> decoded orders -> production settlement, every session."""
    days = market.calendar[(market.calendar >= start) & (market.calendar <= end)]
    prev = market.calendar[market.position(days[0]) - 1]
    book, state, rows = initial_book(RULES, str(prev.date())), {}, []
    aux = tournament.load_aux()
    symbols = ticker_map()
    for i, day in enumerate(days):
        prev = market.calendar[market.position(day) - 1]
        inp = engine.DayInput(day, prev, market, book, CONFIG, RULES, i, len(days) - i,
                              official_holdings=dict(book.holdings), aux=aux, tournament_state=state,
                              rank=1 if forced_switch and i >= 12 else None)
        result = engine.run_day(inp)
        plan = dplan.build(inp, result, IDENTITY, datetime.now(dplan.TAIPEI))
        errors = validate(plan, holdings=book.holdings, close=result.prev_close, nav=result.nav, cash=book.cash,
                          rules=RULES, filename=dplan.filename(plan), allow_placeholder_team=True)
        orders = {symbols[o['ticker']]: o['shares'] * (1 if o['side'] == 'BUY' else -1) for o in plan['orders']}
        assert not errors, (day, errors)
        assert orders == result.plan.orders
        state = result.tournament['state']
        book, record, _, _ = settle_day(book, day, orders, market, RULES, ExecutionConfig(**CONFIG['execution']),
                                        engine.policy_of(CONFIG))
        assert not record['warning'], (day, record)
        rows.append(dict(date=str(day.date()), day_index=i, mode=result.mode,
                         tournament_mode=result.tournament['mode'], switched=result.tournament['switched'],
                         action=result.tournament['action'], orders=len(orders), validation_errors=errors,
                         plan_status=result.plan.status, reasons=result.reasons,
                         step_k=result.tournament.get('step_k'), step_total=result.tournament.get('step_total'),
                         step_attempts=result.tournament.get('step_attempts', []),
                         held_names=sorted(book.holdings), t2_complete=state.get('t2_complete'),
                         t2_names_reached=bool(state.get('t2_target')) and set(book.holdings) == set(state['t2_target']),
                         settlement_warnings=record['warning'],
                         nav=book.nav, cash=book.cash))
    terminal = (book.nav + book.receivable) / RULES.initial_capital - 1
    return dict(terminal_return=terminal, days=rows, state=state)


class SwitchTest(unittest.TestCase):
    def decide(self, index=12, rank=6, remaining=12, state=None):
        return tournament.switch_state(state or {}, '2026-10-01', index, remaining, rank)

    def test_frozen_switch_boundaries(self):
        for index, rank, remaining, mode in [(11, 6, 13, 'K2'), (12, 6, 12, 'T2'),
                                             (12, 7, 12, 'K2'), (12, 1, 3, 'K2'), (12, 1, 2, 'K2')]:
            with self.subTest(index=index, rank=rank, remaining=remaining):
                state, changed, _ = self.decide(index, rank, remaining)
                self.assertEqual(state['mode'], mode)
                self.assertEqual(changed, mode == 'T2')
        state, changed, reasons = self.decide(rank=None)
        self.assertEqual((state['mode'], changed, reasons), ('K2', False, ['RANK_MISSING']))
        locked, _, _ = self.decide(rank=6)
        after, changed, reasons = self.decide(rank=20, state=locked)
        self.assertEqual((after['mode'], after['switched_on'], changed, reasons),
                         ('T2', '2026-10-01', False, []))
        self.assertEqual(after['ranks']['2026-10-01'], 20)
        self.assertEqual(self.decide(index=11, rank=None)[2], [])
        self.assertEqual(self.decide(rank=None, state=locked)[2], [])

    def test_invalid_rank(self):
        for rank in (0, -1, True, 1.5):
            with self.assertRaises(ValueError):
                self.decide(rank=rank)


class TournamentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market, cls.aux = recent_market(), tournament.load_aux()
        cls.day, cls.prev = pd.Timestamp('2026-09-16'), pd.Timestamp('2026-09-15')

    def inp(self, mode='K2', **kwargs):
        return engine.DayInput(self.day, self.prev, kwargs.pop('market', self.market),
                               initial_book(RULES, str(self.prev.date())), CONFIG, RULES, 0, 24,
                               aux=kwargs.pop('aux', self.aux), tournament_state={'mode': mode}, **kwargs)

    def test_k2_and_t2_dplans_validate(self):
        for mode, count in [('K2', 22), ('T2', 25)]:
            inp = self.inp(mode)
            result = engine.run_day(inp)
            self.assertEqual(result.mode, engine.NORMAL, result.reasons)
            self.assertEqual(len(result.target), count)
            plan = dplan.build(inp, result, IDENTITY, datetime.now(dplan.TAIPEI))
            self.assertEqual(validate(plan, holdings={}, close=result.prev_close, nav=result.nav,
                                      cash=inp.book.cash, rules=RULES, filename=dplan.filename(plan),
                                      allow_placeholder_team=True), [])
            self.assertIn(mode, plan['market_view']['logic'])
            self.assertFalse(any('momentum_rank' in o['values'] for o in plan['observations']))
            self.assertTrue(any('trust_ratio' in o['values'] for o in plan['observations']))

    def test_k2_buffer_target_weights(self):
        result = engine.run_day(self.inp())
        self.assertEqual(result.mode, engine.NORMAL, result.reasons)
        self.assertEqual(len(result.target), 22)
        core = [w for w in result.target.values() if w >= .05]
        fillers = [w for w in result.target.values() if 0 < w < .05]
        self.assertEqual(len(core), 10)
        self.assertEqual(len(fillers), 12)
        for weight in core:
            self.assertAlmostEqual(weight, .085)
        for weight in fillers:
            self.assertAlmostEqual(weight, .10 / 12)
        self.assertAlmostEqual(sum(result.target.values()), .95)

    def test_future_market_flow_revenue_corruption(self):
        flows, revenue = self.aux.flows.copy(), self.aux.revenue.copy()
        future_f = pd.to_datetime(flows.date) > self.prev
        future_r = pd.PeriodIndex(revenue.month, freq='M') > tournament.expected_month(self.prev)
        self.assertTrue(future_f.any() and future_r.any())
        flows.loc[future_f, ['trust_net', 'foreign_net']] = 1e30
        revenue.loc[future_r, ['revenue', 'last_year_revenue']] = 1e30
        bad_aux = tournament.AuxData(flows, revenue)
        for mode in ('K2', 'T2'):
            base = engine.run_day(self.inp(mode))
            bad = engine.run_day(self.inp(mode, market=corrupt_after(self.market, self.prev), aux=bad_aux))
            self.assertEqual(base.mode, engine.NORMAL)
            self.assertEqual(bad.mode, engine.NORMAL)
            self.assertEqual(base.target, bad.target)
            self.assertEqual(base.plan.orders, bad.plan.orders)
            pd.testing.assert_series_equal(base.scores, bad.scores)

    def test_revenue_only_usable_on_day_11(self):
        cal = pd.DatetimeIndex(['2026-09-10', '2026-09-11'])
        _, rev = self.aux.panels(cal, self.market.symbols)
        self.assertEqual(str(rev['month'].iloc[0].dropna().max()), '2026-07')
        self.assertEqual(str(rev['month'].iloc[1].dropna().max()), '2026-08')
        self.assertEqual(str(tournament.expected_month(cal[0])), '2026-07')
        self.assertEqual(str(tournament.expected_month(cal[1])), '2026-08')

    def test_flows_cutoff_and_reported_missing_name_is_zero(self):
        view = self.market.asof(self.prev)
        trust, _ = self.aux.panels(view.close.index, view.symbols)
        self.assertEqual(trust.index[-1], self.prev)
        names = view.symbols[:3]
        ratio = tournament.flow_ratio(trust, view, names, 10)
        future = pd.DataFrame(1e30, index=[self.day], columns=trust.columns)
        pd.testing.assert_series_equal(ratio, tournament.flow_ratio(pd.concat([trust, future]), view, names, 10))
        dropped = self.aux.flows[~((self.aux.flows.date == str(self.prev.date())) &
                                  (self.aux.flows.ticker == '2330'))]
        panel, _ = tournament.AuxData(dropped, self.aux.revenue).panels(view.close.index, view.symbols)
        self.assertEqual(panel.loc[self.prev, '2330.TW'], 0.)

    def test_no_active_share_strategy_repair(self):
        with patch.object(engine, '_check_active_share', return_value={'status': active_share.INVALID}), \
                patch.object(active_share, 'repair', side_effect=AssertionError('must not repair')):
            inp = self.inp()
            with self.assertRaisesRegex(RuntimeError, 'ACTIVE_SHARE_INVALID'):
                engine.normal_path(inp, inp.book, inp.book.nav, self.market.close.loc[self.prev])

    def test_freeze_holds_saved_target(self):
        for mode in ('K2', 'T2'):
            inp = self.inp(mode)
            first = engine.run_day(inp)
            book, _, _, _ = settle_day(inp.book, self.day, first.plan.orders, self.market, RULES,
                                        ExecutionConfig(**CONFIG['execution']), engine.policy_of(CONFIG))
            later = replace(inp, trade_date=pd.Timestamp('2026-09-17'), prev_date=self.day,
                            book=book, day_index=1, sessions_remaining=3,
                            tournament_state=first.tournament['state'])
            result = engine.run_day(later)
            self.assertEqual(result.mode, engine.NORMAL)
            self.assertEqual(result.plan.orders, {})
            self.assertEqual(result.tournament['action'], 'hold')

    def test_k2_saved_target_retry_after_hold(self):
        first = engine.run_day(self.inp())
        saved = dict(first.target)
        weights = dict(saved)
        heavy = next(s for s, w in weights.items() if w >= .05)
        weights[heavy] = .099
        state = SimpleNamespace(weights=weights, cash=50e6, nav=1e9, sessions_remaining=20)
        proposed = {'mode': 'K2', 'k2_target': saved, 'k2_month': '2026-08'}
        target, _, info = tournament.TournamentStrategy(RULES, self.aux).decide(
            self.market.asof(self.day), state, tournament_state=proposed)
        self.assertEqual(info['action'], 'repair')
        self.assertEqual(target, saved)
        self.assertEqual(proposed['k2_target'], saved)

    def test_k2_target_survives_planner_hold(self):
        inp = self.inp()
        with patch.object(engine.planner, 'plan', return_value=engine._hold_plan(inp.book)) as mocked:
            mocked.return_value.status = 'HOLD_FALLBACK'
            result = engine.run_day(inp)
        self.assertEqual(result.mode, engine.HOLD)
        self.assertEqual(result.tournament['state']['k2_target'], result.target)
        self.assertEqual(result.tournament['state']['k2_month'], '2026-08')
        retry = engine.run_day(replace(inp, tournament_state=result.tournament['state']))
        self.assertEqual(retry.tournament['action'], 'repair')
        self.assertEqual(retry.target, result.target)

    def switch_input(self):
        inp = self.inp()
        first = engine.run_day(inp)
        book, _, _, _ = settle_day(inp.book, self.day, first.plan.orders, self.market, RULES,
                                    ExecutionConfig(**CONFIG['execution']), engine.policy_of(CONFIG))
        return replace(inp, trade_date=pd.Timestamp('2026-09-17'), prev_date=self.day, book=book,
                       tournament_state=first.tournament['state'], day_index=12, sessions_remaining=12, rank=1)

    def test_staged_switch_largest_accepted_k_and_count(self):
        inp = self.switch_input()
        self.assertEqual(len(inp.book.holdings), 22)
        actual = engine.run_day(inp)
        saved = actual.tournament['state']
        current = engine._weights(inp.book, inp.book.nav)
        candidates = list(tournament.TournamentStrategy(RULES, self.aux).switch_candidates(
            current, saved['t2_target'], actual.scores))
        self.assertGreater(len(candidates), 1)
        self.assertEqual(candidates[0][2], saved['t2_target'])
        for _, _, candidate in candidates:
            self.assertTrue(20 <= len(candidate) <= 30)
            self.assertLessEqual(sum(candidate.values()), .95 + 1e-9)
        calls = []

        def staged_plan(target, *args):
            calls.append(target)
            plan = engine._hold_plan(inp.book)
            plan.status = 'HOLD_FALLBACK' if len(calls) == 1 else 'REPAIRED'
            return plan

        with patch.object(engine.planner, 'plan', side_effect=staged_plan):
            result = engine.run_day(inp)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.tournament['step_k'], candidates[1][0])
        self.assertEqual(result.target, candidates[1][2])
        self.assertIn(f'SWITCH_STEP:{candidates[1][0]}/{candidates[1][1]}', result.reasons)

    def test_staged_switch_infeasible_and_last_three_freeze(self):
        inp = self.switch_input()
        held = engine._hold_plan(inp.book)
        held.status = 'HOLD_FALLBACK'
        with patch.object(engine.planner, 'plan', return_value=held):
            result = engine.run_day(inp)
        self.assertEqual(result.mode, engine.HOLD)
        self.assertIn('SWITCH_STEP_INFEASIBLE', result.reasons)
        self.assertFalse(result.tournament['state']['t2_complete'])
        plan = dplan.build(inp, result, IDENTITY, datetime.now(dplan.TAIPEI))
        self.assertEqual(validate(plan, holdings=inp.book.holdings, close=result.prev_close, nav=result.nav,
                                  cash=inp.book.cash, rules=RULES, filename=dplan.filename(plan),
                                  allow_placeholder_team=True), [])
        for remaining in (3, 2, 1):
            frozen = engine.run_day(replace(inp, sessions_remaining=remaining, rank=20,
                                           tournament_state=result.tournament['state']))
            self.assertEqual(frozen.plan.orders, {})
            self.assertEqual(frozen.tournament['action'], 'hold')

    def test_t2_marks_complete_when_held_names_match(self):
        first = engine.run_day(self.inp('T2'))
        saved = first.tournament['state']
        weights = dict(saved['t2_target'])
        state = SimpleNamespace(weights=weights, cash=50e6, nav=1e9, sessions_remaining=12)
        target, _, info = tournament.TournamentStrategy(RULES, self.aux).decide(
            self.market.asof(self.prev), state, 'T2', tournament_state=saved)
        self.assertTrue(saved['t2_complete'])
        self.assertEqual(info['action'], 'hold')
        self.assertEqual(target, weights)

    def test_hold_dplan_writes_saved_target_state(self):
        inp = self.switch_input()
        result = engine.run_day(inp)
        result.mode, result.plan = engine.HOLD, engine._hold_plan(inp.book)
        result.target = engine._weights(inp.book, inp.book.nav)
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(run_daily, 'load_market_for', return_value=(self.market, self.day, [], {})), \
                patch.object(tournament, 'refresh', return_value=(self.aux, [], [])), \
                patch.object(run_daily, 'settled_book', return_value=(inp.book, [])), \
                patch.object(engine, 'run_day', return_value=result):
            audit = run_daily.run('2026-10-26', offline=True, runs_dir=tmp)
            self.assertEqual(audit['validation_errors'], [])
            self.assertEqual(audit['mode'], engine.HOLD)
            saved = json.loads((Path(tmp) / '2026-10-26/state/tournament.json').read_text())
            self.assertEqual(saved, result.tournament['state'])
            self.assertTrue(saved['t2_target'])

    def test_offline_state_isolated_and_written_only_with_dplan(self):
        for invalid in (False, True):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                live = root / 'state/tournament.json'
                live.parent.mkdir()
                original = {'mode': 'T2', 'switched_on': '2026-10-01', 'ranks': {'2026-10-01': 6}}
                live.write_text(json.dumps(original))
                with patch.object(run_daily, 'load_market_for', return_value=(self.market, pd.Timestamp('2026-10-22'), [], {})), \
                        patch.object(tournament, 'refresh', return_value=(self.aux, [], [])):
                    if invalid:
                        with patch.object(run_daily, 'build_and_validate', return_value=({}, ['fixture invalid'])):
                            audit = run_daily.run('2026-10-26', offline=True, runs_dir=tmp, rank=20)
                    else:
                        audit = run_daily.run('2026-10-26', offline=True, runs_dir=tmp, rank=20)
                self.assertEqual(json.loads(live.read_text()), original)
                saved = root / '2026-10-26/state/tournament.json'
                self.assertEqual(saved.exists(), not invalid)
                if not invalid:
                    state = json.loads(saved.read_text())
                    self.assertEqual((state['mode'], state['switched_on'], state['ranks']['2026-10-26']), ('T2', '2026-10-01', 20))
                    self.assertEqual(audit['validation_errors'], [])
                    self.assertEqual(audit['status'], run_daily.SIMULATION)

    def test_aux_missing_data_gate_and_offline_no_network(self):
        with patch('legacy.ml.hybrid.sources._get', side_effect=AssertionError('offline network')):
            _, reasons, report = tournament.refresh(self.market.calendar, pd.Timestamp('2026-10-23'), offline=True)
        self.assertEqual(report, [])
        self.assertTrue(any(r.startswith('FLOWS_T-1_MISSING') for r in reasons))
        aux = tournament.AuxData(self.aux.flows, self.aux.revenue[self.aux.revenue.month != '2026-09'])
        self.assertIn('REVENUE_MONTH_MISSING:2026-09', tournament.aux_gate(aux, pd.Timestamp('2026-10-22')))


if __name__ == '__main__':
    unittest.main()
