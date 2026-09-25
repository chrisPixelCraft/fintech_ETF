"""Independent reconstruction of every attempted/settled 24-day Yahoo ledger.

No producer accounting, warning, eligibility or metric functions are imported.
An audit PASS authenticates internal arithmetic against supplied market inputs;
it does not establish official platform acceptance or missing Active Share.
"""
from __future__ import annotations
from collections import Counter
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd


def _check(condition, message):
    if not condition:
        raise AssertionError(message)


def _near(actual, expected, label, atol=.005):
    _check(math.isfinite(float(actual)) and math.isfinite(float(expected))
           and math.isclose(float(actual), float(expected), rel_tol=1e-11, abs_tol=atol),
           f'{label}: {actual!r} != {expected!r}')


def _source(ctx):
    """Cache only immutable raw-source preparation across trials in one worker."""
    signature = (id(ctx['daily']), id(ctx['universe']), id(ctx.get('session_dates')))
    cached = ctx.get('_double_check_audit_source')
    if cached is not None and cached[0] == signature:
        return cached[1]
    frame = ctx['daily'].copy()
    frame['date'] = pd.to_datetime(frame.date).dt.strftime('%Y-%m-%d')
    _check(not frame.duplicated(['date', 'symbol']).any(), 'Duplicate market observations')
    universe = set(ctx['universe'].symbol.astype(str))
    market = {}
    for r in frame.itertuples(index=False):
        market.setdefault(r.date, {})[str(r.symbol)] = r
    source = dict(market=market, sessions=sorted(str(pd.Timestamp(d).date()) for d in ctx.get('session_dates', sorted(market))), universe=universe)
    ctx['_double_check_audit_source'] = (signature, source)
    return source


def _groups(frame, key):
    return {str(day): list(batch.itertuples(index=False)) for day, batch in frame.groupby(key, sort=False)}


def _metrics(values, initial):
    wealth = np.r_[initial, np.asarray(values, dtype=float)]
    changes = wealth[1:] / wealth[:-1] - 1
    sigma = float(np.std(changes, ddof=1)) if len(changes) > 1 else 0.
    return dict(total_return=float(wealth[-1] / initial - 1),
                max_drawdown=float(1 - np.min(wealth / np.maximum.accumulate(wealth))),
                annualized_volatility=float(sigma * np.sqrt(252)),
                sharpe_zero_rf=float(changes.mean() / sigma * np.sqrt(252)) if sigma > 0 else None)


