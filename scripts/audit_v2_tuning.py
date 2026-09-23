"""Independent audit of the bounded A/B/C tuning study.

All-trial checks reconstruct equity from trades/actions and verify prefix-only
selection. A second row-wise reconstruction checks saved holdings and signal
provenance for replays and selected continuous ledgers. PASS never certifies
source publication vintages or LIVE.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from audit_v2_study import audit_model, check, equal, sha

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = ('A', 'B', 'C')
CUTOFFS = ('2025-06-30', '2025-12-31', '2026-03-31', '2026-06-30')
FIXED = ('start', 'end', 'initial_cash', 'cash_target', 'commission', 'sell_tax',
         'slippage_bps', 'lot_size', 'execution', 'dividend_cash_policy',
         'research_shadow', 'allocation_mode', 'price_buffer', 'min_count',
         'max_count', 'max_weight', 'tsmc_max_weight')


def read_csv(path):
    return pd.read_csv(path, keep_default_na=False, low_memory=False)


def equity_metrics(equity, initial, cutoff=None):
    """Only completed execution sessions contribute to the selection score."""
    eq = equity if cutoff is None else equity[equity.date <= cutoff]
    check(len(eq) > 0, 'Empty selection prefix')
    check(eq.date.is_monotonic_increasing and not eq.date.duplicated().any(), 'Bad equity calendar')
    values = eq[['nav', 'economic_nav', 'cash', 'fees', 'taxes', 'costs', 'turnover']].to_numpy(float)
    check(np.isfinite(values).all(), 'Nonfinite equity values')
    wealth = np.r_[initial, eq.economic_nav.to_numpy(float)]
    check((wealth > 0).all(), 'Nonpositive economic wealth')
    hard = (eq.cash_ratio.ge(.25) | eq.active_cap_breaches.ne('') | eq.overdue_passive_caps.ne(''))
    returns = wealth[1:] / wealth[:-1] - 1
    std = returns.std(ddof=1) if len(returns) > 1 else 0.
    return dict(economic_total_return=float(wealth[-1] / initial - 1),
                economic_max_drawdown=float(-(wealth / np.maximum.accumulate(wealth) - 1).min()),
                economic_annualized_volatility=float(std * np.sqrt(252)),
                economic_sharpe_zero_rf=float(returns.mean() / std * np.sqrt(252)) if std else None,
                hard_breach_days=int(hard.sum()),
                infeasible_executed_days=int(eq.executed_plan.str.startswith('INFEASIBLE').sum()),
                negative_cash_days=int(eq.cash.lt(-1e-6).sum()),
                turnover_two_way=float(eq.turnover.sum()), costs=float(eq.costs.sum()),
                transaction_costs=float(eq.costs.sum()), commission_cost=float(eq.fees.sum()),
                sell_tax_cost=float(eq.taxes.sum()), trade_days=int(eq.traded_notional.gt(0).sum()),
                raw_rule_breach_days=int(eq.violations.ne('').sum()), sessions=len(eq),
                final_economic_nav=float(wealth[-1]))


def selection_key(metrics, candidate_id):
    return (metrics['hard_breach_days'], metrics['infeasible_executed_days'],
            -metrics['economic_total_return'], metrics['economic_max_drawdown'], str(candidate_id))


def select_candidate(equities, initial, cutoff):
    metrics = {cid: equity_metrics(eq, initial, cutoff) for cid, eq in equities.items()}
    valid = {cid: m for cid, m in metrics.items() if m['negative_cash_days'] == 0}
    check(bool(valid), 'No financially valid candidate at ' + str(cutoff))
    return min(valid, key=lambda cid: selection_key(valid[cid], cid)), metrics


def monthly_audit(path, equity, initial):
    saved = read_csv(path)
    key = 'month' if 'month' in saved else 'period'
    value = next(k for k in ('economic_return', 'economic_total_return', 'return_net') if k in saved)
    previous, reconstructed = initial, []
    for month, frame in equity.groupby(pd.to_datetime(equity.date).dt.to_period('M')):
        row = saved[saved[key].astype(str) == str(month)]
        check(len(row) == 1, str(path) + ': missing/duplicate month')
        wealth = np.r_[previous, frame.economic_nav.to_numpy(float)]
        ret = wealth[-1] / wealth[0] - 1
        equal(row.iloc[0][value], ret, str(path) + ': monthly return', atol=1e-10)
        if 'economic_max_drawdown' in row:
            equal(row.iloc[0].economic_max_drawdown,
                  -(wealth / np.maximum.accumulate(wealth) - 1).min(), str(path) + ': monthly MDD', atol=1e-10)
        reconstructed.append(ret)
        previous = wealth[-1]
    check(len(saved) == len(reconstructed), str(path) + ': extraneous months')
    equal(np.prod(1 + np.array(reconstructed)) - 1, previous / initial - 1,
          str(path) + ': compounded monthly return', atol=1e-10)
    return len(reconstructed)


def compare_metrics(saved, computed, label, require=()):
    for field in require:
        check(field in saved, label + ': missing ' + field)
    for field, expected in computed.items():
        if field in saved and expected is not None:
            equal(saved[field], expected, label + ': ' + field, atol=1e-8)


def trial_trade_audit(path, equity, market, config):
    """Cheap all-trial execution audit, without trusting saved scalar counts."""
    trades = read_csv(path)
    if not len(trades):
        check(equity.traded_notional.eq(0).all(), str(path) + ': missing trades')
        return 0
    check(not trades.duplicated(['date', 'symbol']).any(), str(path) + ': duplicate fills')
    check((pd.to_datetime(trades.signal_date) < pd.to_datetime(trades.date)).all(), str(path) + ': nonprior signal')
    quantity = trades.shares.to_numpy(float)
    check(np.allclose(quantity / config['lot_size'], np.round(quantity / config['lot_size']), atol=1e-10, rtol=0),
          str(path) + ': non-board-lot execution')
    check((quantity != 0).all(), str(path) + ': zero-quantity fill')
    orders = read_csv(path.with_name('orders.csv'))
    check(not orders.duplicated(['signal_date', 'symbol']).any(), str(path) + ': duplicate orders')
    aligned = trades.merge(orders[['signal_date', 'symbol', 'shares']], on=['signal_date', 'symbol'],
                           how='left', validate='one_to_one', suffixes=('', '_order'))
    check(np.array_equal(aligned.shares, aligned.shares_order), str(path) + ': execution quantity differs from prior fixed order')
    days = sorted(market.date.unique())
    preceding = dict(zip(days[1:], days[:-1]))
    check(all(trade.signal_date == preceding[trade.date] for trade in trades.itertuples()), str(path) + ': fill does not use previous market session')
    paired = trades.merge(market, on=['date', 'symbol'], how='left', validate='one_to_one', suffixes=('', '_source'))
    denominator = paired.execution_volume_source if 'execution_volume_source' in paired else paired.execution_volume
    price = paired.turnover / denominator
    check(np.isfinite(price).all() and (price > 0).all() and (denominator > 0).all(), str(path) + ': missing VWAP')
    check(np.allclose(trades.price, price, atol=1e-9, rtol=1e-11), str(path) + ': mismatched paired VWAP')
    amount = np.abs(quantity) * price.to_numpy(float)
    for field, expected in [('notional', amount), ('fee', amount * config['commission']),
                            ('tax', amount * config['sell_tax'] * (quantity < 0))]:
        check(np.allclose(trades[field], expected, atol=.005, rtol=1e-11), str(path) + ': incorrect ' + field)
    sums = trades.groupby('date')[['fee', 'tax', 'notional']].sum().reindex(equity.date, fill_value=0)
    for source, target in [('fee', 'fees'), ('tax', 'taxes'), ('notional', 'traded_notional')]:
        check(np.allclose(sums[source], equity[target], atol=.005, rtol=1e-11), str(path) + ': daily ' + target)
    for date, group in trades.groupby('date'):
        check(not (group.shares.gt(0).any() and group.shares.lt(0).any()), str(path) + ': mixed sell/buy day ' + date)
    return len(trades)


def ledger_inputs(daily, calendar, universe):
    columns = sorted(universe.symbol.astype(str))
    panel = daily[daily.symbol.isin(columns)].copy()
    close = panel.pivot(index='date', columns='symbol', values='close').sort_index().ffill().reindex(index=calendar, columns=columns)
    split = panel.pivot(index='date', columns='symbol', values='split').reindex(index=calendar, columns=columns).fillna(1.)
    dividend = panel.pivot(index='date', columns='symbol', values='dividend').reindex(index=calendar, columns=columns).fillna(0.)
    return dict(dates=[str(x.date()) for x in calendar], symbols=columns,
                close=close.to_numpy(float), split=split.to_numpy(float), dividend=dividend.to_numpy(float))


def trial_ledger_audit(path, equity, inputs, config):
    """Rebuild all-trial positions mathematically, independently of engine loops.

    q[t] = cumulative_split[t] * sum_{u<=t}(trade[u]/cumulative_split[u]).
    Cash entitlements use yesterday's shares, before today's split and orders.
    This also checks the actual hard-constraint fields used to rank candidates.
    """
    trades = read_csv(path)
    dates, symbols = inputs['dates'], inputs['symbols']
    check(set(trades.symbol).issubset(symbols), str(path) + ': outside historical research universe')
    if len(trades):
        changes = trades.pivot(index='date', columns='symbol', values='shares').reindex(index=dates, columns=symbols).fillna(0.).to_numpy(float)
    else:
        changes = np.zeros((len(dates), len(symbols)))
    cumulative = np.cumprod(inputs['split'], axis=0)
    shares = cumulative * np.cumsum(changes / cumulative, axis=0)
    shares[np.abs(shares) < 1e-6] = 0.
    check((shares >= -1e-6).all(), str(path) + ': reconstructed short position')
    previous_shares = np.vstack([np.zeros(len(symbols)), shares[:-1]])
    receivable = np.cumsum((previous_shares * inputs['dividend']).sum(axis=1))
    flows = trades.assign(cash_flow=-trades.shares * trades.price-trades.fee-trades.tax).groupby('date').cash_flow.sum()
    cash = config['initial_cash'] + flows.reindex(dates, fill_value=0.).to_numpy(float).cumsum()
    market_value = np.where(shares != 0, shares * inputs['close'], 0.)
    check(np.isfinite(market_value).all(), str(path) + ': holding without mark')
    nav_before_distribution = cash + market_value.sum(axis=1)
    economic = nav_before_distribution + receivable
    caps = np.array([config['tsmc_max_weight'] if s.split('.')[0] == '2330' else config['max_weight'] for s in symbols])
    breaches = market_value / nav_before_distribution[:, None] > caps[None, :] + 1e-10
    active, passive = breaches & (changes > 0), breaches & ~(changes > 0)
    age = np.zeros(len(symbols), dtype=int)
    overdue = np.zeros_like(breaches)
    for i in range(len(dates)):
        age = np.where(breaches[i], age + 1, 0)
        overdue[i] = passive[i] & (age > 5)
    for field, expected in [('active_cap_breaches', active), ('passive_cap_breaches', passive), ('overdue_passive_caps', overdue)]:
        for i, cell in enumerate(equity[field]):
            observed = set(str(cell).split(';')) - {''}
            check(observed == set(np.array(symbols)[expected[i]]), str(path) + ': incorrect ' + field + ' on ' + dates[i])
    cash[-1] += receivable[-1]
    nav = nav_before_distribution.copy()
    nav[-1] += receivable[-1]
    receivable[-1] = 0.
    max_error = 0.
    for field, expected in [('nav', nav), ('economic_nav', economic), ('cash', cash),
                            ('dividend_receivable', receivable), ('cash_ratio', cash/nav), ('holdings', (shares > 0).sum(axis=1))]:
        actual = equity[field].to_numpy(float)
        max_error = max(max_error, float(np.max(np.abs(actual - expected))))
        check(np.allclose(actual, expected, atol=.005, rtol=1e-11), str(path) + ': independently reconstructed ' + field + ' mismatch')
    raw_caps = market_value / nav[:, None] > caps[None, :] + 1e-10
    holding_count = (shares > 0).sum(axis=1)
    for i, cell in enumerate(equity.violations):
        expected = {'WEIGHT_CAP:' + symbol for symbol in np.array(symbols)[raw_caps[i]]}
        if not config['min_count'] <= holding_count[i] <= config['max_count']:
            expected.add('HOLDING_COUNT')
        if cash[i] < -1e-6:
            expected.add('NEGATIVE_CASH')
        if nav[i] <= 0 or cash[i]/nav[i] >= .25:
            expected.add('CASH_GE_25_PERCENT')
        check(set(str(cell).split(';')) - {''} == expected, str(path) + ': raw rule diagnostic mismatch on ' + dates[i])
    if '2888.TW' in symbols:
        check(not (shares[np.array(dates) >= '2025-07-24', symbols.index('2888.TW')] > 1e-6).any(), str(path) + ': unsupported two-asset merger held')
    return max_error


def exact_replay(folder, reference):
    checked = {}
    for name in ('equity', 'trades', 'orders', 'holdings'):
        before, after = read_csv(reference / f'{name}.csv'), read_csv(folder / f'{name}.csv')
        check(set(before.columns).issubset(after.columns), 'Replay dropped original columns: ' + name)
        pd.testing.assert_frame_equal(after[before.columns], before, check_exact=True, check_dtype=False)
        checked[name] = len(before)
    return checked


def nondominated(rows):
    """Pareto diagnostic, independent of the lexicographic primary rule."""
    ids = list(rows)
    points = {cid: np.array(selection_key(rows[cid], cid)[:-1], float) for cid in ids}
    return sorted(cid for cid in ids if not any(
        np.all(points[other] <= points[cid]) and np.any(points[other] < points[cid])
        for other in ids if other != cid))


def comparison_audit(output, manifest, initial):
    """Recompute both reporting scopes from each actual continuous ledger."""
    comparison = read_csv(output / 'comparison.csv')
    monthly = read_csv(output / 'monthly_comparison.csv')
    curves = read_csv(output / 'nav_comparison.csv')
    check(len(comparison) == 18, 'Expected three roles x three strategies x two scopes')
    monthly_count, curve_roundtrip_error = 0, 0.
    for strategy in STRATEGIES:
        for role, key in [('INCUMBENT', 'baseline_replay'), ('EX_POST_BEST', 'ex_post_best'), ('WALK_FORWARD', 'walk_forward')]:
            eq = read_csv(output / manifest[key][strategy] / 'equity.csv')
            curve_name = strategy + '_' + role
            check(curves.date.tolist() == eq.date.tolist(), curve_name + ': plotted calendar differs')
            plotted, expected = curves[curve_name].to_numpy(float), eq.economic_nav.to_numpy(float)
            error = np.abs(plotted - expected)
            # Incumbents pass through CSV -> float -> CSV in the report writer;
            # allow only floating-point representation noise, not new returns.
            check((error <= 4 * np.spacing(np.maximum(np.abs(expected), 1.))).all(), curve_name + ': plotted NAV differs')
            curve_roundtrip_error = max(curve_roundtrip_error, float(error.max()))
            for scope in ('FULL_DEVELOPMENT', 'AFTER_SELECTION_START'):
                if scope == 'FULL_DEVELOPMENT':
                    part, starting_nav = eq, initial
                else:
                    part = eq[eq.date > CUTOFFS[0]]
                    starting_nav = float(eq.loc[eq.date <= CUTOFFS[0], 'economic_nav'].iloc[-1])
                row = comparison[(comparison.strategy == strategy) & (comparison.role == role) & (comparison.scope == scope)]
                check(len(row) == 1, 'Missing/duplicate comparison row')
                computed = equity_metrics(part, starting_nav)
                compare_metrics(row.iloc[0], computed, curve_name + ':' + scope,
                                require=('economic_total_return', 'economic_max_drawdown', 'hard_breach_days', 'costs'))
                equal(row.iloc[0].annualized_volatility, computed['economic_annualized_volatility'], curve_name + ': economic volatility', atol=1e-10)
                equal(row.iloc[0].sharpe_zero_rf, computed['economic_sharpe_zero_rf'] or 0., curve_name + ': economic Sharpe', atol=1e-10)
            previous, returns = initial, []
            for month, group in eq.groupby(pd.to_datetime(eq.date).dt.to_period('M')):
                row = monthly[(monthly.strategy == strategy) & (monthly.role == role) & (monthly.month == str(month))]
                check(len(row) == 1, 'Missing/duplicate comparison month')
                computed = equity_metrics(group, previous)
                compare_metrics(row.iloc[0], computed, curve_name + ':' + str(month))
                previous = group.economic_nav.iloc[-1]
                returns.append(computed['economic_total_return'])
                monthly_count += 1
            equal(np.prod(1 + np.array(returns)), previous / initial, curve_name + ': monthly compounded comparison', atol=1e-10)
    check(len(monthly) == monthly_count, 'Extraneous monthly comparison rows')
    return dict(comparison_rows=len(comparison), monthly_rows=monthly_count, nav_rows=len(curves),
                max_curve_csv_roundtrip_error=curve_roundtrip_error)


def audit(output):
    manifest = json.loads((output / 'tuning_manifest.json').read_text())
    check(manifest.get('outputs_complete') is True, 'Tuning study incomplete')
    check(manifest.get('historical_period_is_development') is True, 'Historical period incorrectly treated as unseen')
    config = manifest['config']
    check(config['research_shadow'] is True and config['execution'] == 'vwap', 'Changed execution/shadow contract')
    check(config['allocation_mode'] == 'local' and config['cash_target'] == 0, 'Changed shared allocation contract')
    for relative, expected in manifest['hashes'].items():
        check(sha(ROOT / relative) == expected, 'Source/input changed: ' + relative)
        check(sha(output / 'input_snapshot' / relative) == expected, 'Frozen snapshot mismatch: ' + relative)
    daily = pd.read_csv(output / 'input_snapshot/data/v2/market_daily.csv', low_memory=False)
    daily['date'] = pd.to_datetime(daily.date)
    check(not daily.duplicated(['date', 'symbol']).any(), 'Duplicate canonical daily bars')
    calendar = sorted(pd.Timestamp(x) for x in daily.date.unique()
                      if pd.Timestamp(config['start']) <= pd.Timestamp(x) <= pd.Timestamp(config['end']))
    expected_dates = [str(x.date()) for x in calendar]
    market = daily[['date', 'symbol', 'turnover', 'execution_volume']].copy()
    market['date'] = market.date.dt.strftime('%Y-%m-%d')
    universe = read_csv(output / 'input_snapshot/data/extended/processed/universe_20241231.csv')
    vector_inputs = ledger_inputs(daily, calendar, universe)
    initial = config['initial_cash']
    study = json.loads((output / 'input_snapshot/config/v2_tuning_study.json').read_text())
    check(study['cutoffs'] == list(CUTOFFS) and study['trial_budget'] == 192, 'Changed predeclared study size/cutoffs')
    check(study.get('historical_period_is_development') is True and study.get('unseen_test') is False,
          'Study mislabels the historical sample')
    candidates = {str(row['candidate_id']): row['params'] for row in study['candidates']}
    check(len(candidates) == 64, 'Duplicate/missing predefined candidates')
    core_params = [{k: v for k, v in p.items() if not k.startswith('sector_') and k != 'c_alpha'}
                   for p in candidates.values()]
    check(len({json.dumps(p, sort_keys=True) for p in core_params}) == 64, 'Common core search has duplicate settings')
    summaries = read_csv(output / 'trial_summary.csv')
    check(not summaries.duplicated(['strategy', 'candidate_id']).any(), 'Duplicate trial summary')
    equities, trials, failures = {s: {} for s in STRATEGIES}, {}, []
    declared = set()
    for trial in manifest['trials']:
        strategy, cid = trial['strategy'], str(trial['candidate_id'])
        check(strategy in STRATEGIES and (strategy, cid) not in declared, 'Invalid/duplicate trial identity')
        declared.add((strategy, cid))
        if trial.get('status', 'COMPLETE') != 'COMPLETE':
            check(bool(trial.get('error')), 'Failed trial without reason')
            failures.append({'strategy': strategy, 'candidate_id': cid, 'error': trial['error']})
            continue
        folder = output / trial['path']
        settings = json.loads((folder / 'config.json').read_text())
        for field in FIXED:
            check(settings[field] == config[field], str(folder) + ': fixed field changed: ' + field)
        check('tuning_params' in settings, str(folder) + ': missing concrete tuning parameters')
        check(cid in candidates and settings['tuning_params'] == candidates[cid], str(folder) + ': differs from predeclared candidate')
        params = candidates[cid]
        for field in ('target_count', 'replacement_margin', 'max_replacements_per_day', 'volatility_spike_ratio',
                      'one_day_chase_return', 'volume_low', 'volume_high', 'four_hour_mode'):
            check(settings[field] == params[field], str(folder) + ': inactive/mismatched search field ' + field)
        check(settings['feature_presets'] == {k: params[k] for k in ('returns', 'ema', 'macd')}, str(folder) + ': feature preset mismatch')
        check(list(settings['score_weights'].values()) == study['score_profiles'][params['score_profile']], str(folder) + ': score profile mismatch')
        eq = read_csv(folder / 'equity.csv')
        check(eq.date.tolist() == expected_dates, str(folder) + ': incomplete trial calendar')
        check(eq.submission_status.eq('BLOCK_SUBMISSION').all(), str(folder) + ': false live certification')
        metrics = equity_metrics(eq, initial)
        row = summaries[(summaries.strategy == strategy) & (summaries.candidate_id.astype(str) == cid)]
        check(len(row) == 1, str(folder) + ': missing summary')
        required = ('economic_total_return', 'economic_max_drawdown', 'hard_breach_days',
                    'infeasible_executed_days', 'negative_cash_days', 'turnover_two_way',
                    'costs', 'trade_days', 'raw_rule_breach_days')
        compare_metrics(row.iloc[0], metrics, str(folder) + ': summary', require=required)
        saved = json.loads((folder / 'metrics.json').read_text())
        compare_metrics(saved, metrics, str(folder) + ': metrics')
        count = trial_trade_audit(folder / 'trades.csv', eq, market, config)
        ledger_error = trial_ledger_audit(folder / 'trades.csv', eq, vector_inputs, config)
        equal(row.iloc[0]['trades'], count, str(folder) + ': summary trade count', atol=0)
        equal(saved['trades'], count, str(folder) + ': metric trade count', atol=0)
        months = monthly_audit(folder / 'monthly.csv', eq, initial)
        equities[strategy][cid] = eq
        trials[f'{strategy}/{cid}'] = {'months': months, 'max_ledger_error': ledger_error, **metrics}
    check(all(sum(s == strategy for s, _ in declared) == 64 for strategy in STRATEGIES), 'Expected 64 declared candidates per strategy')
    summary_ids = set(zip(summaries.strategy, summaries.candidate_id.astype(str)))
    check(summary_ids == declared - {(x['strategy'], x['candidate_id']) for x in failures}, 'Failure contamination/missing trial summaries')
    check(not failures, 'Incomplete predeclared search: missing trial prefixes cannot be silently excluded at historical cutoffs')
    neutral_control_ids = []
    for cid, params in candidates.items():
        if params['c_alpha'] == 0:
            for table in ('equity', 'trades', 'orders'):
                b = read_csv(output / 'trials/B' / cid / f'{table}.csv')
                c = read_csv(output / 'trials/C' / cid / f'{table}.csv')
                pd.testing.assert_frame_equal(c, b, check_exact=True, check_dtype=False)
            neutral_control_ids.append(cid)
    schedules = read_csv(output / 'schedule.csv')
    check(len(schedules) == len(STRATEGIES) * len(CUTOFFS), 'Expected four schedule choices per strategy')
    prefix_scores = read_csv(output / 'selection_scores.csv')
    check(len(prefix_scores) == 192 * len(CUTOFFS), 'Missing prefix candidate scores')
    check(not prefix_scores.duplicated(['strategy', 'candidate_id', 'selection_cutoff']).any(), 'Duplicate candidate-prefix scores')
    prefix_scores = prefix_scores.set_index(['strategy', 'candidate_id', 'selection_cutoff'])
    selections, full_audits, replays, pareto = {}, {}, {}, {}
    whole_calendar = sorted(pd.Timestamp(x) for x in daily.date.unique())
    next_day = dict(zip(whole_calendar[:-1], whole_calendar[1:]))
    for strategy in STRATEGIES:
        selected = []
        for cutoff in CUTOFFS:
            winner, scored = select_candidate(equities[strategy], initial, cutoff)
            for cid, score in scored.items():
                key = (strategy, cid, cutoff)
                check(key in prefix_scores.index, 'Missing historical candidate score')
                compare_metrics(prefix_scores.loc[key], score, '/'.join(key),
                                require=('economic_total_return', 'economic_max_drawdown', 'hard_breach_days',
                                         'infeasible_executed_days', 'negative_cash_days'))
            row = schedules[(schedules.strategy == strategy) & (schedules.selection_cutoff == cutoff)]
            check(len(row) == 1, strategy + ': missing/duplicate cutoff')
            row = row.iloc[0]
            check(str(row.candidate_id) == winner, strategy + ': wrong prefix-only selection at ' + cutoff)
            check(row.effective_signal_date == cutoff, strategy + ': wrong parameter switch signal date')
            check(pd.Timestamp(row.effective_trade_date) == next_day[pd.Timestamp(cutoff)], strategy + ': switch does not use next real session')
            selected.append({'cutoff': cutoff, 'candidate_id': winner, 'metrics': scored[winner]})
        selections[strategy] = selected
        full = {cid: equity_metrics(eq, initial) for cid, eq in equities[strategy].items()
                if equity_metrics(eq, initial)['negative_cash_days'] == 0}
        pareto[strategy] = {'EX_POST_ONLY': True, 'candidate_ids': nondominated(full),
                            'primary_best': min(full, key=lambda cid: selection_key(full[cid], cid)),
                            'return_best': min(full, key=lambda cid: (-full[cid]['economic_total_return'], full[cid]['economic_max_drawdown'], cid))}
        check(manifest['ex_post_winners'][strategy] == pareto[strategy]['primary_best'], strategy + ': incorrect ex-post primary winner')
        baseline_path = output / manifest['baseline_replay'][strategy]
        reference = ROOT / manifest.get('baseline_reference', 'outputs/backtest_v2_2025_to_now') / strategy
        replays[strategy] = exact_replay(baseline_path, reference)
        _, result = audit_model(baseline_path.parent, baseline_path.name, daily, calendar, config)
        full_audits['baseline/' + strategy] = result
        for kind in ('walk_forward', 'ex_post_best'):
            path = output / manifest[kind][strategy]
            check(path.name == strategy, 'Full ledger folder must preserve strategy identity for local-allocation checks')
            _, result = audit_model(path.parent, path.name, daily, calendar, config)
            full_audits[kind + '/' + strategy] = result
            if kind == 'walk_forward':
                snapshots = read_csv(path / 'snapshots.csv')
                incumbent_eq = read_csv(baseline_path / 'equity.csv')
                walk_eq = read_csv(path / 'equity.csv')
                pd.testing.assert_frame_equal(walk_eq[walk_eq.date <= CUTOFFS[0]][incumbent_eq.columns],
                                              incumbent_eq[incumbent_eq.date <= CUTOFFS[0]],
                                              check_exact=True, check_dtype=False)
                for _, snapshot in snapshots.iterrows():
                    expected = manifest.get('baseline_candidate_id', 'p000')
                    for choice in selected:
                        if snapshot.date >= choice['cutoff']:
                            expected = choice['candidate_id']
                    meta = json.loads(snapshot.metadata)
                    actual = snapshot.get('candidate_id', meta.get('tuning_candidate_id'))
                    check(str(actual) == expected, strategy + ': snapshot uses wrong schedule on ' + snapshot.date)
                    cfg = json.loads((output / 'trials' / strategy / expected / 'config.json').read_text())
                    fingerprint = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()
                    check(meta.get('tuning_config_sha256') == fingerprint, strategy + ': snapshot configuration fingerprint mismatch')
            monthly = path / 'monthly.csv'
            if monthly.exists():
                monthly_audit(monthly, read_csv(path / 'equity.csv'), initial)
    report_tables = comparison_audit(output, manifest, initial)
    return dict(status='PASS', scope='SAVED_ARITHMETIC_PREFIX_SELECTION_AND_SELECTED_CONTINUOUS_LEDGER_ONLY',
                verified_at=datetime.now(timezone.utc).isoformat(), auditor_sha256=sha(__file__),
                trial_count=len(declared), completed_trial_count=len(trials), failed_trials=failures,
                all_trial_max_ledger_error=max(t['max_ledger_error'] for t in trials.values()),
                zero_overnight_coefficient_B_C_exact=neutral_control_ids,
                input_and_code_hashes=len(manifest['hashes']), baseline_exact_replays=replays,
                selections=selections, ex_post_diagnostics=pareto, full_ledger_audits=full_audits,
                report_tables=report_tables,
                limitations=['All historical dates were previously inspected; retrospective walk-forward is not pristine unseen OOS.',
                             'All-trial cash, implied positions and selection constraints are reconstructed; only selected/replay saved holding tables and signal provenance receive the second row-wise audit.',
                             'Historical publication vintages, official whitelist and Active Share remain unverified.',
                             'All results remain research shadow and blocked for submission.',
                             'Capacity diagnostics are not contest fill restrictions and do not certify live investability.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='outputs/backtest_v2_tuning')
    args = parser.parse_args()
    output = ROOT / args.output
    try:
        result = audit(output)
    except Exception as error:
        result = {'status': 'FAIL', 'error': str(error), 'auditor_sha256': sha(__file__)}
        if output.exists():
            payload = json.dumps(result, indent=2) + '\n'
            (output / 'tuning_audit.json').write_text(payload)
            (output / 'audit.json').write_text(payload)
        raise
    payload = json.dumps(result, indent=2) + '\n'
    (output / 'tuning_audit.json').write_text(payload)
    (output / 'audit.json').write_text(payload)
    print(json.dumps(result, indent=2))
