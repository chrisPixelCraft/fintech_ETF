"""Daily-only x0352 features and isolated 24-session competition shadow ledger.

Inputs must be historical nominal share units (not Yahoo split-normalized units).
The source ledger/planner are reused without global monkeypatches or file edits.
Open is an explicit research execution proxy, never an official average price.
"""
from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src import backtest, double_check_ledger, double_check_tuning, tuning_2nd
from src.official_deep_tuning import OfficialPlanner

ROOT = Path(__file__).resolve().parents[1]


def build_config(params=None):
    """Transfer frozen x0352 parameters; the only signal change removes 4H gates."""
    base = json.loads((ROOT / 'config/strategy_v2.json').read_text())
    original = json.loads((ROOT / 'outputs/best_v2/official_ex_post/config.json').read_text())['full_tuning_params']
    trial = {**original, **(params or {})}
    config = double_check_tuning.config_for(
        {'base': base, 'track': 'official_ex_post'},
        {'candidate_id': 'x0352_daily_baseline', 'params': trial})
    config.update(strategy_id='x0352_daily_baseline', initial_cash=1_000_000_000,
                  use_4h=False, match_4h_coverage=False, four_hour_mode='disabled',
                  execution='open_proxy', execution_assumption='DAILY_OPEN_RESEARCH_PROXY',
                  universe_interpretation='COMPETITION_UNIVERSE_STRESS_TEST')
    config['feature_spec']['four_hour_mode'] = 'disabled'
    return config


def _daily(daily):
    out = daily.copy()
    out['date'] = pd.to_datetime(out.date).dt.normalize()
    out['symbol'] = out.symbol.astype(str)
    if out.duplicated(['date', 'symbol']).any():
        raise ValueError('Duplicate daily date/symbol')
    for name, aliases, default in [('dividend', ['dividends'], 0.),
                                    ('split', ['split_ratio', 'stock_splits'], 1.)]:
        if name not in out:
            alias = next((x for x in aliases if x in out), None)
            out[name] = out[alias] if alias else default
        if name == 'split':
            out[name] = out[name].replace(0., 1.)
    if not np.isfinite(out[['dividend', 'split']].to_numpy(float)).all():
        raise ValueError('Missing corporate action values')
    if (out.dividend < 0).any() or (out.split <= 0).any():
        raise ValueError('Invalid corporate action values')
    return out.sort_values(['date', 'symbol']).reset_index(drop=True)


def _valid_signal(rows):
    valid = (np.isfinite(rows.close) & rows.close.gt(0)
             & np.isfinite(rows.volume) & rows.volume.ge(0))
    for quality in ['valid_price', 'valid_for_research']:
        if quality in rows:
            valid &= rows[quality].fillna(False).astype(bool)
    return valid


