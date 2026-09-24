"""Shared, fixed-order VWAP research ledger for the v2 parallel strategies.

Signals are transformed at the prior close. This module never certifies a contest
submission: historical-universe and Active Share evidence remain external gates.
"""
from __future__ import annotations

import copy
import json

import numpy as np
import pandas as pd

from src.backtest import compute_features, rule_check, score_candidates, validate_daily


def _rank(rows):
    rows = rows.copy()
    rows.index = rows.symbol.astype(str)
    rows.index.name = '_symbol_index'
    return rows.sort_values(['score', 'symbol'], ascending=[False, True])


def _cap(symbol, config):
    return config['tsmc_max_weight'] if symbol.split('.')[0] == '2330' else config['max_weight']


def _project(orders, holdings, cash, prices, config):
    """Check fixed quantities at both predeclared execution-price boundaries."""
    projected = dict(holdings)
    for s, q in orders.items():
        projected[s] = projected.get(s, 0.) + q
    if any(q < -1e-6 for q in projected.values()):
        return ['SHORT_POSITION']
    projected = {s: q for s, q in projected.items() if q > 1e-6}
    failures = set()
    for multiplier in [config['price_lower_buffer'], config['price_buffer']]:
        projected_cash = cash
        for s, q in orders.items():
            amount = abs(q) * prices[s] * multiplier
            projected_cash -= q * prices[s] * multiplier
            projected_cash -= amount * (config['commission'] + (config['sell_tax'] if q < 0 else 0.))
        checks = rule_check(projected, projected_cash, prices, config, set(prices))
        failures.update(checks['violations'])
    return sorted(failures)


