"""Independent numerical, temporal and execution-contract tests for A deep tuning."""
import copy
import io
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from src import tuning_a_deep as deep
from src import tuning_2nd as second
from src.backtest import score_candidates
from src.tuning_features import FeatureCache, normalized_config
from scripts.audit_a_deep import (choose_independently, effective_key,
                                  independent_refinement, check_config,
                                  normalize_signal_numbers, read_prefix_csv)

ROOT = Path(__file__).resolve().parents[1]


def market(count=27, sessions=265):
    dates = pd.bdate_range('2024-01-02', periods=sessions)
    frames = []
    for j in range(count):
        x = np.arange(sessions, dtype=float)
        price = 60 + 2*j + (.12 + .003*j)*x + .25*np.sin(x/7 + j)
        split = np.ones(sessions); dividend = np.zeros(sessions)
        # A simultaneous old-share cash entitlement and stock distribution.
        split[218] = 1.1; dividend[218] = .5
        price[218:] = (price[218:] - .5) / 1.1
        volume = 1e6 + 130*x
        frames.append(pd.DataFrame(dict(date=dates, symbol=f'{2000+j}.TW',
            open=price, high=price+.1, low=price-.1, close=price,
            volume=volume, dividend=dividend, split=split,
            turnover=price*volume, execution_volume=volume)))
    daily = pd.concat(frames, ignore_index=True)
    four = daily[['date','symbol','open','high','low','close','volume']].copy()
    # Action must remain causal even when the event day's 4H quote is absent.
    four = four[four.date.ne(dates[218])].reset_index(drop=True)
    base = json.loads((ROOT/'config/strategy_v2.json').read_text())
    base.update(start=str(dates[205].date()), end=str(dates[-1].date()))
    universe = pd.DataFrame(dict(symbol=daily.symbol.unique(), known_at='2023-12-31T19:30:00+08:00'))
    return dict(daily=daily, bars=four, universe=universe, base=base,
                track='historical_pit', cache=FeatureCache(daily, four))


def old_config(track='historical_pit'):
    return json.loads((ROOT/f'outputs/tuning_report_2nd_try/{track}/final/A/config.json').read_text())


def trial_config(ctx, **changes):
    params = deep.from_old(old_config())
    params.update(changes)
    return deep.config_for(ctx, dict(candidate_id='independent_fixture', params=params))


class DeepIndependentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = market()
        cls.numeric = deep.NumericFeatureCache(cls.ctx['cache'])

    def run_deep(self, ctx, config):
        copied = dict(ctx)
        copied['cache'] = deep.NumericFeatureCache(ctx['cache'])
        return deep.run_model(copied, config)

    def test_incumbent_presets_and_score_weights_reproduce_exactly(self):
        for track in ('historical_pit', 'official_ex_post'):
            old = old_config(track)
            new = deep.config_for(self.ctx, dict(candidate_id='anchor', params=deep.from_old(old)))
            self.assertEqual(new['score_weights'], old['score_weights'])
            expected = self.ctx['cache'].frame(old)
            actual = self.numeric.frame(new)
            pd.testing.assert_frame_equal(actual, expected, check_exact=True)

    def test_numeric_horizons_match_separate_direct_formula(self):
        config = trial_config(self.ctx, return_short=7, return_long=83,
                              ema_fast=13, ema_slow=117, macd_fast=5,
                              macd_slow=43, macd_signal=11)
        result = self.numeric.frame(config)
        for _, rows in result.groupby('symbol'):
            p = rows.signal_price
            for name, n in [('return20',7),('return50',83)]:
                pd.testing.assert_series_equal(rows[name], p/p.shift(n)-1, check_names=False)
            for name, n in [('ema20',13),('ema50',117)]:
                pd.testing.assert_series_equal(rows[name], p.ewm(span=n,adjust=False,min_periods=n).mean(),check_names=False)
            line = p.ewm(span=5,adjust=False).mean()-p.ewm(span=43,adjust=False).mean()
            pd.testing.assert_series_equal(rows.macd_hist,line-line.ewm(span=11,adjust=False).mean(),check_names=False)
        for col in ['ema100','ema200','four_hour_ok','four_hour_available','four_hour_ready','volume_ratio']:
            pd.testing.assert_series_equal(result[col], self.ctx['cache'].frame({})[col])

    def test_future_actions_prices_and_four_hour_cannot_change_prefix(self):
        ctx = self.ctx; cutoff = sorted(ctx['daily'].date.unique())[238]
        config = trial_config(ctx, return_short=7, return_long=83, ema_fast=13, ema_slow=117)
        daily = ctx['daily'].copy(); four = ctx['bars'].copy()
        future = daily.date.gt(cutoff); intraday = four.date.gt(cutoff)
        daily.loc[future,['open','high','low','close','turnover']] *= 2.7
        daily.loc[future,'split'] = 1.2; daily.loc[future,'dividend'] = 5.
        four.loc[intraday,['open','high','low','close']] *= .3
        altered = deep.NumericFeatureCache(FeatureCache(daily,four)).frame(config)
        original = self.numeric.frame(config)
        pd.testing.assert_frame_equal(altered[altered.date.le(cutoff)],original[original.date.le(cutoff)],check_exact=True)
        ds=ctx['daily'][ctx['daily'].date.le(cutoff)]; fs=ctx['bars'][ctx['bars'].date.le(cutoff)]
        prefix = deep.NumericFeatureCache(FeatureCache(ds,fs)).frame(config)
        pd.testing.assert_frame_equal(self.numeric.frame(config,ds,fs),prefix,check_exact=True)

    def test_cache_mutation_and_gapped_sources_rejected(self):
        config=trial_config(self.ctx)
        expected=self.numeric.frame(config)
        changed=self.numeric.frame(config); changed.loc[:,'return20']=900
        pd.testing.assert_frame_equal(self.numeric.frame(config),expected,check_exact=True)
        with self.assertRaisesRegex(ValueError,'complete history prefixes'):
            self.numeric.frame(config,self.ctx['daily'].drop(index=3),self.ctx['bars'])
        changed_bars=self.ctx['bars'].copy(); changed_bars.loc[3,'close']+=1
        with self.assertRaisesRegex(ValueError,'4H source changed'):
            self.numeric.frame(config,self.ctx['daily'],changed_bars)
        self.assertFalse(self.numeric.vector('return',(20,)).flags.writeable)

    def test_coverage_only_still_requires_fifty_observed_four_hour_bars(self):
        config=trial_config(self.ctx,four_hour_mode='coverage_only')
        frame=self.numeric.frame(config); rows=frame[frame.date.eq(frame.date.max())].head(3).copy()
        rows['ready']=True; rows['signal_price']=rows.ema50+1
        rows['volatility_ratio']=1.; rows['return1']=.01; rows['volume_ratio']=1.
        rows['four_hour_available']=[True,True,False]
        rows['four_hour_ready']=[True,False,False]; rows['four_hour_ok']=False
        scored=score_candidates(rows,config)
        self.assertEqual(scored.entry_ok.sum(),1)

    def test_invalid_numeric_windows_and_nonfinite_rejected(self):
        for change in [dict(return_short=20,return_long=20),dict(ema_slow=201),
                       dict(macd_signal=True),dict(momentum_weight=np.nan),
                       dict(four_hour_mode='none'),dict(volume_low=3,volume_high=2)]:
            with self.subTest(change=change),self.assertRaises(ValueError):
                trial_config(self.ctx,**change)

    def test_all_fixed_execution_constraints_are_enforced_before_run(self):
        config=trial_config(self.ctx)
        for key,value in deep.FIXED.items():
            altered=copy.deepcopy(config)
            altered[key]=not value if isinstance(value,bool) else ('changed' if isinstance(value,str) else value+1)
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'Frozen execution'):
                deep.run_model({},altered)

    def test_both_anchor_ledgers_match_frozen_second_engine(self):
        for track in ('historical_pit','official_ex_post'):
            old=old_config(track)
            old.update(start=self.ctx['base']['start'],end=self.ctx['base']['end'],universe_mode='historical_pit',ex_post_fixed_universe=False)
            config=deep.config_for(self.ctx,dict(candidate_id='anchor',params=deep.from_old(old)))
            expected=second.adapter(self.ctx).run_v2(self.ctx['daily'],self.ctx['universe'],old,self.ctx['bars'])
            actual=self.run_deep(self.ctx,config)
            self.assertGreater(len(actual['trades']),0)
            for table in ['equity','trades','orders','holdings','signals','warnings']:
                pd.testing.assert_frame_equal(actual[table],expected[table],check_exact=True)

    def test_physical_prefix_signals_orders_and_economic_ledger_match(self):
        config=trial_config(self.ctx,return_short=7,return_long=83,ema_fast=13,ema_slow=117)
        full=self.run_deep(self.ctx,config)
        cut=sorted(self.ctx['daily'].date.unique())[242]
        prefix=dict(self.ctx)
        prefix['daily']=self.ctx['daily'][self.ctx['daily'].date.le(cut)]
        prefix['bars']=self.ctx['bars'][self.ctx['bars'].date.le(cut)]
        prefix['cache']=FeatureCache(prefix['daily'],prefix['bars'])
        truncated=copy.deepcopy(config);truncated['end']=str(pd.Timestamp(cut).date())
        part=self.run_deep(prefix,truncated); day=truncated['end']
        for table,col in [('signals','date'),('orders','signal_date'),('trades','date')]:
            expected=full[table][full[table][col].le(day)].reset_index(drop=True)
            pd.testing.assert_frame_equal(part[table],expected,check_exact=True)
        cols=['date','economic_nav','holdings','fees','taxes','traded_notional']
        pd.testing.assert_frame_equal(part['equity'][cols],full['equity'].query('date <= @day')[cols].reset_index(drop=True),check_exact=True)
        # Prefix end transfers receivable to cash; economic capital stays identical.
        before=part['equity'].date.lt(day)
        pd.testing.assert_frame_equal(part['equity'][before],full['equity'].query('date < @day').reset_index(drop=True),check_exact=True)

    def test_pit_future_membership_is_not_relaxed(self):
        ctx=dict(self.ctx);ctx['universe']=ctx['universe'].copy()
        ctx['universe']['known_at']='2026-09-18T10:05:13+08:00'
        with self.assertRaisesRegex(ValueError,'LOOKAHEAD_UNIVERSE'):
            self.run_deep(ctx,trial_config(ctx))

    def test_zero_hard_and_risk_controlled_selection_are_separate(self):
        rows=[dict(status='COMPLETE',candidate_id='risky',measured_hard_breach_days=0,economic_total_return=3.,economic_max_drawdown=.3),
              dict(status='COMPLETE',candidate_id='safe',measured_hard_breach_days=0,economic_total_return=2.,economic_max_drawdown=.2),
              dict(status='COMPLETE',candidate_id='invalid',measured_hard_breach_days=1,economic_total_return=4.,economic_max_drawdown=.1)]
        self.assertEqual(deep.choose(rows)['candidate_id'],'risky')
        self.assertEqual(deep.choose(rows,max_mdd=.25)['candidate_id'],'safe')
        self.assertIsNone(deep.choose(rows,max_mdd=.1))
        self.assertIsNone(deep.choose([rows[-1]]))

    def test_auditor_refuses_nonfinite_incomplete_and_ineligible_winners(self):
        good=dict(status='COMPLETE',candidate_id='good',measured_hard_breach_days=0,
                  economic_total_return=2.,economic_max_drawdown=.2,four_hour_mode='strict')
        for change in [dict(status='FAILED'),dict(measured_hard_breach_days=1),
                       dict(economic_total_return=np.nan),dict(economic_max_drawdown=np.inf)]:
            self.assertIsNone(choose_independently([{**good,**change}]))
        self.assertIsNone(choose_independently([good],max_mdd=.1))
        self.assertIsNone(choose_independently([good],mode='coverage_only'))
        self.assertEqual(choose_independently([good])['candidate_id'],'good')

    def test_effective_uniqueness_uses_executed_weights_and_inactive_margin(self):
        p=deep.from_old(old_config())
        near={**p,'long_return_fraction':7/15}
        self.assertEqual(effective_key(p),effective_key(near))
        p.update(max_replacements_per_day=0,replacement_margin=0.)
        self.assertEqual(effective_key(p),effective_key({**p,'replacement_margin':.4}))
        study=json.loads((ROOT/'config/a_deep_study.json').read_text())
        self.assertEqual(len(study['candidates']),len({effective_key(t['params']) for t in study['candidates']}))

    def test_auditor_detects_numeric_metadata_execution_and_mode_mismatch(self):
        config=trial_config(self.ctx);p=config['tuning_params']
        check_config(config,p,self.ctx['base'],'historical_pit')
        for change in [dict(commission=0.),dict(use_4h=True),dict(ex_post_fixed_universe=True)]:
            changed=copy.deepcopy(config);changed.update(change)
            with self.assertRaises(AssertionError):
                check_config(changed,p,self.ctx['base'],'historical_pit')
        changed=copy.deepcopy(config);changed['feature_spec']['ema_spans']=[20,50]
        with self.assertRaises(AssertionError):
            check_config(changed,p,self.ctx['base'],'historical_pit')

    def test_adaptive_candidate_membership_matches_independent_seed_reconstruction(self):
        from scripts.run_a_deep_tuning import refinement
        study=json.loads((ROOT/'config/a_deep_study.json').read_text())
        rows=[]
        for i,trial in enumerate(study['candidates']):
            rows.append(dict(**trial['params'],candidate_id=trial['candidate_id'],status='COMPLETE',
                measured_hard_breach_days=int(i%11==5),economic_total_return=(i*17%31)/10,
                economic_max_drawdown=.15+(i%7)/50))
        expected=independent_refinement(study,rows,.2)
        self.assertEqual(refinement(study,rows,.2),expected)
        self.assertEqual(len(expected),64)
        self.assertEqual(len({effective_key(t['params']) for t in expected}),64)

    def test_sparse_numeric_csv_normalization_preserves_identifiers_and_errors(self):
        source=pd.DataFrame(dict(date=['2025-01-02','2025-01-03'],symbol=['0050','2330'],
                                 turnover=['','5361927000.0'],return20=['','0.01'],
                                 plan_reason=['','HOLD']))
        expected=source.copy();expected['turnover']=[np.nan,5361927000.]
        expected['return20']=[np.nan,.01]
        parsed=normalize_signal_numbers(source)
        pd.testing.assert_frame_equal(parsed,expected,check_exact=True)
        self.assertEqual(parsed.symbol.iloc[0],'0050')
        malformed=source.copy();malformed.loc[1,'turnover']='not-a-number'
        with self.assertRaises(ValueError):
            normalize_signal_numbers(malformed)

    def test_exact_prefix_reader_preserves_fractional_entitlement_float(self):
        frame=pd.DataFrame(dict(date=['2025-07-30'],symbol=['3665.TW'],shares=[43653.399999999994]))
        saved=io.StringIO(frame.to_csv(index=False))
        pd.testing.assert_frame_equal(read_prefix_csv(saved),frame,check_exact=True)


if __name__ == '__main__':
    unittest.main()
