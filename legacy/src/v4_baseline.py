"""Frozen V3 strategy reproduction; Stage 1 changes execution only."""
from __future__ import annotations
import copy
import numpy as np
import pandas as pd
from src import double_check_tuning, tuning_2nd
from src.official_deep_tuning import OfficialPlanner
from src.strategy_24d import build_config, build_features, _failed as _v3_failed
from src.v4_ledger import run_ledger
from src.v4_execution import resolve_execution_prices


def _failed(config, dates, reason, detail):
    result = _v3_failed(config, dates, reason, detail)
    result['metrics'].update(execution_mode=config['execution'],
        execution_assumption=config['execution_assumption'],
        sizing_price_mode=config.get('sizing_price_mode', 'signal_close'),
        canonical_execution_available=False, canonical_sizing_available=False,
        canonical_status='BLOCK_CANONICAL_V4', compliance_status='BLOCK_MISSING_DATA',
        alpha_status='INCOMPLETE', submission_status='BLOCK_SUBMISSION',
        missing_execution_price_days=0, unfilled_days=0, unfilled_orders=0,
        cash_violation_days=0, weight_violation_days=0, holding_count_violation_days=0,
        odd_lot_issue_days=0, no_valid_plan_days=0, transaction_cost=0.,
        day_1_invested_ratio=None, day_3_invested_ratio=None, day_5_invested_ratio=None,
        observation_status='NO_OBSERVATIONS')
    return result


def normalize_mode(mode):
    aliases = {'OFFICIAL_DAILY_AVERAGE': 'official_average', 'DAILY_OPEN_RESEARCH_PROXY': 'open_proxy'}
    mode = aliases.get(mode, mode)
    if mode not in ('official_average', 'open_proxy'):
        raise ValueError('Unknown execution mode: ' + str(mode))
    return mode


def _execution_maps(window, execution_data, mode):
    prices, volumes = {}, {}
    table = execution_data if execution_data is not None else pd.DataFrame()
    for day, rows in window.groupby('date', sort=True):
        symbols = rows.symbol.tolist()
        selected = resolve_execution_prices(table, day, symbols, mode,
            proxy_open=rows.set_index('symbol').open)
        for symbol, price in selected.items():
            prices[(pd.Timestamp(day), symbol)] = float(price)
        if mode == 'open_proxy':
            volume = rows.set_index('symbol').volume
        elif len(table):
            selected_rows = table.loc[pd.to_datetime(table.date).dt.normalize().eq(day)]
            volume = selected_rows.set_index('symbol').volume
        else:
            volume = pd.Series(dtype=float)
        for symbol in symbols:
            volumes[(pd.Timestamp(day), symbol)] = float(volume.get(symbol, np.nan))
    return prices, volumes


