"""Independent eligibility and accounting checks for the second tuning study.

Zero measured hard breaches is a research result, never certification of the
official whitelist, Active Share, submission history or unspecified penalties.
The completed-study artifact audit is attached once its frozen schema exists.
"""
from __future__ import annotations

import argparse
import inspect
import json
import sys
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
# Keep the frozen first-round auditor importable both as a script and package.
for import_root in (ROOT, ROOT / 'scripts'):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))
from scripts.audit_v2_study import audit_model as legacy_audit_model, check, equal, sha
from scripts.audit_v2_tuning import (compare_metrics, equity_metrics, exact_replay, ledger_inputs,
                             monthly_audit, read_csv, trial_ledger_audit,
                             trial_trade_audit as legacy_trial_trade_audit)

STRATEGIES = ('A', 'B', 'C')
TRACKS = ('historical_pit', 'official_ex_post')
MIXED_REASON = 'BUY_CASH_CAP_CORRECTION_PREFUNDED_MIXED'


def prefunded_batch_audit(orders, config, starting_cash, starting_shares=None):
    """Different-stock mixed orders must be funded without today's sale proceeds.

    PDF section VI(4) explicitly gives a same-day buy2330/sell2454 example;
    D-Plan section5 also permits same-day funding_for.  The second study uses
    the stricter pre-funded subset of those transactions.  Both order-side
    budgets and pre-existing sale inventory are checked before actual fills.
    """
    check(not orders.symbol.duplicated().any(), 'Same-day duplicate symbol/day trade')
    buys, sells = orders[orders.shares.gt(0)], orders[orders.shares.lt(0)]
    if not len(buys) or not len(sells):
        return False
    check(set(buys.symbol).isdisjoint(sells.symbol), 'Same-security day trading')
    check(orders.reason.eq(MIXED_REASON).all(), 'Mixed orders lack explicit pre-funded reason')
    budget = (buys.shares * buys.sizing_price * config['price_buffer'] * (1 + config['commission'])).sum()
    check(np.isfinite(budget) and budget <= starting_cash + .005,
          f'Mixed buys depend on same-day sale proceeds: {budget} > {starting_cash}')
    if starting_shares is not None:
        for row in sells.itertuples():
            check(-row.shares <= starting_shares.get(row.symbol, 0.) + 1e-6,
                  'Mixed sell exceeds existing inventory: ' + row.symbol)
    return True


def prefunded_fill_audit(fills, order_lookup, config, cash, shares):
    """Row-wise ledger adapter validates the complete planned batch on mixed days."""
    if not len(fills) or not (fills.shares.gt(0).any() and fills.shares.lt(0).any()):
        return
    signals = fills.signal_date.unique()
    check(len(signals) == 1, 'Mixed fills have different decision dates')
    batch = order_lookup.xs(signals[0], level='signal_date').reset_index()
    prefunded_batch_audit(batch, config, cash, shares)
    buys = fills[fills.shares.gt(0)]
    cost = (buys.shares * buys.price + buys.fee + buys.tax).sum()
    check(cost <= cash + .005, 'Actual mixed buys consumed same-day sale proceeds')


def _isolated_auditor(function, replacements, additions=None):
    """Reuse frozen accounting code with only reviewed rule interpretation changes."""
    source = inspect.getsource(function)
    for before, after in replacements:
        check(source.count(before) == 1, 'Frozen auditor source changed; explicit review required')
        source = source.replace(before, after)
    namespace = {**function.__globals__, **(additions or {})}
    exec(compile(source, '<second-round-independent-auditor-adapter>', 'exec'), namespace)
    return namespace[function.__name__]


audit_model = _isolated_auditor(legacy_audit_model, [
    ("check(not (todays.shares.gt(0).any() and todays.shares.lt(0).any()), name + ' ' + date + ': simultaneous funding sells/buys')",
     'prefunded_fill_audit(todays, order_lookup, settings, cash, q)'),
    ("order.reason != 'BUY_CASH_CAP_CORRECTION'",
     "order.reason not in ('BUY_CASH_CAP_CORRECTION', MIXED_REASON)")],
    dict(prefunded_fill_audit=prefunded_fill_audit, MIXED_REASON=MIXED_REASON))
