"""Second-round research engine: explicit universe assumptions, hard eligibility.

An official-list retrospective scenario is intentionally *not* point-in-time.
Only its fixed-membership guard is relaxed, with actual known_at preserved.
Prices, signal timestamps, order quantities and execution stay causal.
"""
from pathlib import Path
import copy
import inspect
import numpy as np
import pandas as pd
from src import replay_context as old
from src.backtest import aggregate_four_hour
from src.tuning_features import FeatureCache, IsolatedEngine
from src.tuning_signals import TuningSignals

ROOT = Path(__file__).resolve().parents[1]
TRACKS = ('historical_pit', 'official_ex_post')


def context(track='historical_pit'):
    if track not in TRACKS:
        raise ValueError('Unknown universe track')
    if track == 'historical_pit':
        old.setup_context()
        ctx = dict(old.CTX)
    else:
        folder = ROOT / 'data/tuning_2nd/official_universe/processed'
        import json
        readiness = json.loads((folder / 'readiness.json').read_text())
        if readiness.get('status') != 'DATA_READY':
            raise ValueError('Official scenario dataset has unresolved blockers')
        daily = pd.read_csv(folder / 'daily.csv')
        hourly = pd.read_csv(folder / 'hourly.csv')
        universe = pd.read_csv(folder / 'universe.csv')
        if len(universe) != 150 or universe.symbol.nunique() != 150:
            raise ValueError('Official scenario requires exactly 150 distinct identities')
        if not set(universe.symbol).issubset(set(daily.symbol)):
            raise ValueError('Incomplete official daily coverage')
        bars = aggregate_four_hour(hourly)
        import json
        signals = TuningSignals(calendar_path=folder / 'daily.csv')
        signals.prewarm(sorted(d for d in daily.date.unique() if '2024-12-31' <= d <= '2026-09-21'), sorted(universe.symbol))
        ctx = dict(daily=daily, bars=bars, universe=universe,
                   cache=FeatureCache(daily, bars), signals=signals,
                   base=json.loads((ROOT / 'config/strategy_v2.json').read_text()),
                   study=json.loads((ROOT / 'config/v2_tuning_study.json').read_text()))
    expected_dates = set(pd.read_csv(ROOT / 'data/v2/market_daily.csv', usecols=['date']).date)
    actual_dates = set(ctx['daily'].date)
    expected_live = {d for d in expected_dates if '2025-01-01' <= d <= '2026-09-21'}
    actual_live = {d for d in actual_dates if '2025-01-01' <= d <= '2026-09-21'}
    if actual_live != expected_live or len(actual_live) != 417:
        raise ValueError('Market calendar mismatch: expected 417 common sessions')
    ctx['track'] = track
    return ctx


def metrics(equity, initial=1e9, min_count=20, max_count=30):
    eq = equity.fillna('')
    m = old.period_metrics(eq, initial)
    m['economic_annualized_volatility'] = m.pop('annualized_volatility')
    m['economic_sharpe_zero_rf'] = m.pop('sharpe_zero_rf')
    v = eq.violations.astype(str)
    cash = eq.cash.astype(float)
    hard = (eq.cash_ratio.astype(float).ge(.25) | cash.lt(-1e-6)
            | eq.holdings.astype(int).lt(min_count) | eq.holdings.astype(int).gt(max_count)
            | v.str.contains('NON_WHITELIST', regex=False)
            | eq.active_cap_breaches.ne('') | eq.overdue_passive_caps.ne(''))
    m.update(measured_hard_breach_days=int(hard.sum()),
             cash_breach_days=int(eq.cash_ratio.astype(float).ge(.25).sum()),
             holding_count_breach_days=int((eq.holdings.astype(int).lt(min_count) | eq.holdings.astype(int).gt(max_count)).sum()),
             whitelist_breach_days=int(v.str.contains('NON_WHITELIST', regex=False).sum()),
             active_cap_breach_days=int(eq.active_cap_breaches.ne('').sum()),
             overdue_passive_cap_days=int(eq.overdue_passive_caps.ne('').sum()),
             infeasible_executed_days=int(eq.executed_plan.astype(str).str.contains('INFEASIBLE', regex=False).sum()),
             cash_ratio_max=float(eq.cash_ratio.astype(float).max()),
             cash_ratio_min=float(eq.cash_ratio.astype(float).min()),
             average_stock_exposure=float(1-eq.cash_ratio.astype(float).mean()),
             holdings_min=int(eq.holdings.min()), holdings_max=int(eq.holdings.max()),
             observed_rule_eligible=not hard.any(),
             official_compliance='UNKNOWN_ACTIVE_SHARE_BLOCK_SUBMISSION')
    return m