def _execution_metrics(result, mode):
    metrics, eq = result['metrics'], result['equity']
    compliance, warnings = result['compliance_daily'], result['warnings']
    missing = warnings.issue.str.contains('UNFILLED_MISSING_', regex=False)
    unfilled = warnings.issue.str.startswith('UNFILLED')
    reasons = eq.violations.fillna('') + ';' + compliance.warning_reasons.fillna('')
    metrics.update(execution_mode=mode,
        execution_assumption='OFFICIAL_DAILY_AVERAGE' if mode == 'official_average' else 'DAILY_OPEN_RESEARCH_PROXY',
        canonical_execution_available=bool(mode == 'official_average' and metrics['complete_period'] and not missing.any()),
        missing_execution_price_days=int(warnings.loc[missing, 'date'].nunique()),
        unfilled_days=int(warnings.loc[unfilled, 'date'].nunique()),
        cash_violation_days=int(reasons.str.contains('CASH').sum()),
        weight_violation_days=int((reasons.str.contains('WEIGHT_CAP|ACTIVE_CAP|OVERDUE_CAP', regex=True) | eq.active_cap_breaches.ne('') | eq.passive_cap_breaches.ne('')).sum()),
        holding_count_violation_days=int(reasons.str.contains('HOLDING_COUNT').sum()),
        odd_lot_issue_days=int(eq.odd_residual_names.gt(0).sum()),
        transaction_cost=float(eq.costs.sum()),
        alpha_status='DESCRIPTIVE_COMPLETE' if metrics['complete_period'] else 'INCOMPLETE',
        compliance_status=('BLOCK_MISSING_OFFICIAL_EXECUTION' if mode == 'official_average' and missing.any() else ('PASS_MEASURED' if metrics['measured_pass'] else 'FAIL_MEASURED')),
        canonical_status='AVAILABLE' if mode == 'official_average' and not missing.any() else 'BLOCK_CANONICAL_V4',
        submission_status='BLOCK_SUBMISSION')
    sizing_mode = result['config'].get('sizing_price_mode', 'signal_close')
    missing_close = result['plan_audit'].final_reason.str.contains('MISSING_OFFICIAL_CLOSE').any() or metrics.get('stale_held_price_days', 0) > 0
    sizing_ok = bool(sizing_mode == 'official_close' and not missing_close and metrics['complete_period'])
    metrics.update(sizing_price_mode=sizing_mode, canonical_sizing_available=sizing_ok,
        canonical_status='AVAILABLE' if metrics['canonical_execution_available'] and sizing_ok else 'BLOCK_CANONICAL_V4')
    if sizing_mode == 'official_close' and missing_close:
        metrics['compliance_status'] = 'BLOCK_MISSING_OFFICIAL_CLOSE'
    for day in (1, 3, 5):
        metrics[f'day_{day}_invested_ratio'] = float(1 - compliance.settled_cash.iloc[day-1] / compliance.settled_nav.iloc[day-1]) if len(eq) >= day else None