def _audit_ledger(result, ctx):
    """Return independently reconstructed metrics or raise AssertionError.

    ``ctx`` requires raw ``daily`` and ``universe`` DataFrames. ``result`` is
    the in-memory result returned by double_check_tuning.run_model, or the
    underlying ledger (tests). Failed/DQ ledgers are audited as well as winners.
    """
    source = _source(ctx)
    market, sessions, universe = source['market'], source['sessions'], source['universe']
    cfg = result['config']
    calendar = [d for d in sessions if str(cfg['start']) <= d <= str(cfg['end'])]
    prior = [d for d in sessions if d < calendar[0]]
    _check(bool(prior), 'Missing sizing session')
    initial_day = prior[-1]
    eq = result['equity']
    compliance = result['compliance_daily']
    dates = list(eq.date.astype(str))
    _check(bool(dates) and dates == calendar[:len(dates)], 'Realized calendar has gaps/duplicates')
    _check(list(compliance.date.astype(str)) == dates, 'Compliance coverage differs')
    _check(eq.submission_status.eq('BLOCK_SUBMISSION').all()
           and eq.active_share_status.str.startswith('UNKNOWN').all(), 'False formal certification')
    orders = _groups(result['orders'], 'signal_date')
    trades = _groups(result['trades'], 'date')
    rejected = _groups(result['rejected_trades'], 'date')
    positions = _groups(result['holdings'], 'date')
    snapshot_rows = list(result['snapshots'].itertuples(index=False))
    snapshots = {str(r.date): r for r in snapshot_rows}
    _check(len(snapshots) == len(snapshot_rows), 'Duplicate signal snapshots')
    _check(set(trades) <= set(dates) and set(rejected) <= set(dates)
           and set(positions) <= set(dates), 'Artifact outside realized calendar')
    actual_dq = bool(compliance.iloc[-1].disqualified)
    signal_days = [initial_day] + (dates[:-1] if actual_dq else dates)
    _check(list(snapshots) == signal_days, 'Signal calendar not prior-session causal')
    _check(set(orders) <= set(signal_days), 'Orders outside signal calendar')
    eqrows = {str(r.date): r for r in eq.itertuples(index=False)}
    cr = {str(r.date): r for r in compliance.itertuples(index=False)}
    episode_sessions = [initial_day] + calendar
    following = dict(zip(episode_sessions[:-1], episode_sessions[1:]))
    cash, receivable = float(cfg['initial_cash']), 0.
    holdings, prices, ages = {}, {}, {}
    # Every episode starts empty. Its first orders require a quote on exactly
    # initial_day; later orders likewise require the immediately prior quote.
    # Earlier marks cannot value any reachable holding and need not be replayed.
    prices.update({s: float(r.close) for s, r in market.get(initial_day, {}).items()
                   if s in universe and np.isfinite(r.close) and r.close > 0})
    warning_count = stale_days = bound_count = unfilled_count = rejected_count = 0
    hard_days = cash_breach_days = active_days = overdue_days = count_days = raw_breach_days = 0
    fees_total = taxes_total = turnover_total = 0.
    nav_values, economic_values, expected_warnings = [], [], []
    previous_nav = cash
    accepted_fill_count = 0
    max_participation = 0.
    unknown_participation = False

    def cap(symbol):
        return float(cfg['tsmc_max_weight'] if symbol.split('.')[0] == '2330' else cfg['max_weight'])

    def book(q, c):
        return c + sum(n * prices[s] for s, n in q.items())

    def basic(q, c):
        value = book(q, c)
        failures = []
        if not cfg['min_count'] <= len(q) <= cfg['max_count']:
            failures.append('HOLDING_COUNT')
        if c < -1e-6:
            failures.append('NEGATIVE_CASH')
        if value <= 0 or c / value >= .25:
            failures.append('CASH_GE_25_PERCENT')
        for s, n in q.items():
            if s not in universe:
                failures.append('NON_WHITELIST:' + s)
            if value > 0 and n * prices[s] / value > cap(s) + 1e-10:
                failures.append('WEIGHT_CAP:' + s)
        return failures

    def cap_ages(q, c, history, bought, counterfactual=None):
        value = book(q, c)
        before_value = book(*counterfactual) if counterfactual is not None else None
        new, active, passive = {}, [], []
        for s, n in q.items():
            if value <= 0 or n * prices[s] / value > cap(s) + 1e-10:
                new[s] = history.get(s, 0) + 1
                was_within = (before_value is not None and before_value > 0
                    and counterfactual[0].get(s, 0.) * prices[s] / before_value <= cap(s) + 1e-10)
                (active if s in bought or was_within else passive).append(s)
        return new, active, passive

    def check_plan(day):
        signal = snapshots[day]
        _check((signal.decision_date or None) == following.get(day), day + ': wrong next session')
        if following.get(day):
            cutoff = pd.Timestamp(following[day]).tz_localize('Asia/Taipei') + pd.Timedelta(hours=8, minutes=55)
            _check(pd.Timestamp(signal.cutoff_at) == cutoff, 'Decision cutoff mismatch')
        _check(signal.submission_status == 'BLOCK_SUBMISSION', 'Snapshot certification')
        batch = orders.get(day, [])
        names = [r.symbol for r in batch]
        _check(len(names) == len(set(names)), day + ': duplicate/opposing orders')
        value = book(holdings, cash)
        for r in batch:
            _check(r.symbol in universe and r.symbol in market[day], 'Order lacks official identity/close')
            quantity = float(r.shares)
            _check(quantity != 0 and abs(quantity / 1000 - round(quantity / 1000)) < 1e-10, 'Nonlot/zero order')
            held = holdings.get(r.symbol, 0.)
            _check(abs(held - round(held / 1000) * 1000) <= 1e-6, 'Odd holding changed')
            _check(quantity >= -held - 1e-6, 'Prior-holding oversale')
            _near(r.sizing_price, market[day][r.symbol].close, 'Prior exchange close', 1e-9)
            _near(r.signal_nav, value, 'Prior book NAV')
            _near(r.target_shares, held + quantity, 'Target inventory', 1e-6)
            _near(r.odd_entitlement, held % 1000, 'Recorded odd entitlement', 1e-6)
            _check(0 <= r.target_weight <= cap(r.symbol), 'Declared weight cap')
            w, n, p = (Decimal(str(x)) for x in (r.target_weight, r.signal_nav, r.sizing_price))
            target = int((w * n / p / 1000).to_integral_value(rounding=ROUND_FLOOR)) * 1000
            _near(target, held + quantity, 'Official Decimal sizing formula', 1e-6)
            _check(r.reason == signal.plan_reason, 'Order reason does not match decision')
        if signal.plan_reason.startswith('HOLD_REVALIDATED'):
            _check(not batch and not basic(holdings, cash), 'Invalid nominal HOLD')
            _check(all(s in market.get(day, {}) and np.isfinite(market[day][s].close) and market[day][s].close > 0 for s in holdings), 'HOLD relies on stale quotes')
            lower, upper = float(cfg['price_lower_buffer']), float(cfg['price_buffer'])
            values = {s: q * prices[s] for s, q in holdings.items()}
            low_nav = cash + lower * sum(values.values())
            envelope = (cash >= -1e-6 and low_nav > 0 and cash/low_nav < .25-1e-12
                        and all(upper*v/(low_nav+(upper-lower)*v) <= cap(s)+1e-10
                                for s, v in values.items()))
            _check(envelope == (signal.plan_reason == 'HOLD_REVALIDATED_ENVELOPE'),
                   'False HOLD price-envelope status')
        return batch

    pending = check_plan(initial_day)
    for index, day in enumerate(dates):
        row, comp, raw = eqrows[day], cr[day], market.get(day, {})
        signal_day = initial_day if index == 0 else dates[index - 1]
        _check(row.executed_plan == snapshots[signal_day].plan_reason, 'Same-day/lookahead plan')
        stale = [s for s in holdings if s not in raw or not np.isfinite(raw[s].close)
                 or raw[s].close <= 0 or not np.isfinite(raw[s].volume) or raw[s].volume < 0
                 or not bool(getattr(raw[s], 'valid_price', True))
                 or not bool(getattr(raw[s], 'valid_for_research', True))]
        for s, n in list(holdings.items()):
            if s in raw:
                receivable += n * float(getattr(raw[s], 'dividend', 0.))
                holdings[s] = n * float(getattr(raw[s], 'split', 1.))
                if not np.isfinite(raw[s].close) or raw[s].close <= 0:
                    prices[s] /= float(getattr(raw[s], 'split', 1.))
        restored, old_cash, old_ages = dict(holdings), cash, dict(ages)
        proposed = dict(holdings)
        attempted = []
        daily_fees = daily_taxes = notional_total = 0.
        for order in sorted(pending, key=lambda r: (r.shares > 0, r.symbol)):
            s, quantity = order.symbol, float(order.shares)
            quote = raw.get(s)
            volume = float(getattr(quote, 'execution_volume', getattr(quote, 'volume', float('nan'))))
            price = float(getattr(quote, 'open', float('nan')))
            if quote is None or not np.isfinite(price) or price <= 0:
                expected_warnings.append((day, s, 'UNFILLED_MISSING_OPEN_PROXY'))
                unfilled_count += 1
                continue
            if proposed.get(s, 0.) + quantity < -1e-6:
                expected_warnings.append((day, s, 'UNFILLED_ACTION_ADJUSTED_INSUFFICIENT_SHARES'))
                unfilled_count += 1
                continue
            gross = abs(quantity) * price
            fee, tax = gross * float(cfg['commission']), gross * float(cfg['sell_tax']) if quantity < 0 else 0.
            cash -= quantity * price + fee + tax
            proposed[s] = proposed.get(s, 0.) + quantity
            if proposed[s] < 1e-6:
                del proposed[s]
            daily_fees += fee
            daily_taxes += tax
            notional_total += gross
            if (price > order.sizing_price * cfg['price_buffer'] + 1e-6
                    or price < order.sizing_price * cfg['price_lower_buffer'] - 1e-6):
                expected_warnings.append((day, s, 'EXECUTION_OUTSIDE_PREDECLARED_PRICE_BOUND'))
                bound_count += 1
            attempted.append(dict(symbol=s, signal_date=signal_day, shares=quantity, price=price,
                                  notional=gross, fee=fee, tax=tax, slippage_cost=0.,
                                  volume_participation=abs(quantity) / volume if np.isfinite(volume) and volume > 0 else np.nan,
                                  execution_volume=volume,
                                  trade_symbol=getattr(quote, 'source_symbol', s)))
        prices.update({s: float(r.close) for s, r in raw.items() if s in universe and np.isfinite(r.close) and r.close > 0})
        stale = sorted(set(stale) | {s for s in proposed if s not in raw
                       or not np.isfinite(raw[s].close) or raw[s].close <= 0
                       or not np.isfinite(raw[s].volume) or raw[s].volume < 0
                       or not bool(getattr(raw[s], 'valid_price', True))
                       or not bool(getattr(raw[s], 'valid_for_research', True))})
        bought = {r['symbol'] for r in attempted if r['shares'] > 0}
        ages, active, passive = cap_ages(proposed, cash, old_ages, bought, (restored, old_cash))
        overdue = [s for s in passive if ages[s] > 5]
        failures = basic(proposed, cash)
        reasons = sorted({s for s in failures if not s.startswith('WEIGHT_CAP:')}
                         | {'ACTIVE_CAP:' + s for s in active} | {'OVERDUE_CAP:' + s for s in overdue})
        _near(comp.proposed_nav, book(proposed, cash), 'Proposed NAV')
        _near(comp.proposed_cash, cash, 'Proposed cash')
        _check(comp.proposed_holdings == len(proposed), 'Proposed holding count')
        _check(comp.warning_reasons == ';'.join(reasons), 'Warning reason mismatch')
        _check(bool(comp.warning_today) == bool(reasons) == bool(comp.rolled_back), 'Rollback flag mismatch')
        _check(comp.active_caps == ';'.join(active) and comp.passive_caps == ';'.join(passive)
               and comp.overdue_caps == ';'.join(overdue), 'Active/passive/grace mismatch')
        actual_fills = rejected.get(day, []) if reasons else trades.get(day, [])
        _check(not (trades.get(day, []) if reasons else rejected.get(day, [])), 'Partial-day rollback or false rejection')
        _check(len(actual_fills) == len(attempted), 'Missing/duplicate/spurious fill')
        actual_by_symbol = {r.symbol: r for r in actual_fills}
        _check(len(actual_by_symbol) == len(actual_fills), 'Duplicate actual fills')
        for expected in attempted:
            _check(expected['symbol'] in actual_by_symbol, 'Filled identity differs')
            actual = actual_by_symbol[expected['symbol']]
            for key, value in expected.items():
                if isinstance(value, str):
                    _check(getattr(actual, key) == value, 'Fill identity/timing mismatch: ' + key)
                elif key in ('volume_participation', 'execution_volume') and not np.isfinite(value):
                    _check(pd.isna(getattr(actual, key)), 'Missing volume diagnostic falsely observed')
                else:
                    _near(getattr(actual, key), value, 'Fill ' + key, 1e-7)
            if reasons:
                _check(actual.rejection == ';'.join(reasons), 'Rejected trade explanation differs')
        if reasons:
            warning_count += 1
            rejected_count += len(attempted)
            holdings, cash = restored, old_cash
            ages, _, _ = cap_ages(holdings, cash, old_ages, set())
            daily_fees = daily_taxes = notional_total = 0.
            expected_warnings.append((day, '', 'SIMULATED_DAY_ROLLBACK:' + ';'.join(reasons)))
        else:
            holdings = proposed
            accepted_fill_count += len(attempted)
            unknown_participation |= any(not np.isfinite(t['volume_participation']) for t in attempted)
            max_participation = max([max_participation] + [t['volume_participation'] for t in attempted
                                                          if np.isfinite(t['volume_participation'])])
        _check(comp.rejected_fills == (len(attempted) if reasons else 0), 'Rejected-fill count')
        _check(comp.cumulative_warnings == warning_count and bool(comp.disqualified) == (warning_count >= 3), 'Warning/DQ state')
        _check(warning_count < 3 or index == len(dates) - 1, 'Trading continued after third warning')
        nav = book(holdings, cash)
        terminal = receivable if index == len(dates) - 1 and warning_count < 3 else 0.
        published_nav = nav + terminal
        settled_failures = basic(holdings, cash)
        raw_breach_days += bool(settled_failures)
        if stale:
            expected_warnings.append((day, ';'.join(stale), 'STALE_HELD_PRICES'))
        stale_days += bool(stale)
        _check(row.stale_count == comp.stale_count == len(stale), 'Stale source coverage')
        _check(row.violations == ';'.join(settled_failures), 'Settled rule report')
        _check(row.active_cap_breaches == ';'.join(active) and row.passive_cap_breaches == ';'.join(passive)
               and row.overdue_passive_caps == ';'.join(overdue), 'Equity cap report')
        _near(comp.settled_nav, nav, 'Settlement NAV')
        _near(comp.settled_cash, cash, 'Settlement cash')
        _check(row.holdings == comp.settled_holdings == len(holdings), 'Settlement holding count')
        _check(row.odd_residual_names == sum(q % cfg['lot_size'] > 1e-6 for q in holdings.values()),
               'Odd entitlements not fully reported')
        for key, expected in dict(nav=published_nav, economic_nav=nav + receivable, cash=cash,
                                  cash_ratio=cash / published_nav, dividend_receivable=receivable-terminal,
                                  terminal_dividend_credit=terminal, fees=daily_fees, taxes=daily_taxes,
                                  costs=daily_fees+daily_taxes, traded_notional=notional_total,
                                  turnover=notional_total/previous_nav).items():
            _near(getattr(row, key), expected, day + ': ' + key, 1e-8 if key in ('cash_ratio', 'turnover') else .005)
        actual_positions = {r.symbol: r for r in positions.get(day, [])}
        _check(len(actual_positions) == len(positions.get(day, [])) and set(actual_positions) == set(holdings), 'Holding identity/completeness')
        for s, n in holdings.items():
            _near(actual_positions[s].shares, n, 'Corporate-action/fill inventory', 1e-6)
            _near(actual_positions[s].close, prices[s], 'Holding valuation', 1e-9)
            _near(actual_positions[s].weight, n*prices[s]/published_nav, 'Holding NAV weight', 1e-10)
        hard = cash < -1e-6 or cash/nav >= .25 or not cfg['min_count'] <= len(holdings) <= cfg['max_count'] or bool(active or overdue) or not set(holdings) <= universe
        hard_days += hard
        cash_breach_days += cash/nav >= .25
        count_days += not cfg['min_count'] <= len(holdings) <= cfg['max_count']
        active_days += bool(active)
        overdue_days += bool(overdue)
        fees_total += daily_fees
        taxes_total += daily_taxes
        turnover_total += notional_total/previous_nav
        nav_values.append(published_nav)
        economic_values.append(nav+receivable)
        previous_nav = nav
        if day in snapshots:
            pending = check_plan(day)
    dq = warning_count >= 3
    _check(dq or dates == calendar, 'Unexplained truncated run')
    actual_warnings = Counter((str(r.date), r.symbol, r.issue) for r in result['warnings'].itertuples(index=False))
    _check(actual_warnings == Counter(expected_warnings), 'Missing/spurious warnings')
    plan = result.get('plan_audit')
    if plan is not None:
        _check(list(plan.date) == signal_days, 'Planner audit coverage')
        _check(list(plan.final_reason) == [snapshots[d].plan_reason for d in signal_days], 'Planner audit differs from emitted plan')
        effective = plan if dq else plan.iloc[:-1]
        invalid_plans = int(effective.final_reason.str.startswith('INFEASIBLE').sum())
        unsupported_holds = int(effective.final_reason.eq('HOLD_REVALIDATED_CURRENT_ONLY').sum())
    else:
        invalid_plans = sum(snapshots[d].plan_reason.startswith('INFEASIBLE') for d in signal_days[:len(dates)])
        unsupported_holds = sum(snapshots[d].plan_reason == 'HOLD_REVALIDATED_CURRENT_ONLY' for d in signal_days[:len(dates)])
    metrics = _metrics(nav_values, cfg['initial_cash'])
    metrics.update({'economic_'+key: value for key, value in _metrics(economic_values, cfg['initial_cash']).items()})
    metrics.update(final_nav=nav_values[-1], sessions=len(dates), transaction_costs=fees_total+taxes_total,
                   commission_cost=fees_total, sell_tax_cost=taxes_total, turnover_two_way=turnover_total,
                   max_daily_volume_participation=None if unknown_participation else max_participation, trades=accepted_fill_count,
                   simulated_warning_days=warning_count, disqualified=dq, complete_period=dates==calendar,
                   rejected_trade_count=rejected_count, measured_hard_breach_days=int(hard_days),
                   raw_rule_breach_days=int(raw_breach_days),
                   cash_breach_days=int(cash_breach_days), holding_count_breach_days=int(count_days),
                   active_cap_breach_days=int(active_days), overdue_passive_cap_days=int(overdue_days),
                   stale_held_price_days=int(stale_days), execution_price_bound_breaches=bound_count,
                   unfilled_orders=unfilled_count, no_valid_plan_days=invalid_plans,
                   hold_without_envelope_days=unsupported_holds,
                   official_compliance='UNKNOWN_BLOCK_SUBMISSION', independent_audit='PASS_INTERNAL_ACCOUNTING')
    for key, value in metrics.items():
        if key not in result['metrics']:
            continue
        actual = result['metrics'][key]
        if value is None or isinstance(value, (str, bool)):
            _check(actual == value, 'Metric ' + key + ' differs')
        else:
            _near(actual, value, 'Metric ' + key, 1e-7)
    return metrics