def build_features(daily, config):
    """Reproduce frozen indicators within causally clean price-history segments.

    An observed unexplained >30% action-neutral discontinuity quarantines that
    observation and resets the indicator history. The next valid observations
    must complete the original 200-observation warm-up. Ordinary missing quotes
    carry corporate-action rights across the gap, without using future rows to
    choose the arithmetic for an earlier observation.
    """
    data = _daily(daily)
    p = config['deep_feature_params']
    frames = []
    for _, source in data.groupby('symbol', sort=True):
        rows = source.copy()
        flags = rows.get('quality_flags', pd.Series('', index=rows.index)).fillna('').astype(str)
        reset = flags.str.split('|').map(lambda parts: 'ACTION_NEUTRAL_RETURN_GT_30PCT' in parts)
        valid = _valid_signal(rows) & ~reset
        segments = reset.cumsum()
        calculated_segments = []
        for _, segment in rows.groupby(segments, sort=True):
            observed = segment.loc[valid.loc[segment.index]].copy()
            if observed.empty:
                continue
            # Keep the canonical arithmetic for adjacent observations. Only a
            # past gap needs accumulated actions between its two known closes.
            cumulative = segment.split.cumprod()
            distributions = (segment.dividend * cumulative.shift(fill_value=1.)).cumsum()
            units = cumulative.loc[observed.index]
            positions = pd.Series(np.arange(len(segment)), index=segment.index).loc[observed.index]
            gross = (observed.close * observed.split + observed.dividend) / observed.close.shift()
            gaps = positions.diff().gt(1)
            gap_split = units / units.shift()
            gap_dividend = distributions.loc[observed.index].diff() / units.shift()
            gross.loc[gaps] = ((observed.close * gap_split + gap_dividend) / observed.close.shift()).loc[gaps]
            price = gross.fillna(1.).cumprod() * float(observed.close.iloc[0])
            volume = observed.volume / units
            returns = price.pct_change(fill_method=None)
            calculated = pd.DataFrame(index=observed.index)
            calculated['signal_price'] = price
            calculated['return20'] = price.pct_change(p['return_short'], fill_method=None)
            calculated['return50'] = price.pct_change(p['return_long'], fill_method=None)
            calculated['return1'] = returns
            for column, span in [('ema20', p['ema_fast']), ('ema50', p['ema_slow']),
                                 ('ema100', 100), ('ema200', 200)]:
                calculated[column] = price.ewm(span=span, adjust=False, min_periods=span).mean()
            calculated['volume_ratio'] = volume.rolling(5, min_periods=5).mean() / volume.shift().rolling(20, min_periods=20).mean()
            calculated['volatility_ratio'] = returns.rolling(5).std() / returns.shift().rolling(20).std()
            macd = price.ewm(span=p['macd_fast'], adjust=False).mean() - price.ewm(span=p['macd_slow'], adjust=False).mean()
            calculated['macd_hist'] = macd - macd.ewm(span=p['macd_signal'], adjust=False).mean()
            calculated['signal_history_valid_sessions'] = np.arange(len(observed), dtype=float) + 1
            calculated['ready'] = calculated.signal_history_valid_sessions >= config['warmup_sessions']
            calculated['trend'] = ((price > calculated.ema20) & (calculated.ema20 > calculated.ema50)).astype(float)
            calculated['long_trend'] = ((price > calculated.ema100) & (calculated.ema100 > calculated.ema200)).astype(float)
            calculated_segments.append(calculated)
        if calculated_segments:
            calculated = pd.concat(calculated_segments)
        else:
            columns = ['signal_price', 'return20', 'return50', 'return1', 'ema20', 'ema50',
                       'ema100', 'ema200', 'volume_ratio', 'volatility_ratio', 'macd_hist',
                       'signal_history_valid_sessions', 'ready', 'trend', 'long_trend']
            calculated = pd.DataFrame(index=pd.Index([], dtype=rows.index.dtype), columns=columns)
        rows = rows.join(calculated)
        rows['ready'] = rows.ready.fillna(False).astype(bool)
        rows['signal_available'] = valid
        rows['signal_history_reset'] = reset
        rows['signal_history_segment'] = segments
        frames.append(rows)
    if not frames:
        raise ValueError('No daily input')
    result = pd.concat(frames).sort_values(['date', 'symbol']).reset_index(drop=True)
    result.attrs['feature_parameters'] = dict(p)
    result.attrs['warmup_sessions'] = config['warmup_sessions']
    result.attrs['quality_policy'] = 'CAUSAL_SEVERE_DISCONTINUITY_RESET_ORIGINAL_WARMUP'
    return result


def _score(rows, config):
    # Invalid close rows remain present for execution/action handling, but do
    # not enter cross-sectional ranks or prior-close portfolio construction.
    available = rows.loc[rows.signal_available].copy()
    if not len(available):
        out = available.set_index('symbol', drop=False)
        for name in ['score', 'entry_ok', 'exit']:
            out[name] = pd.Series(dtype=float)
        return out
    ranked = backtest.score_candidates(available, config)
    if config.get('research_reference') == 'simple_momentum':
        ranked['score'] = ranked.return20.rank(pct=True)
        ranked = ranked.sort_values(['score', 'symbol'], ascending=[False, True])
    return ranked


