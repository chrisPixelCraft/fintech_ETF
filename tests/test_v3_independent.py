"""Independent OLS, observation-calendar, intervention and causality tests."""
import copy
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from src.v3_signals import WeakMarketSignals
from scripts.audit_v3 import reference_factors,compare_features,expected_ranks

ROOT=Path(__file__).resolve().parents[1]


def parameters():
    return json.loads((ROOT/'config/v3_weak_market.json').read_text())['signal_parameters']


def panel(count=23,sessions=245):
    dates=pd.bdate_range('2024-01-02',periods=sessions)
    x=np.arange(sessions,dtype=float)
    market_return=.002*np.sin(x/3)+.003*np.cos(x/11)+np.where(x<170,.0015,-.004)
    frames=[]
    for j,symbol in enumerate(['0050.TW']+[f'{3000+k}.TW' for k in range(count)]):
        ret=market_return if j==0 else (-.5+j*.12)*market_return+.001*np.sin(x/(4+j*.3)+j)+.0001*j
        split=np.ones(sessions);cash=np.zeros(sessions)
        split[180]=4. if j==0 else 1.1
        cash[180]=.5
        price=np.empty(sessions);price[0]=100+j
        for t in range(1,sessions):price[t]=(price[t-1]*(1+ret[t])-cash[t])/split[t]
        volume=np.full(sessions,1e6)
        if j==2:volume[192]=0
        frame=pd.DataFrame(dict(date=dates,symbol=symbol,open=price,high=price+.2,low=price-.2,
             close=price,volume=volume,dividend=cash,split=split,turnover=price*volume,execution_volume=volume))
        # Market and stock halts must invalidate both the halt and resumption
        # one-day return, without inventing a zero or multiday paired return.
        if j==0:frame=frame[frame.date.ne(dates[155])]
        if j==1:frame=frame[frame.date.ne(dates[164])]
        frames.append(frame)
    return pd.concat(frames,ignore_index=True),dates


def ranked(signals,day):
    symbols=sorted(s for s in signals.features.symbol.unique() if s!='0050.TW')
    rows=pd.DataFrame(dict(symbol=symbols,date=day,score=np.linspace(.9,.3,len(symbols)),
                          entry_ok=np.arange(len(symbols))%2==0,exit=np.arange(len(symbols))%5==0))
    return rows.set_index('symbol',drop=False)