_trade_arithmetic_audit = _isolated_auditor(legacy_trial_trade_audit, [
    ("check(not (group.shares.gt(0).any() and group.shares.lt(0).any()), str(path) + ': mixed sell/buy day ' + date)",
     'pass  # Complete fixed-order budgets are checked by trial_trade_audit below.')])


def trial_trade_audit(path, equity, market, config):
    trades = _trade_arithmetic_audit(path, equity, market, config)
    orders = read_csv(path.with_name('orders.csv'))
    fills = read_csv(path)
    cash_on_signal = equity.set_index('date').cash.astype(float).to_dict()
    days = sorted(market.date.unique())
    following = dict(zip(days[:-1], days[1:]))
    active_days = set(equity.date)
    for signal, batch in orders.groupby('signal_date'):
        day = following.get(signal)
        if day not in active_days:
            continue  # End-of-period distributions cannot fund an unexecuted plan.
        cash = cash_on_signal.get(signal, config['initial_cash'])
        mixed = prefunded_batch_audit(batch, config, cash)
        if mixed:
            purchases = fills[fills.date.eq(day) & fills.shares.gt(0)]
            check((purchases.notional + purchases.fee + purchases.tax).sum() <= cash + .005,
                  str(path) + ': mixed purchase cost consumes sale proceeds')
    return trades


def measured_hard_mask(equity, config):
    """Do not overlook count/whitelist failures hidden by the old cash-only gate."""
    required = {'cash', 'cash_ratio', 'holdings', 'violations',
                'active_cap_breaches', 'overdue_passive_caps'}
    if required - set(equity):
        raise AssertionError('Missing hard-rule evidence: ' + str(sorted(required - set(equity))))
    raw = equity.violations.fillna('').astype(str)
    non_cap_raw = raw.map(lambda cell: any(token and not token.startswith('WEIGHT_CAP:')
                                          for token in cell.split(';')))
    return (equity.cash.astype(float).lt(-1e-6)
            | equity.cash_ratio.astype(float).ge(.25)
            | ~equity.holdings.astype(int).between(config['min_count'], config['max_count'])
            | non_cap_raw
            | equity.active_cap_breaches.fillna('').astype(str).ne('')
            | equity.overdue_passive_caps.fillna('').astype(str).ne(''))


def select_zero_measured(candidates, config, cutoff=None):
    """Return no winner when every candidate breaches a measured hard rule.

    Later failures cannot disqualify an otherwise valid historical prefix.
    Official certification is deliberately separate from research eligibility.
    """
    scored = {}
    for candidate_id, frame in candidates.items():
        eq = frame if cutoff is None else frame[frame.date <= cutoff]
        if not len(eq):
            raise AssertionError('Candidate has no selection-prefix observations')
        values = eq[['nav', 'economic_nav', 'cash']].to_numpy(float)
        if not np.isfinite(values).all() or (eq.economic_nav.astype(float) <= 0).any():
            scored[candidate_id] = {'eligible': False, 'reason': 'INVALID_ACCOUNTING'}
            continue
        hard = measured_hard_mask(eq, config)
        wealth = np.r_[config['initial_cash'], eq.economic_nav.to_numpy(float)]
        scored[candidate_id] = dict(
            eligible=not hard.any(), hard_breach_days=int(hard.sum()),
            economic_total_return=float(wealth[-1] / wealth[0] - 1),
            economic_max_drawdown=float(-(wealth / np.maximum.accumulate(wealth) - 1).min()))
    eligible = [candidate_id for candidate_id, row in scored.items() if row['eligible']]
    winner = min(eligible, key=lambda cid: (-scored[cid]['economic_total_return'],
                                          scored[cid]['economic_max_drawdown'], str(cid))) if eligible else None
    return dict(status='ZERO_MEASURED_HARD_BREACH_WINNER' if winner is not None else 'NO_ELIGIBLE_WINNER',
                candidate_id=winner, scores=scored,
                formal_compliance='UNKNOWN', submission_status='BLOCK_SUBMISSION')


