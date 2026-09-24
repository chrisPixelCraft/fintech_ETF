"""V5 episode runner over the sealed Stage-1 ledger (spec §3.5).

Pipeline: strategy.generate_weights -> v5_planner.plan -> whole-lot orders ->
official-average fills / official-close sizing in src/v4_ledger.run_ledger.

Exactly as the V4 Stage-2 adapter (git show 72163500:src/v4_stage2_episode.py):
private function namespaces substitute the planner and an identity scorer; the
executed ledger/baseline code objects are the sealed Stage-1 ones, unmodified.
Every failure is preserved; a disqualified episode keeps only a forensic return.

Odd-lot classification (spec §5): v4_baseline marks FAIL_ROUND_LOT whenever the
book holds any non-lot residual (e.g. after a stock dividend). V5 never trades an
odd-held name (see src/v5_planner.py) and records such days as
ASSUMPTION_ODD_LOT. Original sealed metrics are never overwritten; the V5 view
is added as separate ``v5_*`` keys, applied identically to V5 and to A0_V3.
"""
from __future__ import annotations

import copy
import json
import traceback
from types import FunctionType

import numpy as np
import pandas as pd

from src import double_check_tuning, v4_baseline, v4_ledger, v5_planner
from src.v5_planner import ASSUMPTION_ODD_LOT


def _bind(function, **overrides):
    namespace = dict(function.__globals__)
    namespace.update(overrides)
    return FunctionType(function.__code__, namespace, function.__name__,
                        function.__defaults__, function.__closure__)


def _identity_score(rows, config):
    rows = rows.set_index('symbol', drop=False).copy()
    rows['score'] = 0.
    rows['entry_ok'] = rows.ready
    rows['exit'] = False
    return rows


def _json(value):
    def default(x):
        if isinstance(x, (np.integer,)):
            return int(x)
        if isinstance(x, (np.floating,)):
            return float(x)
        if isinstance(x, (pd.Timestamp,)):
            return str(x)
        return str(x)
    return json.dumps(value, sort_keys=True, default=default, allow_nan=True)


class StrategyPlanner:
    """Ledger planner callback: D-1 state -> strategy weights -> planned lots."""

    def __init__(self, strategy, panel, dates, planner_config=None):
        self.strategy, self.panel = strategy, panel
        self.dates = pd.DatetimeIndex(pd.to_datetime(dates)).normalize()
        self.planner_config = dict(planner_config or {})
        self.audit, self.predictions, self.plans = [], [], []

    def __call__(self, ranked, holdings, cash, nav, cfg, *args):
        observed = pd.Timestamp(ranked.date.iloc[0]).normalize()
        upcoming = self.dates[self.dates > observed]
        reason, orders, selected, v5 = 'TERMINAL_NO_PLAN', {}, list(holdings), {}
        if len(upcoming):
            decision = upcoming[0]
            prices = ranked.close.astype(float).copy()
            portfolio_state = dict(holdings=dict(holdings), cash=float(cash), nav=float(nav),
                                   previous_close=prices.copy(), observed_date=observed)
            competition_state = dict(decision_date=decision, remaining_sessions=len(upcoming),
                                     session_index=len(self.dates) - len(upcoming),
                                     episode_sessions=self.dates, initial_cash=float(cfg['initial_cash']))
            try:
                target = self.strategy.generate_weights(decision, self.panel, portfolio_state, competition_state)
                error = None
            except Exception as exc:  # preserved as an explicit failed plan
                target, error = None, f'{type(exc).__name__}: {exc}'
                v5['traceback'] = traceback.format_exc(limit=5)
            identity = str(getattr(target, 'attrs', {}).get('identity', '')) if target is not None else ''
            if error is not None:
                reason = 'INFEASIBLE_STRATEGY_ERROR:' + error
            elif target is None or 'target_weight' not in getattr(target, 'columns', []) or not len(target):
                reason = 'INFEASIBLE_STRATEGY_NO_TARGET:' + str(getattr(target, 'attrs', {}).get('reason', ''))
            else:
                weights = target['target_weight'].astype(float)
                weights.index = weights.index.astype(str)
                plan = v5_planner.plan(weights, prices, nav, holdings, cash, self.planner_config)
                v5 = dict(plan_status=plan.status, plan_reason=plan.reason, audit=plan.audit,
                          strategy_reason=str(target.attrs.get('reason', '')), identity=identity)
                if plan.status in ('OK', 'REPAIRED'):
                    reason, orders = 'V5_PLAN_' + plan.status, dict(plan.orders)
                elif plan.status == 'HOLD_FALLBACK':
                    reason = 'V5_HOLD_FALLBACK'
                else:
                    reason, orders = 'INFEASIBLE_V5_PLAN', dict(plan.orders)
                selected = sorted(plan.target_shares)
                saved = target.copy()
                saved.index = saved.index.astype(str)
                saved['symbol'] = saved.index
                saved['signal_date'] = observed
                saved['decision_date'] = decision
                saved['strategy_reason'] = v5['strategy_reason']
                saved['identity'] = identity
                self.predictions.append(saved.reset_index(drop=True))
            self.plans.append(dict(signal_date=str(observed.date()), decision_date=str(decision.date()),
                                   reason=reason, orders=_json(orders), detail=_json(v5)))
        assumptions = v5.get('audit', {}).get('assumptions', [])
        self.audit.append(dict(date=str(observed.date()), original_reason=reason, final_reason=reason,
            attempts=1, replanning_triggered=False, orders=len(orders),
            nominal_failures=reason if reason.startswith('INFEASIBLE') else '',
            hold_envelope_failures='NOT_CERTIFIED',
            v5_plan_status=v5.get('plan_status', ''), v5_assumptions=';'.join(assumptions),
            v5_repairs=';'.join(v5.get('audit', {}).get('repairs', [])),
            v5_failures=';'.join(v5.get('audit', {}).get('failures', []))))
        return orders, reason, selected


