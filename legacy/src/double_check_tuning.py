"""Strict measured-rule eligibility over the versioned penalty-aware ledger."""
from __future__ import annotations
import copy
from types import FunctionType
import numpy as np
import pandas as pd
from src import double_check_ledger as ledger, official_deep_tuning as original, tuning_2nd

FIELDS = original.FIELDS
FIXED = original.FIXED
OfficialPlanner = original.OfficialPlanner


def config_for(ctx, trial):
    config = original.config_for(ctx, trial)
    config['strategy_id'] = 'full_tuned_v2_double_check_' + trial['candidate_id']
    config['official_review_policy'].update(
        penalty_simulation='WHOLE_DAY_ROLLBACK_ONE_WARNING_THREE_DISQUALIFY',
        rollback_corporate_actions='RETAIN_ENTITLEMENTS_MARK_CURRENT_CLOSE_RESEARCH_ASSUMPTION',
        terminal_dividend='PERFORMANCE_CREDIT_SEPARATE_FROM_DAILY_CASH_COMPLIANCE',
        price_envelope='ZERO_OBSERVED_BOUND_BREACHES_RESEARCH_ELIGIBILITY_NOT_OFFICIAL_RULE',
        cap_cause='OWN_BUY_OR_TRANSACTION_CREATED_VERSUS_NO_TRADE_CURRENT_MARK_COUNTERFACTUAL',
        selection_cap_policy='ZERO_RAW_CAP_BREACHES_EVEN_WITHIN_OFFICIAL_PASSIVE_GRACE')
    return config


def run_model(ctx, config):
    for key, value in FIXED.items():
        if config.get(key) != value:
            raise ValueError('Fixed rule changed: ' + key)
    original.validate_params(config['full_tuning_params'])
    if config['universe_mode'] != ctx['track']:
        raise ValueError('Universe mismatch')
    engine = tuning_2nd.adapter(ctx)
    planner = OfficialPlanner()
    # Bind the new, explicit ledger to the causal feature cache. No global
    # monkeypatch and no rewriting of the frozen original source.
    namespace = dict(vars(ledger))
    namespace.update(compute_features=engine.module.compute_features,
                     score_candidates=engine.module.score_candidates,
                     make_plan_v2=planner)
    run = FunctionType(ledger.run_v2.__code__, namespace, argdefs=ledger.run_v2.__defaults__)
    result = run(ctx['daily'], ctx['universe'], copy.deepcopy(config), ctx['bars'])
    if len(result['holdings'].query("symbol == '2888.TW' and date >= '2025-07-24'")):
        raise ValueError('Unsupported multi-security merger held')
    if not np.isfinite(result['equity'][['nav', 'economic_nav', 'cash']].to_numpy()).all():
        raise ValueError('Nonfinite accounting')
    result['plan_audit'] = pd.DataFrame(planner.audit)
    # Disqualification breaks before another plan; completed runs have one
    # terminal plan that has not executed and must be excluded from counts.
    active = result['plan_audit']
    if not result['metrics']['disqualified']:
        active = active.iloc[:-1]
    primary = {prefix + key: result['metrics'][prefix + key]
               for prefix in ('', 'economic_') for key in
               ('total_return', 'max_drawdown', 'annualized_volatility', 'sharpe_zero_rf')}
    settlement = result['equity'].copy()
    # The performance credit must not conceal a final-session cash violation.
    settlement['nav'] = result['compliance_daily']['settled_nav'].to_numpy()
    settlement['cash_ratio'] = settlement.cash / settlement.nav
    result['metrics'].update(tuning_2nd.metrics(settlement, config['initial_cash']))
    result['metrics'].update(primary)
    result['metrics'].update(
        planner='DOUBLE_CHECK_OFFICIAL_RULE_SHADOW', universe_track=ctx['track'],
        no_valid_plan_days=int(active.final_reason.str.startswith('INFEASIBLE').sum()),
        hold_without_envelope_days=int(active.final_reason.eq('HOLD_REVALIDATED_CURRENT_ONLY').sum()),
        unfilled_orders=int(result['warnings'].issue.str.startswith('UNFILLED').sum()),
        official_compliance='UNKNOWN_BLOCK_SUBMISSION')
    return result


ZERO_COUNTS = ('measured_hard_breach_days', 'no_valid_plan_days', 'unfilled_orders',
               'simulated_warning_days', 'stale_held_price_days', 'hold_without_envelope_days',
               'execution_price_bound_breaches', 'raw_rule_breach_days')


def eligible(row):
    def finite_number(value):
        return (isinstance(value, (int, float, np.integer, np.floating))
                and not isinstance(value, (bool, np.bool_)) and np.isfinite(value))
    return (row.get('status') == 'COMPLETE' and row.get('complete_period') is True
            and row.get('disqualified') is False
            and all(finite_number(row.get(key)) and row[key] == 0 for key in ZERO_COUNTS)
            and all(finite_number(row.get(key)) for key in
                    ('total_return', 'max_drawdown', 'turnover_two_way')))


def choose(rows):
    candidates = [row for row in rows if eligible(row)]
    return min(candidates, key=lambda r: (-r['total_return'], r['max_drawdown'],
               r['turnover_two_way'], r['candidate_id'])) if candidates else None