def posttrade_price_envelope(holdings, cash, prices, orders, config):
    """Closed-form independent oracle for fixed shares and independent price bands.

    This is conditional on the stated raw-price bands, all fills and no new
    unmodeled corporate actions. It makes no unconditional market guarantee.
    """
    lower, upper = config.get('price_lower_buffer', .9), config['price_buffer']
    fee, tax = config['commission'], config['sell_tax']
    positions = {s: holdings.get(s, 0.) + orders.get(s, 0.) for s in set(holdings) | set(orders)}
    if any(q < -1e-6 for q in positions.values()):
        raise ValueError('Orders create a short position')
    positions = {s: q for s, q in positions.items() if q > 1e-6}
    low_cash = high_cash = float(cash)
    for symbol, quantity in orders.items():
        p = prices[symbol]
        if quantity > 0:
            low_cash -= quantity * p * upper * (1 + fee)
            high_cash -= quantity * p * lower * (1 + fee)
        elif quantity < 0:
            low_cash += abs(quantity) * p * lower * (1 - fee - tax)
            high_cash += abs(quantity) * p * upper * (1 - fee - tax)
    low_values = {s: q * prices[s] * lower for s, q in positions.items()}
    high_values = {s: q * prices[s] * upper for s, q in positions.items()}
    denominator = high_cash + sum(low_values.values())
    cash_ratio_max = high_cash / denominator if denominator > 0 else np.inf
    max_weights = {s: high_values[s] / (low_cash + high_values[s] + sum(v for other, v in low_values.items() if other != s))
                   for s in positions}
    return dict(cash_min=low_cash, cash_max=high_cash, cash_ratio_max=cash_ratio_max,
                max_weights=max_weights, posttrade_shares=positions)


def enumerate_price_corners(holdings, cash, prices, orders, config):
    """Slow oracle used only by small adversarial tests; never by the planner."""
    names = sorted(set(holdings) | set(orders))
    traded = sorted(s for s, q in orders.items() if q)
    if len(names) + len(traded) > 12:
        raise ValueError('Corner enumeration is restricted to bounded test fixtures')
    band = (config.get('price_lower_buffer', .9), config['price_buffer'])
    positions = {s: holdings.get(s, 0.) + orders.get(s, 0.) for s in names}
    results = []
    for execution in product(band, repeat=len(traded)):
        settled_cash = float(cash)
        for symbol, multiple in zip(traded, execution):
            quantity = orders[symbol]
            amount = abs(quantity) * prices[symbol] * multiple
            settled_cash -= quantity * prices[symbol] * multiple
            settled_cash -= amount * (config['commission'] + (config['sell_tax'] if quantity < 0 else 0.))
        for marks in product(band, repeat=len(names)):
            values = {s: positions[s] * prices[s] * multiple for s, multiple in zip(names, marks)}
            nav = settled_cash + sum(values.values())
            results.append(dict(cash=settled_cash, cash_ratio=settled_cash/nav,
                                weights={s: v/nav for s, v in values.items() if positions[s] > 0}))
    return results


def second_metrics(equity, config):
    result = equity_metrics(equity, config['initial_cash'])
    result.update(measured_hard_breach_days=int(measured_hard_mask(equity, config).sum()),
                  infeasible_executed_days=int(equity.executed_plan.astype(str).str.contains('INFEASIBLE', regex=False).sum()),
                  cash_breach_days=int(equity.cash_ratio.astype(float).ge(.25).sum()),
                  holding_count_breach_days=int((~equity.holdings.astype(int).between(config['min_count'], config['max_count'])).sum()),
                  whitelist_breach_days=int(equity.violations.astype(str).str.contains('NON_WHITELIST', regex=False).sum()),
                  active_cap_breach_days=int(equity.active_cap_breaches.ne('').sum()),
                  overdue_passive_cap_days=int(equity.overdue_passive_caps.ne('').sum()),
                  cash_ratio_max=float(equity.cash_ratio.max()), cash_ratio_min=float(equity.cash_ratio.min()),
                  average_stock_exposure=float((1 - equity.cash_ratio).mean()),
                  holdings_min=int(equity.holdings.min()), holdings_max=int(equity.holdings.max()))
    return result


