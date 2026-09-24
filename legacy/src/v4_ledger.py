"""V4 Stage 1 ledger: frozen V3 accounting with explicit execution inputs.

The ordinary source below materializes the V3 ledger adapter. No legacy module
is patched. Orders are fixed at D-1; only fills access execution-day prices.
Whole-day rollback and terminal dividend credit retain V3 research assumptions;
unknown organizer settlement and Active Share contracts block submission.
"""
from __future__ import annotations
import json
import numpy as np
import pandas as pd
from src.backtest import rule_check
from src.backtest_v2 import _settings, _cap, _safe_meta, _metrics
from src.double_check_ledger import cap_state, warning_reasons
from src.official_deep_tuning import representable_weight
from src.strategy_24d import _score

def run_ledger(daily, universe, config, requested_calendar, planner, execution_prices, execution_volumes, execution_mode):
    """Run prior-close fixed orders against an explicit execution-price mapping.

    Input daily rows carry already-built frozen V3 features. The planner never
    receives execution_prices or execution_volumes; these are read only on fill.
    """
    c = _settings(config)
    c['execution'] = execution_mode
    if not c.get('research_shadow', False):
        raise ValueError('BLOCK_SUBMISSION: set research_shadow=True for unverified historical comparisons')
    # Input is the causal frozen V3 feature panel.
    universe = universe.copy()
    if 'known_at' not in universe:
        raise ValueError('Universe requires known_at')
    universe['symbol'] = universe.symbol.astype(str)
    if universe.symbol.duplicated().any():
        raise ValueError('Expected one fixed universe snapshot')
    universe['known_at'] = pd.to_datetime(universe.known_at, utc=True)
    symbols = set(universe.symbol)
    start, end = pd.Timestamp(c['start']), pd.Timestamp(c['end'])
    all_sessions = requested_calendar
    next_session = dict(zip(all_sessions[:-1], all_sessions[1:]))
    calendar = [x for x in all_sessions if start <= x <= end]
    if not calendar:
        raise ValueError('No sessions in requested interval')
    prior = sorted(pd.Timestamp(x) for x in daily.date.unique() if pd.Timestamp(x) < calendar[0])
    if not prior:
        raise ValueError('Missing prior-close sizing session')
    initial_day = prior[-1]
    cutoff = calendar[0].tz_localize('Asia/Taipei') + pd.Timedelta(hours=8, minutes=55)
    if (universe.known_at > cutoff.tz_convert('UTC')).any() and not c.get('ex_post_fixed_universe', False):
        raise ValueError('LOOKAHEAD_UNIVERSE: constituents not known at first decision')
    stock_data = daily[daily.symbol.isin(symbols)]
    # Missing historical names stay in the whitelist and coverage denominator.
    features = stock_data.copy()
    grouped = {pd.Timestamp(d): rows.copy() for d, rows in features.groupby('date')}
    holdings, last_prices, pending = {}, {}, {}
    cash, receivable = float(c['initial_cash']), 0.
    previous_nav = cash
    signal_day, plan_reason, buy_phase, selected = None, 'NO_PRIOR_SIGNAL', False, []
    records, trades, order_rows, holdings_rows, signal_rows, warnings, snapshots = [], [], [], [], [], [], []
    cap_age = {}
    warning_count = 0
    compliance_rows, rejected_trades = [], []
    initial_prices = stock_data[(stock_data.date <= initial_day) & np.isfinite(stock_data.close) & stock_data.close.gt(0)].sort_values('date').groupby('symbol').tail(1)
    last_prices.update(initial_prices.set_index('symbol').close.to_dict())
    for day in [initial_day] + calendar:
        rows = grouped.get(day, features.iloc[:0].copy())
        bars = rows.set_index('symbol')
        is_live = day != initial_day
        fees = taxes = notional_total = 0.
        stale = []
        executed_reason = plan_reason
        previous_ages = dict(cap_age)
        trade_start = len(trades)
        if is_live:
            for s, q in list(holdings.items()):
                if s not in bars.index:
                    stale.append(s)
                    continue
                if not np.isfinite(bars.at[s, 'close']) or bars.at[s, 'close'] <= 0 or not bool(bars.at[s, 'signal_available']):
                    stale.append(s)
                receivable += q * float(bars.at[s, 'dividend'])
                holdings[s] = q * float(bars.at[s, 'split'])
                if not np.isfinite(bars.at[s, 'close']) or bars.at[s, 'close'] <= 0:
                    last_prices[s] /= float(bars.at[s, 'split'])
            # Roll back trading only, never erase corporate entitlements.
            settlement_holdings, settlement_cash = dict(holdings), cash
            for s, q in sorted(pending.items(), key=lambda item: (item[1] > 0, item[0])):
                price = execution_prices.get((day, s), np.nan)
                volume = execution_volumes.get((day, s), np.nan)
                valid = np.isfinite(price) and price > 0
                if not valid:
                    warnings.append(dict(date=str(day.date()), symbol=s, issue=('UNFILLED_MISSING_OFFICIAL_AVERAGE' if execution_mode == 'official_average' else 'UNFILLED_MISSING_OPEN_PROXY')))
                    continue
                if holdings.get(s, 0.) + q < -1e-6:
                    warnings.append(dict(date=str(day.date()), symbol=s, issue='UNFILLED_ACTION_ADJUSTED_INSUFFICIENT_SHARES'))
                    continue
                # Execution quantity was fixed at the preceding close.
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
                                   trade_symbol=bars.at[s, 'source_symbol'] if s in bars.index and 'source_symbol' in bars else s,
                                   shares=q, price=price, notional=notional, fee=fee, tax=tax,
                                   slippage_cost=0., volume_participation=abs(q) / volume if np.isfinite(volume) and volume > 0 else np.nan,
                                   execution_volume=volume))
            buy_phase = executed_reason == 'SELL_THEN_WAIT_SETTLEMENT'
        last_prices.update(rows.loc[np.isfinite(rows.close) & rows.close.gt(0)].set_index('symbol').close.to_dict())
        stale = sorted(set(stale) | {s for s in holdings if s not in bars.index or not bool(bars.at[s, 'signal_available'])})
        nav = cash + sum(q * last_prices[s] for s, q in holdings.items())
        checks = rule_check(holdings, cash, last_prices, c, symbols)
        if is_live:
            bought = {t['symbol'] for t in trades[trade_start:] if t['shares'] > 0}
            cap_age, active, passive = cap_state(holdings, last_prices, nav, previous_ages, bought, c,
                                                 settlement_holdings, settlement_cash)
            overdue = [s for s in passive if cap_age[s] > 5]
            reasons = warning_reasons(checks, active, overdue)
            proposed_nav, proposed_cash = nav, cash
            proposed_count = len(holdings)
            rejected_count = len(trades) - trade_start if reasons else 0
            if reasons:
                warning_count += 1
                rejected_trades.extend(dict(t, rejection=';'.join(reasons)) for t in trades[trade_start:])
                del trades[trade_start:]
                holdings, cash = settlement_holdings, settlement_cash
                fees = taxes = notional_total = 0.
                # A rolled-back sell phase never creates a pending buy phase.
                # Replan from the authoritative restored book on this close.
                buy_phase = False
                nav = cash + sum(q * last_prices[s] for s, q in holdings.items())
                checks = rule_check(holdings, cash, last_prices, c, symbols)
                cap_age, _, _ = cap_state(holdings, last_prices, nav, previous_ages, set(), c)
                warnings.append(dict(date=str(day.date()), symbol='', issue='SIMULATED_DAY_ROLLBACK:' + ';'.join(reasons)))
            compliance_rows.append(dict(date=str(day.date()), proposed_nav=proposed_nav,
                proposed_cash=proposed_cash, proposed_holdings=proposed_count,
                warning_reasons=';'.join(reasons), warning_today=bool(reasons),
                cumulative_warnings=warning_count, rolled_back=bool(reasons),
                rejected_fills=rejected_count, disqualified=warning_count >= 3,
                active_caps=';'.join(active), passive_caps=';'.join(passive), overdue_caps=';'.join(overdue),
                settled_nav=nav, settled_cash=cash, settled_holdings=len(holdings),
                stale_count=len(stale), active_share_status='UNKNOWN',
                submission_status='BLOCK_SUBMISSION'))
            if stale:
                warnings.append(dict(date=str(day.date()), symbol=';'.join(stale), issue='STALE_HELD_PRICES'))
            records.append(dict(date=str(day.date()), nav=nav, economic_nav=nav + receivable, cash=cash,
                                cash_ratio=cash / nav, holdings=len(holdings), dividend_receivable=receivable,
                                odd_residual_names=sum(q % c['lot_size'] > 1e-6 for q in holdings.values()),
                                fees=fees, taxes=taxes, costs=fees + taxes, traded_notional=notional_total,
                                turnover=notional_total / previous_nav, violations=';'.join(checks['violations']),
                                active_cap_breaches=';'.join(active), passive_cap_breaches=';'.join(passive),
                                overdue_passive_caps=';'.join(overdue),
                                active_share_status='UNKNOWN_MISSING_HISTORICAL_ETF_HOLDINGS',
                                submission_status='BLOCK_SUBMISSION', stale_count=len(stale), executed_plan=executed_reason))
            for s, q in sorted(holdings.items()):
                holdings_rows.append(dict(date=str(day.date()), symbol=s, shares=q, close=last_prices[s], weight=q * last_prices[s] / nav))
            previous_nav = nav
            if warning_count >= 3:
                break
        if len(rows):
            ranked = _score(rows, c)
        else:
            ranked = rows.copy().set_index('symbol', drop=False)
            for col in ['score', 'entry_ok', 'exit']:
                ranked[col] = pd.Series(dtype=float)
        meta = {}
        if len(ranked):
            pending, plan_reason, selected = planner(ranked, holdings, cash, nav, c, buy_phase, selected)
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
                                   target_weight=representable_weight(holdings.get(s, 0.) + q, sizing_price, nav, _cap(s, c)),
                                   odd_entitlement=holdings.get(s, 0.) % c['lot_size']))
    equity = pd.DataFrame(records)
    # Keep pre-credit settlement checks in compliance_daily; no tradable cash is
    # invented. Period-end distribution is a separate performance-only credit.
    equity['terminal_dividend_credit'] = 0.
    if warning_count < 3:
        equity.loc[equity.index[-1], 'terminal_dividend_credit'] = receivable
        equity.loc[equity.index[-1], 'nav'] += receivable
        equity.loc[equity.index[-1], 'dividend_receivable'] = 0.
    equity.loc[equity.index[-1], 'cash_ratio'] = equity.iloc[-1]['cash'] / equity.iloc[-1]['nav']
    for record in holdings_rows:
        if record['date'] == equity.date.iloc[-1]:
            record['weight'] = record['shares'] * record['close'] / equity.nav.iloc[-1]
    metrics = _metrics(equity, trades, snapshots, c['initial_cash'])
    metrics['execution_price_bound_breaches'] = sum(w['issue'] == 'EXECUTION_OUTSIDE_PREDECLARED_PRICE_BOUND' for w in warnings)
    metrics['accounting_status'] = 'INVALID_NEGATIVE_CASH' if metrics['negative_cash_days'] else 'SELF_FINANCING'
    metrics.update(simulated_warning_days=warning_count, disqualified=warning_count >= 3,
                   complete_period=len(equity) == len(calendar),
                   rejected_trade_count=len(rejected_trades),
                   official_compliance='UNKNOWN_BLOCK_SUBMISSION')
    return dict(metrics=metrics, equity=equity,
                trades=pd.DataFrame(trades, columns=['date', 'signal_date', 'symbol', 'trade_symbol', 'shares', 'price', 'notional', 'fee', 'tax', 'slippage_cost', 'volume_participation', 'execution_volume']),
                orders=pd.DataFrame(order_rows, columns=['signal_date', 'symbol', 'shares', 'signal_nav', 'sizing_price', 'reason', 'target_shares', 'target_weight', 'odd_entitlement']),
                holdings=pd.DataFrame(holdings_rows, columns=['date', 'symbol', 'shares', 'close', 'weight']),
                signals=pd.DataFrame(signal_rows), warnings=pd.DataFrame(warnings, columns=['date', 'symbol', 'issue']),
                snapshots=pd.DataFrame(snapshots), config=c,
                compliance_daily=pd.DataFrame(compliance_rows),
                rejected_trades=pd.DataFrame(rejected_trades, columns=[*pd.DataFrame(trades, columns=['date', 'signal_date', 'symbol', 'trade_symbol', 'shares', 'price', 'notional', 'fee', 'tax', 'slippage_cost', 'volume_participation', 'execution_volume']).columns, 'rejection']))
