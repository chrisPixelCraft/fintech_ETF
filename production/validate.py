"""D-Plan checks before submission: the official schema, then the semantic rules of docs/task.md section 7.

    errors = validate(plan, holdings=..., close=..., nav=..., cash=..., rules=...)

Semantic checks (official verify_dplan.py is unreleased, U12): consecutive
ids per layer, C1 citation chain, holding coverage, decision <-> order
pairing and action meaning, C2 official lot formula, C13 no oversell, C12
posture and cash band at T-1 closes, C9/C11 post-trade count / caps / cash
estimate, file name and time stamps. An empty list means ready to submit.
"""
from __future__ import annotations

import json
import re
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path

import jsonschema
import numpy as np
import pandas as pd

from competition.rules import ROOT, CompetitionRules
from production.state import symbol_to_ticker, ticker_map

SCHEMA_PATH = ROOT / 'official_docs/D-Plan.schema.json'
HOLD_TOLERANCE = .02                # server default for "hold" (schema market_view.posture)
PLACEHOLDER_TEAM = 'TEAM_UNSET'


def schema_errors(plan: dict) -> list[str]:
    schema = json.loads(SCHEMA_PATH.read_text())
    checker = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
    return [f'SCHEMA:{"/".join(map(str, e.absolute_path))}:{e.message[:160]}' for e in checker.iter_errors(plan)]


def _sequence(items: list, key: str, prefix: str) -> list[str]:
    got = [x[key] for x in items]
    want = [f'{prefix}{i}' for i in range(1, len(items) + 1)]
    return [] if got == want else [f'ID_SEQUENCE:{prefix}:{got[:5]}...']


def _lots(weight: float, nav: float, close: float, lot: int) -> int:
    value = Decimal(str(weight)) * Decimal(str(nav)) / Decimal(str(close)) / lot
    return int(value.to_integral_value(rounding=ROUND_FLOOR)) * lot


