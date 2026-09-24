"""Bounded A-core tuning and causal replanning under the supplied D-Plan contract.

Historical accounting is research evidence. Missing official Active Share,
corporate-action semantics and platform acceptance never become a live PASS.
"""
from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src import backtest_v2, tuning_2nd, tuning_a_deep
from src.official_v2_review import isolated_planner, representable_weight as strict_weight, whole_lots

ROOT = Path(__file__).resolve().parents[1]
FIELDS = (*tuning_a_deep.FIELDS, 'cash_guard_ratio')
FIXED = {k: v for k, v in tuning_a_deep.FIXED.items() if k != 'cash_guard_ratio'}


def representable_weight(target, price, nav, cap):
    """Remove only sub-microshare floating error at an exact board-lot boundary.

    The research ledger multiplies float corporate-action ratios. A mathematical
    1,000 shares can become 999.9999999990687. This never rounds an actual odd
    entitlement to a lot; official live inventory remains integer-valued.
    """
    exact_lot = round(float(target) / 1000) * 1000
    if abs(float(target) - exact_lot) > 1e-6:
        raise ValueError('DPLAN_ODD_TARGET_UNREPRESENTABLE')
    return strict_weight(exact_lot, price, nav, cap)


def validate_params(params):
    if set(params) != set(FIELDS):
        raise ValueError('Unexpected full-tuning parameter fields')
    tuning_a_deep.validate_params({k: params[k] for k in tuning_a_deep.FIELDS})
    if not .08 <= params['cash_guard_ratio'] <= .18:
        raise ValueError('Cash guard must retain the declared safety buffer')


def config_for(ctx, trial):
    validate_params(trial['params'])
    old_trial = {**trial, 'params': {k: trial['params'][k] for k in tuning_a_deep.FIELDS}}
    cfg = tuning_a_deep.config_for(ctx, old_trial)
    cfg.update(strategy_id='full_tuned_v2_' + trial['candidate_id'],
               cash_guard_ratio=trial['params']['cash_guard_ratio'],
               full_tuning_params=copy.deepcopy(trial['params']))
    cfg['study_policy'] = dict(parameter_search=True, historical_period_is_development=True,
        official_live_submission='BLOCK_IF_UNKNOWN', selection_scope='EX_POST_DEVELOPMENT',
        selection='ZERO_MEASURED_HARD_THEN_BOOK_NAV_RETURN_THEN_MDD',
        primary_track='official_ex_post', historical_track='SAME_PARAMETERS_SENSITIVITY')
    cfg['official_review_policy'] = dict(odd_holdings='HOLD_UNTIL_OFFICIAL_FORMULA_CLARIFIED',
        weight_serialization='INTERIOR_SAME_LOT_BIN', active_share='UNKNOWN_BLOCK_SUBMISSION',
        penalty_simulation='NOT_APPLIED_SHADOW_DIAGNOSTIC',
        fallback='EXPAND_ELIGIBLE_BUY_POOL_THEN_REVALIDATE_HOLD')
    return cfg


class OfficialPlanner:
    """Retry funded, disjoint orders, then independently check a no-trade plan.

    Existing holdings are changed only for exits, replacements or a rule repair.
    Replanning may use up to 30 eligible names; it never relaxes hard limits.
    A current-state HOLD is distinguished from a stress-envelope HOLD.
    """
    def __init__(self):
        self.base = isolated_planner()
        self.audit = []

    def _nominal_failures(self, holdings, cash, ranked, config):
        failures = []
        if any(s not in ranked.index for s in holdings):
            return ['MISSING_HELD_QUOTE']
        nav = cash + sum(q * float(ranked.at[s, 'close']) for s, q in holdings.items())
        if nav <= 0 or cash < 0 or cash / nav >= .25:
            failures.append('CASH')
        if not config['min_count'] <= len(holdings) <= config['max_count']:
            failures.append('COUNT')
        for s, q in holdings.items():
            if q < 0 or q * float(ranked.at[s, 'close']) / nav > self.base._cap(s, config) + 1e-10:
                failures.append('POSITION:' + s)
        return failures

    def __call__(self, ranked, holdings, cash, nav, config, buy_phase=False, desired_symbols=None):
        ranked = self.base._rank(ranked)
        orders, original, selected = self.base.make_plan_v2(
            ranked, holdings, cash, nav, config, buy_phase, desired_symbols)
        reason = original
        attempts = 1
        triggered = not orders and ('INFEASIBLE' in original or original.startswith('WAIT'))
        if triggered and all(s in ranked.index for s in holdings):
            # Keep every existing name in the repair set. Add only entry-eligible
            # names, using today's causal ranking, within the 30-name ceiling.
            pool = [s for s in ranked.index if s in holdings]
            pool += [s for s in ranked.index if s not in holdings and bool(ranked.at[s, 'entry_ok'])]
            pool = pool[:int(config['max_count'])]
            caps = self.base._weight_cap_orders(ranked, holdings, cash, config)
            for count in sorted(set([int(config['target_count']), 25, 30])):
                repair = dict(config, target_count=count)
                chosen = pool[:max(count, len(holdings))]
                attempts += 1
                if caps:
                    candidate = self.base._prefunded_mixed_plan(ranked, holdings, cash, nav,
                        repair, chosen, set(), caps)
                else:
                    candidate = self.base._buy_plan(ranked, holdings, cash, nav, repair,
                        chosen, set(), require_guard_target=True)
                if candidate and not self.base.stress_violations(candidate, holdings, cash,
                        ranked, config, allow_mixed=True):
                    orders, selected, reason = candidate, chosen, 'REPLAN_FUNDED_RULE_REPAIR'
                    break
        nominal = self._nominal_failures(holdings, cash, ranked, config) if not orders else []
        envelope = []
        if not orders:
            if all(s in ranked.index for s in holdings):
                envelope = self.base.stress_violations({}, holdings, cash, ranked, config, cash_ceiling=.25)
            else:
                envelope = ['MISSING_HELD_QUOTE']
            if not nominal:
                reason = 'HOLD_REVALIDATED_ENVELOPE' if not envelope else 'HOLD_REVALIDATED_CURRENT_ONLY'
            else:
                reason = 'INFEASIBLE_NO_VALID_PLAN:' + ';'.join(nominal)
        # Validate every emitted order again, including odd-held names and the
        # exact official target-weight round trip. Never silently drop an order.
        for s, q in orders.items():
            if not whole_lots(q) or not whole_lots(holdings.get(s, 0)):
                raise ValueError('Unrepresentable odd holding/order')
            representable_weight(holdings.get(s, 0) + q, ranked.at[s, 'close'], nav, self.base._cap(s, config))
        self.audit.append(dict(date=str(pd.Timestamp(ranked.date.iloc[0]).date()),
            original_reason=original, final_reason=reason, attempts=attempts, replanning_triggered=triggered,
            orders=len(orders), nominal_failures=';'.join(nominal), hold_envelope_failures=';'.join(envelope)))
        return orders, reason, selected