def make_plan_v2(ranked, holdings, cash, nav, config, buy_phase=False, desired_symbols=None):
    """Prior-close planning with feasible staged exits and settled-cash buys.

    Cash/count constraints apply after EACH sell-only or buy-only market day.
    Corporate odd-share residues occupy slots, but do not freeze other names.
    """
    ranked = _rank(ranked)
    if any(s not in ranked.index for s in holdings):
        return {}, 'INFEASIBLE_MISSING_HELD_QUOTE', list(holdings)
    prices = ranked.close.to_dict()
    lot = config['lot_size']
    upper, fee = config['price_buffer'], config['commission']
    forced = {s for s in holdings if bool(ranked.at[s, 'exit'])}
    retained = [s for s in ranked.index if s in holdings and (s not in forced or holdings[s] < lot)]
    entrants = [s for s in ranked.index if bool(ranked.at[s, 'entry_ok']) and s not in holdings]
    selected = list(retained)
    remaining_entrants = list(entrants)
    while len(selected) < config['target_count'] and remaining_entrants:
        selected.append(remaining_entrants.pop(0))
    tradable_forced = {s for s in forced if holdings[s] >= lot}
    if not buy_phase and len(holdings) >= config['target_count'] and not tradable_forced:
        for _ in range(config['max_replacements_per_day']):
            eligible_held = [s for s in selected if s in holdings and holdings[s] >= lot]
            if not eligible_held or not remaining_entrants:
                break
            weakest = min(eligible_held, key=lambda x: (ranked.at[x, 'score'], x))
            if ranked.at[remaining_entrants[0], 'score'] <= ranked.at[weakest, 'score'] + config['replacement_margin']:
                break
            selected.remove(weakest)
            selected.append(remaining_entrants.pop(0))
    selected = selected[:config['max_count']]
    if not holdings and len(selected) < config['min_count']:
        return {}, 'INFEASIBLE_FEWER_THAN_MIN', selected
    target_value = nav * (1 - config['cash_target']) / config['target_count']
    cap_orders = {}
    for symbol, shares in holdings.items():
        if shares * prices[symbol] / nav > _cap(symbol, config):
            target = np.floor(_cap(symbol, config) * nav / (prices[symbol] * upper) / lot) * lot
            sell = min(int(np.ceil(max(0., shares - target) / lot)), int(np.floor(shares / lot))) * lot
            if sell:
                cap_orders[symbol] = -sell
    if cap_orders:
        failures = _project(cap_orders, holdings, cash, prices, config)
        if failures:
            return {}, 'INFEASIBLE_CAP_CORRECTION:' + ';'.join(failures), selected
        return cap_orders, 'SELL_THEN_WAIT_SETTLEMENT', selected

    def buys(allow_topups=False, cash_correction=False):
        vacancies = max(0, min(config['target_count'], config['max_count']) - len(holdings))
        names = [s for s in selected if s not in holdings][:vacancies]
        if allow_topups:
            names += [s for s in selected if s in holdings and s not in forced]
        if cash_correction:
            # An explicit compliance exception, never a routine equal-weight reset.
            names += [s for s in ranked.index if s in holdings and bool(ranked.at[s, 'entry_ok'])]
        names = list(dict.fromkeys(names))
        desired = {}
        for symbol in names:
            target_amount = target_value
            if cash_correction and symbol in holdings:
                target_amount = _cap(symbol, config) * nav
            target = np.floor(target_amount / (prices[symbol] * upper * (1 + fee)) / lot) * lot
            desired[symbol] = max(0., target - holdings.get(symbol, 0.))
        total = sum(q * prices[symbol] * upper * (1 + fee) for symbol, q in desired.items())
        scale = min(1., max(0., cash) / total) if total else 0.
        orders = {symbol: int(np.floor(q * scale / lot)) * lot for symbol, q in desired.items()}
        orders = {symbol: q for symbol, q in orders.items() if q}
        if not orders:
            return {}, 'INFEASIBLE_CASH_OR_NO_ELIGIBLE_VACANCY'
        failures = _project(orders, holdings, cash, prices, config)
        if failures:
            return {}, 'INFEASIBLE_BUY_TRANSITION:' + ';'.join(failures)
        return orders, 'BUY_CASH_CAP_CORRECTION' if cash_correction else 'BUY_WITH_SETTLED_CASH'

    if cash / nav >= .25:
        orders, reason = buys(cash_correction=True)
        return orders, reason, selected
    # This branch is deliberately BEFORE general exits: fund the vacancies from
    # the previous settled sale, instead of endlessly selling the remaining exits.
    if buy_phase:
        orders, reason = buys(allow_topups=config['allocation_mode'] == 'full')
        return orders, reason if orders else 'WAIT_FUNDED_VACANCY:' + reason, selected
    proposals = []
    for symbol, shares in holdings.items():
        if symbol not in selected:
            quantity = -int(np.floor(shares / lot)) * lot
            priority = 0
        elif config['allocation_mode'] == 'full' and set(selected) != set(holdings):
            target = np.floor(target_value / (prices[symbol] * upper * (1 + fee)) / lot) * lot
            quantity = min(0, int(np.trunc((target - shares) / lot)) * lot)
            priority = 1
        else:
            quantity = 0
            priority = 1
        if quantity:
            proposals.append((priority, float(ranked.at[symbol, 'score']), symbol, quantity))
    orders = {}
    for _, _, symbol, requested in sorted(proposals):
        # Feasibility is monotone in a sale's size while already accepted sales
        # are fixed: larger sales raise cash and eventually remove a held name.
        lo, hi, accepted = 1, abs(requested) // lot, 0
        while lo <= hi:
            count = (lo + hi) // 2
            trial = {**orders, symbol: -count * lot}
            if not _project(trial, holdings, cash, prices, config):
                accepted = count
                lo = count + 1
            else:
                hi = count - 1
        if accepted:
            orders[symbol] = -accepted * lot
    if orders:
        return orders, 'SELL_THEN_WAIT_SETTLEMENT', selected
    # A blocked sale must not prevent filling an existing vacancy with cash.
    orders, reason = buys()
    if orders:
        return orders, reason, selected
    if proposals:
        return {}, 'INFEASIBLE_SELL_TRANSITION', selected
    if any(q % lot > 1e-6 for q in holdings.values()):
        return {}, 'HOLD_WITH_ODD_ENTITLEMENTS', selected
    return {}, 'HOLD', selected