def validate(plan: dict, *, holdings: dict, close: pd.Series, nav: float, cash: float, rules: CompetitionRules,
             filename: str | None = None, allow_placeholder_team: bool = False) -> list[str]:
    """All problems found (empty = submittable). ``holdings`` and ``close`` are keyed by panel symbol."""
    errors = schema_errors(plan)
    if errors:
        return errors
    tickers = ticker_map()
    symbol = {t: s for t, s in tickers.items()}
    held = {symbol_to_ticker(s): q for s, q in holdings.items() if q > 0}
    px = {symbol_to_ticker(s): float(v) for s, v in close.items() if np.isfinite(v)}
    src = {x['source_id'] for x in plan['sources']}
    obs = {x['obs_id'] for x in plan['observations']}
    inf = {x['inf_id'] for x in plan['inferences']}
    dec = {x['decision_id']: x for x in plan['decisions']}
    for items, key, prefix in ((plan['sources'], 'source_id', 'S'), (plan['observations'], 'obs_id', 'O'),
                               (plan['inferences'], 'inf_id', 'I'), (plan['decisions'], 'decision_id', 'D')):
        errors += _sequence(items, key, prefix)

    def cite(refs, pool, where):
        errors.extend(f'C1:{where}->{r}' for r in refs if r not in pool)

    for o in plan['observations']:
        cite(o['source_ref'], src, o['obs_id'])
    cite(plan['market_view']['basis_refs'], obs, 'market_view')
    for i in plan['inferences']:
        cite(i['premise_refs'], obs, i['inf_id'])
    for d in plan['decisions']:
        cite(d['inference_refs'], inf, d['decision_id'])
        for f in d.get('funding_for', []):
            if f not in dec or dec[f]['action'] not in ('BUY', 'ADD') or d['action'] not in ('TRIM', 'SELL_ALL'):
                errors.append(f'C14:{d["decision_id"]}->{f}')
    for n in plan['no_trade_decisions']:
        cite(n['reason_refs'], inf, 'no_trade:' + n['ticker'])
    for o in plan['orders']:
        cite([o['decision_ref']], set(dec), 'order:' + o['ticker'])

    decided = [d['ticker'] for d in plan['decisions']]
    kept = [n['ticker'] for n in plan['no_trade_decisions']]
    for t in set(decided + kept):
        if t not in tickers:
            errors.append(f'NOT_IN_UNIVERSE:{t}')
    if len(decided) != len(set(decided)) or len(kept) != len(set(kept)) or set(decided) & set(kept):
        errors.append('COVERAGE:DUPLICATE_TICKER')
    for t in held:
        if t not in decided and t not in kept:
            errors.append(f'COVERAGE:HELD_NOT_COVERED:{t}')
    errors += [f'COVERAGE:NO_TRADE_NOT_HELD:{t}' for t in kept if t not in held]

    signed = {}
    by_decision = {}
    for o in plan['orders']:
        by_decision.setdefault(o['decision_ref'], []).append(o)
        signed[o['ticker']] = o['shares'] if o['side'] == 'BUY' else -o['shares']
    for did, d in dec.items():
        rows = by_decision.get(did, [])
        if len(rows) != 1 or rows[0]['ticker'] != d['ticker']:
            errors.append(f'ORDER_PAIRING:{did}')
            continue
        t, q, have = d['ticker'], signed[d['ticker']], held.get(d['ticker'], 0.)
        if t not in px:
            errors.append(f'NO_CLOSE:{t}')
            continue
        target = _lots(d['target_weight'], nav, px[t], rules.lot_size)
        if target - have != q:
            errors.append(f'C2:{t}:formula {target - have} != order {q}')
        meaning = ('BUY' if have <= 0 and q > 0 else 'SELL_ALL' if have > 0 and have + q == 0
                   else 'ADD' if have > 0 and q > 0 else 'TRIM' if have > 0 and 0 < have + q < have else None)
        if meaning != d['action']:
            errors.append(f'ACTION:{t}:{d["action"]} but order means {meaning}')
        if d['target_weight'] > rules.cap(symbol.get(t, t)) + 1e-12:
            errors.append(f'C9:TARGET_OVER_CAP:{t}')
        if q < 0 and -q > have + 1e-6:
            errors.append(f'C13:OVERSELL:{t}')

    buy = sum(q * px[t] for t, q in signed.items() if q > 0 and t in px)
    sell = sum(-q * px[t] for t, q in signed.items() if q < 0 and t in px)
    net, cash_after = buy - sell, (cash - buy + sell) / nav
    post = plan['market_view']['posture']
    intent, (low, high) = post['net_exposure_intent'], post['target_cash_pct_range']
    if low > high:
        errors.append('C12:CASH_RANGE_INVERTED')
    if not low <= cash_after <= high:
        errors.append(f'C12:CASH {cash_after:.4f} outside [{low}, {high}]')
    if (intent == 'increase' and net <= 0) or (intent == 'reduce' and net >= 0) or \
            (intent == 'hold' and abs(net) > HOLD_TOLERANCE * nav):
        errors.append(f'C12:POSTURE {intent} vs net {net / nav:+.4f} NAV')

    after = {t: held.get(t, 0.) + signed.get(t, 0) for t in set(held) | set(signed)}
    after = {t: q for t, q in after.items() if q > 0}
    if not rules.min_positions <= len(after) <= rules.max_positions:
        errors.append(f'C11:COUNT:{len(after)}')
    for t, q in after.items():          # traded names only: price drift has a 5-session grace (rules p10 註3)
        if t in signed and t in px and q * px[t] / nav > rules.cap(symbol.get(t, t)) + 1e-9:
            errors.append(f'C9:WEIGHT_OVER_CAP:{t}')
    if not rules.cash_min <= cash_after < rules.cash_max_exclusive:
        errors.append(f'C9:CASH_RATIO:{cash_after:.4f}')

    meta = plan['agent_metadata']
    if pd.Timestamp(meta['run_completed_at']) < pd.Timestamp(meta['run_started_at']):
        errors.append('C6:COMPLETED_BEFORE_STARTED')
    if filename is not None and filename != f'D-Plan_{plan["team_id"]}_{plan["trade_date"]}.json':
        errors.append(f'FILENAME:{filename}')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', plan['team_id']) or \
            (plan['team_id'] == PLACEHOLDER_TEAM and not allow_placeholder_team):
        errors.append(f'TEAM_ID:{plan["team_id"]} (P-U6: set the organizer-assigned id)')
    return errors


def validate_file(path: Path | str, **context) -> list[str]:
    path = Path(path)
    return validate(json.loads(path.read_text()), filename=path.name, **context)