class V3IndependentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daily,cls.dates=panel()
        cls.parameters=parameters()
        cls.signals=WeakMarketSignals(cls.daily,cls.parameters)

    def test_direct_numpy_ols_windows_and_market_state(self):
        expected,state=reference_factors(self.daily,self.parameters)
        compare_features(self.signals.features,expected,self.signals.market_state,state)

    def test_missing_market_day_does_not_create_zero_or_multiday_pair(self):
        features=self.signals.features.set_index(['date','symbol'])
        for day in self.dates[[155,156]]:
            row=features.loc[(day,'3002.TW')]
            self.assertFalse(row.current_pair_valid)
            self.assertFalse(row.common_valid)
            self.assertTrue(np.isnan(row.beta))
        stock=features.loc[(self.dates[165],'3000.TW')]
        self.assertFalse(stock.current_pair_valid)
        self.assertGreater(features.loc[(self.dates[200],'3002.TW')].beta_span_sessions,60)

    def test_zero_volume_quote_and_resumption_are_not_observations(self):
        frame=self.signals.features.set_index(['date','symbol'])
        for n in [192,193]:
            self.assertFalse(frame.loc[(self.dates[n],'3001.TW')].current_pair_valid)
        self.assertFalse(frame.loc[(self.dates[192],'3001.TW')].quote_present)

    def test_same_day_response_cannot_enter_its_lagged_ols_fit(self):
        day=self.dates[222];future=self.daily.copy()
        mask=future.symbol.eq('3003.TW')&future.date.ge(day)
        future.loc[mask,['open','high','low','close','turnover']]*=1.15
        changed=WeakMarketSignals(future,self.parameters).features.set_index(['date','symbol'])
        original=self.signals.features.set_index(['date','symbol'])
        for col in ['ols_alpha_lagged','ols_beta_lagged','ols_window_start','ols_window_end']:
            self.assertEqual(changed.loc[(day,'3003.TW'),col],original.loc[(day,'3003.TW'),col])
        self.assertNotAlmostEqual(changed.loc[(day,'3003.TW'),'one_step_residual'],original.loc[(day,'3003.TW'),'one_step_residual'])

    def test_future_rows_and_actions_cannot_change_prefix_factors_or_scores(self):
        cutoff=self.dates[218];changed=self.daily.copy()
        mask=changed.date.gt(cutoff)
        changed.loc[mask,['open','high','low','close','turnover']]*=1.7
        changed.loc[mask,'dividend']=4.;changed.loc[mask,'split']=1.01
        altered=WeakMarketSignals(changed,self.parameters)
        prefix=WeakMarketSignals(self.daily[self.daily.date.le(cutoff)],self.parameters)
        for name in ('features','market_state'):
            original=getattr(self.signals,name);original=original[original.date.le(cutoff)].reset_index(drop=True)
            pd.testing.assert_frame_equal(getattr(prefix,name),original,check_exact=True)
            modified=getattr(altered,name);modified=modified[modified.date.le(cutoff)].reset_index(drop=True)
            pd.testing.assert_frame_equal(modified,original,check_exact=True)
        for version in ('A','B','C','D'):
            source=ranked(self.signals,cutoff)
            a,ma=self.signals.callback(version)(cutoff,source)
            b,mb=altered.callback(version)(cutoff,source)
            pd.testing.assert_frame_equal(a,b,check_exact=True);self.assertEqual(ma,mb)

    def test_weak_b_c_d_percentiles_and_unchanged_entry_exit(self):
        day=self.dates[-1];source=ranked(self.signals,day)
        f=self.signals.features.query('date == @day').set_index('symbol').reindex(source.index)
        scores,valid=expected_ranks(f)
        self.assertGreaterEqual(valid.sum(),20)
        for version in ('B','C','D'):
            result,meta=self.signals.callback(version)(day,source)
            self.assertEqual(meta['status'],'WEAK_MARKET_RERANKED')
            pd.testing.assert_series_equal(result.score,scores[version].reindex(source.index).fillna(0.),check_names=False)
            for col in source.columns:
                if col!='score':pd.testing.assert_series_equal(result[col],source[col],check_exact=True)

    def test_baseline_normal_unknown_and_insufficient_days_preserve_scores(self):
        normal=self.signals.market_state.query("regime == 'NORMAL'").date.iloc[-1]
        for day,version,status in [(self.dates[-1],'A','BASELINE_A'),
                (normal,'D','NORMAL_MARKET_BASELINE'),
                (self.dates[155],'B','BASELINE_FALLBACK_MARKET_UNKNOWN')]:
            source=ranked(self.signals,day)
            result,meta=self.signals.callback(version)(day,source)
            pd.testing.assert_series_equal(result.score,source.score,check_exact=True)
            self.assertEqual(meta['status'],status)
        source=ranked(self.signals,self.dates[-1]).iloc[:19]
        result,meta=self.signals.callback('C')(self.dates[-1],source)
        self.assertEqual(meta['status'],'BASELINE_FALLBACK_INSUFFICIENT_COMMON_VALID')
        pd.testing.assert_series_equal(result.score,source.score,check_exact=True)

    def test_rank_ties_signed_beta_and_blend_repercentiling(self):
        factor=pd.DataFrame(dict(beta=[-1.,0.,0.,2.],volatility=[.1,.2,.2,.01],
             residual_momentum=[1.,-1.,0.,2.],common_valid=True),index=['a','b','c','d'])
        score,valid=expected_ranks(factor)
        self.assertEqual(score['B']['b'],score['B']['c'])
        self.assertGreater(score['B']['a'],score['B']['b'])
        pd.testing.assert_series_equal(score['D'],(score['B'].rank(pct=True,method='average')+score['C'])/2)
        self.assertFalse(np.allclose(score['D'],(score['B']+score['C'])/2))

    def test_parameters_must_be_complete_and_unsupported_policies_rejected(self):
        missing=copy.deepcopy(self.parameters);missing.pop('ols_window')
        with self.assertRaises(ValueError):WeakMarketSignals(self.daily,missing)
        for key,value in [('ddof',0),('current_pair_required',False),('rank_ties','first'),('residual_std_floor',0.)]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                WeakMarketSignals(self.daily,{**self.parameters,key:value})

    def test_report_property_mutations_cannot_change_cached_signal(self):
        original=self.signals.features
        edited=self.signals.features;edited.loc[:,'beta']=999.
        pd.testing.assert_frame_equal(self.signals.features,original,check_exact=True)
        source=ranked(self.signals,self.dates[-1]);saved=source.copy(deep=True)
        self.signals.callback('B')(self.dates[-1],source)
        pd.testing.assert_frame_equal(source,saved,check_exact=True)


if __name__=='__main__':unittest.main()
