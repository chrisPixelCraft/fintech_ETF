"""Frozen data/context and metric helpers retained for the x0352 replay.

Function bodies are copied unchanged from the historical tuning runner.
Search launchers and candidate-selection workflows are intentionally absent.
"""
from pathlib import Path
import copy
import json
import numpy as np
import pandas as pd
from src.backtest import aggregate_four_hour
from src.tuning_features import FeatureCache, normalized_config

ROOT = Path(__file__).resolve().parents[1]
CTX = {}

def trial_config(base, study, trial, strategy):
    c=copy.deepcopy(base);p=trial['params']
    for name in ['target_count','replacement_margin','max_replacements_per_day','volatility_spike_ratio','one_day_chase_return','volume_low','volume_high','four_hour_mode']:c[name]=p[name]
    c['score_weights']=dict(zip(['return20','return50','volume','macd','trend','long_trend'],study['score_profiles'][p['score_profile']]))
    c['feature_presets']={k:p[k] for k in ['returns','ema','macd']}
    c['strategy_id']=strategy+'_'+trial['candidate_id'];c['tuning_candidate_id']=trial['candidate_id'];c['tuning_params']=p
    c['study_policy']={'parameter_search':True,'historical_period_is_development':True,'official_live_submission':'BLOCK_IF_UNKNOWN'}
    return normalized_config(c)

def period_metrics(eq,initial=1e9):
    eq=eq.copy().fillna('');nav=np.r_[initial,eq.economic_nav.to_numpy(float)]
    r=nav[1:]/nav[:-1]-1
    hard=eq.cash_ratio.astype(float).ge(.25)|eq.active_cap_breaches.ne('')|eq.overdue_passive_caps.ne('')
    return dict(economic_total_return=float(nav[-1]/initial-1),economic_max_drawdown=float(-(nav/np.maximum.accumulate(nav)-1).min()),
                annualized_volatility=float(np.std(r,ddof=1)*np.sqrt(252)) if len(r)>1 else 0,
                sharpe_zero_rf=float(np.mean(r)/np.std(r,ddof=1)*np.sqrt(252)) if len(r)>1 and np.std(r,ddof=1)>0 else 0,
                hard_breach_days=int(hard.sum()),infeasible_executed_days=int(eq.executed_plan.astype(str).str.startswith('INFEASIBLE').sum()),
                raw_rule_breach_days=int(eq.violations.ne('').sum()),negative_cash_days=int(eq.cash.astype(float).lt(-1e-6).sum()),
                trade_days=int(eq.traded_notional.astype(float).gt(0).sum()),costs=float(eq.costs.astype(float).sum()),
                turnover_two_way=float(eq.turnover.astype(float).sum()),sessions=len(eq),final_economic_nav=float(nav[-1]))

def monthly_rows(eq,initial=1e9):
    rows=[];previous=initial
    for month,group in eq.groupby(pd.to_datetime(eq.date).dt.to_period('M')):
        s=period_metrics(group,previous);rows.append({'month':str(month),'economic_return':s['economic_total_return'],**s});previous=group.economic_nav.iloc[-1]
    return rows

def setup_context():
    from src.tuning_signals import TuningSignals
    daily=pd.read_csv(ROOT/'data/v2/market_daily.csv');hourly=pd.read_csv(ROOT/'data/extended/processed/hourly_canonical.csv')
    uni=pd.read_csv(ROOT/'data/extended/processed/universe_20241231.csv');uni['known_at']=uni.known_at_assumption
    bars=aggregate_four_hour(hourly)
    cache=FeatureCache(daily,bars)
    base=json.loads((ROOT/'config/strategy_v2.json').read_text());study=json.loads((ROOT/'config/v2_tuning_study.json').read_text())
    signals=TuningSignals(calendar_path=ROOT/'data/v2/market_daily.csv',history_path=ROOT/'data/sector/industry_history.csv',index_path=ROOT/'data/sector/sector_index_daily.csv')
    # Per-date market state is independent of trial weights. Cache only causal data.
    dates=sorted(d for d in daily.date.unique() if '2024-12-31'<=d<=base['end'])
    signals.prewarm(dates,sorted(uni.symbol))
    CTX.update(daily=daily,bars=bars,universe=uni,cache=cache,signals=signals,base=base,study=study)