def audit_report_tables(output, frames, initial):
    comparison = read_csv(output / 'comparison.csv').set_index('model')
    monthly = read_csv(output / 'monthly_comparison.csv')
    curves = read_csv(output / 'nav_comparison.csv')
    check(set(comparison.index) == set(frames) and not comparison.index.duplicated().any(), 'Missing/extraneous final comparisons')
    check(set(curves.columns) == {'date', *frames}, 'Missing/extraneous plotted models')
    count, maximum_error = 0, 0.
    for name, eq in frames.items():
        config = json.loads((output / 'final' / name / 'config.json').read_text())
        expected = second_metrics(eq, config)
        compare_metrics(comparison.loc[name], expected, name + ': final comparison',
                        require=('economic_total_return', 'economic_max_drawdown', 'measured_hard_breach_days'))
        dates = [str((pd.Timestamp(config['start']) - pd.Timedelta(days=1)).date()), *eq.date.tolist()]
        check(curves.date.tolist() == dates, name + ': missing initial/shared NAV calendar')
        wealth = np.r_[initial, eq.economic_nav.to_numpy(float)]
        error = np.abs(curves[name].to_numpy(float) - wealth)
        check((error <= 4 * np.spacing(np.maximum(np.abs(wealth), 1.))).all(), name + ': plotted NAV mismatch')
        maximum_error = max(maximum_error, float(error.max()))
        previous, returns = initial, []
        for month, group in eq.groupby(pd.to_datetime(eq.date).dt.to_period('M')):
            rows = monthly[(monthly.model == name) & (monthly.month == str(month))]
            check(len(rows) == 1, name + ': missing/duplicate month')
            values = np.r_[previous, group.economic_nav.to_numpy(float)]
            ret = values[-1] / values[0] - 1
            equal(rows.iloc[0].economic_total_return, ret, name + ': monthly return', atol=1e-10)
            equal(rows.iloc[0].economic_max_drawdown, -(values / np.maximum.accumulate(values) - 1).min(), name + ': monthly MDD', atol=1e-10)
            previous = values[-1]
            returns.append(ret)
            count += 1
        equal(np.prod(1 + np.array(returns)), wealth[-1] / initial, name + ': compounded month returns', atol=1e-10)
    check(len(monthly) == count, 'Extraneous final monthly rows')
    return dict(comparison_rows=len(comparison), monthly_rows=count, curve_rows=len(curves),
                max_curve_csv_roundtrip_error=maximum_error)


