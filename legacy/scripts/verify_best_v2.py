"""Verify the compact x0352 release without its discarded search history.

A full rebuild reconstructs every retained double-check ledger from raw prices.
The default checks the immutable files and the resulting deterministic audit.
The two annual baselines receive equity-return checks, not a ledger audit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_double_check import audit_result

MANIFEST = 'config/best_v2_release.json'
AUDIT = 'outputs/best_v2/audit.json'
TRACKS = ('official_ex_post', 'historical_pit')
CANDIDATES = ('x0352', 'x0454', 'mx0010')
SCOPE = 'COMPACT_SELECTED_REPLAY_AND_RETAINED_COMPARISONS'
TABLES = ('equity', 'orders', 'trades', 'holdings', 'warnings', 'snapshots',
          'plan_audit', 'compliance_daily', 'rejected_trades')
PAYLOAD = {name + '.csv' for name in TABLES} | {'config.json', 'metrics.json', 'independent_audit.json'}
ZERO = ('measured_hard_breach_days', 'no_valid_plan_days', 'unfilled_orders',
        'simulated_warning_days', 'stale_held_price_days', 'hold_without_envelope_days',
        'execution_price_bound_breaches', 'raw_rule_breach_days')
PARAMS = dict(target_count=20, replacement_margin=.2, max_replacements_per_day=0,
              volatility_spike_ratio=2., one_day_chase_return=.04, volume_low=.8,
              volume_high=2.5, four_hour_mode='coverage_only', return_short=10,
              return_long=30, ema_fast=10, ema_slow=30, macd_fast=8, macd_slow=21,
              macd_signal=5, momentum_weight=.55, long_return_fraction=.2,
              cash_guard_ratio=.12)
EPISODES = ('contest_2025', 'full') + tuple(
    'm25_' + str(month) for month in pd.period_range('2025-01', '2026-08', freq='M'))
RUNTIME_FILES = {
    'scripts/audit_double_check.py', 'src/replay_context.py',
    *(f'src/{name}.py' for name in ('backtest', 'backtest_v2', 'compliance_planner',
      'double_check_ledger', 'double_check_tuning', 'official_deep_tuning',
      'official_v2_review', 'tuning_2nd', 'tuning_a_deep', 'tuning_features', 'tuning_signals')),
    'config/strategy_v2.json', 'config/v2_tuning_study.json', 'config/v2_auxiliary_signals.json',
    'data/v2/market_daily.csv', 'data/extended/processed/hourly_canonical.csv',
    'data/extended/processed/universe_20241231.csv',
    *(f'data/tuning_2nd/official_universe/processed/{name}' for name in
      ('daily.csv', 'hourly.csv', 'universe.csv', 'readiness.json')),
    'data/sector/industry_history.csv', 'data/sector/sector_index_daily.csv',
    'data/v2_auxiliary/overnight_daily.csv', 'data/v2_auxiliary/source_status.json',
    'best_v2.py', 'scripts/verify_best_v2.py', 'tests/test_best_v2_release.py',
    'outputs/comparisons/monthly.csv', 'outputs/comparisons/annual.csv',
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def same(actual, expected, label):
    if isinstance(expected, dict):
        require(isinstance(actual, dict) and set(actual) == set(expected), label + ': keys differ')
        for key in expected:
            same(actual[key], expected[key], label + '.' + key)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(actual) == len(expected), label + ': length differs')
        for index, value in enumerate(expected):
            same(actual[index], value, label + ':' + str(index))
    elif isinstance(expected, bool) or expected is None:
        require(actual is expected, label + ': boolean/null differs')
    elif isinstance(expected, (int, float)):
        require(isinstance(actual, (int, float)) and not isinstance(actual, bool)
                and math.isfinite(actual) and math.isfinite(expected)
                and math.isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-7),
                label + ': number differs')
    else:
        require(actual == expected, label + ': value differs')


def safe_path(root, relative):
    require(isinstance(relative, str), 'Manifest path must be text')
    path = Path(relative)
    require(not path.is_absolute() and '..' not in path.parts and path.as_posix() == relative,
            'Unsafe manifest path: ' + relative)
    result = (root / path).resolve()
    require(root.resolve() in result.parents, 'Escaping manifest path: ' + relative)
    return result


def verify_hashes(root, hashes, required=()):
    require(isinstance(hashes, dict) and set(required) <= set(hashes), 'Incomplete hash manifest')
    for relative, expected in hashes.items():
        path = safe_path(Path(root), relative)
        require(isinstance(expected, str) and len(expected) == 64 and
                all(c in '0123456789abcdef' for c in expected), 'Malformed digest: ' + relative)
        require(path.is_file() and sha(path) == expected, 'Hash mismatch: ' + relative)


def verify_receipt(folder, payload):
    require(not (folder / 'failure.json').exists(), 'Failed artifact in release: ' + str(folder))
    receipt = read(folder / 'receipt.json')
    require(set(receipt) == payload, 'Incomplete result receipt: ' + str(folder))
    verify_hashes(folder, receipt, payload)


def selected_config(config, track):
    require(config.get('tuning_candidate_id') == 'x0352', 'Selected candidate differs')
    require(config.get('universe_mode') == track, 'Selected track differs')
    require(config.get('research_shadow') is True and
            config.get('official_review_policy', {}).get('active_share') == 'UNKNOWN_BLOCK_SUBMISSION',
            'Selected submission gate differs')
    same(config.get('full_tuning_params'), PARAMS, 'Selected x0352 parameters')
    require(config.get('start') == '2025-01-01' and config.get('end') == '2026-09-21',
            'Selected period differs')


def run_folders(root):
    for track in TRACKS:
        yield track, 'x0352', 'selected', root / 'outputs/best_v2' / track
        for candidate in CANDIDATES:
            for episode in EPISODES:
                yield track, candidate, episode, root / 'outputs/comparisons/monthly' / track / candidate / episode


def verify_files(root=ROOT):
    root = Path(root)
    manifest = read(root / MANIFEST)
    require(manifest.get('schema_version') == 1 and manifest.get('candidate_id') == 'x0352'
            and manifest.get('strategy_id') == 'best_finetuned_double_check_v2'
            and manifest.get('submission_status') == 'BLOCK_SUBMISSION'
            and manifest.get('scope') == SCOPE, 'Release identity/scope/submission gate differs')
    require(isinstance(manifest.get('source_commit'), str) and len(manifest['source_commit']) == 40,
            'Missing original source commit')
    hashes = manifest.get('files')
    required = set(RUNTIME_FILES)
    for track, candidate, episode, folder in run_folders(root):
        payload = PAYLOAD | ({'signals.csv'} if episode == 'selected' else set())
        required.update(str((folder / name).relative_to(root)) for name in payload | {'receipt.json'})
    for model in ('v1', '0050'):
        required.update(f'outputs/comparisons/annual/{model}/{name}'
                        for name in ('config.json', 'equity.csv', 'metrics.json'))
    require(isinstance(hashes, dict) and MANIFEST not in hashes and AUDIT not in hashes,
            'Manifest cannot hash itself or its audit')
    verify_hashes(root, hashes, required)
    for track, candidate, episode, folder in run_folders(root):
        payload = PAYLOAD | ({'signals.csv'} if episode == 'selected' else set())
        verify_receipt(folder, payload)
        config = read(folder / 'config.json')
        require(config.get('tuning_candidate_id') == candidate and config.get('universe_mode') == track,
                'Result identity differs: ' + str(folder))
        if episode == 'selected':
            selected_config(config, track)
    return manifest


def load_result(folder):
    result = {name: pd.read_csv(folder / (name + '.csv'), keep_default_na=False,
                               float_precision='round_trip') for name in TABLES}
    result.update(config=read(folder / 'config.json'), metrics=read(folder / 'metrics.json'))
    return result


def measured_eligible(audit):
    finite = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return (audit.get('independent_audit') == 'PASS' and audit.get('complete_period') is True
            and audit.get('disqualified') is False
            and all(finite(audit.get(key)) and audit[key] == 0 for key in ZERO)
            and all(finite(audit.get(key)) for key in ('total_return', 'max_drawdown', 'turnover_two_way'))
            and audit.get('official_compliance') == 'UNKNOWN_BLOCK_SUBMISSION')


def _annual(root):
    rows = []
    for model in ('x0352', 'v1', '0050'):
        folder = (root / 'outputs/best_v2/official_ex_post' if model == 'x0352' else
                  root / 'outputs/comparisons/annual' / model)
        cfg, metrics = read(folder / 'config.json'), read(folder / 'metrics.json')
        require(isinstance(metrics, dict) and bool(metrics), 'Missing annual metrics')
        eq = pd.read_csv(folder / 'equity.csv', keep_default_na=False, float_precision='round_trip')
        dates = pd.to_datetime(eq.date)
        require(dates.is_monotonic_increasing and dates.is_unique and len(eq) == 417 and
                str(dates.iloc[0].date()) == '2025-01-02' and str(dates.iloc[-1].date()) == '2026-09-21',
                'Annual equity calendar differs: ' + model)
        wealth = eq['economic_nav'] if 'economic_nav' in eq else eq['nav']
        require(all(math.isfinite(float(v)) and v > 0 for v in wealth), 'Invalid annual wealth')
        previous = float(cfg['initial_cash'])
        for period, year in (('2025', 2025), ('2026_YTD', 2026)):
            ending = float(wealth[dates.dt.year == year].iloc[-1])
            rows.append(dict(model=model, period=period, return_economic=ending / previous - 1))
            previous = ending
    actual = pd.read_csv(root / 'outputs/comparisons/annual.csv', dtype={'model': str, 'period': str},
                         keep_default_na=False, float_precision='round_trip')
    require(set(actual.columns) == {'model', 'period', 'return_economic'}, 'Annual comparison columns differ')
    same(sorted(actual.to_dict('records'), key=lambda r: (r['model'], r['period'])),
         sorted(rows, key=lambda r: (r['model'], r['period'])), 'Annual returns')
    return rows


def _summary(root, reconstruct=False):
    contexts = {}
    if reconstruct:
        from src.tuning_a_deep import context
        contexts = {track: context(track) for track in TRACKS}
    selected, monthly = {}, []
    frame = pd.read_csv(root / 'outputs/comparisons/monthly.csv', keep_default_na=False,
                        float_precision='round_trip')
    require(len(frame) == 132 and not frame.duplicated(['track', 'candidate_id', 'episode']).any(),
            'Monthly comparison identity/count differs')
    rows = frame.to_dict('records')
    # CSV columns containing nullable Sharpes otherwise become strings in pandas.
    for row in rows:
        for key in ('sharpe_zero_rf', 'economic_sharpe_zero_rf'):
            row[key] = None if row[key] == '' else float(row[key])
    actual_rows = {(r['track'], r['candidate_id'], r['episode']): r for r in rows}
    for track, candidate, episode, folder in run_folders(root):
        cfg = read(folder / 'config.json')
        frozen = read(folder / 'independent_audit.json')
        if reconstruct:
            audit = audit_result(load_result(folder), contexts[track])
            same(audit, frozen, 'Independent reconstruction: ' + str(folder))
        else:
            audit = frozen
        require(audit.get('independent_audit') == 'PASS' and
                audit.get('official_compliance') == 'UNKNOWN_BLOCK_SUBMISSION', 'Audit gate differs')
        if episode == 'selected':
            require(measured_eligible(audit) and audit['sessions'] == 417, 'Selected model is ineligible/incomplete')
            selected[track] = audit
        else:
            kind = ('continuous' if episode == 'full' else 'reset_calendar_analogue'
                    if episode == 'contest_2025' else 'reset_25_sessions')
            row = dict(track=track, candidate_id=candidate, episode=episode, kind=kind,
                       start=cfg['start'], end=cfg['end'], **audit, research_eligible=measured_eligible(audit))
            same(actual_rows.get((track, candidate, episode)), row, 'Monthly comparison ' + '/'.join((track, candidate, episode)))
            monthly.append(row)
    require(math.isclose(selected['official_ex_post']['total_return'], 3.0915579554583044,
                         rel_tol=0, abs_tol=1e-12), 'Selected 309.16% result differs')
    return dict(schema_version=1, status='PASS', scope=SCOPE, submission_status='BLOCK_SUBMISSION',
                candidate_id='x0352', manifest_sha256=sha(root / MANIFEST),
                selected_count=2, monthly_count=132, selected=selected, monthly=monthly,
                annual=_annual(root),
                annual_validation='SAVED_EQUITY_RETURNS_ONLY_BASELINE_LEDGERS_NOT_REAUDITED')


def verify_release(root=ROOT):
    root = Path(root)
    verify_files(root)
    expected = _summary(root)
    same(read(root / AUDIT), expected, 'Sealed release audit')
    return expected


def rebuild(root=ROOT, *, write=False):
    root = Path(root)
    verify_files(root)
    audit = _summary(root, reconstruct=True)
    target = root / AUDIT
    if target.exists():
        same(read(target), audit, 'Rebuilt release audit')
    elif write:
        target.write_text(json.dumps(audit, indent=2, sort_keys=True, allow_nan=False) + '\n')
    else:
        raise ValueError('Missing sealed audit; use --write for the initial independent reconstruction')
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--write', action='store_true', help='Reconstruct and create the immutable compact audit')
    modes.add_argument('--rebuild', action='store_true', help='Reconstruct and compare the existing compact audit')
    args = parser.parse_args()
    audit = rebuild(write=args.write) if args.write or args.rebuild else verify_release()
    print(json.dumps({k: audit[k] for k in ('status', 'candidate_id', 'selected_count', 'monthly_count', 'submission_status')}))


if __name__ == '__main__':
    main()
