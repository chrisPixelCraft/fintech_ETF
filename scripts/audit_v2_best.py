"""Audit pinned v2 winners and fixed matched-v1/0050 contextual controls.

An exact historical replay and correct ledger do not undo ex-post parameter
selection. This auditor intentionally preserves that distinction in its result.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from audit_v2_study import audit_model, check, equal, sha
from audit_v2_tuning import (compare_metrics, equity_metrics, exact_replay,
                             ledger_inputs, read_csv, trial_ledger_audit,
                             trial_trade_audit)

ROOT = Path(__file__).resolve().parents[1]
MODELS = ('v1_matched', 'A', 'B', 'C', '0050')
PINNED = {'A': 'p005', 'B': 'p005', 'C': 'p006'}
OLD = ROOT / 'outputs/backtest_v2_2025_to_now'
TUNING = ROOT / 'outputs/v2_abc_tuning_20260922'


def audit_aggregates(output, frames, config):
    initial = config['initial_cash']
    counts = {}
    for filename, freq, key in [('monthly_comparison.csv', 'M', 'month'),
                                ('annual_comparison.csv', 'Y', 'year')]:
        table = pd.read_csv(output / filename, dtype={key: str})
        check(not table.duplicated(['model', key]).any(), filename + ': duplicate groups')
        checked = 0
        for model, eq in frames.items():
            previous, returns = initial, []
            for period, group in eq.groupby(pd.to_datetime(eq.date).dt.to_period(freq)):
                rows = table[(table.model == model) & (table[key] == str(period))]
                check(len(rows) == 1, filename + ': missing group')
                row = rows.iloc[0]
                wealth = np.r_[previous, group.economic_nav.to_numpy(float)]
                ret = wealth[-1] / wealth[0] - 1
                equal(row.economic_return, ret, model + ': period return', atol=1e-10)
                equal(row.economic_max_drawdown, -(wealth / np.maximum.accumulate(wealth) - 1).min(), model + ': period MDD', atol=1e-10)
                check(row.sessions == len(group) and row.through == group.date.iloc[-1], filename + ': period boundaries')
                previous = wealth[-1]
                returns.append(ret)
                checked += 1
            equal(np.prod(1 + np.array(returns)), previous / initial, model + ': compounded period returns', atol=1e-10)
        check(len(table) == checked, filename + ': extraneous groups')
        counts[filename] = checked
    summary = read_csv(output / 'summary.csv').set_index('model')
    check(set(summary.index) == set(MODELS) and not summary.index.duplicated().any(), 'Summary model identity mismatch')
    curves = read_csv(output / 'economic_nav_comparison.csv')
    check(set(curves.columns) == {'date', *MODELS}, 'Curve model identity mismatch')
    max_curve_error = 0.
    blocks = read_csv(output / 'blocks_24_sessions.csv')
    checked_blocks = 0
    for model, eq in frames.items():
        row = summary.loc[model]
        metrics = equity_metrics(eq, initial)
        compare_metrics(row, metrics, model + ': summary',
                        require=('economic_total_return', 'economic_max_drawdown', 'hard_breach_days',
                                 'infeasible_executed_days', 'trade_days', 'costs'))
        elapsed = (pd.Timestamp(eq.date.iloc[-1]) - pd.Timestamp(config['start']) + pd.Timedelta(days=1)).days
        equal(row.calendar_cagr, (eq.economic_nav.iloc[-1] / initial) ** (365.25 / elapsed) - 1, model + ': CAGR', atol=1e-10)
        equal(row.average_stock_exposure, (1 - eq.cash_ratio).mean(), model + ': stock exposure', atol=1e-10)
        for field, expected in [('cash_ratio_max', eq.cash_ratio.max()), ('cash_ratio_min', eq.cash_ratio.min()),
                                ('holdings_min', eq.holdings.min()), ('holdings_max', eq.holdings.max())]:
            equal(row[field], expected, model + ': ' + field, atol=1e-10)
        expected_dates = [str((pd.Timestamp(config['start']) - pd.Timedelta(days=1)).date()), *eq.date.tolist()]
        check(curves.date.tolist() == expected_dates, 'Missing initial wealth/shared plotted calendar')
        expected = np.r_[initial, eq.economic_nav.to_numpy(float)]
        error = np.abs(curves[model].to_numpy(float) - expected)
        check((error <= 4 * np.spacing(np.maximum(np.abs(expected), 1.))).all(), model + ': plotted wealth mismatch')
        max_curve_error = max(max_curve_error, float(error.max()))
        previous = initial
        for n, start in enumerate(range(0, len(eq), 24), 1):
            group = eq.iloc[start:start + 24]
            row = blocks[(blocks.model == model) & (blocks.block == n)]
            check(len(row) == 1, 'Missing/duplicate 24-session block')
            row = row.iloc[0]
            wealth = np.r_[previous, group.economic_nav.to_numpy(float)]
            equal(row.return_net, wealth[-1] / wealth[0] - 1, model + ': block return', atol=1e-10)
            equal(row.mdd, -(wealth / np.maximum.accumulate(wealth) - 1).min(), model + ': block MDD', atol=1e-10)
            check(row.sessions == len(group) and bool(row.complete) == (len(group) == 24), 'Block completeness')
            check(row.start == group.date.iloc[0] and row.end == group.date.iloc[-1], 'Block dates')
            previous = wealth[-1]
            checked_blocks += 1
    check(len(blocks) == checked_blocks, 'Extraneous blocks')
    counts.update(summary_rows=len(summary), curve_rows=len(curves), block_rows=checked_blocks,
                  max_curve_csv_roundtrip_error=max_curve_error)
    return counts


def audit_sector_report(output):
    """The disclosed concentration statistic is conditional on known taxonomy."""
    labels = read_csv(output / 'B/signals.csv')[['date', 'symbol', 'sector_id']].drop_duplicates(['date', 'symbol'])
    saved = read_csv(output / 'sector_exposure.csv')
    summary = read_csv(output / 'summary.csv').set_index('model')
    expected_rows = 0
    for model in MODELS:
        holdings = read_csv(output / model / 'holdings.csv')
        tagged = holdings.merge(labels, on=['date', 'symbol'], how='left', validate='many_to_one')
        tagged['sector_id'] = tagged.sector_id.fillna('UNKNOWN')
        weights = tagged.groupby(['date', 'sector_id']).weight.sum().sort_index()
        actual = saved[saved.model == model].set_index(['date', 'sector_id']).weight.sort_index()
        pd.testing.assert_series_equal(actual, weights, check_exact=False, rtol=1e-11, atol=1e-12)
        coverage, hhi, maximum = [], [], []
        for _, group in weights.reset_index().groupby('date'):
            known = group[group.sector_id.ne('UNKNOWN')].weight
            total = known.sum()
            coverage.append(total)
            if total > 0:
                hhi.append(((known / total) ** 2).sum())
                maximum.append(known.max())
        equal(summary.loc[model, 'mean_classified_nav_weight'], np.mean(coverage), model + ': classified exposure', atol=1e-10)
        if hhi:
            equal(float(summary.loc[model, 'known_sector_hhi_mean']), np.mean(hhi), model + ': conditional sector HHI', atol=1e-10)
            equal(float(summary.loc[model, 'max_known_sector_weight']), max(maximum), model + ': known sector cap', atol=1e-10)
        else:
            check(summary.loc[model, 'known_sector_hhi_mean'] == '' and summary.loc[model, 'max_known_sector_weight'] == '',
                  model + ': missing taxonomy reported as numeric sector concentration')
        equal(summary.loc[model, 'max_single_weight'], holdings.weight.max(), model + ': maximum single-stock weight', atol=1e-10)
        expected_rows += len(weights)
    check(len(saved) == expected_rows, 'Extraneous sector exposure rows')
    return expected_rows


def audit(output):
    manifest = json.loads((output / 'provenance.json').read_text())
    check(manifest.get('outputs_complete') is True and manifest.get('completed_at'), 'Pinned-model study is incomplete')
    check(manifest.get('selected_candidates') == PINNED, 'Selected candidates differ from user-approved rule-first winners')
    check(manifest.get('selection_scope') == 'EX_POST_DEVELOPMENT', 'Ex-post development selection not disclosed')
    check(manifest.get('historical_period_is_development') is True, 'Inspected history mislabeled unseen')
    tuning_audit = json.loads((TUNING / 'tuning_audit.json').read_text())
    check(tuning_audit['status'] == 'PASS', 'Source tuning study is not verified')
    check({s: tuning_audit['ex_post_diagnostics'][s]['primary_best'] for s in PINNED} == PINNED,
          'Pinned choices are not the verified rule-first selections')
    previous_manifest = json.loads((TUNING / 'tuning_manifest.json').read_text())
    for relative, expected in manifest['hashes'].items():
        check(sha(ROOT / relative) == expected, 'Current source/input changed: ' + relative)
        check(sha(output / 'input_snapshot' / relative) == expected, 'Source snapshot mismatch: ' + relative)
    for relative, expected in previous_manifest['hashes'].items():
        check(manifest['hashes'].get(relative) == expected, 'Frozen tuning dependency was altered or omitted: ' + relative)
    config = manifest['config']
    daily = pd.read_csv(output / 'input_snapshot/data/v2/market_daily.csv', low_memory=False)
    daily['date'] = pd.to_datetime(daily.date)
    check(not daily.duplicated(['date', 'symbol']).any(), 'Duplicate canonical daily observations')
    calendar = sorted(pd.Timestamp(x) for x in daily.date.unique()
                      if pd.Timestamp(config['start']) <= pd.Timestamp(x) <= pd.Timestamp(config['end']))
    universe = read_csv(output / 'input_snapshot/data/extended/processed/universe_20241231.csv')
    market = daily[['date', 'symbol', 'turnover', 'execution_volume']].copy()
    market['date'] = market.date.dt.strftime('%Y-%m-%d')
    replays, results, frames = {}, {}, {}
    stock_inputs = ledger_inputs(daily, calendar, universe)
    for model in MODELS:
        print('Auditing pinned comparison ' + model, flush=True)
        folder = output / model
        reference = TUNING / 'ex_post_best' / model if model in PINNED else OLD / model
        replays[model] = exact_replay(folder, reference)
        settings = json.loads((folder / 'config.json').read_text())
        prior_settings = json.loads((reference / 'config.json').read_text())
        if model in PINNED:
            check(settings['tuning_params'] == prior_settings['tuning_params'], model + ': pinned parameters changed')
            check(settings['max_replacements_per_day'] == (1 if model == 'C' else 0), model + ': incorrect replacement budget')
            check(settings['allocation_mode'] == 'local', model + ': incorrect allocation mode')
        for key, value in prior_settings.items():
            if key == 'study_policy' and model in PINNED:
                policy = settings[key]
                check(policy.get('parameter_search') is False and policy.get('selected_from_parameter_search') is True,
                      model + ': pinned replay search metadata is misleading')
                check(policy.get('selection_scope') == 'EX_POST_DEVELOPMENT'
                      and policy.get('historical_period_is_development') is True
                      and policy.get('official_live_submission') == 'BLOCK_IF_UNKNOWN', model + ': unsafe policy metadata')
            elif key != 'strategy_id':
                check(settings.get(key) == value, model + ': original selected/benchmark setting changed: ' + key)
        _, rowwise = audit_model(output, model, daily, calendar, config)
        frames[model] = read_csv(folder / 'equity.csv')
        trade_count = trial_trade_audit(folder / 'trades.csv', frames[model], market, settings)
        if model == '0050':
            inputs = ledger_inputs(daily, calendar, pd.DataFrame({'symbol': ['0050.TW']}))
            # A one-asset pivot lacks rows for its trading suspension. Keep the
            # shared market calendar and carry its last mark across those rows.
            inputs['close'] = pd.DataFrame(inputs['close']).ffill().to_numpy(float)
        else:
            inputs = stock_inputs
        vector_error = trial_ledger_audit(folder / 'trades.csv', frames[model], inputs, settings)
        metrics = equity_metrics(frames[model], config['initial_cash'])
        saved_metrics = json.loads((folder / 'metrics.json').read_text())
        compare_metrics(saved_metrics, metrics, model + ': additional metrics',
                        require=('hard_breach_days', 'infeasible_executed_days', 'trade_days', 'costs'))
        equal(saved_metrics['trades'], trade_count, model + ': trade count', atol=0)
        results[model] = {**rowwise, 'max_vector_ledger_error': vector_error,
                          'hard_breach_days': metrics['hard_breach_days'],
                          'infeasible_executed_days': metrics['infeasible_executed_days']}
    aggregate_rows = audit_aggregates(output, frames, config)
    aggregate_rows['sector_exposure_rows'] = audit_sector_report(output)
    ab_identical = all((output / 'A' / f'{name}.csv').read_bytes() == (output / 'B' / f'{name}.csv').read_bytes()
                       for name in ('equity', 'orders', 'trades', 'holdings'))
    check(ab_identical, 'Previously identical pinned A/B portfolios diverged')
    return dict(status='PASS', scope='ACCOUNTING_EXACT_REPLAY_AND_REPORT_ARITHMETIC_NOT_OOS_OR_CONTEST_CERTIFICATION',
                verified_at=datetime.now(timezone.utc).isoformat(), auditor_sha256=sha(__file__),
                audit_dependencies={name: sha(Path(__file__).with_name(name)) for name in ('audit_v2_study.py', 'audit_v2_tuning.py')},
                selected_candidates=PINNED, input_and_code_hashes=len(manifest['hashes']),
                exact_replays=replays, models=results, aggregate_rows=aggregate_rows,
                A_B_portfolios_identical=ab_identical,
                limitations=['The pinned v2 models were selected on this already inspected development history; this rerun adds no unseen-period evidence.',
                             'v2 received a 64-candidate-per-strategy search; matched v1 and 0050 were fixed controls without equal tuning.',
                             'A/B versus matched v1 changes both allocation and voluntary replacement policy; C versus A/B also changes its replacement budget.',
                             '0050 is a contextual single-ETF benchmark, not a contest-compliant 20-to-30-stock portfolio.',
                             'Official historical whitelist/Active Share and source publication vintages remain unverified; all submissions stay blocked.',
                             'Known-sector concentration is conditional on incomplete historical classifications, and actual invested exposure differs.',
                             'VWAP full fills are the research/contest assumption; capacity statistics do not certify live execution.'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='outputs/v2_best_final_20260922')
    args = parser.parse_args()
    output = ROOT / args.output
    try:
        result = audit(output)
    except Exception as error:
        result = dict(status='FAIL', error=str(error), auditor_sha256=sha(__file__))
        if output.exists():
            (output / 'audit.json').write_text(json.dumps(result, indent=2) + '\n')
        raise
    (output / 'audit.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))