def verify_hashes(root, expected):
    """Authenticate exact source/config/artifact bytes, without trusting summaries."""
    root = Path(root).resolve()
    _check(bool(expected), 'Empty provenance manifest')
    for relative, digest in expected.items():
        path = (root / relative).resolve()
        _check(path.is_relative_to(root), 'Manifest path escapes root')
        _check(path.is_file(), 'Missing provenance artifact: ' + relative)
        _check(hashlib.sha256(path.read_bytes()).hexdigest() == digest,
               'Provenance hash mismatch: ' + relative)
    return dict(status='PASS_HASHES', files=len(expected))


def audit_episode(result, ctx, episode=None):
    """Audit complete and failed runs; a partial return is never a 24D return."""
    cfg = result['config']
    for key, value in dict(initial_cash=1e9, lot_size=1000, min_count=20, max_count=30,
                           max_weight=.1, tsmc_max_weight=.25,
                           commission=.001425, sell_tax=.003).items():
        _near(cfg[key], value, 'Pinned competition constraint ' + key, 1e-12)
    _check(not cfg.get('use_4h', False) and not cfg.get('match_4h_coverage', False),
           'Daily variant still depends on 4H observations')
    _check(cfg.get('research_shadow') is True, 'Missing research-only guard')
    _check(cfg.get('slippage_bps', 0) == 0, 'Undeclared execution-cost change')
    if result['equity'].empty:
        failure = result['metrics']
        _check(failure.get('status') in {'FAILED', 'ERROR'} and failure.get('measured_pass') is False,
               'Empty ledger represented as a successful episode')
        _check(failure.get('episode_return') is None or pd.isna(failure.get('episode_return')),
               'Empty ledger has fabricated return')
        _check(bool(failure.get('error') or failure.get('error_message')), 'Empty ledger lacks failure evidence')
        _check(all(result[name].empty for name in ('trades', 'holdings', 'orders', 'compliance_daily')),
               'Failed empty ledger has unaccounted trading artifacts')
        return dict(independent_audit='NO_LEDGER_FAILURE_RETAINED', measured_pass=False,
                    episode_return=None, complete_period=False,
                    source_accuracy_verified=False, active_share_status='ACTIVE_SHARE_NOT_VERIFIED',
                    ready_status='BLOCK_READY')
    metrics = _audit_ledger(result, ctx)
    equity = result['equity']
    full_calendar = sorted(str(pd.Timestamp(d).date()) for d in ctx.get('session_dates', ctx['daily'].date.unique()))
    start, end = str(cfg['start']), str(cfg['end'])
    expected = [d for d in full_calendar if start <= d <= end]
    _check(len(expected) == 24, 'Requested episode is not 24 trading sessions')
    if episode is not None:
        _check(str(episode['start']) == start and str(episode['end']) == end,
               'Episode config differs from frozen registry')
    complete = metrics['complete_period'] and metrics['sessions'] == 24 and not metrics['disqualified']
    zero_keys = ('measured_hard_breach_days', 'raw_rule_breach_days', 'simulated_warning_days',
                 'stale_held_price_days', 'execution_price_bound_breaches', 'unfilled_orders',
                 'no_valid_plan_days', 'hold_without_envelope_days')
    measured = (complete and all(metrics[k] == 0 for k in zero_keys)
                and not equity.odd_residual_names.gt(0).any())
    wealth = np.r_[float(cfg['initial_cash']), equity.economic_nav.to_numpy(float)]
    episode_return = float(wealth[-1] / wealth[0] - 1) if complete else None
    drawdown = float(1 - np.min(wealth / np.maximum.accumulate(wealth)))
    turnover = float(equity.traded_notional.sum() / cfg['initial_cash'])
    additions = dict(episode_return=episode_return, episode_max_drawdown=drawdown,
                     episode_turnover=turnover, measured_pass=bool(measured),
                     official_compliance='UNKNOWN_BLOCK_SUBMISSION',
                     active_share_status='ACTIVE_SHARE_NOT_VERIFIED', ready_status='BLOCK_READY',
                     forensic_partial_return=float(wealth[-1] / wealth[0] - 1) if not complete else None)
    for key, value in additions.items():
        if key not in result['metrics']:
            continue
        actual = result['metrics'][key]
        if value is None or isinstance(value, (str, bool)):
            _check(actual == value, 'Episode metric differs: ' + key)
        else:
            _near(actual, value, 'Episode metric ' + key, 1e-9)
    metrics.update(additions)
    metrics.update(independent_audit='PASS_INTERNAL_ACCOUNTING',
                   source_accuracy_verified=False, universe_point_in_time=False)
    return metrics