def run_model(ctx, config):
    for key, value in FIXED.items():
        if config.get(key) != value:
            raise ValueError('Fixed competition/execution setting changed: ' + key)
    if config['universe_mode'] != ctx['track']:
        raise ValueError('Universe mismatch')
    engine = tuning_2nd.adapter(ctx)
    planner = OfficialPlanner()
    engine.module.make_plan_v2 = planner
    source = inspect.getsource(backtest_v2.run_v2)
    before = "if (universe.known_at > cutoff.tz_convert('UTC')).any():"
    source = source.replace(before, "if (universe.known_at > cutoff.tz_convert('UTC')).any() and not c.get('ex_post_fixed_universe', False):")
    before = 'target_weight=(holdings.get(s, 0.) + q) * sizing_price / nav,'
    if source.count(before) != 1:
        raise ValueError('Review changed upstream order serialization')
    source = source.replace(before, 'target_weight=representable_weight(holdings.get(s, 0.) + q, sizing_price, nav, _cap(s, c)),')
    engine.module.representable_weight = representable_weight
    exec(compile(source, '<full-tuned-official-ledger>', 'exec'), engine.module.__dict__)
    result = engine.module.run_v2(ctx['daily'], ctx['universe'], copy.deepcopy(config), ctx['bars'])
    if len(result['holdings'].query("symbol == '2888.TW' and date >= '2025-07-24'")):
        raise ValueError('Unsupported multi-security merger held')
    if not np.isfinite(result['equity'].economic_nav).all():
        raise ValueError('Nonfinite accounting')
    result['plan_audit'] = pd.DataFrame(planner.audit)
    audit = result['plan_audit'].iloc[:-1]
    result['metrics'].update(tuning_2nd.metrics(result['equity'], config['initial_cash']))
    result['metrics'].update(planner='OFFICIAL_DPLAN_REPLAN', universe_track=ctx['track'],
        trade_count=len(result['trades']), replanning_days=int(audit.replanning_triggered.sum()),
        recovered_trade_days=int(audit.final_reason.eq('REPLAN_FUNDED_RULE_REPAIR').sum()),
        validated_hold_days=int(audit.final_reason.str.startswith('HOLD_REVALIDATED').sum()),
        hold_without_envelope_days=int(audit.final_reason.eq('HOLD_REVALIDATED_CURRENT_ONLY').sum()),
        no_valid_plan_days=int(audit.final_reason.str.startswith('INFEASIBLE').sum()),
        unfilled_orders=int(result['warnings'].issue.str.startswith('UNFILLED').sum()),
        official_compliance='UNKNOWN_BLOCK_SUBMISSION')
    return result


def eligible(row):
    return (row.get('status') == 'COMPLETE' and row['measured_hard_breach_days'] == 0
            and row['no_valid_plan_days'] == 0 and row['unfilled_orders'] == 0)


def choose(rows):
    valid = [row for row in rows if eligible(row)]
    return min(valid, key=lambda r: (-r['total_return'], r['max_drawdown'], r['turnover_two_way'], r['candidate_id'])) if valid else None
