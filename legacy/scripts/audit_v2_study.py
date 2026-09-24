"""Independent read-only accounting and provenance audit of a completed v2 study.

PASS is limited to the saved inputs, arithmetic and observable timing. It never
certifies market-data completeness, Active Share, submissions or investability.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
MODELS = ['v1_matched', 'A', 'B', 'C', 'D', '0050']


def check(condition, message):
    if not bool(condition):
        raise AssertionError(message)


def equal(actual, expected, label, atol=.005):
    check(np.isfinite(float(actual)) and np.isfinite(float(expected)), label + ': nonfinite')
    check(np.isclose(actual, expected, rtol=1e-11, atol=atol), f'{label}: {actual} != {expected}')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tables(folder):
    return {name: pd.read_csv(folder / f'{name}.csv', keep_default_na=False, low_memory=False)
            for name in ['equity', 'trades', 'orders', 'holdings', 'signals', 'snapshots', 'warnings']}


def availability(meta, cutoff, label):
    """Inspect explicit provenance timestamps, including nested observations."""
    if isinstance(meta, dict):
        for key, value in meta.items():
            if key in {'available_at', 'known_at', 'cutoff_at', 'sector_cutoff'} and value:
                stamp = pd.Timestamp(value)
                check(stamp.tzinfo is not None, label + ': naive provenance timestamp')
                check(stamp <= cutoff, label + ': post-decision provenance ' + key)
            elif isinstance(value, (dict, list)):
                availability(value, cutoff, label)
    elif isinstance(meta, list):
        for value in meta:
            availability(value, cutoff, label)


def audit_model(output, name, daily, calendar, config):
    folder = output / name
    t = tables(folder)
    settings = json.loads((folder / 'config.json').read_text())
    saved_metrics = json.loads((folder / 'metrics.json').read_text())
    eq, trades, orders, stored_holdings = t['equity'], t['trades'], t['orders'], t['holdings']
    expected_dates = [str(x.date()) for x in calendar]
    check(eq.date.tolist() == expected_dates, name + ': incomplete/shared calendar mismatch')
    check(not orders.duplicated(['signal_date', 'symbol']).any(), name + ': duplicate orders')
    check(not trades.duplicated(['date', 'symbol']).any(), name + ': duplicate fills')
    check(not stored_holdings.duplicated(['date', 'symbol']).any(), name + ': duplicate holdings')
    check(settings.get('research_shadow') is True, name + ': missing explicit shadow authorization')
    check(saved_metrics.get('certification') == 'RESEARCH_SHADOW_ONLY_BLOCK_SUBMISSION', name + ': false certification')
    for field in ['initial_cash', 'cash_target', 'price_buffer', 'commission', 'sell_tax', 'start', 'end', 'execution', 'dividend_cash_policy']:
        check(settings[field] == config[field], name + ': mismatched common assumption ' + field)
    check(t['snapshots'].submission_status.eq('BLOCK_SUBMISSION').all(), name + ': unverified live submission')
    check(t['snapshots'].active_share_status.str.startswith('UNKNOWN').all(), name + ': unsupported Active Share certification')
    check(eq.submission_status.eq('BLOCK_SUBMISSION').all(), name + ': false equity certification')
    check((pd.to_datetime(t['signals'].date) <= calendar[-1]).all(), name + ': future signal date')
    check((pd.to_datetime(orders.signal_date) <= calendar[-1]).all(), name + ': future order date')
    for _, row in t['snapshots'].iterrows():
        if row.cutoff_at:
            cutoff = pd.Timestamp(row.cutoff_at)
            check(cutoff.hour == 8 and cutoff.minute == 55, name + ': wrong decision cutoff')
            check(pd.Timestamp(row.decision_date).date() == cutoff.date(), name + ': cutoff/day mismatch')
            check(pd.Timestamp(row.date).date() < cutoff.date(), name + ': same-day close leakage')
            availability(json.loads(row.metadata), cutoff, name + ' ' + row.date)
    order_lookup = orders.set_index(['signal_date', 'symbol'])
    price_rows = {(pd.Timestamp(day), symbol): row for (day, symbol), row in daily.set_index(['date', 'symbol']).iterrows()}
    by_day_trades = {date: group for date, group in trades.groupby('date')}
    by_day_holdings = {date: group for date, group in stored_holdings.groupby('date')}
    previous = daily[daily.date < calendar[0]].sort_values('date').groupby('symbol').tail(1)
    marks = previous.set_index('symbol').close.to_dict()
    whole_clock = sorted(pd.Timestamp(x) for x in daily.date.unique())
    preceding = dict(zip(whole_clock[1:], whole_clock[:-1]))
    q, cash, receivable = {}, float(settings['initial_cash']), 0.
    previous_book = cash
    rebuilt = []
    max_error = 0.
    for _, saved in eq.iterrows():
        date = saved.date
        day = pd.Timestamp(date)
        for symbol, old in list(q.items()):
            row = price_rows.get((day, symbol))
            if row is not None:
                receivable += old * float(row.dividend)
                q[symbol] = old * float(row.split)
        fees = taxes = notional = 0.
        todays = by_day_trades.get(date, trades.iloc[:0])
        check(not (todays.shares.gt(0).any() and todays.shares.lt(0).any()), name + ' ' + date + ': simultaneous funding sells/buys')
        for _, trade in todays.iterrows():
            symbol, quantity = trade.symbol, float(trade.shares)
            signal = pd.Timestamp(trade.signal_date)
            check(signal == preceding[day], name + ': trade not sized on prior market close')
            check(quantity and abs(quantity / settings['lot_size'] - round(quantity / settings['lot_size'])) < 1e-10,
                  name + ': non-board-lot trade')
            key = (trade.signal_date, symbol)
            check(key in order_lookup.index, name + ': fill has no prior fixed order')
            order = order_lookup.loc[key]
            equal(quantity, order.shares, name + ': order/fill quantity', atol=1e-8)
            row = price_rows.get((day, symbol))
            check(row is not None, name + ': fill on missing source bar')
            denominator = row.execution_volume if 'execution_volume' in daily else row.volume
            check(np.isfinite(denominator) and denominator > 0 and np.isfinite(row.turnover) and row.turnover > 0,
                  name + ': fill without paired official VWAP')
            price = float(row.turnover / denominator)
            equal(trade.price, price, name + ': paired VWAP', atol=1e-9)
            amount = abs(quantity) * price
            commission = amount * settings['commission']
            tax = amount * settings['sell_tax'] if quantity < 0 else 0.
            for field, expected in [('notional', amount), ('fee', commission), ('tax', tax),
                                    ('volume_participation', abs(quantity) / denominator), ('slippage_cost', 0.)]:
                equal(trade[field], expected, name + ': fill ' + field)
            if name in ['A', 'B', 'C', 'D'] and quantity > 0 and order.reason != 'BUY_CASH_CAP_CORRECTION':
                check(q.get(symbol, 0.) == 0, name + ': local mode topped up a retained name')
            q[symbol] = q.get(symbol, 0.) + quantity
            check(q[symbol] >= -1e-6, name + ': short holding')
            if q[symbol] < 1e-6:
                del q[symbol]
            cash -= quantity * price + commission + tax
            fees += commission
            taxes += tax
            notional += amount
        check(cash >= -.005, name + ' ' + date + ': negative cash; cannot certify accounting')
        observed = daily[daily.date == day]
        marks.update(observed.set_index('symbol').close.to_dict())
        book = cash + sum(shares * marks[symbol] for symbol, shares in q.items())
        economic = book + receivable
        if day == calendar[-1]:
            cash += receivable
            book += receivable
            receivable = 0.
        values = dict(nav=book, economic_nav=economic, cash=cash, dividend_receivable=receivable,
                      costs=fees+taxes, fees=fees, taxes=taxes, traded_notional=notional,
                      turnover=notional/previous_book, cash_ratio=cash/book, holdings=len(q))
        for field, expected in values.items():
            max_error = max(max_error, abs(float(saved[field]) - expected))
            equal(saved[field], expected, name + ' ' + date + ': ' + field)
        saved_positions = by_day_holdings.get(date, stored_holdings.iloc[:0]).set_index('symbol')
        check(set(saved_positions.index) == set(q), name + ' ' + date + ': phantom/missing holding')
        for symbol, shares in q.items():
            equal(saved_positions.at[symbol, 'shares'], shares, name + ': action/fill shares', atol=1e-7)
            equal(saved_positions.at[symbol, 'close'], marks[symbol], name + ': mark', atol=1e-8)
            equal(saved_positions.at[symbol, 'weight'], shares*marks[symbol]/book, name + ': weight', atol=1e-10)
        if day >= pd.Timestamp('2025-07-24'):
            check('2888.TW' not in q, name + ': unsupported two-asset merger held')
        rebuilt.append({'date': date, **values})
        previous_book = book
    rebuilt = pd.DataFrame(rebuilt)
    expected_metrics = {}
    for column, prefix in [('nav', ''), ('economic_nav', 'economic_')]:
        wealth = np.r_[settings['initial_cash'], rebuilt[column].to_numpy()]
        returns = wealth[1:] / wealth[:-1] - 1
        std = np.std(returns, ddof=1) if len(returns) > 1 else 0.
        expected_metrics.update({prefix+'total_return': wealth[-1]/wealth[0]-1,
                                 prefix+'max_drawdown': -(wealth/np.maximum.accumulate(wealth)-1).min(),
                                 prefix+'annualized_volatility': std*np.sqrt(252),
                                 prefix+'sharpe_zero_rf': returns.mean()/std*np.sqrt(252) if std else None})
    expected_metrics.update(transaction_costs=rebuilt.costs.sum(), commission_cost=rebuilt.fees.sum(),
                            sell_tax_cost=rebuilt.taxes.sum(), turnover_two_way=rebuilt.turnover.sum(),
                            final_nav=rebuilt.nav.iloc[-1], sessions=len(rebuilt), trades=len(trades), negative_cash_days=0)
    for field, expected in expected_metrics.items():
        if expected is None:
            check(saved_metrics[field] is None, name + ': nonnull undefined metric ' + field)
        else:
            equal(saved_metrics[field], expected, name + ': metric ' + field, atol=1e-8)
    return rebuilt, dict(sessions=len(eq), trades=len(trades), max_ledger_absolute_error=max_error,
                         raw_rule_breach_days=int(eq.violations.ne('').sum()),
                         unfilled=int(t['warnings'].issue.str.startswith('UNFILLED').sum()),
                         price_bound_warnings=int(t['warnings'].issue.eq('EXECUTION_OUTSIDE_PREDECLARED_PRICE_BOUND').sum()),
                         submission_status='BLOCK_SUBMISSION', active_share_status='UNKNOWN')


def audit_aggregate(output, reconstructed, config):
    initial = config['initial_cash']
    counts = {}
    for filename, freq, key in [('monthly_comparison.csv', 'M', 'month'), ('annual_comparison.csv', 'Y', 'year')]:
        frame = pd.read_csv(output / filename, dtype={key: str})
        checked = set()
        for model, eq in reconstructed.items():
            prev = initial
            periods = pd.to_datetime(eq.date).dt.to_period(freq)
            for period, group in eq.groupby(periods):
                rows = frame[(frame.model == model) & (frame[key] == str(period))]
                check(len(rows) == 1, filename + ': missing/duplicate period')
                saved = rows.iloc[0]
                values = np.r_[prev, group.economic_nav.to_numpy()]
                equal(saved.economic_return, values[-1]/values[0]-1, filename + ': return', atol=1e-10)
                equal(saved.economic_max_drawdown, -(values/np.maximum.accumulate(values)-1).min(), filename + ': MDD', atol=1e-10)
                check(saved.sessions == len(group), filename + ': period session count')
                check(saved.through == group.date.iloc[-1], filename + ': period end')
                checked.add((model, str(period)))
                prev = values[-1]
        check(len(frame) == len(checked), filename + ': extraneous rows')
        counts[filename] = len(checked)
    summary = pd.read_csv(output / 'summary.csv').set_index('model')
    check(set(summary.index) == set(MODELS), 'Summary model mismatch')
    for model, eq in reconstructed.items():
        row = summary.loc[model]
        wealth = np.r_[initial, eq.economic_nav.to_numpy()]
        equal(row.economic_total_return, wealth[-1]/initial-1, model + ': summary return', atol=1e-10)
        equal(row.economic_max_drawdown, -(wealth/np.maximum.accumulate(wealth)-1).min(), model + ': summary MDD', atol=1e-10)
        days = (pd.Timestamp(eq.date.iloc[-1])-pd.Timestamp(config['start'])+pd.Timedelta(days=1)).days
        equal(row.calendar_cagr, (wealth[-1]/initial)**(365.25/days)-1, model + ': CAGR', atol=1e-10)
    block_table = pd.read_csv(output / 'blocks_24_sessions.csv')
    for model, eq in reconstructed.items():
        prev = initial
        for n, start in enumerate(range(0, len(eq), 24), 1):
            group = eq.iloc[start:start+24]
            rows = block_table[(block_table.model == model) & (block_table.block == n)]
            check(len(rows) == 1, 'Missing 24-session block')
            saved = rows.iloc[0]
            wealth = np.r_[prev, group.economic_nav.to_numpy()]
            equal(saved.return_net, wealth[-1]/wealth[0]-1, 'Block return', atol=1e-10)
            equal(saved.mdd, -(wealth/np.maximum.accumulate(wealth)-1).min(), 'Block MDD', atol=1e-10)
            check(saved.sessions == len(group) and bool(saved.complete) == (len(group) == 24), 'Block completeness')
            prev = wealth[-1]
    counts['blocks_24_sessions.csv'] = len(block_table)
    return counts


def audit(output):
    provenance = json.loads((output / 'provenance.json').read_text())
    check(provenance.get('outputs_complete') is True and provenance.get('completed_at'), 'Run is not complete; audit not permitted')
    for relative, expected in provenance['hashes'].items():
        check(sha(ROOT / relative) == expected, 'Current source/input hash changed: ' + relative)
        check(sha(output / 'input_snapshot' / relative) == expected, 'Snapshot hash mismatch: ' + relative)
    config = provenance['config']
    daily = pd.read_csv(output / 'input_snapshot/data/v2/market_daily.csv')
    daily['date'] = pd.to_datetime(daily.date)
    check(not daily.duplicated(['date', 'symbol']).any(), 'Duplicate canonical bars')
    calendar = sorted(pd.Timestamp(x) for x in daily.date.unique()
                      if pd.Timestamp(config['start']) <= pd.Timestamp(x) <= pd.Timestamp(config['end']))
    reconstructed, models = {}, {}
    for name in MODELS:
        print('Auditing ' + name, flush=True)
        reconstructed[name], models[name] = audit_model(output, name, daily, calendar, config)
    aggregates = audit_aggregate(output, reconstructed, config)
    return dict(status='PASS', scope='ACCOUNTING_AND_RECORDED_TIMING_ONLY_NOT_MARKET_OR_CONTEST_CERTIFICATION',
                verified_at=datetime.now(timezone.utc).isoformat(), models=models, aggregate_rows=aggregates,
                input_and_code_hashes=len(provenance['hashes']), auditor_sha256=sha(__file__),
                limitations=['Source truth and historical publication vintages are not certified.',
                             'Signals and input publication metadata still require independent source validation.',
                             'Unknown Active Share/official historical whitelist block contest submission.',
                             'Raw rule breaches and missing fills are retained, not converted to passes.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='outputs/backtest_v2_2025_to_now')
    args = parser.parse_args()
    output = Path(args.output)
    if not output.is_absolute():
        output = ROOT / output
    try:
        result = audit(output)
    except Exception as error:
        if (output / 'provenance.json').exists():
            (output / 'audit.json').write_text(json.dumps(dict(status='FAIL', error=str(error), verified_at=datetime.now(timezone.utc).isoformat()), indent=2)+'\n')
        raise
    (output / 'audit.json').write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
    print(json.dumps(result, indent=2, ensure_ascii=False))
