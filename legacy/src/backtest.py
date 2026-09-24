"""Causal, pre-close-sized Taiwan equity research backtest (not a contest emulator)."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


def validate_daily(data):
    required = {'date', 'symbol', 'open', 'high', 'low', 'close', 'volume'}
    if required - set(data):
        raise ValueError(f'Missing daily columns: {required - set(data)}')
    data = data.copy()
    data['date'] = pd.to_datetime(data.date).dt.normalize()
    data['symbol'] = data.symbol.astype(str)
    if data.duplicated(['date', 'symbol']).any():
        raise ValueError('Duplicate daily rows')
    for col, default in [('dividend', 0.), ('split', 1.)]:
        if col not in data:
            data[col] = default
    nums = ['open', 'high', 'low', 'close', 'volume', 'dividend', 'split']
    if not np.isfinite(data[nums].to_numpy(float)).all():
        raise ValueError('Nonfinite prices/actions; missing rows must remain missing')
    if (data[['open', 'high', 'low', 'close', 'split']] <= 0).any().any():
        raise ValueError('Nonpositive price or split')
    if (data.volume < 0).any() or (data.dividend < 0).any():
        raise ValueError('Negative volume/dividend')
    market = data.groupby('date').agg(symbols=('symbol', 'nunique'), volume=('volume', 'sum'))
    if ((market.symbols >= 20) & (market.volume == 0)).any():
        raise ValueError('MARKET_CALENDAR: market-wide zero-volume rows require closure verification')
    if (data.high + 1e-5 < data[['open', 'close', 'low']].max(axis=1)).any():
        raise ValueError('OHLC high inconsistent')
    if (data.low - 1e-5 > data[['open', 'close', 'high']].min(axis=1)).any():
        raise ValueError('OHLC low inconsistent')
    return data.sort_values(['symbol', 'date']).reset_index(drop=True)


def causal_prices(frame):
    """Total-return index and volume in initial-share units, using past events only."""
    # Cash dividends are amounts per pre-action share (official TWSE/TPEx convention).
    gross = ((frame.close * frame.split + frame.dividend) / frame.close.shift()).fillna(1.)
    index = gross.cumprod() * float(frame.close.iloc[0])
    adjusted_volume = frame.volume / frame.split.cumprod()
    return index, adjusted_volume


def aggregate_four_hour(hourly):
    """Yahoo timestamps are bar starts. Only four complete hourly bars qualify."""
    hourly = hourly.copy()
    timecol = next((x for x in ['timestamp', 'datetime', 'time'] if x in hourly), None)
    if timecol is None:
        raise ValueError('Hourly input requires timestamp')
    ts = pd.to_datetime(hourly[timecol], utc=True).dt.tz_convert('Asia/Taipei')
    hourly['date'] = ts.dt.tz_localize(None).dt.normalize()
    hourly['hour'] = ts.dt.hour
    hourly['minute'] = ts.dt.minute
    valid = ((hourly.hour >= 9) & (hourly.hour < 13) & (hourly.minute == 0)
             & np.isfinite(hourly[['open', 'high', 'low', 'close', 'volume']]).all(axis=1)
             & (hourly[['open', 'high', 'low', 'close']] > 0).all(axis=1))
    grouped = hourly[valid].sort_values(['symbol', 'date', 'hour']).groupby(['symbol', 'date'], sort=True)
    bars = grouped.agg(open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
                       close=('close', 'last'), volume=('volume', 'sum'), count=('hour', 'size'), unique_hours=('hour', 'nunique')).reset_index()
    bars = bars[(bars['count'] == 4) & (bars.unique_hours == 4)]
    bars['symbol'] = bars.symbol.astype(str)
    return bars[['symbol', 'date', 'open', 'high', 'low', 'close', 'volume']].reset_index(drop=True)


def compute_features(daily, config, four_hour=None):
    daily = validate_daily(daily)
    frames = []
    for symbol, rows in daily.groupby('symbol', sort=True):
        rows = rows.copy().reset_index(drop=True)
        price, volume = causal_prices(rows)
        ret = price.pct_change(fill_method=None)
        for span in [20, 50, 100, 200]:
            rows[f'ema{span}'] = price.ewm(span=span, adjust=False, min_periods=span).mean()
        rows['signal_price'] = price
        rows['return20'] = price.pct_change(20, fill_method=None)
        rows['return50'] = price.pct_change(50, fill_method=None)
        rows['return1'] = ret
        rows['volume_ratio'] = volume.rolling(5, min_periods=5).mean() / volume.shift().rolling(20, min_periods=20).mean()
        rows['volatility_ratio'] = ret.rolling(5).std() / ret.shift().rolling(20).std()
        macd = price.ewm(span=12, adjust=False).mean() - price.ewm(span=26, adjust=False).mean()
        rows['macd_hist'] = macd - macd.ewm(span=9, adjust=False).mean()
        rows['ready'] = np.arange(len(rows)) + 1 >= config['warmup_sessions']
        rows['trend'] = ((price > rows.ema20) & (rows.ema20 > rows.ema50)).astype(float)
        rows['long_trend'] = ((price > rows.ema100) & (rows.ema100 > rows.ema200)).astype(float)
        frames.append(rows)
    features = pd.concat(frames, ignore_index=True)
    features['four_hour_ok'] = False
    features['four_hour_available'] = False
    features['four_hour_ready'] = False
    if four_hour is not None and len(four_hour):
        four_hour = four_hour.copy()
        four_hour['date'] = pd.to_datetime(four_hour.date).dt.normalize()
        four_hour['symbol'] = four_hour.symbol.astype(str)
        # Carry all daily events across gaps; a missing intraday bar must not erase a split.
        event_frames = []
        for symbol, actions in daily.groupby('symbol'):
            actions = actions.sort_values('date').copy()
            actions['split_cumulative'] = actions.split.cumprod()
            actions['dividend_cumulative'] = (actions.dividend * actions.split_cumulative.shift(fill_value=1.)).cumsum()
            event_frames.append(actions[['date', 'symbol', 'split_cumulative', 'dividend_cumulative']])
        bars = four_hour.merge(pd.concat(event_frames), on=['date', 'symbol'], validate='one_to_one')
        four_frames = []
        for symbol, rows in bars.groupby('symbol'):
            rows = rows.sort_values('date').copy()
            rows['split'] = rows.split_cumulative.div(rows.split_cumulative.shift()).fillna(1.)
            rows['dividend'] = rows.dividend_cumulative.diff().fillna(0.) / rows.split_cumulative.shift(fill_value=1.)
            p, _ = causal_prices(rows)
            e20 = p.ewm(span=20, adjust=False, min_periods=20).mean()
            macd = p.ewm(span=12, adjust=False).mean() - p.ewm(span=26, adjust=False).mean()
            rows['four_hour_ready'] = np.arange(len(rows)) >= 49
            rows['four_hour_ok'] = (p > e20) & (macd > macd.ewm(span=9, adjust=False).mean()) & rows.four_hour_ready
            four_frames.append(rows[['date', 'symbol', 'four_hour_ok', 'four_hour_ready']])
        temp = pd.concat(four_frames)
        features = features.drop(columns=['four_hour_ok', 'four_hour_available', 'four_hour_ready']).merge(temp, on=['date', 'symbol'], how='left', validate='one_to_one')
        features['four_hour_available'] = features.four_hour_ok.notna()
        features['four_hour_ok'] = features.four_hour_ok.fillna(False).astype(bool)
        features['four_hour_ready'] = features.four_hour_ready.fillna(False).astype(bool)
    return features.sort_values(['date', 'symbol']).reset_index(drop=True)


def score_candidates(rows, config):
    rows = rows.copy().set_index('symbol', drop=False)
    rows.index.name = '_symbol_index'
    weights = config['score_weights']
    score = (weights['return20'] * rows.return20.rank(pct=True)
             + weights['return50'] * rows.return50.rank(pct=True)
             + weights['volume'] * rows.volume_ratio.clip(0, 3).rank(pct=True)
             + weights['macd'] * (rows.macd_hist > 0).astype(float)
             + weights['trend'] * rows.trend + weights['long_trend'] * rows.long_trend)
    rows['score'] = score
    rows['entry_ok'] = (rows.ready & (rows.signal_price > rows.ema50)
                        & (rows.volatility_ratio <= config['volatility_spike_ratio'])
                        & (rows.return1 <= config['one_day_chase_return'])
                        & rows.volume_ratio.between(config['volume_low'], config['volume_high'])
                        & (rows.volume > 0))
    if config['use_4h']:
        rows['entry_ok'] &= rows.four_hour_ok
    elif config.get('match_4h_coverage', False):
        rows['entry_ok'] &= rows.four_hour_available & rows.four_hour_ready
    rows['exit'] = ((rows.signal_price < rows.ema50)
                    | ((rows.return20 < 0) & (rows.macd_hist < 0)))
    return rows.sort_values(['score', 'symbol'], ascending=[False, True])


def make_plan(rows, holdings, cash, nav, config):
    """All arguments refer to the signal close; never pass execution-day data."""
    ranked = score_candidates(rows, config)
    retained = [s for s in holdings if s in ranked.index and not bool(ranked.at[s, 'exit'])]
    entrants = [s for s in ranked.index if bool(ranked.at[s, 'entry_ok']) and s not in retained]
    selected = list(retained)
    while len(selected) < config['target_count'] and entrants:
        selected.append(entrants.pop(0))
    for _ in range(config['max_replacements_per_day']):
        if not selected or not entrants:
            break
        weakest = min(selected, key=lambda s: (float(ranked.at[s, 'score']), s))
        if ranked.at[entrants[0], 'score'] <= ranked.at[weakest, 'score'] + config['replacement_margin']:
            break
        selected.remove(weakest)
        selected.append(entrants.pop(0))
    if len(selected) < config['min_count']:
        return {}, 'INFEASIBLE_FEWER_THAN_20', ranked
    selected = selected[:config['max_count']]
    changed = set(selected) != set(holdings)
    cash_ratio = cash / nav
    cap_breach = any(holdings[s] * ranked.at[s, 'close'] / nav > (config['tsmc_max_weight'] if s.split('.')[0] == '2330' else config['max_weight']) for s in holdings if s in ranked.index)
    rebalance = changed or cap_breach or not (config['cash_rebalance_lower'] <= cash_ratio <= config['cash_rebalance_upper'])
    if not rebalance:
        return {}, 'HOLD', ranked
    lot = config['lot_size']
    target_weight = (1 - config['cash_target']) / len(selected)
    desired = {s: int(np.floor(target_weight * nav / ranked.at[s, 'close'] / lot)) * lot for s in selected}
    if any(q <= 0 for q in desired.values()):
        return {}, 'INFEASIBLE_ROUND_LOTS', ranked
    # Corporate actions can create odd-share entitlements. Do not invent odd-lot trades.
    orders = {s: int(np.trunc((desired.get(s, 0) - holdings.get(s, 0)) / lot)) * lot
              for s in sorted(set(desired) | set(holdings))}
    orders = {s: q for s, q in orders.items() if q}
    projected = {s: holdings.get(s, 0.) + orders.get(s, 0.) for s in set(holdings) | set(orders)}
    projected = {s: q for s, q in projected.items() if q > 1e-6}
    projected_cash = float(cash)
    for s, q in orders.items():
        price = float(ranked.at[s, 'close']) * (1 + np.sign(q) * config['slippage_bps'] / 10000.)
        projected_cash -= q * price + abs(q) * price * (config['commission'] + (config['sell_tax'] if q < 0 else 0.))
    checks = rule_check(projected, projected_cash, ranked.close.to_dict(), config, set(ranked.index))
    if checks['violations']:
        return {}, 'INFEASIBLE_PRETRADE:' + ';'.join(checks['violations']), ranked
    return orders, 'REBALANCE', ranked


def rule_check(holdings, cash, prices, config, whitelist, benchmark_top10=None):
    nav = cash + sum(q * prices[s] for s, q in holdings.items())
    violations = []
    if not config['min_count'] <= len(holdings) <= config['max_count']:
        violations.append('HOLDING_COUNT')
    if cash < -1e-6:
        violations.append('NEGATIVE_CASH')
    if nav <= 0 or cash / nav >= .25:
        violations.append('CASH_GE_25_PERCENT')
    weights = {s: q * prices[s] / nav for s, q in holdings.items()} if nav > 0 else {}
    for s, weight in weights.items():
        if s not in whitelist:
            violations.append('NON_WHITELIST:' + s)
        if weight > (config['tsmc_max_weight'] if s.split('.')[0] == '2330' else config['max_weight']) + 1e-10:
            violations.append('WEIGHT_CAP:' + s)
    # This diagnostic is intentionally not a certification of the undefined contest top10 normalization.
    active = {}
    if benchmark_top10 is not None:
        top = dict(sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[:10])
        for name, benchmark in benchmark_top10.items():
            active[name] = .5 * sum(abs(top.get(s, 0.) - benchmark.get(s, 0.)) for s in set(top) | set(benchmark))
    return dict(nav=nav, cash_ratio=cash / nav if nav else None, holding_count=len(holdings),
                violations=violations, active_share=active,
                active_share_status='UNVERIFIED_FORMULA' if active else 'UNKNOWN_MISSING_HISTORICAL_ETF_HOLDINGS')


def run_backtest(daily, universe, config, four_hour=None):
    daily = validate_daily(daily)
    universe = universe.copy()
    universe['symbol'] = universe.symbol.astype(str)
    if 'known_at' not in universe:
        raise ValueError('Universe requires known_at; future/current constituents are not historical truth')
    universe['known_at'] = pd.to_datetime(universe.known_at, utc=True)
    start, end = pd.Timestamp(config['start']), pd.Timestamp(config['end'])
    if universe.symbol.duplicated().any():
        raise ValueError('This version requires a single fixed universe snapshot')
    symbols = set(universe.symbol)
    daily = daily[daily.symbol.isin(symbols)].copy()
    if symbols - set(daily.symbol):
        raise ValueError(f'Universe symbols without any data: {symbols - set(daily.symbol)}')
    features = compute_features(daily, config, four_hour)
    days = sorted(features.date.unique())
    holdings, last_prices, pending = {}, {}, {}
    cash = float(config['initial_cash'])
    receivable = 0.
    signal_date = None
    plan_reason = 'NO_PRIOR_SIGNAL'
    nav_rows, trades, orders_log, holdings_log, signals, warnings = [], [], [], [], [], []
    executed_days = [pd.Timestamp(d) for d in days if start <= pd.Timestamp(d) <= end]
    if not executed_days:
        raise ValueError('No observed sessions in requested period')
    first = executed_days[0]
    prior = [pd.Timestamp(d) for d in days if pd.Timestamp(d) < first]
    if not prior:
        raise ValueError('No prior-day data for first order')
    last_pre = prior[-1]
    cutoff = last_pre.tz_localize('Asia/Taipei') + pd.Timedelta(hours=19, minutes=30)
    if (universe.known_at > cutoff.tz_convert('UTC')).any():
        raise ValueError('LOOKAHEAD_UNIVERSE: snapshot not known by initial signal time')
    previous_nav = config['initial_cash']
    for day in [pd.Timestamp(x) for x in days if last_pre <= pd.Timestamp(x) <= end]:
        rows = features[features.date == day].copy()
        bars = rows.set_index('symbol')
        stale = []
        is_live = day >= first
        costs = 0.
        traded_notional = 0.
        if is_live:
            # Corporate actions apply to pre-opening shares, never to newly bought shares.
            for s in list(holdings):
                if s not in bars.index:
                    stale.append(s)
                    continue
                split = float(bars.at[s, 'split'])
                receivable += holdings[s] * float(bars.at[s, 'dividend'])
                holdings[s] *= split
            for s, quantity in sorted(pending.items(), key=lambda x: (x[1] > 0, x[0])):
                # Orders are fixed whole-lot share instructions, even on an ex-right date.
                if s not in bars.index or bars.at[s, 'volume'] <= 0:
                    warnings.append(dict(date=str(day.date()), symbol=s, issue='UNFILLED_MISSING_PRICE_OR_VOLUME'))
                    continue
                if config['execution'] == 'vwap':
                    if 'turnover' not in bars or not np.isfinite(bars.at[s, 'turnover']) or bars.at[s, 'turnover'] <= 0:
                        raise ValueError(f'Missing actual turnover for VWAP: {s} {day}')
                    price = float(bars.at[s, 'turnover']) / float(bars.at[s, 'volume'])
                elif config['execution'] == 'next_open':
                    price = float(bars.at[s, 'open']) * (1 + np.sign(quantity) * config['slippage_bps'] / 10000.)
                else:
                    raise ValueError('Unknown execution model')
                if holdings.get(s, 0.) + quantity < -1e-6:
                    raise ValueError('Short sale requested')
                notional = abs(quantity) * price
                fee = notional * config['commission']
                tax = notional * config['sell_tax'] if quantity < 0 else 0.
                cash -= quantity * price + fee + tax
                costs += fee + tax
                traded_notional += notional
                holdings[s] = holdings.get(s, 0.) + quantity
                if abs(holdings[s]) < 1e-6:
                    del holdings[s]
                trades.append(dict(date=str(day.date()), signal_date=str(signal_date.date()), symbol=s,
                                   trade_symbol=bars.at[s, 'source_symbol'] if 'source_symbol' in bars else s,
                                   shares=quantity, price=price, notional=notional, fee=fee, tax=tax,
                                   slippage_cost=abs(quantity) * abs(price - float(bars.at[s, 'open'])) if config['execution'] == 'next_open' else 0.,
                                   volume_participation=abs(quantity) / float(bars.at[s, 'volume'])))
        last_prices.update(dict(zip(rows.symbol, rows.close)))
        nav = cash + sum(q * last_prices[s] for s, q in holdings.items())
        if is_live:
            checks = rule_check(holdings, cash, last_prices, config, symbols)
            if stale:
                warnings.append(dict(date=str(day.date()), issue='STALE_HELD_PRICES', symbol=';'.join(stale)))
            nav_rows.append(dict(date=str(day.date()), nav=nav, economic_nav=nav + receivable,
                                 cash=cash, cash_ratio=cash / nav, holdings=len(holdings),
                                 dividend_receivable=receivable, costs=costs, traded_notional=traded_notional,
                                 turnover=traded_notional / previous_nav,
                                 violations=';'.join(checks['violations']), active_share_status=checks['active_share_status'],
                                 stale_count=len(stale), executed_plan=plan_reason))
            for s, q in sorted(holdings.items()):
                holdings_log.append(dict(date=str(day.date()), symbol=s, shares=q, close=last_prices[s], weight=q * last_prices[s] / nav))
            previous_nav = nav
        # Do not send impossible sells against missing close rows: freeze and mark the day.
        if any(s not in bars.index for s in holdings):
            pending, plan_reason, ranked = {}, 'MISSING_HELD_QUOTE', score_candidates(rows, config)
        else:
            pending, plan_reason, ranked = make_plan(rows, holdings, cash, nav, config)
        signal_date = day
        if len(ranked):
            for s, row in ranked.iterrows():
                signals.append(dict(date=str(day.date()), symbol=s, score=row['score'], entry_ok=bool(row.entry_ok),
                                    exit=bool(row['exit']), four_hour_available=bool(row.four_hour_available),
                                    four_hour_ok=bool(row.four_hour_ok), plan_reason=plan_reason))
        for s, quantity in pending.items():
            orders_log.append(dict(signal_date=str(day.date()), symbol=s, shares=quantity, signal_nav=nav,
                                   sizing_price=float(bars.at[s, 'close']), reason=plan_reason))
    equity = pd.DataFrame(nav_rows)
    if config['dividend_cash_policy'] == 'end_of_period':
        equity.loc[equity.index[-1], 'nav'] += receivable
        equity.loc[equity.index[-1], 'cash'] += receivable
        equity.loc[equity.index[-1], 'cash_ratio'] = equity.iloc[-1]['cash'] / equity.iloc[-1]['nav']
        equity.loc[equity.index[-1], 'dividend_receivable'] = 0.
        final_checks = rule_check(holdings, cash + receivable, last_prices, config, symbols)
        equity.loc[equity.index[-1], 'violations'] = ';'.join(final_checks['violations'])
        for record in holdings_log:
            if record['date'] == equity.date.iloc[-1]:
                record['weight'] = record['shares'] * record['close'] / equity.iloc[-1]['nav']
    else:
        raise ValueError('Unsupported dividend policy')
    series = np.r_[config['initial_cash'], equity.nav.to_numpy()]
    returns = series[1:] / series[:-1] - 1
    drawdown = series / np.maximum.accumulate(series) - 1
    metrics = dict(total_return=float(series[-1] / series[0] - 1), max_drawdown=float(-drawdown.min()),
                   economic_max_drawdown=float(-(np.r_[config['initial_cash'], equity.economic_nav.to_numpy()] / np.maximum.accumulate(np.r_[config['initial_cash'], equity.economic_nav.to_numpy()]) - 1).min()),
                   annualized_volatility=float(np.std(returns, ddof=1) * np.sqrt(252)),
                   sharpe_zero_rf=float(np.mean(returns) / np.std(returns, ddof=1) * np.sqrt(252)) if np.std(returns, ddof=1) > 0 else None,
                   transaction_costs=float(equity.costs.sum()), turnover_two_way=float(equity.turnover.sum()),
                   slippage_cost=float(sum(t['slippage_cost'] for t in trades)),
                   max_daily_volume_participation=float(max((t['volume_participation'] for t in trades), default=0.)),
                   trades_above_10pct_daily_volume=sum(t['volume_participation'] > .10 for t in trades),
                   trades=len(trades), sessions=len(equity), start=equity.date.iloc[0], end=equity.date.iloc[-1],
                   final_nav=float(series[-1]), raw_rule_breach_days=int((equity.violations != '').sum()),
                   stale_held_price_days=int((equity.stale_count > 0).sum()),
                   infeasible_signal_days=len({r['date'] for r in signals if r['plan_reason'].startswith('INFEASIBLE')}),
                   active_share_status='UNKNOWN_MISSING_HISTORICAL_ETF_HOLDINGS',
                   certification='RESEARCH_ONLY_NOT_CONTEST_CERTIFIED')
    return dict(metrics=metrics, equity=equity, trades=pd.DataFrame(trades), orders=pd.DataFrame(orders_log),
                holdings=pd.DataFrame(holdings_log), signals=pd.DataFrame(signals), warnings=pd.DataFrame(warnings), config=copy.deepcopy(config))


def save_result(result, path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    for key, value in result.items():
        if isinstance(value, pd.DataFrame):
            value.to_csv(path / f'{key}.csv', index=False)
        else:
            (path / f'{key}.json').write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
