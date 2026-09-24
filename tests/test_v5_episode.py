"""V5 episode runner: completion, causality, odd lots in the sealed ledger, V3 parity."""
import json
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from src import strategy_24d, v4_baseline
from src.v5_episode import run_episode, run_v3_baseline
from src.v5_features import build_features, history_at
from src.v5_planner import ASSUMPTION_ODD_LOT
from tests.test_strategy_24d import fixture
from tests.test_v4_episode import official_fixture

ROOT = Path(__file__).resolve().parents[1]
SEALED_FRAMES = ['equity', 'trades', 'orders', 'holdings', 'signals', 'warnings', 'snapshots',
                 'compliance_daily', 'rejected_trades', 'plan_audit']


class EqualWeight:
    """Trivial strategy: equal weight over the first N ready names (symbol order)."""
    identity = 'test_equal_weight'

    def __init__(self, n=22, total=.9):
        self.n, self.total = n, total
        self.seen = []

    def generate_weights(self, decision_date, panel, portfolio_state, competition_state):
        rows = history_at(panel, decision_date)
        self.seen.append((pd.Timestamp(decision_date), pd.Timestamp(rows.date.max())))
        names = sorted(rows.loc[rows.feature_ready, 'symbol'])[:self.n]
        frame = pd.DataFrame({'target_weight': self.total / len(names)}, index=pd.Index(names, name='symbol'))
        frame.attrs.update(reason='EQUAL', identity=self.identity)
        return frame


class Momentum(EqualWeight):
    """Top-N by R20 from the D-1 cross-section; incumbents kept (low turnover)."""
    identity = 'test_momentum'

    def generate_weights(self, decision_date, panel, portfolio_state, competition_state):
        rows = history_at(panel, decision_date)
        rows = rows.loc[rows.feature_ready].set_index('symbol')
        rows = rows.loc[rows.index.isin(portfolio_state['previous_close'].dropna().index)]
        ranked = list(rows.R20.sort_values(ascending=False, kind='mergesort').index)
        held = [s for s in portfolio_state['holdings'] if s in rows.index]
        names = list(dict.fromkeys(held + ranked))[:self.n]
        frame = pd.DataFrame({'target_weight': self.total / len(names), 'score': rows.R20.reindex(names)},
                             index=pd.Index(names, name='symbol'))
        frame.attrs.update(reason='TOPN_R20', identity=self.identity)
        return frame


class SyntheticEpisodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daily, cls.universe, cls.dates = fixture(count=26, sessions=260)
        # A 5.37% stock dividend (non-lot result) on session 6 of the episode for one name.
        cls.ex_date, cls.odd = cls.dates[6], '1003.TW'
        stock = cls.daily.copy()
        hit = stock.symbol.eq(cls.odd) & stock.date.ge(cls.ex_date)
        stock.loc[hit, ['open', 'high', 'low', 'close']] /= 1.0537
        stock.loc[stock.symbol.eq(cls.odd) & stock.date.eq(cls.ex_date), 'split'] = 1.0537
        cls.stock_daily = stock
        cls.config = strategy_24d.build_config()
        cls.panel = build_features(cls.daily)
        cls.stock_panel = build_features(stock)
        cls.execution = official_fixture(cls.daily)
        cls.stock_execution = official_fixture(stock)

    def run_v5(self, strategy, daily=None, panel=None, execution=None):
        return run_episode(self.daily if daily is None else daily, self.universe, self.config, self.dates,
                           self.execution if execution is None else execution,
                           self.panel if panel is None else panel, strategy)

    def test_equal_weight_completes(self):
        strategy = EqualWeight()
        result = self.run_v5(strategy)
        m = result['metrics']
        self.assertEqual(m['observed_sessions'], 24)
        self.assertTrue(m['complete_episode'])
        self.assertEqual(m['simulated_warning_days'], 0, result['compliance_daily'].warning_reasons.tolist())
        self.assertTrue(m['v5_measured_pass'], m['v5_failure_reasons'])
        self.assertTrue(result['orders'].shares.mod(1000).eq(0).all())
        self.assertEqual(result['equity'].holdings.iloc[0], 22)
        self.assertTrue(result['equity'].cash_ratio.lt(.25).all())
        self.assertTrue(result['equity'].cash.ge(0).all())
        # The strategy only ever saw the cross-section of the preceding session.
        for decision, observed in strategy.seen:
            self.assertLess(observed, decision)
        self.assertEqual(len(result['predictions'].decision_date.unique()), 24)

    def test_future_corruption_does_not_change_earlier_plans(self):
        cut = self.dates[10]
        base = self.run_v5(Momentum())
        rng = np.random.default_rng(7)
        daily, panel, execution = self.daily.copy(), self.panel.copy(), self.execution.copy()
        for frame in (daily, panel, execution):
            future = frame.date.ge(cut)
            factor = pd.Series(rng.uniform(.5, 1.8, frame.symbol.nunique()), index=sorted(frame.symbol.unique()))
            scale = frame.loc[future, 'symbol'].map(factor).to_numpy()
            for col in [c for c in ('open', 'high', 'low', 'close', 'signal_price', 'R20', 'R5', 'trading_value')
                        if c in frame]:
                frame.loc[future, col] = frame.loc[future, col].to_numpy() * scale
        # Also delete some future rows outright.
        panel = panel.drop(panel.index[panel.date.gt(cut) & panel.symbol.eq('1010.TW')])
        changed = self.run_v5(Momentum(), daily, panel, execution)
        before = lambda r: r['orders'].loc[pd.to_datetime(r['orders'].signal_date) < cut].reset_index(drop=True)
        pd.testing.assert_frame_equal(before(base), before(changed))
        early = lambda r: r['predictions'].loc[r['predictions'].decision_date <= cut].reset_index(drop=True)
        pd.testing.assert_frame_equal(early(base), early(changed))
        self.assertFalse(before(base).empty)
        # The corruption is material: later plans or fills do change.
        self.assertFalse(base['orders'].equals(changed['orders']) and base['equity'].equals(changed['equity']))

    def test_stock_dividend_odd_lot_is_held_and_ledger_accepts(self):
        result = self.run_v5(EqualWeight(), self.stock_daily, self.stock_panel, self.stock_execution)
        m = result['metrics']
        holdings = result['holdings']
        odd = holdings.loc[holdings.symbol.eq(self.odd)]
        after = odd.loc[pd.to_datetime(odd.date) >= self.ex_date]
        self.assertFalse(after.empty)
        self.assertTrue((after.shares % 1000).gt(1e-6).all())  # remainder held through the end
        orders = result['orders']
        self.assertFalse(((orders.symbol == self.odd) & (pd.to_datetime(orders.signal_date) >= self.ex_date)).any())
        self.assertTrue(m['complete_episode'])
        self.assertGreater(m['odd_lot_residual_days'], 0)
        self.assertEqual(m['odd_lot_assumption'], ASSUMPTION_ODD_LOT)
        self.assertIn('FAIL_ROUND_LOT', m['failure_reasons'])  # sealed classification kept
        self.assertNotIn('FAIL_ROUND_LOT', m['v5_failure_reasons'])
        self.assertTrue(result['plan_audit'].v5_assumptions.str.contains(ASSUMPTION_ODD_LOT).any())

    def test_v3_wrapper_matches_sealed_call(self):
        features = strategy_24d.build_features(self.daily, self.config)
        wrapped = run_v3_baseline(self.daily, self.universe, self.config, self.dates, self.execution, features)
        direct = v4_baseline.run_episode(self.daily, self.universe, self.config, self.dates, self.execution,
                                         features=features, sizing_price_mode='official_close')
        for key in SEALED_FRAMES:
            pd.testing.assert_frame_equal(direct[key], wrapped[key])
        for key, value in direct['metrics'].items():
            self.assertEqual(value, wrapped['metrics'][key], key)