def _safe_meta(value):
    if isinstance(value, dict):
        return {str(k): _safe_meta(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_meta(v) for v in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return str(value)
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _metrics(equity, trades, snapshots, initial):
    result = {}
    for column, prefix in [('nav', ''), ('economic_nav', 'economic_')]:
        values = np.r_[initial, equity[column].to_numpy(float)]
        returns = values[1:] / values[:-1] - 1
        std = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.
        result[prefix + 'total_return'] = float(values[-1] / initial - 1)
        result[prefix + 'max_drawdown'] = float(-(values / np.maximum.accumulate(values) - 1).min())
        result[prefix + 'annualized_volatility'] = std * np.sqrt(252)
        result[prefix + 'sharpe_zero_rf'] = float(returns.mean() / std * np.sqrt(252)) if std > 0 else None
    result.update(transaction_costs=float(equity.costs.sum()),
                  commission_cost=float(equity.fees.sum()), sell_tax_cost=float(equity.taxes.sum()),
                  turnover_two_way=float(equity.turnover.sum()), slippage_cost=0.,
                  max_daily_volume_participation=max((x['volume_participation'] for x in trades), default=0.),
                  trades_above_10pct_daily_volume=sum(x['volume_participation'] > .1 for x in trades),
                  trades_above_100pct_daily_volume=sum(x['volume_participation'] > 1 for x in trades),
                  trades=len(trades), sessions=len(equity), start=equity.date.iloc[0], end=equity.date.iloc[-1],
                  final_nav=float(equity.nav.iloc[-1]), raw_rule_breach_days=int(equity.violations.ne('').sum()),
                  negative_cash_days=int(equity.cash.lt(-1e-6).sum()),
                  stale_held_price_days=int(equity.stale_count.gt(0).sum()),
                  infeasible_signal_days=sum(x['plan_reason'].startswith('INFEASIBLE') for x in snapshots),
                  active_share_status='UNKNOWN_MISSING_HISTORICAL_ETF_HOLDINGS',
                  certification='RESEARCH_SHADOW_ONLY_BLOCK_SUBMISSION')
    return result


def _settings(config):
    c = copy.deepcopy(config)
    c.setdefault('cash_target', 0.)
    c.setdefault('price_buffer', 1.1)
    c.setdefault('price_lower_buffer', .9)
    c.setdefault('allocation_mode', 'local')
    if c['allocation_mode'] not in ('local', 'full'):
        raise ValueError('allocation_mode must be local or full')
    if not 0 <= c['cash_target'] < .25 or not 0 < c['price_lower_buffer'] <= 1 <= c['price_buffer']:
        raise ValueError('Invalid cash target or predeclared price boundaries')
    if c.get('dividend_cash_policy', 'end_of_period') != 'end_of_period':
        raise ValueError('Only end_of_period distribution cash policy is supported')
    c['dividend_cash_policy'] = 'end_of_period'
    c['execution'] = 'vwap'
    return c


def run_v2(daily, universe, config, four_hour=None, signal_transform=None):
    """Run a prior-close, fixed-quantity shadow portfolio.

    ``signal_transform(day, ranked) -> (ranked, metadata)`` may change score,
    entry_ok and exit, and add diagnostic columns, but cannot change prices,
    identifiers or the available rows. The caller must enforce the metadata's
    point-in-time provenance. Returns v1-compatible tables plus snapshots.
    """
    c = _settings(config)
    if not c.get('research_shadow', False):
        raise ValueError('BLOCK_SUBMISSION: set research_shadow=True for unverified historical comparisons')
    daily = validate_daily(daily)
    universe = universe.copy()
    if 'known_at' not in universe:
        raise ValueError('Universe requires known_at')
    universe['symbol'] = universe.symbol.astype(str)
    if universe.symbol.duplicated().any():
        raise ValueError('Expected one fixed universe snapshot')
    universe['known_at'] = pd.to_datetime(universe.known_at, utc=True)
    symbols = set(universe.symbol)
    start, end = pd.Timestamp(c['start']), pd.Timestamp(c['end'])
    all_sessions = sorted(pd.Timestamp(x) for x in daily.date.unique())
    next_session = dict(zip(all_sessions[:-1], all_sessions[1:]))
    calendar = [x for x in all_sessions if start <= x <= end]
    if not calendar:
        raise ValueError('No sessions in requested interval')
    prior = sorted(pd.Timestamp(x) for x in daily.date.unique() if pd.Timestamp(x) < calendar[0])
    if not prior:
        raise ValueError('Missing prior-close sizing session')
    initial_day = prior[-1]
    cutoff = calendar[0].tz_localize('Asia/Taipei') + pd.Timedelta(hours=8, minutes=55)
    if (universe.known_at > cutoff.tz_convert('UTC')).any():
        raise ValueError('LOOKAHEAD_UNIVERSE: constituents not known at first decision')
    stock_data = daily[daily.symbol.isin(symbols)]
    if symbols - set(stock_data.symbol):
        raise ValueError('Universe symbols without price history')
    features = compute_features(stock_data, c, four_hour)
    grouped = {pd.Timestamp(d): rows.copy() for d, rows in features.groupby('date')}
    holdings, last_prices, pending = {}, {}, {}
    cash, receivable = float(c['initial_cash']), 0.
    previous_nav = cash
    signal_day, plan_reason, buy_phase, selected = None, 'NO_PRIOR_SIGNAL', False, []
    records, trades, order_rows, holdings_rows, signal_rows, warnings, snapshots = [], [], [], [], [], [], []
    cap_age = {}
    initial_prices = stock_data[stock_data.date <= initial_day].sort_values('date').groupby('symbol').tail(1)
    last_prices.update(initial_prices.set_index('symbol').close.to_dict())
    for day in [initial_day] + calendar:
        rows = grouped.get(day, features.iloc[:0].copy())
        bars = rows.set_index('symbol')
        is_live = day != initial_day
        fees = taxes = notional_total = 0.
        stale = []
        executed_reason = plan_reason
        if is_live:
            for s, q in list(holdings.items()):
                if s not in bars.index:
                    stale.append(s)
                    continue
                receivable += q * float(bars.at[s, 'dividend'])
                holdings[s] = q * float(bars.at[s, 'split'])
            for s, q in sorted(pending.items(), key=lambda item: (item[1] > 0, item[0])):
                volume_col = 'execution_volume' if 'execution_volume' in bars else 'volume'
                valid = s in bars.index and 'turnover' in bars
                if valid:
                    volume, turnover = float(bars.at[s, volume_col]), float(bars.at[s, 'turnover'])
                    valid = np.isfinite(volume) and np.isfinite(turnover) and volume > 0 and turnover > 0
                if not valid:
                    warnings.append(dict(date=str(day.date()), symbol=s, issue='UNFILLED_MISSING_OFFICIAL_VWAP'))
                    continue
                if holdings.get(s, 0.) + q < -1e-6:
                    warnings.append(dict(date=str(day.date()), symbol=s, issue='UNFILLED_ACTION_ADJUSTED_INSUFFICIENT_SHARES'))
                    continue
                price = turnover / volume
                notional = abs(q) * price
                fee = notional * c['commission']
                tax = notional * c['sell_tax'] if q < 0 else 0.
                # Never resize on the execution date, even if a price bound fails.
                cash -= q * price + fee + tax
                fees += fee
                taxes += tax
                notional_total += notional
                holdings[s] = holdings.get(s, 0.) + q
                if holdings[s] < 1e-6:
                    del holdings[s]
                bound_price = next(x['sizing_price'] for x in reversed(order_rows)
                                   if x['signal_date'] == str(signal_day.date()) and x['symbol'] == s)
                if price > bound_price * c['price_buffer'] + 1e-6 or price < bound_price * c['price_lower_buffer'] - 1e-6:
                    warnings.append(dict(date=str(day.date()), symbol=s, issue='EXECUTION_OUTSIDE_PREDECLARED_PRICE_BOUND'))
                trades.append(dict(date=str(day.date()), signal_date=str(signal_day.date()), symbol=s,
                                   trade_symbol=bars.at[s, 'source_symbol'] if 'source_symbol' in bars else s,
                                   shares=q, price=price, notional=notional, fee=fee, tax=tax,
                                   slippage_cost=0., volume_participation=abs(q) / volume,
                                   execution_volume=volume))
            buy_phase = executed_reason == 'SELL_THEN_WAIT_SETTLEMENT'
        last_prices.update(rows.set_index('symbol').close.to_dict())
        nav = cash + sum(q * last_prices[s] for s, q in holdings.items())
        checks = rule_check(holdings, cash, last_prices, c, symbols)
        if is_live:
            passive = []
            active = []
            for s, q in holdings.items():
                if q * last_prices[s] / nav > _cap(s, c) + 1e-10:
                    added = pending.get(s, 0) > 0 and any(x['date'] == str(day.date()) and x['symbol'] == s for x in trades)
                    cap_age[s] = cap_age.get(s, 0) + 1
                    (active if added else passive).append(s)
                else:
                    cap_age.pop(s, None)
            cap_age = {s: count for s, count in cap_age.items() if s in holdings}
            if stale:
                warnings.append(dict(date=str(day.date()), symbol=';'.join(stale), issue='STALE_HELD_PRICES'))
            records.append(dict(date=str(day.date()), nav=nav, economic_nav=nav + receivable, cash=cash,
                                cash_ratio=cash / nav, holdings=len(holdings), dividend_receivable=receivable,
                                odd_residual_names=sum(q % c['lot_size'] > 1e-6 for q in holdings.values()),
                                fees=fees, taxes=taxes, costs=fees + taxes, traded_notional=notional_total,
                                turnover=notional_total / previous_nav, violations=';'.join(checks['violations']),
                                active_cap_breaches=';'.join(active), passive_cap_breaches=';'.join(passive),
                                overdue_passive_caps=';'.join(s for s in passive if cap_age[s] > 5),
                                active_share_status='UNKNOWN_MISSING_HISTORICAL_ETF_HOLDINGS',
                                submission_status='BLOCK_SUBMISSION', stale_count=len(stale), executed_plan=executed_reason))
            for s, q in sorted(holdings.items()):
                holdings_rows.append(dict(date=str(day.date()), symbol=s, shares=q, close=last_prices[s], weight=q * last_prices[s] / nav))
            previous_nav = nav
        if len(rows):
            ranked = score_candidates(rows, c)
        else:
            ranked = rows.copy().set_index('symbol', drop=False)
            for col in ['score', 'entry_ok', 'exit']:
                ranked[col] = pd.Series(dtype=float)
        meta = {}
        if signal_transform is not None:
            before = ranked.copy(deep=True)
            ranked, meta = signal_transform(day, ranked.copy(deep=True))
            ranked = _rank(ranked)
            if set(ranked.index) != set(before.index) or len(ranked) != len(before):
                raise ValueError('Signal transform must preserve available symbols')
            for col in before.columns:
                if col in ('score', 'entry_ok', 'exit'):
                    continue
                pd.testing.assert_series_equal(ranked[col].sort_index(), before[col].sort_index(), check_names=False)
            if not np.isfinite(ranked['score'].dropna()).all():
                raise ValueError('Nonfinite transformed scores')
        if len(ranked):
            pending, plan_reason, selected = make_plan_v2(ranked, holdings, cash, nav, c, buy_phase, selected)
        else:
            pending, plan_reason = {}, 'INFEASIBLE_NO_SIGNAL_ROWS'
        signal_day = day
        decision_date = next_session.get(day)
        decision_cutoff = decision_date.tz_localize('Asia/Taipei') + pd.Timedelta(hours=8, minutes=55) if decision_date is not None else None
        snapshots.append(dict(date=str(day.date()), decision_date=str(decision_date.date()) if decision_date is not None else None,
                              cutoff_at=str(decision_cutoff) if decision_cutoff is not None else None,
                              plan_reason=plan_reason, metadata=json.dumps(_safe_meta(meta), ensure_ascii=False, allow_nan=False),
                              submission_status='BLOCK_SUBMISSION', official_whitelist_status='UNKNOWN_HISTORICAL_RESEARCH_POOL',
                              active_share_status='UNKNOWN_MISSING_HISTORICAL_ETF_HOLDINGS'))
        for s, row in ranked.iterrows():
            record = row.to_dict()
            record.update(date=str(day.date()), symbol=s, plan_reason=plan_reason)
            signal_rows.append(record)
        for s, q in sorted(pending.items()):
            sizing_price = float(ranked.at[s, 'close'])
            order_rows.append(dict(signal_date=str(day.date()), symbol=s, shares=q, signal_nav=nav,
                                   sizing_price=sizing_price, reason=plan_reason,
                                   target_shares=holdings.get(s, 0.) + q,
                                   target_weight=(holdings.get(s, 0.) + q) * sizing_price / nav,
                                   odd_entitlement=holdings.get(s, 0.) % c['lot_size']))
    equity = pd.DataFrame(records)
    equity.loc[equity.index[-1], 'nav'] += receivable
    equity.loc[equity.index[-1], 'cash'] += receivable
    equity.loc[equity.index[-1], 'cash_ratio'] = equity.iloc[-1]['cash'] / equity.iloc[-1]['nav']
    equity.loc[equity.index[-1], 'dividend_receivable'] = 0.
    final_checks = rule_check(holdings, cash + receivable, last_prices, c, symbols)
    equity.loc[equity.index[-1], 'violations'] = ';'.join(final_checks['violations'])
    for record in holdings_rows:
        if record['date'] == equity.date.iloc[-1]:
            record['weight'] = record['shares'] * record['close'] / equity.nav.iloc[-1]
    metrics = _metrics(equity, trades, snapshots, c['initial_cash'])
    metrics['execution_price_bound_breaches'] = sum(w['issue'] == 'EXECUTION_OUTSIDE_PREDECLARED_PRICE_BOUND' for w in warnings)
    metrics['accounting_status'] = 'INVALID_NEGATIVE_CASH' if metrics['negative_cash_days'] else 'SELF_FINANCING'
    return dict(metrics=metrics, equity=equity,
                trades=pd.DataFrame(trades, columns=['date', 'signal_date', 'symbol', 'trade_symbol', 'shares', 'price', 'notional', 'fee', 'tax', 'slippage_cost', 'volume_participation', 'execution_volume']),
                orders=pd.DataFrame(order_rows, columns=['signal_date', 'symbol', 'shares', 'signal_nav', 'sizing_price', 'reason', 'target_shares', 'target_weight', 'odd_entitlement']),
                holdings=pd.DataFrame(holdings_rows, columns=['date', 'symbol', 'shares', 'close', 'weight']),
                signals=pd.DataFrame(signal_rows), warnings=pd.DataFrame(warnings, columns=['date', 'symbol', 'issue']),
                snapshots=pd.DataFrame(snapshots), config=c)


def run_buy_hold_v2(daily, config, symbol='0050.TW'):
    """Contextual buy-and-hold using the same ledger, one initial buffered buy."""
    c = copy.deepcopy(config)
    c.update(research_shadow=True, target_count=1, min_count=1, max_count=1,
             max_weight=1., tsmc_max_weight=1., max_replacements_per_day=0, allocation_mode='local')
    first_day = pd.Timestamp(config['start'])
    universe = pd.DataFrame([dict(symbol=symbol, known_at=str(first_day - pd.Timedelta(days=3650)))])

    def held(day, ranked):
        ranked['entry_ok'] = True
        ranked['exit'] = False
        ranked['score'] = 1.
        return ranked, {'benchmark': 'buy_and_hold', 'not_contest_compliant': True}

    result = run_v2(daily, universe, c, signal_transform=held)
    result['metrics']['classification'] = 'CONTEXTUAL_BENCHMARK'
    return result
