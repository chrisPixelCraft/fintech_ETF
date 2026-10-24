"""Official account truth: receivables, missing settlement, and submission gates."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import pandas as pd

from production.state import load_datapack, load_official_holdings
from production import run_daily


class DataPackTest(unittest.TestCase):
    def setUp(self):
        strategy = patch.object(run_daily, 'STRATEGY', Path(__file__).parents[1] / 'production/strategy_mom20.json')
        strategy.start()
        self.addCleanup(strategy.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'datapack.json'
        self.pack = dict(schema_version='3.0', doc_type='DataPack', team_id='TEAM_042',
                         trade_date='2026-10-28', as_of_date='2026-10-27', missing=[],
                         account=dict(cash=80000, nav_tradable=100000, aum=150000,
                                      receivable_cash=50000, holdings_count=1),
                         holdings=[dict(code='2330', shares=1000, receivable_shares=2000, close_price=20),
                                   dict(code='6669', shares=0, receivable_shares=3000, close_price=100)])

    def load(self, pack=None, **kwargs):
        self.path.write_text(json.dumps(self.pack if pack is None else pack))
        return load_datapack(self.path, team_id='TEAM_042', trade_date='2026-10-28', **kwargs)

    def test_account_denominator_excludes_all_receivables(self):
        book = self.load(expected_as_of='2026-10-27')
        self.assertEqual(book.holdings, {'2330.TW': 1000})
        self.assertEqual((book.cash, book.nav, book.receivable), (80000, 100000, 50000))
        self.assertEqual(book.cash / book.nav, .8)
        self.assertEqual(load_official_holdings(self.path), {'2330.TW': 1000})

    def test_first_day_is_valid_but_missing_settlement_is_not_empty(self):
        pack = copy.deepcopy(self.pack)
        pack.update(as_of_date=None, holdings=[])
        pack['account'].update(cash=100000, holdings_count=0)
        self.assertEqual(self.load(pack).holdings, {})
        pack.update(account=None, holdings=None, missing=['prev_settlement'])
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            self.load(pack)
        with self.assertRaises(ValueError):
            load_official_holdings(self.path)

    def test_wrong_identity_date_and_inconsistent_account_are_rejected(self):
        cases = [('team_id', 'TEAM_043'), ('trade_date', '2026-10-29'),
                 ('as_of_date', '2026-10-28'), ('holdings', None), ('account', None),
                 ('missing', ['prev_close'])]
        for key, value in cases:
            with self.subTest(key=key):
                pack = copy.deepcopy(self.pack)
                pack[key] = value
                with self.assertRaises(ValueError):
                    self.load(pack)
        for field, value in [('nav_tradable', 150000), ('holdings_count', 2), ('cash', float('nan'))]:
            pack = copy.deepcopy(self.pack)
            pack['account'][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.load(pack)
        with self.assertRaisesRegex(ValueError, 'as_of_date'):
            self.load(expected_as_of='2026-10-26')

    def test_invalid_shares_and_held_missing_price_rejected(self):
        for field, value in [('shares', 1000.5), ('shares', -1), ('shares', float('inf')),
                             ('close_price', None), ('close_price', 0)]:
            pack = copy.deepcopy(self.pack)
            pack['holdings'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.load(pack)

    def test_live_run_without_datapack_stops_before_shadow_ledger(self):
        with patch.object(run_daily, 'load_market_for', return_value=(SimpleNamespace(calendar=pd.DatetimeIndex(['2026-10-27'])), pd.Timestamp('2026-10-27'), [], {})), \
                patch.object(run_daily, 'settled_book') as shadow:
            result = run_daily.run('2026-10-28', runs_dir=self.tmp.name)
        self.assertEqual(result['status'], run_daily.BLOCKED)
        self.assertTrue(any('OFFICIAL_DATAPACK_REQUIRED' in r for r in result['reasons']))
        shadow.assert_not_called()
        self.assertFalse(list(Path(self.tmp.name).rglob('D-Plan_*.json')))

    def test_failed_rerun_archives_only_previous_generated_plan(self):
        folder = Path(self.tmp.name) / '2026-10-28'
        folder.mkdir()
        old = folder / 'D-Plan_TEAM_042_2026-10-28.json'
        old.write_text('{}')
        unrelated = folder / 'input.json'
        unrelated.write_text('unchanged')
        (folder / 'audit.json').write_text(json.dumps({'dplan': str(old)}))
        with patch.object(run_daily, 'load_market_for', return_value=(SimpleNamespace(calendar=pd.DatetimeIndex(['2026-10-27'])), pd.Timestamp('2026-10-27'), [], {})):
            audit = run_daily.run('2026-10-28', runs_dir=self.tmp.name)
        self.assertEqual(audit['status'], run_daily.BLOCKED)
        self.assertFalse(old.exists())
        self.assertEqual(len(list((folder / 'superseded').rglob(old.name))), 1)
        self.assertEqual(unrelated.read_text(), 'unchanged')

    def test_holiday_contest_start_accepts_initial_next_session(self):
        from production import active_share, engine
        self.pack.update(as_of_date=None, holdings=[], trade_date='2026-10-27')
        self.pack['account'].update(cash=100000, holdings_count=0)
        settings = json.loads(run_daily.SETTINGS.read_text())
        self.pack['team_id'] = settings['team_id']
        self.path.write_text(json.dumps(self.pack))
        market = SimpleNamespace(calendar=pd.DatetimeIndex(['2026-10-23']), close=pd.DataFrame())
        result = SimpleNamespace(mode=engine.NORMAL, reasons=[], active_share={'status': active_share.UNVERIFIED},
                                 reconciliation='MATCH', data_gate=[], plan=SimpleNamespace(status='OK', audit={}),
                                 submittable=True)
        with patch.object(run_daily, 'load_market_for', return_value=(market, pd.Timestamp('2026-10-23'), [], {})), \
                patch('production.state.ticker_map', return_value={}), \
                patch.object(run_daily.engine, 'run_day', return_value=result) as decide, \
                patch.object(run_daily.pd, 'read_csv', return_value=pd.DataFrame({'ticker': ['00981A']})), \
                patch.object(run_daily.active_share, 'load_etf_holdings', return_value={}), \
                patch.object(run_daily.etf_holdings_module, 'fetch_all', return_value=(pd.DataFrame(), [])):
            audit = run_daily.run('2026-10-27', datapack=str(self.path), runs_dir=self.tmp.name)
        decide.assert_called_once()
        self.assertEqual(decide.call_args.args[0].day_index, 0)
        self.assertFalse(any('Null as_of_date' in r for r in audit['reasons']))


class SubmissionGateTest(unittest.TestCase):
    def setUp(self):
        strategy = patch.object(run_daily, 'STRATEGY', Path(__file__).parents[1] / 'production/strategy_mom20.json')
        strategy.start()
        self.addCleanup(strategy.stop)

    def test_unverified_active_share_blocks_live_output(self):
        from production.state import SettledBook
        from production import active_share, engine
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        market = SimpleNamespace(calendar=pd.DatetimeIndex(['2026-10-27']), close=pd.DataFrame())
        book = SettledBook('2026-10-27', cash=100000, nav_tradable=100000)
        result = SimpleNamespace(mode=engine.NORMAL, reasons=[], active_share={'status': active_share.UNVERIFIED},
                                 reconciliation='MATCH', data_gate=[], plan=SimpleNamespace(status='OK', audit={}),
                                 submittable=True)
        # Keep fixture independent of live market/ETF availability and test the actual output gate.
        with patch.object(run_daily, 'load_market_for', return_value=(market, pd.Timestamp('2026-10-27'), [], {})), \
                patch.object(run_daily, 'load_datapack', return_value=book), \
                patch.object(run_daily.engine, 'run_day', return_value=result), \
                patch.object(run_daily, 'build_and_validate') as build, \
                patch.object(run_daily.pd, 'read_csv', return_value=pd.DataFrame({'ticker': ['00981A']})), \
                patch.object(run_daily.active_share, 'load_etf_holdings', return_value={}), \
                patch.object(run_daily.etf_holdings_module, 'fetch_all', return_value=(pd.DataFrame(), [])):
            # settings are real but team identity parser is mocked: no account state is fabricated in production.
            audit = run_daily.run('2026-10-28', datapack='fixture.json', runs_dir=tmp.name)
        self.assertEqual(audit['status'], run_daily.BLOCKED)
        self.assertTrue(any('LIVE_ACTIVE_SHARE_BLOCKED' in r for r in audit['reasons']), audit)
        build.assert_not_called()


    def test_fallback_rechecks_active_share_before_writing(self):
        from production.state import SettledBook
        from production import active_share, engine
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        market = SimpleNamespace(calendar=pd.DatetimeIndex(['2026-10-27']),
                                 close=pd.DataFrame({'2330.TW': [20.]}, index=pd.DatetimeIndex(['2026-10-27'])))
        book = SettledBook('2026-10-27', holdings={'2330.TW': 1000}, marks={'2330.TW': 20.},
                           cash=80000, nav_tradable=100000)
        def outcome(status, confirmed):
            return SimpleNamespace(mode=engine.NORMAL, reasons=[], active_share={'status': status,
                                   'official_method_confirmed': confirmed}, nav=100000,
                                   prev_close=market.close.iloc[0], reconciliation='MATCH', data_gate=[],
                                   plan=SimpleNamespace(status='OK', audit={}), submittable=True)
        normal, fallback = outcome(active_share.PASS, True), outcome(active_share.UNVERIFIED, False)
        with patch.object(run_daily, 'load_market_for', return_value=(market, pd.Timestamp('2026-10-27'), [], {})), \
                patch.object(run_daily, 'load_datapack', return_value=book), \
                patch.object(run_daily.engine, 'run_day', return_value=normal), \
                patch.object(run_daily.engine, 'fallback_path', return_value=fallback), \
                patch.object(run_daily, 'build_and_validate', side_effect=[({}, ['invalid initial plan']), ({}, [])]), \
                patch.object(run_daily.pd, 'read_csv', return_value=pd.DataFrame({'ticker': ['00981A']})), \
                patch.object(run_daily.active_share, 'load_etf_holdings', return_value={}), \
                patch.object(run_daily.etf_holdings_module, 'fetch_all', return_value=(pd.DataFrame(), [])):
            audit = run_daily.run('2026-10-28', datapack='fixture.json', runs_dir=tmp.name)
        self.assertEqual(audit['status'], run_daily.BLOCKED)
        self.assertEqual(audit['active_share']['status'], active_share.UNVERIFIED)
        self.assertIn('LIVE_ACTIVE_SHARE_BLOCKED_AFTER_VALIDATION', audit['validation_errors'])
        self.assertFalse(list(Path(tmp.name).rglob('D-Plan_*.json')))


if __name__ == '__main__':
    unittest.main()