def _ledger_code():
    """Assert every adapter seam so upstream changes fail closed."""
    source = inspect.getsource(double_check_ledger.run_v2)
    changes = [
        ("all_sessions = sorted(pd.Timestamp(x) for x in daily.date.unique())",
         "all_sessions = requested_calendar"),
        ("if symbols - set(stock_data.symbol):\n        raise ValueError('Universe symbols without price history')", "# Missing historical names stay in the whitelist and coverage denominator."),
        ("if s not in bars.index:\n                    stale.append(s)\n                    continue", "if s not in bars.index:\n                    stale.append(s)\n                    continue\n                if not np.isfinite(bars.at[s, 'close']) or bars.at[s, 'close'] <= 0 or not bool(bars.at[s, 'signal_available']):\n                    stale.append(s)"),
        ("volume_col = 'execution_volume' if 'execution_volume' in bars else 'volume'\n                valid = s in bars.index and 'turnover' in bars\n                if valid:\n                    volume, turnover = float(bars.at[s, volume_col]), float(bars.at[s, 'turnover'])\n                    valid = np.isfinite(volume) and np.isfinite(turnover) and volume > 0 and turnover > 0",
         "valid = s in bars.index and np.isfinite(bars.at[s, 'open']) and bars.at[s, 'open'] > 0\n                volume = float(bars.at[s, 'volume']) if s in bars.index else np.nan"),
        ("holdings[s] = q * float(bars.at[s, 'split'])",
         "holdings[s] = q * float(bars.at[s, 'split'])\n                if not np.isfinite(bars.at[s, 'close']) or bars.at[s, 'close'] <= 0:\n                    last_prices[s] /= float(bars.at[s, 'split'])"),
        ("UNFILLED_MISSING_OFFICIAL_VWAP", "UNFILLED_MISSING_OPEN_PROXY"),
        ("price = turnover / volume", "price = float(bars.at[s, 'open'])"),
        ("volume_participation=abs(q) / volume", "volume_participation=abs(q) / volume if np.isfinite(volume) and volume > 0 else np.nan"),
        ("last_prices.update(rows.set_index('symbol').close.to_dict())",
         "last_prices.update(rows.loc[np.isfinite(rows.close) & rows.close.gt(0)].set_index('symbol').close.to_dict())\n        stale = sorted(set(stale) | {s for s in holdings if s not in bars.index or not bool(bars.at[s, 'signal_available'])})"),
        ("initial_prices = stock_data[stock_data.date <= initial_day].sort_values('date').groupby('symbol').tail(1)",
         "initial_prices = stock_data[(stock_data.date <= initial_day) & np.isfinite(stock_data.close) & stock_data.close.gt(0)].sort_values('date').groupby('symbol').tail(1)"),
    ]
    for before, after in changes:
        if source.count(before) != 1:
            raise RuntimeError('Frozen ledger adapter seam changed: ' + before[:80])
        source = source.replace(before, after)
    return compile(source, '<strategy_24d_isolated_ledger>', 'exec')


_LEDGER_CODE = _ledger_code()


def _failed(config, dates, reason, detail):
    metrics = dict(status='FAILED', episode_status=reason, failure_reasons=reason,
                   measured_pass=False, complete_period=False, disqualified=False,
                   episode_return=None, episode_max_drawdown=None, episode_turnover=None,
                   forensic_partial_return=None,
                   requested_sessions=len(dates), observed_sessions=0,
                   active_share_status='ACTIVE_SHARE_NOT_VERIFIED', error=detail)
    result = {key: pd.DataFrame() for key in ['equity', 'trades', 'orders', 'holdings', 'signals',
              'warnings', 'snapshots', 'compliance_daily', 'rejected_trades', 'plan_audit']}
    result.update(metrics=metrics, config=config)
    return result


def run_episode(daily, universe, config, session_dates, features=None):
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
    c = copy.deepcopy(config)
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
    planner = OfficialPlanner()
    def safe_planner(ranked, holdings, cash, nav, cfg, *args):
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
    namespace = dict(vars(double_check_ledger))
    def settings(cfg):
        result = double_check_ledger._settings(cfg)
        result['execution'] = 'open_proxy'
        return result
    namespace.update(compute_features=lambda *_: window.copy(), validate_daily=lambda x: x,
                     score_candidates=_score, make_plan_v2=safe_planner, _settings=settings,
                     requested_calendar=list(dates.insert(0, prior)))
    exec(_LEDGER_CODE, namespace)
    try:
        result = namespace['run_v2'](window, universe, c)
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
    return result