def audit_track(output):
    manifest = json.loads((output / 'manifest.json').read_text())
    check(manifest.get('outputs_complete') is True and manifest.get('completed_at'), 'Track is incomplete')
    track = manifest['track']
    check(track in TRACKS, 'Unknown universe track')
    check(manifest['official_compliance'] == 'UNKNOWN_BLOCK_SUBMISSION', 'Unknown formal compliance falsely certified')
    for relative, expected in manifest['hashes'].items():
        check(sha(ROOT / relative) == expected, 'Frozen source/input changed: ' + relative)
        check(sha(output / 'input_snapshot' / relative) == expected, 'Snapshot mismatch: ' + relative)
    snapshot = output / 'input_snapshot'
    base = json.loads((snapshot / 'config/strategy_v2.json').read_text())
    study = json.loads((snapshot / 'config/tuning_2nd_study.json').read_text())
    check(manifest['study'] == study, 'Embedded study differs from frozen declared candidate set')
    candidates = {str(x['candidate_id']): x['params'] for x in study['candidates']}
    check(len(candidates) == len(study['candidates']), 'Duplicate declared candidates')
    check(manifest['expected_trials'] == len(candidates) * 3, 'Incorrect declared trial count')
    if track == 'historical_pit':
        daily_path = snapshot / 'data/v2/market_daily.csv'
        universe_path = snapshot / 'data/extended/processed/universe_20241231.csv'
    else:
        folder = snapshot / 'data/tuning_2nd/official_universe/processed'
        daily_path, universe_path = folder / 'daily.csv', folder / 'universe.csv'
        readiness = json.loads((folder / 'readiness.json').read_text())
        check(readiness['status'] == 'DATA_READY', 'Official scenario launched before data release')
        qa = json.loads((ROOT / 'outputs/tuning_report_2nd_try/official_data_audit.json').read_text())
        check(qa['status'] == 'PASS' and qa['verified_output_hashes'] == readiness['output_hashes'],
              'Official canonical inputs lack matching independent data gate')
        for name, expected in readiness['output_hashes'].items():
            check(sha(folder / name) == expected, 'Official data differs from independent gate: ' + name)
    daily = pd.read_csv(daily_path, low_memory=False)
    daily['date'] = pd.to_datetime(daily.date)
    check(not daily.duplicated(['date', 'symbol']).any(), 'Duplicate price observations')
    universe = read_csv(universe_path)
    check(len(universe) == 150 and universe.symbol.nunique() == 150, 'Universe is not exactly 150 identities')
    known = universe['known_at'] if 'known_at' in universe else universe['known_at_assumption']
    check(sorted(known.astype(str).unique()) == manifest['universe_publication_dates'], 'Actual universe publication dates lost')
    first_cutoff = pd.Timestamp(base['start']).tz_localize('Asia/Taipei') + pd.Timedelta(hours=8, minutes=55)
    actual_dates = pd.to_datetime(known, utc=True)
    if track == 'historical_pit':
        check((actual_dates <= first_cutoff).all(), 'Future membership leaked into PIT track')
    else:
        check((actual_dates > first_cutoff).any(), 'Official ex-post track backdated publication evidence')
    calendar = sorted(pd.Timestamp(x) for x in daily.date.unique()
                      if pd.Timestamp(base['start']) <= pd.Timestamp(x) <= pd.Timestamp(base['end']))
    expected_dates = [str(x.date()) for x in calendar]
    inputs = ledger_inputs(daily, calendar, universe)
    market = daily[['date', 'symbol', 'turnover', 'execution_volume']].copy()
    market['date'] = market.date.dt.strftime('%Y-%m-%d')
    summary = read_csv(output / 'trial_summary.csv')
    check(summary.status.eq('COMPLETE').all(), 'Cannot rank an incomplete declared candidate pool')
    expected_ids = {(s, cid) for s in STRATEGIES for cid in candidates}
    check(not summary.duplicated(['strategy', 'candidate_id']).any()
          and set(zip(summary.strategy, summary.candidate_id)) == expected_ids, 'Trial identity/count mismatch')
    summary = summary.set_index(['strategy', 'candidate_id'])
    equities = {s: {} for s in STRATEGIES}
    errors, trial_counts = [], {}
    fixed = ('start', 'end', 'initial_cash', 'cash_target', 'commission', 'sell_tax',
             'lot_size', 'execution', 'dividend_cash_policy', 'research_shadow',
             'min_count', 'max_count', 'max_weight', 'tsmc_max_weight', 'price_buffer')
    for strategy, cid in sorted(expected_ids):
        folder = output / 'trials' / strategy / cid
        config = json.loads((folder / 'config.json').read_text())
        for key in fixed:
            check(config[key] == base[key], str(folder) + ': fixed assumption changed: ' + key)
        check(config['tuning_params'] == candidates[cid], str(folder) + ': changed declared tuple')
        check(config['cash_guard_ratio'] == study['cash_guard_ratio'], str(folder) + ': guard changed mid-study')
        check(config['cash_guard_headroom'] == study['cash_guard_headroom'], str(folder) + ': changed guard headroom')
        check(config['allocation_mode'] == 'local', str(folder) + ': changed local allocation policy')
        params = candidates[cid]
        for key in ('target_count', 'replacement_margin', 'max_replacements_per_day', 'volatility_spike_ratio',
                    'one_day_chase_return', 'volume_low', 'volume_high', 'four_hour_mode'):
            check(config[key] == params[key], str(folder) + ': recorded parameter not applied: ' + key)
        check(config['feature_presets'] == {key: params[key] for key in ('returns', 'ema', 'macd')},
              str(folder) + ': feature presets not applied')
        score_keys = ('return20', 'return50', 'volume', 'macd', 'trend', 'long_trend')
        check(config['score_weights'] == dict(zip(score_keys, study['score_profiles'][params['score_profile']])),
              str(folder) + ': score profile not applied')
        check(config['use_4h'] == (params['four_hour_mode'] == 'strict')
              and config['match_4h_coverage'] == (params['four_hour_mode'] == 'coverage_only'),
              str(folder) + ': 4H mode not applied')
        check(config['universe_mode'] == track and config['ex_post_fixed_universe'] == (track == 'official_ex_post'),
              str(folder) + ': wrong universe interpretation')
        eq = read_csv(folder / 'equity.csv')
        check(eq.date.tolist() == expected_dates, str(folder) + ': incomplete calendar')
        check(eq.submission_status.eq('BLOCK_SUBMISSION').all(), str(folder) + ': false submission certification')
        trades = trial_trade_audit(folder / 'trades.csv', eq, market, config)
        errors.append(trial_ledger_audit(folder / 'trades.csv', eq, inputs, config))
        actual = second_metrics(eq, config)
        metrics = json.loads((folder / 'metrics.json').read_text())
        for row, label in [(metrics, 'metrics'), (summary.loc[(strategy, cid)], 'summary')]:
            compare_metrics(row, actual, str(folder) + ': ' + label,
                            require=('measured_hard_breach_days', 'economic_total_return', 'economic_max_drawdown'))
            equal(row['trades'], trades, str(folder) + ': trade count', atol=0)
            check(str(row['observed_rule_eligible']).lower() in ('true', 'false'), str(folder) + ': invalid eligibility value')
            check((str(row['observed_rule_eligible']).lower() == 'true') == (actual['measured_hard_breach_days'] == 0), str(folder) + ': false measured eligibility')
            formal = ('UNKNOWN_OFFICIAL_WHITELIST_AND_ACTIVE_SHARE_BLOCK_SUBMISSION'
                      if track == 'historical_pit' else 'UNKNOWN_ACTIVE_SHARE_BLOCK_SUBMISSION')
            check(row['official_compliance'] == formal, str(folder) + ': false formal compliance')
        monthly_audit(folder / 'monthly.csv', eq, base['initial_cash'])
        equities[strategy][cid] = eq
        trial_counts[strategy + '/' + cid] = trades
    selections = json.loads((output / 'selection.json').read_text())
    check(set(selections) == set(STRATEGIES), 'Missing strategy selection states')
    verified_selection, frames, full_audits, replay_counts = {}, {}, {}, {}
    for strategy in STRATEGIES:
        expected = select_zero_measured(equities[strategy], base)
        winner = expected['candidate_id']
        selected = selections[strategy]
        check(selected['candidate_id'] == winner, strategy + ': incorrect zero-hard then return selection')
        check(selected['formal_compliance'] == 'UNKNOWN_BLOCK_SUBMISSION', strategy + ': formal certification unsupported')
        if winner is None:
            check(selected['status'] == 'NO_ELIGIBLE_WINNER', strategy + ': least-violating fallback mislabeled best')
            check(not (output / 'final' / strategy).exists(), strategy + ': stale/ineligible best artifact')
        else:
            check(selected['status'] == 'SELECTED_ZERO_MEASURED_HARD', strategy + ': incorrect eligible label')
            source, target = output / 'trials' / strategy / winner, output / 'final' / strategy
            for name in ('equity', 'orders', 'trades'):
                pd.testing.assert_frame_equal(read_csv(source / f'{name}.csv'), read_csv(target / f'{name}.csv'),
                                              check_exact=True, check_dtype=False)
            replay_counts[strategy] = dict(candidate_id=winner, equity=len(equities[strategy][winner]),
                                           trades=trial_counts[strategy + '/' + winner])
            frames[strategy] = read_csv(target / 'equity.csv')
            _, full_audits[strategy] = audit_model(output / 'final', strategy, daily, calendar, base)
        verified_selection[strategy] = dict(status=expected['status'], candidate_id=winner,
                                             eligible_count=sum(x['eligible'] for x in expected['scores'].values()))
    for benchmark in ('v1_matched', '0050'):
        folder = output / 'final' / benchmark
        frames[benchmark] = read_csv(folder / 'equity.csv')
        _, full_audits[benchmark] = audit_model(output / 'final', benchmark, daily, calendar, base)
        if track == 'historical_pit' or benchmark == '0050':
            replay_counts[benchmark] = exact_replay(folder, ROOT / 'outputs/backtest_v2_2025_to_now' / benchmark)
        config = json.loads((folder / 'config.json').read_text())
        metrics = json.loads((folder / 'metrics.json').read_text())
        check(metrics['planner'] == 'FIXED_LEGACY_PLANNER', benchmark + ': benchmark planner was silently changed')
        compare_metrics(metrics, second_metrics(frames[benchmark], config), benchmark + ': measured diagnostics')
    report_tables = audit_report_tables(output, frames, base['initial_cash'])
    artifact_paths = [output / x for x in ('manifest.json', 'selection.json', 'trial_summary.csv',
                                         'comparison.csv', 'monthly_comparison.csv', 'nav_comparison.csv')]
    artifact_paths.extend(output / 'final' / name / filename
                          for name in frames for filename in ('config.json', 'metrics.json', 'equity.csv',
                                                               'orders.csv', 'trades.csv', 'holdings.csv'))
    artifact_hashes = {str(path.relative_to(output)): sha(path) for path in artifact_paths}
    return dict(status='PASS', track=track, scope='MEASURED_RULES_AND_ACCOUNTING_ONLY_NOT_FORMAL_CONTEST_CERTIFICATION',
                verified_at=datetime.now(timezone.utc).isoformat(), auditor_sha256=sha(__file__),
                trial_count=len(expected_ids), input_and_code_hashes=len(manifest['hashes']),
                actual_universe_publication_dates=manifest['universe_publication_dates'],
                max_all_trial_ledger_error=max(errors), selection=verified_selection,
                full_ledger_audits=full_audits, exact_replays=replay_counts, report_tables=report_tables,
                artifact_hashes=artifact_hashes,
                limitations=['The historical sample was already inspected; the new grid/planner study is development evidence, not unseen OOS.',
                             'Exhaustive coverage applies only to the explicit declared tuple list, not to the full Cartesian product of parameter ranges.',
                             'Zero measured hard breaches does not resolve unknown Active Share, source vintages or submission success.',
                             'Official fixed-universe scenarios deliberately use later-known membership and cannot establish historical PIT performance.',
                             'The new compliance planner and tuning both differ from the fixed legacy-v1 benchmark.',
                             'Price-band safety is conditional; corporate events, missing fills and out-of-band moves can invalidate its assumptions.'])