def choose(rows):
    """No least-violating fallback. Missing evidence never becomes PASS."""
    eligible = [r for r in rows if r.get('status') == 'COMPLETE' and r['measured_hard_breach_days'] == 0]
    return min(eligible, key=lambda r: (-r['economic_total_return'], r['economic_max_drawdown'], r['candidate_id'])) if eligible else None


def config_for(ctx, trial, strategy, guard):
    c = old.trial_config(ctx['base'], ctx['study'], trial, strategy)
    c.update(cash_guard_ratio=guard, cash_guard_headroom=.02,
             universe_mode=ctx['track'], ex_post_fixed_universe=ctx['track'] == 'official_ex_post')
    c['study_policy'].update(selection='ZERO_MEASURED_HARD_THEN_NET_RETURN_THEN_MDD',
                             formal_compliance='UNKNOWN_BLOCK_SUBMISSION',
                             historical_period_is_development=True)
    return c


def adapter(ctx, use_new_planner=True):
    engine = IsolatedEngine(ctx['cache'])
    if use_new_planner:
        from src.compliance_planner import make_plan_v2
        engine.module.make_plan_v2 = make_plan_v2
    if ctx['track'] == 'official_ex_post':
        # Retain actual publication dates. This explicit scenario is knowingly
        # membership-biased and must never be described as historical PIT.
        source = inspect.getsource(engine.module.run_v2)
        old_guard = "if (universe.known_at > cutoff.tz_convert('UTC')).any():"
        new_guard = "if (universe.known_at > cutoff.tz_convert('UTC')).any() and not c.get('ex_post_fixed_universe', False):"
        if source.count(old_guard) != 1:
            raise ValueError('Source guard changed; scenario adapter requires review')
        exec(compile(source.replace(old_guard, new_guard), '<explicit-official-ex-post-scenario>', 'exec'), engine.module.__dict__)
    return engine


def run_model(ctx, strategy, config):
    engine = adapter(ctx, use_new_planner=strategy in ('A', 'B', 'C'))
    callback = None
    if strategy in ('B', 'C'):
        p = config['tuning_params']
        callback = ctx['signals'].make_callback(strategy, dict(target_count=config['target_count'], min_count=config['min_count'],
            sector_top_fraction=p['sector_top_fraction'], sector_short_weight=p['sector_short_weight'],
            sector_fallback_mode=p['sector_fallback_mode'], c_alpha=p['c_alpha']))
    if strategy == '0050':
        from src.backtest_v2 import run_buy_hold_v2
        result = run_buy_hold_v2(ctx['daily'], config)
    else:
        result = engine.run_v2(ctx['daily'], ctx['universe'], copy.deepcopy(config), ctx['bars'], signal_transform=callback)
    if len(result['holdings'].query("symbol == '2888.TW' and date >= '2025-07-24'")):
        raise ValueError('Unsupported multi-security merger held')
    eq = result['equity']
    if not np.isfinite(eq.economic_nav.astype(float)).all():
        raise ValueError('Nonfinite accounting')
    result['metrics'].update(metrics(eq, config['initial_cash'], 1 if strategy == '0050' else 20, 1 if strategy == '0050' else 30))
    result['metrics'].update(trade_count=len(result['trades']), universe_track=ctx['track'],
                             planner='COMPLIANCE_GUARD_V2' if strategy in ('A', 'B', 'C') else 'FIXED_LEGACY_PLANNER')
    result['metrics']['official_compliance'] = ('UNKNOWN_OFFICIAL_WHITELIST_AND_ACTIVE_SHARE_BLOCK_SUBMISSION' if ctx['track'] == 'historical_pit' else 'UNKNOWN_ACTIVE_SHARE_BLOCK_SUBMISSION')
    result['snapshots']['universe_mode'] = ctx['track']
    if ctx['track'] == 'official_ex_post':
        result['snapshots']['official_whitelist_status'] = 'FIXED_OFFICIAL_150_RETROSPECTIVE_MEMBERSHIP_BIAS'
    return result
