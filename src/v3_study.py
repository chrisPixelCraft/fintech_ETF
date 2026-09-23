"""Fixed weak-market interventions on independently audited v2 A portfolios."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pandas as pd

from src import tuning_2nd as second
from src.v3_signals import WeakMarketSignals

ROOT = Path(__file__).resolve().parents[1]
STUDY_PATH = ROOT / 'config/v3_weak_market.json'
VERSIONS = ('A', 'B', 'C', 'D')
BASELINES = {'historical_pit': 'p052', 'official_ex_post': 'p049'}


def load_study(path=STUDY_PATH):
    study = json.loads(Path(path).read_text())
    required = {'study_id', 'status', 'start', 'end', 'versions', 'tracks',
                'baseline_candidates', 'baseline_source', 'signal_parameters',
                'evaluation', 'formal_submission'}
    if set(study) != required or study['status'] != 'FROZEN':
        raise ValueError('BLOCK: incomplete or unfrozen v3 study')
    if study['versions'] != list(VERSIONS) or study['tracks'] != list(second.TRACKS):
        raise ValueError('Unexpected study versions/tracks')
    if study['baseline_candidates'] != BASELINES or study['baseline_source'] != 'v2_A_best.py':
        raise ValueError('Frozen A baseline changed')
    if study['evaluation']['parameter_search'] or not study['evaluation']['historical_period_is_development']:
        raise ValueError('Fixed development study cannot become a tuning or unseen test')
    if study['formal_submission'] != 'BLOCKED_UNKNOWN_ACTIVE_SHARE':
        raise ValueError('Formal submission must remain blocked')
    return study


def context(track='historical_pit', study=None):
    from v2_A_best import build_config
    study = study or load_study()
    ctx = second.context(track)
    ctx['v3_base'] = build_config(track)
    if (ctx['v3_base']['start'], ctx['v3_base']['end']) != (study['start'], study['end']):
        raise ValueError('Frozen baseline study dates changed')
    ctx['v3_signals'] = WeakMarketSignals(ctx['daily'], study['signal_parameters'])
    return ctx


def config_for(ctx, version, study=None):
    study = study or load_study()
    if version not in VERSIONS or ctx['track'] not in BASELINES:
        raise ValueError('Unknown v3 version/track')
    c = copy.deepcopy(ctx['v3_base'])
    c['strategy_id'] = 'v3_weak_market_' + version
    c['v3'] = {'version': version, 'baseline_candidate': BASELINES[ctx['track']],
               'signal_parameters': copy.deepcopy(study['signal_parameters']),
               'study_id': study['study_id'], 'parameter_search': False,
               'formal_submission': study['formal_submission']}
    return c


def run_model(ctx, version, study=None):
    study = study or load_study()
    config = config_for(ctx, version, study)
    engine = second.adapter(ctx)
    transform = ctx['v3_signals'].callback(version)

    def checked_transform(day, ranked):
        before = ranked.copy(deep=True)
        transformed, meta = transform(day, ranked)
        if set(before.index) != set(transformed.index):
            raise ValueError('v3 must preserve the available stock universe')
        # This stricter wrapper prevents the generic engine callback from
        # changing entry/exit or any other frozen field except the ranking.
        for key in before:
            if key != 'score':
                pd.testing.assert_series_equal(before[key].sort_index(),
                    transformed[key].sort_index(), check_names=False)
        transformed['audit_base_score'] = before['score']
        transformed['audit_base_entry_ok'] = before['entry_ok']
        transformed['audit_base_exit'] = before['exit']
        transformed['audit_score_changed'] = ~np.isclose(transformed['score'],
            before['score'], rtol=0, atol=1e-14, equal_nan=True)
        return transformed, meta

    result = engine.run_v2(ctx['daily'], ctx['universe'], config, ctx['bars'],
                          signal_transform=checked_transform)
    eq = result['equity']
    if not np.isfinite(eq.economic_nav.astype(float)).all():
        raise ValueError('Nonfinite v3 accounting')
    if len(result['holdings'].query("symbol == '2888.TW' and date >= '2025-07-24'")):
        raise ValueError('Unsupported multi-security merger held')
    result['metrics'].update(second.metrics(eq))
    result['metrics'].update(version=version, universe_track=ctx['track'],
        baseline_candidate=BASELINES[ctx['track']], trade_count=len(result['trades']),
        planner='COMPLIANCE_GUARD_V2', parameter_search=False,
        formal_submission=study['formal_submission'])
    result['snapshots']['universe_mode'] = ctx['track']
    if ctx['track'] == 'official_ex_post':
        result['snapshots']['official_whitelist_status'] = 'FIXED_OFFICIAL_150_RETROSPECTIVE_MEMBERSHIP_BIAS'
    return result


def assert_baseline_replay(result, track):
    folder = ROOT / 'outputs/tuning_report_2nd_try' / track / 'final/A'
    for name in ('equity', 'orders', 'trades', 'holdings'):
        expected = pd.read_csv(folder / f'{name}.csv', float_precision='round_trip').fillna('')
        pd.testing.assert_frame_equal(expected, result[name].fillna(''),
                                      check_dtype=False, check_exact=True)


def instrument_series(daily):
    """Report-only total-return mark. Missing sessions carry the last known mark.

    The strategy feature module never consumes this valuation-only series.
    """
    from src.backtest import validate_daily, causal_prices
    frame = validate_daily(daily)
    calendar = pd.DatetimeIndex(sorted(frame.date.unique()))
    rows = frame[frame.symbol.eq('0050.TW')].sort_values('date')
    index, _ = causal_prices(rows)
    series = pd.Series(index.to_numpy(), index=pd.DatetimeIndex(rows.date))
    # Retain effective corporate events even if their row has no traded quote;
    # only the valuation mark is suppressed on that row.
    series = series.where(rows.volume.to_numpy() > 0)
    return series.reindex(calendar).ffill().rename('instrument_total_return_index')


def evaluation_tables(results, daily):
    from scripts.run_v2_tuning import monthly_rows
    instrument = instrument_series(daily)
    monthly = pd.DataFrame([dict(model=name, **r)
        for name, result in results.items() for r in monthly_rows(result['equity'])])
    # monthly_rows exposes economic return as return, see frozen utility.
    return_column = 'return' if 'return' in monthly else 'economic_return'
    bench = monthly[monthly.model.eq('0050')].set_index('month')[return_column]
    incumbent = monthly[monthly.model.eq('A')].set_index('month')[return_column]
    labels = {}
    for month, rows in monthly.groupby('month', sort=True):
        eq = results['A']['equity']
        days = pd.DatetimeIndex(pd.to_datetime(eq.date))
        selected = days[days.strftime('%Y-%m') == month]
        first, last = selected.min(), selected.max()
        previous = instrument.index[instrument.index < first][-1]
        r = float(instrument.loc[last] / instrument.loc[previous] - 1)
        labels[month] = r
    monthly['instrument_return'] = monthly.month.map(labels)
    monthly['weak_month'] = monthly.instrument_return.lt(0)
    monthly['excess_vs_0050'] = monthly[return_column] - monthly.month.map(bench)
    monthly['excess_vs_instrument'] = monthly[return_column] - monthly.instrument_return
    monthly['difference_vs_A'] = monthly[return_column] - monthly.month.map(incumbent)
    monthly['partial_month'] = monthly.month.eq('2026-09')
    summary = []
    base_mdd = results['A']['metrics']['economic_max_drawdown']
    base_weak = monthly[monthly.model.eq('A') & monthly.weak_month]
    for name, group in monthly.groupby('model', sort=False):
        weak, strong = group[group.weak_month], group[~group.weak_month]
        metrics = results[name]['metrics']
        row = dict(model=name, weak_months=len(weak), strong_months=len(strong),
            weak_mean_return=float(weak[return_column].mean()),
            weak_mean_excess_0050=float(weak.excess_vs_0050.mean()),
            weak_mean_excess_instrument=float(weak.excess_vs_instrument.mean()),
            weak_mean_difference_A=float(weak.difference_vs_A.mean()),
            weak_positive_months=int(weak[return_column].gt(0).sum()),
            weak_outperform_0050_months=int(weak.excess_vs_0050.gt(0).sum()),
            weak_worst_month=float(weak[return_column].min()),
            strong_mean_excess_0050=float(strong.excess_vs_0050.mean()),
            strong_mean_difference_A=float(strong.difference_vs_A.mean()),
            full_economic_return=metrics['economic_total_return'],
            full_economic_mdd=metrics['economic_max_drawdown'],
            measured_hard_breach_days=metrics.get('measured_hard_breach_days'),
            costs=metrics.get('transaction_costs'), turnover=metrics.get('turnover_two_way'))
        row['development_objective_met'] = bool(name in ('B', 'C', 'D')
            and row['measured_hard_breach_days'] == 0 and row['weak_mean_difference_A'] > 0
            and row['weak_positive_months'] >= int(base_weak[return_column].gt(0).sum())
            and row['weak_outperform_0050_months'] >= int(base_weak.excess_vs_0050.gt(0).sum())
            and row['full_economic_mdd'] <= base_mdd + 1e-12)
        row['adoption'] = 'HOLD'
        summary.append(row)
    return monthly, pd.DataFrame(summary)