SPLITS = {'development': ('2010-01-01', '2019-12-31'),
          'validation': ('2020-01-01', '2022-12-31'),
          'holdout': ('2023-01-01', '2024-12-31')}


def interval_split(start, end):
    for name, (lower, upper) in SPLITS.items():
        if lower <= start <= end <= upper:
            return name
    if '2010-01-01' <= start <= '2024-12-31':
        return 'purged'
    return 'outside'


def audit_episode_registry(episodes, calendar):
    """Reject start-year splitting, omitted primary episodes and forged lengths."""
    required = {'episode_id', 'kind', 'start', 'end', 'split', 'session_count'}
    _check(required <= set(episodes), 'Episode registry missing fields')
    _check(episodes.episode_id.is_unique, 'Duplicate episode identifiers')
    sessions = sorted({str(pd.Timestamp(d).date()) for d in calendar})
    indices = {d: i for i, d in enumerate(sessions)}
    for r in episodes.itertuples(index=False):
        start, end = str(r.start), str(r.end)
        _check(start in indices and end in indices, 'Episode boundary not in calendar')
        _check(indices[end] - indices[start] + 1 == r.session_count == 24, 'Episode length differs')
        _check(r.split == interval_split(start, end), 'Cross-boundary episode leaked into split')
        _check(r.kind in {'monthly', 'oct_nov', 'rolling'}, 'Unknown episode kind')
        if r.kind == 'monthly':
            _check(not any(d[:7] == start[:7] for d in sessions[:indices[start]]),
                   'Monthly episode does not start on first session')
        if r.kind == 'oct_nov':
            earliest = start[:4] + '-10-26'
            eligible = [d for d in sessions if d >= earliest and d[:4] == start[:4]]
            _check(bool(eligible) and start == eligible[0], 'Seasonal episode start differs')
    months = episodes.loc[episodes.kind.eq('monthly'), 'start'].astype(str).str[:7]
    expected_months = pd.period_range('2010-01', '2024-12', freq='M').astype(str)
    _check(len(months) == 180 and set(months) == set(expected_months),
           'Primary monthly denominator must retain all 180 starts')
    return dict(status='PASS_EPISODE_REGISTRY', episodes=len(episodes),
                monthly_episodes=180, purged=int(episodes.split.eq('purged').sum()))