def normalize_episode_result(result):
    """V4 Stage-2 semantics: a disqualified episode keeps only a forensic return."""
    normalized = dict(result)
    metrics = dict(result['metrics'])
    normalized['metrics'] = metrics
    equity = result.get('equity', pd.DataFrame())
    requested = int(metrics.get('requested_sessions', 24))
    complete = bool(metrics.get('complete_period', False)
                    and len(equity) == requested
                    and not metrics.get('disqualified', False))
    metrics['complete_episode'] = complete
    if metrics.get('disqualified', False):
        metrics['episode_return'] = None
        metrics['forensic_partial_return'] = (float(equity.economic_nav.iloc[-1]
            / float(result['config']['initial_cash']) - 1) if len(equity) else None)
        metrics['status'] = 'FAILED'
        metrics['alpha_status'] = 'DISQUALIFIED_FORENSIC_ONLY'
    return normalized


def classify_odd_lot(result):
    """Add the V5 view: legacy odd-lot residual days are an assumption, not a failure.

    FAIL_ROUND_LOT is removed only when it comes solely from held residuals; any
    planner-side round-lot failure keeps it. Sealed keys are left untouched.
    """
    metrics = result['metrics']
    equity = result.get('equity', pd.DataFrame())
    audit = result.get('plan_audit', pd.DataFrame())
    odd_days = int(equity.odd_residual_names.gt(0).sum()) if 'odd_residual_names' in equity else 0
    plans = ';'.join(audit.final_reason.astype(str)) if 'final_reason' in audit else ''
    planner_round_lot = 'odd' in plans.lower() or 'ROUND_LOT' in plans
    if 'failure_reasons' in metrics:
        reasons = [r for r in str(metrics.get('failure_reasons') or '').split(';') if r]
    else:  # _failed() episodes carry only an episode_status
        reasons = [str(metrics.get('episode_status') or 'FAIL_OTHER')]
    if odd_days and not planner_round_lot:
        reasons = [r for r in reasons if r != 'FAIL_ROUND_LOT']
    eligible = bool(double_check_tuning.eligible(metrics))
    if not eligible and not reasons:
        reasons.append('FAIL_OTHER')
    measured = bool(eligible and not reasons)
    plan_odd_days = int(audit.v5_assumptions.astype(str).str.contains(ASSUMPTION_ODD_LOT).sum()) if 'v5_assumptions' in audit else 0
    metrics.update(v5_measured_pass=measured, v5_episode_status='PASS' if measured else reasons[0],
                   v5_failure_reasons=';'.join(dict.fromkeys(reasons)),
                   odd_lot_residual_days=odd_days, odd_lot_plan_days=plan_odd_days,
                   odd_lot_assumption=ASSUMPTION_ODD_LOT if odd_days or plan_odd_days else None,
                   odd_lot_policy='HOLD_LEGACY_ODD_REMAINDER_UNCHANGED')
    return result