def audit_pilot(output):
    """Engineering gate only; guard selection never consults pilot returns."""
    status = json.loads((output / 'status.json').read_text())
    check(status['status'] == 'PILOT_COMPLETE', 'Pilot is incomplete')
    provenance = json.loads((output / 'pilot_provenance.json').read_text())
    reviewed_post_pilot_changes = {}
    for relative, expected in provenance['hashes'].items():
        check(sha(output / 'input_snapshot' / relative) == expected, 'Pilot source snapshot mismatch: ' + relative)
        if sha(ROOT / relative) != expected:
            # Reviewed after the completed historical pilot: fail-closed data
            # readiness was added only inside the unused official-track branch.
            current = (ROOT / relative).read_text()
            inserted = ("        import json\n"
                        "        readiness = json.loads((folder / 'readiness.json').read_text())\n"
                        "        if readiness.get('status') != 'DATA_READY':\n"
                        "            raise ValueError('Official scenario dataset has unresolved blockers')\n")
            frozen = (output / 'input_snapshot' / relative).read_text()
            check(relative == 'src/tuning_2nd.py' and current.count(inserted) == 1
                  and current.replace(inserted, '') == frozen,
                  'Unreviewed change after pilot: ' + relative)
            reviewed_post_pilot_changes[relative] = dict(current_sha256=sha(ROOT / relative),
                                                        scope='Official-only DATA_READY precondition; historical pilot branch unchanged')
    old = json.loads((ROOT / 'outputs/v2_abc_tuning_20260922/tuning_manifest.json').read_text())
    for relative, expected in old['hashes'].items():
        check(sha(ROOT / relative) == expected, 'Existing study dependency changed: ' + relative)
    daily = pd.read_csv(ROOT / 'data/v2/market_daily.csv', low_memory=False)
    daily['date'] = pd.to_datetime(daily.date)
    universe = read_csv(ROOT / 'data/extended/processed/universe_20241231.csv')
    base = json.loads((ROOT / 'config/strategy_v2.json').read_text())
    calendar = sorted(pd.Timestamp(x) for x in daily.date.unique()
                      if pd.Timestamp(base['start']) <= pd.Timestamp(x) <= pd.Timestamp(base['end']))
    inputs = ledger_inputs(daily, calendar, universe)
    market = daily[['date', 'symbol', 'turnover', 'execution_volume']].copy()
    market['date'] = market.date.dt.strftime('%Y-%m-%d')
    summary = read_csv(output / 'trial_summary.csv')
    check(len(summary) == provenance['expected_trials'] and summary.status.eq('COMPLETE').all(), 'Incomplete pilot candidates')
    checks, full_audits = [], {}
    for row in summary.to_dict('records'):
        folder = output / row['path']
        cfg = json.loads((folder / 'config.json').read_text())
        eq = read_csv(folder / 'equity.csv')
        count = trial_trade_audit(folder / 'trades.csv', eq, market, cfg)
        error = trial_ledger_audit(folder / 'trades.csv', eq, inputs, cfg)
        metrics = second_metrics(eq, cfg)
        compare_metrics(row, metrics, str(folder))
        orders = read_csv(folder / 'orders.csv')
        mixed = int(orders[orders.reason.eq(MIXED_REASON)].signal_date.nunique())
        name = row['strategy'] + '/' + row['candidate_id']
        # Row-wise holdings reconstruction cross-checks every compact ledger.
        _, full_audits[name] = audit_model(output / 'trials' / row['strategy'], row['candidate_id'], daily, calendar, base)
        checks.append(dict(strategy=row['strategy'], candidate_id=row['candidate_id'], guard_ratio=row['guard_ratio'],
                           measured_hard_breach_days=metrics['measured_hard_breach_days'],
                           infeasible_executed_days=metrics['infeasible_executed_days'],
                           trades=count, mixed_plan_days=mixed, max_ledger_error=error))
    incumbents = pd.DataFrame(checks)
    incumbents = incumbents[incumbents.candidate_id.str.contains('_g', regex=False)]
    totals = incumbents.groupby('guard_ratio').measured_hard_breach_days.sum().to_dict()
    chosen = .12 if totals.get(.12) == 0 else min(totals, key=lambda g: (totals[g], abs(g - .12), g))
    return dict(status='PASS', scope='PILOT_ACCOUNTING_AND_MEASURED_RULES_ONLY',
                auditor_sha256=sha(__file__), source_hashes=provenance['hashes'],
                reviewed_post_pilot_changes=reviewed_post_pilot_changes,
                verified_at=datetime.now(timezone.utc).isoformat(), trials=checks, full_ledger_audits=full_audits,
                guard_rule='zero incumbent breaches at .12 else minimum total incumbent hard days then nearest .12; no return criterion',
                incumbent_hard_day_totals=totals, independently_selected_guard=chosen,
                formal_compliance='UNKNOWN_BLOCK_SUBMISSION')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, help='One completed track directory')
    parser.add_argument('--pilot', action='store_true', help='Audit the completed repaired engineering pilot')
    args = parser.parse_args()
    output = ROOT / args.output
    try:
        result = audit_pilot(output) if args.pilot else audit_track(output)
    except Exception as error:
        result = {'status': 'FAIL', 'error': str(error), 'auditor_sha256': sha(__file__)}
        if output.exists():
            (output / 'audit.json').write_text(json.dumps(result, indent=2) + '\n')
        raise
    (output / 'audit.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