def audit_selection_provenance(selection, episodes, candidates, *, root=None,
                               holdout_started_at=None):
    _check(selection['candidate_id'] in set(candidates.candidate_id), 'Unknown selected candidate')
    _check(candidates.candidate_id.is_unique, 'Duplicate candidate identifiers')
    _check(selection['required_measured_pass_rate'] == 1., 'Selection relaxed measured-rule threshold')
    _check(selection['submission_status'] == 'BLOCK_SUBMISSION'
           and selection['ready_status'] == 'BLOCK_READY'
           and selection['active_share_status'] == 'ACTIVE_SHARE_NOT_VERIFIED',
           'Missing official evidence falsely certified')
    lookup = episodes.set_index('episode_id')
    selection_ids = selection['selection_episode_ids']
    holdout_ids = selection['holdout_episode_ids']
    _check(len(selection_ids) == len(set(selection_ids)) and len(holdout_ids) == len(set(holdout_ids)),
           'Duplicate selection/holdout episodes')
    _check(set(selection_ids).isdisjoint(holdout_ids), 'Holdout directly included in selection')
    _check(set(selection_ids) | set(holdout_ids) <= set(lookup.index), 'Unregistered selection episode')
    _check(lookup.loc[selection_ids, 'split'].isin(['development', 'validation']).all(),
           'Selection includes purged or holdout episodes')
    _check(lookup.loc[holdout_ids, 'split'].eq('holdout').all(), 'Holdout list includes foreign episodes')
    _check(lookup.loc[selection_ids, 'kind'].eq('monthly').all(), 'Selection optimized secondary episodes')
    if holdout_started_at is not None:
        _check(pd.Timestamp(selection['selection_frozen_at']) < pd.Timestamp(holdout_started_at),
               'Selection was frozen after holdout started')
    _check(bool(selection['candidate_config_sha256']), 'No frozen candidate hash')
    if root is not None:
        verify_hashes(root, selection['selection_input_hashes'])
        relative = selection.get('candidate_config_path',
                                 'outputs/24d/configs/' + selection['candidate_id'] + '.json')
        verify_hashes(root, {relative: selection['candidate_config_sha256']})
    return dict(status='PASS_DECLARED_SELECTION_PROVENANCE',
                prior_2025_2026_baseline_selection_confound=True,
                historical_point_in_time_claim=False,
                candidates_evaluated=len(candidates))