def run_episode(daily, universe, config, session_dates, execution_data=None, execution_mode='official_average', features=None, sizing_price_mode='signal_close'):
    """Reset cash for one specified 24-session window; retain all failures.

    Pass a build_features panel to reuse full-history indicators across windows.
    A disqualified run stops under the frozen ledger's three-warning rule and
    remains an incomplete failed episode in the requested-window denominator.
    """
    universe = universe.copy()
    if 'symbol' not in universe and 'yahoo_symbol' in universe:
        universe['symbol'] = universe.yahoo_symbol.astype(str)
    if 'known_at' not in universe and 'attachment_created_at' in universe:
        universe['known_at'] = universe.attachment_created_at
    dates = pd.DatetimeIndex(pd.to_datetime(session_dates)).normalize()
    if len(dates) != 24 or not dates.is_unique or not dates.is_monotonic_increasing:
        raise ValueError('session_dates must contain exactly 24 increasing unique sessions')
    execution_mode = normalize_mode(execution_mode)
    if sizing_price_mode not in ('signal_close', 'official_close'):
        raise ValueError('Unknown sizing_price_mode')
    c = copy.deepcopy(config)
    c['sizing_price_mode'] = sizing_price_mode
    c.update(execution=execution_mode, execution_assumption=('OFFICIAL_DAILY_AVERAGE' if execution_mode == 'official_average' else 'DAILY_OPEN_RESEARCH_PROXY'))
    c.update(start=str(dates[0].date()), end=str(dates[-1].date()))
    if c.get('use_4h') or c.get('match_4h_coverage'):
        raise ValueError('24D engine must have no 4H dependency')
    panel = build_features(daily, c) if features is None else features
    if panel.attrs.get('feature_parameters') != c['deep_feature_params'] or panel.attrs.get('warmup_sessions') != c['warmup_sessions']:
        raise ValueError('Feature cache/config mismatch')
    before = panel.loc[(panel.date < dates[0]) & np.isfinite(panel.close) & panel.close.gt(0), 'date']
    if before.empty:
        return _failed(c, dates, 'FAIL_MISSING_DATA', 'Missing prior-close session')
    prior = pd.Timestamp(c.get('prior_session_date', before.max()))
    if prior >= dates[0] or not panel.date.eq(prior).any():
        return _failed(c, dates, 'FAIL_MISSING_DATA', 'Missing specified prior-close session')
    window = panel.loc[panel.date.isin(dates.insert(0, prior))].copy()
    symbols = set(universe.symbol.astype(str))
    window = window.loc[window.symbol.isin(symbols)].copy()
    if window.empty:
        return _failed(c, dates, 'FAIL_MISSING_DATA', 'No whitelist observations')
    if sizing_price_mode == 'official_close':
        # Overlay accounting/sizing closes only after computing unchanged signals.
        # Missing closes stay missing; do not substitute Yahoo prices.
        table = execution_data if execution_data is not None else pd.DataFrame()
        if len(table):
            table = table.copy()
            table['date'] = pd.to_datetime(table.date).dt.normalize()
            if table.duplicated(['date', 'symbol']).any():
                raise ValueError('Duplicate official closes')
            official = table.set_index(['date', 'symbol'])
            close = official.close.reindex(pd.MultiIndex.from_frame(window[['date', 'symbol']])).to_numpy(float)
            source = official.source.astype(str)
            symbol = official.index.get_level_values('symbol').astype(str)
            market_matches = ~((symbol.str.endswith('.TW') & source.eq('TPEX_OFFICIAL'))
                               | (symbol.str.endswith('.TWO') & source.eq('TWSE_OFFICIAL')))
            trusted = (source.isin(['TWSE_OFFICIAL', 'TPEX_OFFICIAL']) & market_matches).reindex(pd.MultiIndex.from_frame(window[['date', 'symbol']]), fill_value=False).to_numpy(bool)
            window['close'] = np.where(trusted & np.isfinite(close) & (close > 0), close, np.nan)
        else:
            window['close'] = np.nan
    planner = OfficialPlanner()
    def safe_planner(ranked, holdings, cash, nav, cfg, *args):
        if sizing_price_mode == 'official_close':
            valid = np.isfinite(ranked.close) & ranked.close.gt(0)
            if set(holdings) - set(ranked.index[valid]) or int(valid.sum()) < cfg['min_count']:
                reason = 'INFEASIBLE_MISSING_OFFICIAL_CLOSE'
                planner.audit.append(dict(date=str(pd.Timestamp(ranked.date.iloc[0]).date()),
                    original_reason=reason, final_reason=reason, attempts=1,
                    replanning_triggered=False, orders=0, nominal_failures='MISSING_OFFICIAL_CLOSE',
                    hold_envelope_failures='UNKNOWN'))
                return {}, reason, list(holdings)
            ranked = ranked.loc[valid]
        try:
            return planner(ranked, holdings, cash, nav, cfg, *args)
        except ValueError as error:
            if not any(word in str(error).lower() for word in ['odd', 'unrepresentable']):
                raise
            reason = 'INFEASIBLE_ROUND_LOT:' + str(error)
            planner.audit.append(dict(date=str(pd.Timestamp(ranked.date.iloc[0]).date()),
                original_reason=reason, final_reason=reason, attempts=1, replanning_triggered=False,
                orders=0, nominal_failures='ROUND_LOT', hold_envelope_failures='UNKNOWN'))
            return {}, reason, list(holdings)
    prices, volumes = _execution_maps(window, execution_data, execution_mode)
    try:
        result = run_ledger(window, universe, c, list(dates.insert(0, prior)), safe_planner, prices, volumes, execution_mode)
    except (ValueError, KeyError, ZeroDivisionError) as error:
        reason = 'FAIL_ROUND_LOT' if 'odd' in str(error).lower() else 'FAIL_OTHER'
        return _failed(c, dates, reason, f'{type(error).__name__}: {error}')
    audit_by_date = {row['date']: row for row in planner.audit}
    result['plan_audit'] = pd.DataFrame([audit_by_date.get(row.date, dict(
        date=row.date, original_reason=row.plan_reason, final_reason=row.plan_reason,
        attempts=0, replanning_triggered=False, orders=0,
        nominal_failures='NO_SIGNAL_ROWS', hold_envelope_failures='UNKNOWN'))
        for row in result['snapshots'].itertuples()])
    active = result['plan_audit']
    if not result['metrics']['disqualified'] and len(active):
        active = active.iloc[:-1]
    metrics, eq, compliance = result['metrics'], result['equity'], result['compliance_daily']
    settlement = eq.copy()
    settlement['nav'] = compliance.settled_nav.to_numpy()
    settlement['cash_ratio'] = settlement.cash / settlement.nav
    # Preserve native return/MDD/turnover fields; attach stricter measured checks.
    extra = tuning_2nd.metrics(settlement, c['initial_cash'])
    metrics.update({key: value for key, value in extra.items() if key not in metrics})
    metrics.update(no_valid_plan_days=int(active.final_reason.str.startswith('INFEASIBLE').sum()) if len(active) else len(eq),
                   hold_without_envelope_days=int(active.final_reason.eq('HOLD_REVALIDATED_CURRENT_ONLY').sum()) if len(active) else 0,
                   unfilled_orders=int(result['warnings'].issue.str.startswith('UNFILLED').sum()),
                   status='COMPLETE' if metrics['complete_period'] else 'FAILED')
    reasons = []
    violations = ';'.join(eq.violations.astype(str))
    plans = ';'.join(active.final_reason.astype(str)) if len(active) else ''
    if metrics['stale_held_price_days'] or metrics['unfilled_orders'] or 'MISSING' in plans:
        reasons.append('FAIL_MISSING_DATA')
    if 'FEWER_THAN_MIN' in plans:
        reasons.append('FAIL_TOO_FEW_ELIGIBLE_STOCKS')
    for needle, label in [('CASH', 'FAIL_CASH'), ('WEIGHT_CAP', 'FAIL_WEIGHT_CAP'),
                           ('HOLDING_COUNT', 'FAIL_HOLDING_COUNT')]:
        if needle in violations or needle in ';'.join(compliance.warning_reasons):
            reasons.append(label)
    if 'odd' in plans.lower() or 'ROUND_LOT' in plans or eq.odd_residual_names.gt(0).any():
        reasons.append('FAIL_ROUND_LOT')
    measured = double_check_tuning.eligible(metrics)
    if not measured and not reasons:
        reasons.append('FAIL_OTHER')
    values = np.r_[c['initial_cash'], eq.economic_nav.to_numpy(float)]
    finite = np.isfinite(values).all() and np.isfinite(eq.cash.to_numpy(float)).all()
    if not finite:
        reasons.append('FAIL_OTHER')
    measured = bool(measured and finite and not reasons)
    if result['trades'].volume_participation.isna().any() or not np.isfinite(metrics['max_daily_volume_participation']):
        metrics['max_daily_volume_participation'] = None
    metrics.update(measured_pass=measured, episode_status='PASS' if measured else reasons[0],
                   failure_reasons=';'.join(dict.fromkeys(reasons)),
                   episode_return=float(values[-1] / values[0] - 1) if metrics['complete_period'] else None,
                   forensic_partial_return=float(values[-1] / values[0] - 1) if not metrics['complete_period'] else None,
                   episode_max_drawdown=float(-(values / np.maximum.accumulate(values) - 1).min()),
                   episode_turnover=float(eq.traded_notional.sum() / c['initial_cash']),
                   requested_sessions=24, observed_sessions=len(eq),
                   active_share_status='ACTIVE_SHARE_NOT_VERIFIED',
                   universe_interpretation='COMPETITION_UNIVERSE_STRESS_TEST')
    _execution_metrics(result, execution_mode)
    return result