REAL = [ROOT / 'data/yahoo_daily/v3_20260923/nominal_daily.parquet',
        ROOT / 'outputs/v5/data/execution_data.csv', ROOT / 'outputs/v5/data/episodes.json']


@unittest.skipUnless(all(p.exists() for p in REAL), 'V5 data cache not built')
class RealDataParityTests(unittest.TestCase):
    """A0_V3 through the V5 wrapper equals the sealed call and V4 Stage-2 records."""
    RECORDED_V4_STAGE2_A0_V3 = {  # git show 72163500:outputs/v4/stage2_review/t2_episode_returns_finalists.csv
        'development_2010_01': -0.0872682, 'development_2010_07': 0.0797404, 'validation_2019_01': 0.0531376}

    @classmethod
    def setUpClass(cls):
        daily = pd.read_parquet(REAL[0])
        daily['date'] = pd.to_datetime(daily.date)
        cls.daily = daily
        universe = pd.read_csv(ROOT / 'data/reference/universe_competition_20260731.csv')
        universe['symbol'] = universe.yahoo_symbol.astype(str)
        universe['known_at'] = universe.attachment_created_at
        cls.universe = universe
        cls.config = json.loads((ROOT / 'configs/competition_24d_final.json').read_text())
        episodes = {e['episode_id']: e for e in json.loads(REAL[2].read_text())}
        cls.episodes = episodes
        needed = set()
        for key in list(cls.RECORDED_V4_STAGE2_A0_V3) + ['development_2011_07']:
            needed |= {episodes[key]['prior_session_date'], *episodes[key]['sessions']}
        execution = pd.read_csv(REAL[1], parse_dates=['date'])
        cls.execution = execution.loc[execution.date.isin(pd.to_datetime(sorted(needed)))].reset_index(drop=True)
        cls.v3 = strategy_24d.build_features(daily, cls.config)

    def test_v3_parity_three_episodes(self):
        for episode_id, recorded in self.RECORDED_V4_STAGE2_A0_V3.items():
            e = self.episodes[episode_id]
            with self.subTest(episode_id):
                wrapped = run_v3_baseline(self.daily, self.universe, self.config, e['sessions'], self.execution,
                                          self.v3, prior_session_date=e['prior_session_date'])
                direct = v4_baseline.run_episode(self.daily, self.universe,
                    dict(self.config, prior_session_date=e['prior_session_date']), e['sessions'], self.execution,
                    features=self.v3, sizing_price_mode='official_close')
                for key in SEALED_FRAMES:
                    pd.testing.assert_frame_equal(direct[key], wrapped[key])
                for key, value in direct['metrics'].items():
                    self.assertEqual(value, wrapped['metrics'][key], key)
                self.assertAlmostEqual(wrapped['metrics']['episode_return'], recorded, places=7)

    def test_real_stock_dividend_episode_passes_under_assumption(self):
        e = self.episodes['development_2011_07']
        panel = build_features(self.daily)
        result = run_episode(self.daily, self.universe, self.config, e['sessions'], self.execution, panel,
                             Momentum(), prior_session_date=e['prior_session_date'])
        m = result['metrics']
        self.assertTrue(m['complete_episode'])
        self.assertGreater(m['odd_lot_residual_days'], 0)
        self.assertTrue(m['v5_measured_pass'], m['v5_failure_reasons'])
        self.assertTrue(result['orders'].shares.mod(1000).eq(0).all())


if __name__ == '__main__':
    unittest.main()