def audit_saved_group(folder, metrics_rows, configs, ctx, episodes):
    """Re-read grouped Parquet evidence and audit every registered attempt."""
    folder = Path(folder)
    names = ('equity', 'trades', 'orders', 'holdings', 'compliance_daily',
             'rejected_trades', 'warnings', 'snapshots', 'plan_audit')
    tables = {name: pd.read_parquet(folder / (name + '.parquet')) for name in names}
    ids = set(metrics_rows.episode_id)
    _check(metrics_rows.episode_id.is_unique, 'Duplicate episode metric rows in group')
    for name, table in tables.items():
        _check('episode_id' in table, 'Missing artifact episode identity: ' + name)
        _check(set(table.episode_id) <= ids, 'Unregistered table episode: ' + name)
    registry = episodes.set_index('episode_id')
    audited = []
    for row in metrics_rows.to_dict('records'):
        episode_id = row['episode_id']
        _check(episode_id in registry.index, 'Attempt absent from episode registry')
        cfg = dict(configs[row['candidate_id']])
        episode = registry.loc[episode_id].to_dict()
        cfg.update(start=episode['start'], end=episode['end'])
        result = {name: table.loc[table.episode_id.eq(episode_id)].drop(columns='episode_id').reset_index(drop=True)
                  for name, table in tables.items()}
        result.update(config=cfg, metrics={k: None if isinstance(v, float) and np.isnan(v) else v for k, v in row.items()})
        audited.append(dict(episode_id=episode_id, **audit_episode(result, ctx, episode)))
    return pd.DataFrame(audited)


