"""Live Active Share policy: isolated market/ETF fixtures, no network."""
import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from competition.data import load_market
from competition.rules import ROOT, load_rules
from production import active_share, engine, etf_holdings, run_daily
from production.state import initial_book


class LiveActiveShareGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = load_market()
        cls.trade = pd.Timestamp('2026-06-01')
        cls.prev = cls.market.calendar[cls.market.position(cls.trade) - 1]
        cls.required = etf_holdings.required()

    def run_fixture(self, *, count=10, missing=False, age=0, minimum=None, offline=False,
                    supplied=False, fallback_minimum=None, validation_error=False):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            folder = Path(tmp)
            rows = [
                        dict(etf=e, as_of=str((self.trade - pd.Timedelta(days=age)).date()),
                             ticker=str(8000 + i), weight=.05)
                        for e in (self.required[1:] if missing else self.required) for i in range(count)]
            frame = pd.DataFrame(rows)
            source = folder / 'holdings.csv'
            frame.to_csv(source, index=False)
            book = initial_book(load_rules(), str(self.prev.date()))
            stack.enter_context(patch.object(run_daily, 'STRATEGY', ROOT / 'production/strategy_mom20.json'))
            stack.enter_context(patch.object(run_daily, 'code_version', return_value='fixture'))
            stack.enter_context(patch.object(run_daily, 'load_market_for', return_value=(
                self.market, self.prev, [], {})))
            stack.enter_context(patch.object(run_daily, 'load_datapack', return_value=book))
            stack.enter_context(patch.object(run_daily, 'settled_book', return_value=(book, [])))
            fetch = stack.enter_context(patch.object(etf_holdings, 'fetch_all', return_value=(
                frame, {self.required[0]: 'fixture fetch failed'} if missing else {})))
            if minimum is not None:
                stack.enter_context(patch.object(active_share, 'active_share', return_value=minimum))
            if fallback_minimum is not None:
                captured = {}

                def changed_fallback(*args):
                    result = copy.deepcopy(captured['result'])
                    result.mode = engine.HOLD
                    result.active_share['minimum'] = fallback_minimum
                    return result

                stack.enter_context(patch.object(run_daily, 'build_and_validate',
                                                side_effect=[({}, ['fixture initial validation failure']), ({}, [])]))
                # Preserve the real planner/AS result, then emulate an existing held account.
                decide = engine.run_day

                def held_input(inp):
                    result = decide(inp)
                    book.holdings.update(result.plan.target_shares)
                    book.marks.update(result.prev_close.to_dict())
                    captured['result'] = result
                    return result

                stack.enter_context(patch.object(engine, 'run_day', side_effect=held_input))
                stack.enter_context(patch.object(engine, 'fallback_path', side_effect=changed_fallback))
            elif validation_error:
                stack.enter_context(patch.object(run_daily, 'build_and_validate',
                                                return_value=({}, ['fixture validator rejection'])))
            audit = run_daily.run(str(self.trade.date()), offline=offline, runs_dir=tmp,
                                  datapack=None if offline else 'fixture.json',
                                  etf_holdings=str(source) if supplied or offline else None)
            plans = list(folder.rglob('D-Plan_*.json'))
            self.assertFalse(any('PIPELINE_ERROR' in r for r in audit['reasons']), audit)
            if supplied or offline:
                fetch.assert_not_called()
            else:
                fetch.assert_called_once_with(self.required)
            saved = json.loads((folder / str(self.trade.date()) / 'audit.json').read_text())
            self.assertEqual(saved['status'], audit['status'])
            return audit, bool(plans)

    def assert_blocked(self, audit, written, condition):
        self.assertEqual(audit['status'], run_daily.BLOCKED)
        self.assertEqual(audit['active_share_gate'], 'BLOCKED')
        self.assertFalse(written)
        self.assertTrue(any(condition in r for r in audit['active_share_gate_failures']), audit)
        self.assertTrue(any('LIVE_ACTIVE_SHARE_BLOCKED' in r for r in audit['reasons']))

    def test_live_top_ten_writes_valid_dplan(self):
        audit, written = self.run_fixture()
        self.assertEqual(audit['status'], run_daily.READY)
        self.assertTrue(written)
        self.assertEqual(audit['validation_errors'], [])
        self.assertEqual(audit['active_share_gate'], 'HEURISTIC_TOP10_PASS')
        self.assertIn('ACTIVE_SHARE_METHOD_UNCONFIRMED', audit['reasons'])
        self.assertIn(f"ACTIVE_SHARE_HEURISTIC_PASS:top10:min={audit['active_share']['minimum']:.4f}:closest={audit['active_share']['closest_etf']}", audit['reasons'])
        self.assertEqual(audit['active_share']['evaluation_basis'], 'lot_rounded_book_at_previous_close_after_costs')

    def test_minimum_point_two_five_blocks(self):
        self.assert_blocked(*self.run_fixture(minimum=.25), 'minimum=0.25')

    def test_missing_fetch_blocks(self):
        audit, written = self.run_fixture(missing=True)
        self.assert_blocked(audit, written, 'missing=')
        self.assertIn(self.required[0], audit['etf_fetch']['failures'])

    def test_eight_day_old_data_blocks(self):
        self.assert_blocked(*self.run_fixture(age=8), 'stale=')

    def test_nine_holdings_blocks(self):
        self.assert_blocked(*self.run_fixture(count=9), 'holdings_below_10=')

    def test_offline_remains_simulation(self):
        audit, written = self.run_fixture(offline=True, minimum=.25)
        self.assertEqual(audit['status'], run_daily.SIMULATION)
        self.assertTrue(written)
        self.assertNotIn('active_share_gate', audit)

    def test_supplied_complete_top_fifteen_works(self):
        audit, written = self.run_fixture(count=15, supplied=True)
        self.assertEqual(audit['status'], run_daily.READY)
        self.assertTrue(written)

    def test_final_fallback_report_blocks(self):
        audit, written = self.run_fixture(fallback_minimum=.25)
        self.assert_blocked(audit, written, 'minimum=0.25')
        self.assertEqual(audit['active_share']['minimum'], .25)
        self.assertIn('LIVE_ACTIVE_SHARE_BLOCKED_AFTER_VALIDATION', audit['validation_errors'])
        self.assertFalse(any('HEURISTIC_PASS' in r for r in audit['reasons']))

    def test_validator_rejection_still_blocks(self):
        audit, written = self.run_fixture(validation_error=True)
        self.assertEqual(audit['status'], run_daily.BLOCKED)
        self.assertFalse(written)
        self.assertIn('fixture validator rejection', audit['validation_errors'])

    def test_boundary_invalid_and_official_policy(self):
        settings = json.loads(run_daily.SETTINGS.read_text())
        etfs = {e: active_share.EtfTop10(e, '2026-06-01', {str(i): .05 for i in range(10)})
                for e in self.required}
        report = active_share.check({'9999': .6}, etfs, self.required, '2026-06-01').as_dict()
        report['minimum'] = .30
        self.assertEqual(run_daily.live_active_share_gate(report, etfs, settings), ('HEURISTIC_TOP10_PASS', []))
        for value in (.29999, None, float('nan'), float('inf')):
            with self.subTest(value=value):
                report['minimum'] = value
                self.assertEqual(run_daily.live_active_share_gate(report, etfs, settings)[0], 'BLOCKED')
        report.update(minimum=.30, invalid_data=[self.required[0]])
        self.assertEqual(run_daily.live_active_share_gate(report, etfs, settings)[0], 'BLOCKED')
        for status in (active_share.PASS, active_share.CAUTION):
            self.assertEqual(run_daily.live_active_share_gate(
                dict(status=status, official_method_confirmed=True), {}, settings), ('OFFICIAL_PASS', []))


if __name__ == '__main__':
    unittest.main()