def _episode_frames(session_dates, execution_data):
    """Restrict the official table to the episode neighbourhood (speed only).

    Rows from 30 calendar days before the first session are kept so the prior
    sizing session's official close is always present; extra rows are unused.
    """
    dates = pd.DatetimeIndex(pd.to_datetime(session_dates)).normalize()
    execution = execution_data
    if execution is not None and len(execution):
        day = pd.to_datetime(execution.date).dt.normalize()
        keep = (day >= dates[0] - pd.Timedelta(days=30)) & (day <= dates[-1])
        execution = execution.loc[keep].copy()
        execution['date'] = day.loc[keep]
        execution = execution.reset_index(drop=True)
    return dates, execution


def run_episode(daily, universe, base_config, session_dates, execution_data, panel, strategy,
                planner_config=None, prior_session_date=None):
    """Run one 24-session episode for a weight-emitting V5 strategy.

    strategy.generate_weights(decision_date, panel, portfolio_state, competition_state)
    returns a DataFrame indexed by symbol with column ``target_weight`` (plus
    optional score columns; attrs 'reason' and 'identity'). ``panel`` must be a
    causal v5_features panel; the strategy receives it whole and must only use
    rows dated before decision_date. Sizing uses official D-1 closes; fills use
    official daily averages; missing official data stay missing.
    """
    config = copy.deepcopy(base_config)
    if prior_session_date is not None:
        config['prior_session_date'] = str(pd.Timestamp(prior_session_date).date())
    config.update(strategy_id=str(getattr(strategy, 'identity', type(strategy).__name__)))
    dates, execution = _episode_frames(session_dates, execution_data)
    # Ledger view: only the prior session and the 24 sessions (as V4 Stage 2 did).
    before = panel.loc[(panel.date < dates[0]), 'date']
    prior = pd.Timestamp(config.get('prior_session_date', before.max() if len(before) else dates[0]))
    ledger_rows = panel.loc[panel.date.isin(dates.insert(0, prior))]
    ledger_panel = ledger_rows.copy(deep=False)
    ledger_panel.attrs = dict(panel.attrs, feature_parameters=config['deep_feature_params'],
                              warmup_sessions=config['warmup_sessions'])
    planner = StrategyPlanner(strategy, panel, dates, planner_config)
    ledger = _bind(v4_ledger.run_ledger, _score=_identity_score)
    episode = _bind(v4_baseline.run_episode, OfficialPlanner=lambda: planner, run_ledger=ledger)
    result = episode(daily, universe, config, [str(d.date()) for d in dates], execution,
                     execution_mode='official_average', features=ledger_panel, sizing_price_mode='official_close')
    result['predictions'] = pd.concat(planner.predictions, ignore_index=True) if planner.predictions else pd.DataFrame()
    result['v5_plans'] = pd.DataFrame(planner.plans, columns=['signal_date', 'decision_date', 'reason', 'orders', 'detail'])
    result['planner_config'] = dict(v5_planner.settings(planner_config))
    return classify_odd_lot(normalize_episode_result(result))


def run_v3_baseline(daily, universe, base_config, session_dates, execution_data, v3_features,
                    prior_session_date=None):
    """A0_V3 exactly as V4 Stage 2 ran it (v4_baseline, official average, official close).

    v3_features = strategy_24d.build_features(daily, base_config). The sealed
    result is returned unchanged apart from normalize_episode_result and the
    additive ``v5_*`` odd-lot classification keys.
    """
    config = dict(base_config)
    if prior_session_date is not None:
        config['prior_session_date'] = str(pd.Timestamp(prior_session_date).date())
    dates, execution = _episode_frames(session_dates, execution_data)
    result = v4_baseline.run_episode(daily, universe, config, [str(d.date()) for d in dates], execution,
                                     features=v3_features, sizing_price_mode='official_close')
    return classify_odd_lot(normalize_episode_result(result))