def audit_attempt_coverage(rows, expected_episode_ids, candidate_ids):
    """Require the exact candidate × episode denominator, including failures."""
    expected = {(str(candidate), str(episode)) for candidate in candidate_ids
                for episode in expected_episode_ids}
    _check(not rows.duplicated(['candidate_id', 'episode_id']).any(), 'Duplicate candidate/episode attempt')
    actual = set(zip(rows.candidate_id.astype(str), rows.episode_id.astype(str)))
    _check(actual == expected, 'Candidate/episode denominator differs: '
           + str(len(expected-actual)) + ' missing, ' + str(len(actual-expected)) + ' extra')
    for r in rows.to_dict('records'):
        complete = bool(r.get('complete_period', False))
        _check(complete or r.get('episode_return') is None or pd.isna(r.get('episode_return')),
               'Partial episode falsely contributes a 24D return')
        _check(not r.get('measured_pass', False) or complete, 'Incomplete episode marked measured PASS')
    return dict(status='PASS_ATTEMPT_DENOMINATOR', attempted=len(actual),
                failed=int((~rows.measured_pass.astype(bool)).sum()))


def audit_decision_causality(runner, daily, universe, config, session_dates, cutoff):
    """Challenge prior decisions with independent future shocks and truncation.

    Pass the public episode runner, not a cached feature panel. This forces all
    indicators to rebuild from each challenged input. It tests observed prefix
    invariance; it cannot prove Yahoo's historical snapshot was available then.
    """
    cutoff = pd.Timestamp(cutoff)
    dates = pd.DatetimeIndex(session_dates)
    _check(dates[0] <= cutoff < dates[-1], 'Causality cutoff outside episode')
    base = runner(daily, universe, config, session_dates)
    changed = daily.copy()
    future = pd.to_datetime(changed.date) > cutoff
    _check(future.any(), 'Causality fixture has no future observations')
    changed.loc[future, ['open', 'high', 'low', 'close']] *= 1.7
    changed.loc[future, 'volume'] *= 3.1
    changed.loc[future, 'dividend'] += 2.3
    changed.loc[future, 'split'] *= 1.1
    shocked = runner(changed, universe, config, session_dates)
    truncated = runner(daily.loc[~future].copy(), universe, config, session_dates)
    rows = 0
    for name, column in (('orders', 'signal_date'), ('trades', 'date'),
                         ('rejected_trades', 'date'), ('signals', 'date'),
                         ('compliance_daily', 'date'), ('snapshots', 'date')):
        original = base[name]
        if column not in original:
            _check(original.empty, 'Missing causal table date column')
            continue
        expected = original.loc[pd.to_datetime(original[column]) <= cutoff].reset_index(drop=True)
        for variant in (shocked, truncated):
            actual = variant[name]
            _check(column in actual or not len(expected), 'Challenge erased earlier evidence')
            if column in actual:
                actual = actual.loc[pd.to_datetime(actual[column]) <= cutoff].reset_index(drop=True)
                pd.testing.assert_frame_equal(expected, actual, check_exact=True)
        rows += len(expected)
    orders = base['orders']
    informative = ('signal_date' in orders and
                   (pd.to_datetime(orders.signal_date) <= cutoff).any())
    return dict(status='PASS_PREFIX_INVARIANCE' if informative else 'INCONCLUSIVE_NO_ORDERS',
                compared_rows=rows, source_snapshot_point_in_time=False)
