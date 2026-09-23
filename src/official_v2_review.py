"""Literal D-Plan compatibility sensitivity, with frozen v2 alpha parameters.

This is a research adapter, not a certified submission engine. Corporate odd
holdings cannot be changed using both the supplied target-lot formula and the
whole-lot order constraint. Keep those names unchanged until official handling
is known; never sell their remainder or pretend it vanished.
"""
from __future__ import annotations

import copy
import inspect
import types
from decimal import Decimal, ROUND_FLOOR

import numpy as np

from src import compliance_planner, tuning_2nd


def whole_lots(quantity, lot=1000):
    return abs(float(quantity) / lot - round(float(quantity) / lot)) < 1e-10


def representable_weight(target, price, nav, cap):
    """Choose an interior point of the SAME target-lot bin, then round-trip.

    The declared weight is a sizing instruction, not the realized weight. Using
    the exact lower bin edge would make JSON/float rounding change an order.
    """
    if not whole_lots(target):
        raise ValueError('DPLAN_ODD_TARGET_UNREPRESENTABLE')
    if target == 0:
        return 0.
    q, p, n, maximum = map(lambda x: Decimal(str(x)), (target, price, nav, cap))
    lower = q * p / n
    upper = min((q + 1000) * p / n, maximum)
    if upper <= lower:
        raise ValueError('NO_INTERIOR_TARGET_WEIGHT_WITHIN_CAP')
    weight = float((lower + upper) / 2)
    recovered = (Decimal(str(weight)) * n / p / 1000).to_integral_value(rounding=ROUND_FLOOR) * 1000
    if recovered != q:
        raise ValueError('DPLAN_WEIGHT_ROUNDTRIP_FAILED')
    return weight


def isolated_planner():
    """Clone the frozen planner and exclude odd-held names from its actions.

    Matching exact source fragments is deliberate: a changed upstream planner
    requires review instead of silently applying these policy changes elsewhere.
    """
    source = inspect.getsource(compliance_planner)
    changes = [
        ('names = list(dict.fromkeys(vacancies + retained))',
         'names = [s for s in dict.fromkeys(vacancies + retained) if whole_lots(holdings.get(s, 0.), lot)]', 2),
        ('for symbol in sorted(violations):\n        maximum',
         'for symbol in sorted(violations):\n        if not whole_lots(holdings[symbol], lot):\n            continue\n        maximum', 1),
        ('symbol for symbol in holdings if bool(ranked.at[symbol, "exit"])',
         'symbol for symbol in holdings if bool(ranked.at[symbol, "exit"]) and whole_lots(holdings[symbol], lot)', 1),
        ('if symbol in holdings and holdings[symbol] >= lot\n',
         'if symbol in holdings and holdings[symbol] >= lot and whole_lots(holdings[symbol], lot)\n', 1),
        ('for symbol, shares in holdings.items():\n        if symbol not in selected:',
         'for symbol, shares in holdings.items():\n        if not whole_lots(shares, lot):\n            continue\n        if symbol not in selected:', 1),
    ]
    for before, after, count in changes:
        if source.count(before) != count:
            raise ValueError('Frozen planner changed; review adapter: ' + before)
        source = source.replace(before, after)
    module = types.ModuleType('official_v2_private_planner')
    # dataclass requires its defining module to be discoverable during creation.
    import sys
    sys.modules[module.__name__] = module
    module.__dict__['whole_lots'] = whole_lots
    exec(compile(source, '<official-dplan-planner>', 'exec'), module.__dict__)
    return module


def run_guard(ctx, config):
    cfg = copy.deepcopy(config)
    cfg['official_review_policy'] = {
        'selection': 'NO_RETUNING', 'odd_holdings': 'HOLD_UNTIL_OFFICIAL_FORMULA_CLARIFIED',
        'weight_serialization': 'INTERIOR_SAME_LOT_BIN', 'active_share': 'UNKNOWN_BLOCK_SUBMISSION',
        'penalty_simulation': 'NOT_APPLIED_SHADOW_DIAGNOSTIC',
    }
    engine = tuning_2nd.adapter(ctx)
    planner = isolated_planner()
    engine.module.make_plan_v2 = planner.make_plan_v2
    source = inspect.getsource(engine.module.run_v2) if ctx['track'] == 'historical_pit' else inspect.getsource(__import__('src.backtest_v2', fromlist=['run_v2']).run_v2)
    if ctx['track'] == 'official_ex_post':
        before = "if (universe.known_at > cutoff.tz_convert('UTC')).any():"
        source = source.replace(before, "if (universe.known_at > cutoff.tz_convert('UTC')).any() and not c.get('ex_post_fixed_universe', False):")
    before = "target_weight=(holdings.get(s, 0.) + q) * sizing_price / nav,"
    if source.count(before) != 1:
        raise ValueError('Frozen order serialization changed')
    source = source.replace(before, "target_weight=representable_weight(holdings.get(s, 0.) + q, sizing_price, nav, _cap(s, c)),")
    engine.module.representable_weight = representable_weight
    exec(compile(source, '<official-dplan-ledger>', 'exec'), engine.module.__dict__)
    result = engine.run_v2(ctx['daily'], ctx['universe'], cfg, ctx['bars'])
    if len(result['holdings'].query("symbol == '2888.TW' and date >= '2025-07-24'")):
        raise ValueError('Unsupported multi-security merger held')
    if not np.isfinite(result['equity'].economic_nav).all():
        raise ValueError('Invalid accounting')
    result['metrics'].update(tuning_2nd.metrics(result['equity'], cfg['initial_cash']))
    result['metrics'].update(planner='DPLAN_ODD_HOLD_GUARD', universe_track=ctx['track'],
        trade_count=len(result['trades']), official_compliance='UNKNOWN_BLOCK_SUBMISSION')
    return result
