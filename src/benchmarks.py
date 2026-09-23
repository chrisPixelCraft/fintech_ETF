"""Fixed-universe buy-and-hold references, with the same initial risky allocation."""
import numpy as np
import pandas as pd

from src.backtest import validate_daily


def run_buy_hold(data, symbols, config):
    data = validate_daily(data)
    start, end = pd.Timestamp(config['start']), pd.Timestamp(config['end'])
    # Preserve the common market clock while an individual benchmark is suspended.
    sessions = sorted(d for d in data.date.unique() if start <= pd.Timestamp(d) <= end)
    data = data[data.symbol.isin(symbols)]
    first = pd.Timestamp(sessions[0])
    pre = data[data.date < first].sort_values('date').groupby('symbol').tail(1).set_index('symbol')
    if set(symbols) - set(pre.index):
        raise ValueError('Missing initial benchmark sizing price')
    initial = config['initial_cash']
    lot = config['lot_size']
    desired = {s: int((1 - config['cash_target']) * initial / len(symbols) / pre.at[s, 'close'] / lot) * lot for s in symbols}
    holdings = {}
    cash, receivable = float(initial), 0.
    prices = pre.close.to_dict()
    records = []
    for day in sessions:
        day = pd.Timestamp(day)
        rows = data[data.date == day].set_index('symbol')
        costs = 0.
        for s, q in list(holdings.items()):
            if s in rows.index:
                receivable += q * rows.at[s, 'dividend']
                holdings[s] = q * rows.at[s, 'split']
        if day == first:
            for s, q in desired.items():
                if not q:
                    continue
                if s not in rows.index or rows.at[s, 'volume'] <= 0:
                    raise ValueError('Missing first-session benchmark price')
                if config['execution'] == 'vwap':
                    price = rows.at[s, 'turnover'] / rows.at[s, 'volume']
                else:
                    price = rows.at[s, 'open'] * (1 + config['slippage_bps'] / 10000.)
                if not np.isfinite(price) or price <= 0:
                    raise ValueError('Invalid benchmark execution price')
                fee = q * price * config['commission']
                costs += fee
                cash -= q * price + fee
                holdings[s] = q
        stale = len(set(holdings) - set(rows.index))
        prices.update(rows.close.to_dict())
        nav = cash + sum(q * prices[s] for s, q in holdings.items())
        records.append(dict(date=str(day.date()), nav=nav, economic_nav=nav + receivable, cash=cash,
                            dividend_receivable=receivable, stale_count=stale, costs=costs))
    equity = pd.DataFrame(records)
    equity.loc[equity.index[-1], 'nav'] += receivable
    equity.loc[equity.index[-1], 'cash'] += receivable
    equity.loc[equity.index[-1], 'dividend_receivable'] = 0.
    values = np.r_[initial, equity.nav.to_numpy()]
    ret = values[1:] / values[:-1] - 1
    std = float(np.std(ret, ddof=1))
    metrics = dict(total_return=float(values[-1] / initial - 1), max_drawdown=float(-(values / np.maximum.accumulate(values) - 1).min()),
                   annualized_volatility=std * np.sqrt(252), sharpe_zero_rf=float(ret.mean() / std * np.sqrt(252)) if std > 0 else None,
                   transaction_costs=float(equity.costs.sum()), final_nav=float(values[-1]), sessions=len(equity),
                   start=equity.date.iloc[0], end=equity.date.iloc[-1], stale_held_price_days=int((equity.stale_count > 0).sum()),
                   risky_allocation_at_sizing=1 - config['cash_target'], classification='CONTEXTUAL_BENCHMARK')
    return dict(metrics=metrics, equity=equity, config=config)
