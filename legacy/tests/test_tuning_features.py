"""Compatibility, causality, and isolation tests for the tuning adapter."""
import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd
import unittest

import src.backtest_v2 as frozen_engine
from src.backtest import compute_features, score_candidates
from src.tuning_features import FeatureCache, IsolatedEngine, normalized_config, feature_spec


def panel(count=3, sessions=270):
    dates = pd.bdate_range('2024-01-02', periods=sessions)
    frames = []
    for j in range(count):
        x = np.arange(sessions, dtype=float)
        price = 50 + j + .1*x + 3*np.sin(x/9+j)
        frame = pd.DataFrame(dict(date=dates, symbol=f'{2000+j}.TW', open=price,
                                 high=price+1, low=price-1, close=price,
                                 volume=1e6+100*x, dividend=0., split=1.,
                                 turnover=price*(1e6+100*x), execution_volume=1e6+100*x))
        # Deliberate simultaneous action with a missing intraday event-date bar.
        frame.loc[135, ['split', 'dividend']] = [1.1, .2]
        frames.append(frame)
    daily = pd.concat(frames, ignore_index=True)
    four = daily[['date','symbol','open','high','low','close','volume']].copy()
    four = four[four.date != dates[135]].reset_index(drop=True)
    return daily, four


def market():
    daily, four = panel()
    return daily, four, FeatureCache(daily, four)


class TuningFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = market()

    def test_baseline_all_columns_exact_including_actions_and_4h_gap(self):
        market = self.market
        daily, four, cache = market
        expected = compute_features(daily, {'warmup_sessions':200}, four)
        pd.testing.assert_frame_equal(cache.frame({}), expected, check_exact=True)

    def test_future_rows_do_not_change_prefix(self):
        market = self.market
        for preset in ['fast', 'base', 'slow']:
            daily, four, cache = market
            cut = sorted(daily.date.unique())[219]
            ds = daily[daily.date <= cut].copy(); fs = four[four.date <= cut].copy()
            prefix = FeatureCache(ds, fs)
            config = {'feature_presets': {'returns':preset,'ema':preset,'macd':preset}}
            expected = prefix.frame(config)
            actual = cache.frame(config, ds, fs)
            pd.testing.assert_frame_equal(actual, expected, check_exact=True)

    def test_alternative_horizons_are_explicit_and_independent(self):
        market = self.market
        _, _, cache = market
        config={'feature_presets': {'returns':'fast','ema':'slow','macd':'fast'}}
        result = cache.frame(config)
        rows=result[result.symbol=='2000.TW'].reset_index(drop=True)
        p=rows.signal_price
        pd.testing.assert_series_equal(rows.return20,p.pct_change(10,fill_method=None),check_names=False)
        pd.testing.assert_series_equal(rows.return50,p.pct_change(30,fill_method=None),check_names=False)
        pd.testing.assert_series_equal(rows.ema20,p.ewm(span=30,min_periods=30,adjust=False).mean(),check_names=False)
        pd.testing.assert_series_equal(rows.ema50,p.ewm(span=90,min_periods=90,adjust=False).mean(),check_names=False)
        macd=p.ewm(span=8,adjust=False).mean()-p.ewm(span=21,adjust=False).mean()
        pd.testing.assert_series_equal(rows.macd_hist,macd-macd.ewm(span=5,adjust=False).mean(),check_names=False)
        original=cache.frame({})
        for name in ['four_hour_ok','four_hour_available','four_hour_ready','ema100','ema200','volume_ratio']:
            pd.testing.assert_series_equal(result[name],original[name])

    def test_returned_mutations_cannot_pollute_cache(self):
        market = self.market
        _, _, cache=market
        a=cache.frame({});saved=a.copy(deep=True)
        a.loc[:, 'signal_price']=0.;a.loc[:, 'close']=0.;a.loc[:, 'return20']=100.
        pd.testing.assert_frame_equal(cache.frame({}),saved,check_exact=True)

    def test_changed_source_rejected(self):
        market = self.market
        daily,four,cache=market
        changed=daily.copy();changed.loc[3,'close'] += .01
        with self.assertRaisesRegex(ValueError,'daily values changed'):
            cache.frame({},changed,four)
        with self.assertRaisesRegex(ValueError,'complete history prefixes'):
            cache.frame({},daily.drop(index=3),four)
        changed_four=four.copy();changed_four.loc[3,'close'] += .01
        with self.assertRaisesRegex(ValueError,'4H source changed'):
            cache.frame({},daily,changed_four)

    def test_universe_subset_matches_uncached_compute(self):
        market = self.market
        daily,four,cache=market
        subset=daily[daily.symbol.isin(['2000.TW','2002.TW'])]
        expected=compute_features(subset,{'warmup_sessions':200},four)
        pd.testing.assert_frame_equal(cache.frame({},subset,four),expected,check_exact=True)

    def test_scheduled_presets_apply_forward_without_backfill(self):
        market = self.market
        _,_,cache=market
        base=cache.frame({});date=base.date.sort_values().unique()[210]
        config={'feature_presets': {'returns':'fast','ema':'slow','macd':'fast'}}
        schedule={date:config,'2030-01-01':{'feature_presets':{'returns':'slow'}}}
        result=cache.frame_for_schedule(schedule)
        before=result.date < date
        pd.testing.assert_frame_equal(result[before],base[before],check_exact=True)
        alternate=cache.frame(config)
        pd.testing.assert_frame_equal(result[~before],alternate[~before],check_exact=True)

    def test_coverage_only_removes_direction_not_observation_gate(self):
        market = self.market
        _,_,cache=market
        config=json.loads((Path(__file__).parents[1]/'config/strategy_v2.json').read_text())
        strict=normalized_config({**config,'four_hour_mode':'strict'})
        coverage=normalized_config({**config,'four_hour_mode':'coverage_only'})
        assert strict['use_4h'] and not strict['match_4h_coverage']
        assert not coverage['use_4h'] and coverage['match_4h_coverage']
        rows=cache.frame({});day=rows.date.unique()[-1]
        rows=rows[rows.date==day].copy()
        rows['ready']=True;rows['signal_price']=rows.ema50+1
        rows['volatility_ratio']=1.;rows['return1']=.01;rows['volume_ratio']=1.
        rows['four_hour_available']=[True,True,False]
        rows['four_hour_ready']=[True,False,False]
        rows['four_hour_ok']=False
        a=score_candidates(rows,strict);b=score_candidates(rows,coverage)
        assert not a.entry_ok.any()
        assert b.set_index('symbol').entry_ok.to_dict()=={'2000.TW':True,'2001.TW':False,'2002.TW':False}

    def test_private_engine_baseline_ledger_exact_and_global_unmodified(self):
        daily,four=panel(count=26,sessions=235)
        cache=FeatureCache(daily,four)
        original=frozen_engine.compute_features
        adapter=IsolatedEngine(cache)
        other=IsolatedEngine(cache)
        assert adapter.module is not other.module
        assert frozen_engine.compute_features is original
        config=json.loads((Path(__file__).parents[1]/'config/strategy_v2.json').read_text())
        config.update(start=str(daily.date.sort_values().unique()[205])[:10],
                      end=str(daily.date.max().date()),target_count=20,
                      use_4h=False,match_4h_coverage=True)
        universe=pd.DataFrame({'symbol':daily.symbol.unique(),'known_at':'2023-12-31T19:30:00+08:00'})
        expected=frozen_engine.run_v2(daily,universe,copy.deepcopy(config),four)
        actual=adapter.run_v2(daily,universe,config,four)
        self.assertGreater(len(actual['trades']), 0, 'Ledger comparison must exercise actual trades')
        for name in ['equity','trades','orders','holdings','signals','warnings']:
            pd.testing.assert_frame_equal(actual[name],expected[name],check_exact=True)
        assert frozen_engine.compute_features is original

    def test_invalid_presets_or_unmatched_4h_rejected(self):
        with self.assertRaisesRegex(ValueError,'Unknown returns'):
            feature_spec({'feature_presets':{'returns':'future'}})
        with self.assertRaisesRegex(ValueError,'requires strict or coverage_only'):
            feature_spec({'use_4h':False})
