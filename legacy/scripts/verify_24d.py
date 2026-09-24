#!/usr/bin/env python3
"""Offline integrity, denominator and freeze verification for a completed 24D study.

Default verifies saved independent audit receipts; --rebuild reconstructs every
saved ledger using audit_24d (no strategy execution, tuning or network access).
Hashes establish artifact consistency, not historical vendor or official accuracy.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.audit_24d import (audit_attempt_coverage, audit_episode_registry,
                               audit_saved_group, audit_selection_provenance)

TABLES = ('equity', 'trades', 'orders', 'holdings', 'compliance_daily',
          'rejected_trades', 'warnings', 'snapshots', 'plan_audit')
ARTIFACTS = {'metrics.csv', 'audit.csv', 'serialized_audit.csv', 'config.json'} | {
    name + '.parquet' for name in TABLES}
AUDIT_STATUSES = {'PASS_INTERNAL_ACCOUNTING', 'NO_LEDGER_FAILURE_RETAINED'}
DELIVERY_REQUIRED = {
    'scripts/verify_24d.py', 'tests/test_verify_24d.py',
    'scripts/report_24d.py', 'tests/test_report_24d.py',
    'configs/competition_24d_final_metadata.json',
    'scripts/supplement_24d.py', 'tests/test_24d_supplement.py',
    'outputs/24d_supplement/supplement_manifest.json',
    'outputs/24d_supplement/supplement_audit.json',
    'outputs/24d/monthly_episodes.csv', 'outputs/24d/rolling_episodes.csv',
    'outputs/24d/oct_nov_episodes.csv', 'outputs/24d/recent_regime.csv',
    'outputs/24d/canonical_aliases.json',
    'scripts/complete_24d_diagnostics.py', 'tests/test_complete_24d_diagnostics.py',
    'outputs/24d_diagnostics/diagnostics_audit.json',
    'outputs/24d_diagnostics/diagnostics_manifest.json',
} | {'reports/24d_' + name + '.md' for name in [
    'strategy_summary', 'x0352_baseline', 'parameter_search', 'validation',
    'holdout', 'recent_regime', 'oct_nov_analogs', 'failure_analysis', 'final_candidate']}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    checksum = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text())


def read_csv(path):
    return pd.read_csv(path, float_precision='round_trip')


def local_cache(root, recorded_path):
    """The launch path is provenance; verification reads this checkout's snapshot."""
    name = Path(recorded_path).name
    require(name not in {'', '.', '..'}, 'Invalid recorded cache directory')
    cache = inside(root, 'data/yahoo_daily/' + name)
    require(cache.is_dir(), 'Local immutable data snapshot is missing: ' + str(cache))
    return cache


def inside(root, name):
    path = Path(root) / name
    require(not Path(name).is_absolute() and path.resolve().is_relative_to(Path(root).resolve()),
            'Unsafe artifact path: ' + str(name))
    return path


def verify_hash_map(root, hashes):
    require(bool(hashes), 'Empty hash map: ' + str(root))
    for name, expected in hashes.items():
        path = inside(root, name)
        require(path.is_file(), 'Missing artifact: ' + str(path))
        require(sha256(path) == expected, 'Hash mismatch: ' + str(path))


def require_receipt_sealed(relative, receipt, output_hashes):
    prefix = str(Path(relative).parent)
    required = {relative} | {prefix+'/'+name for name in receipt['artifact_sha256']}
    require(required <= set(output_hashes), 'Final artifact seal omits receipt or its evidence: ' + relative)


def same_frame(actual, expected, keys, label):
    require(set(actual.columns) == set(expected.columns), label + ': columns differ')
    try:
        pd.testing.assert_frame_equal(
            actual.sort_values(keys).reset_index(drop=True)[sorted(actual.columns)],
            expected.sort_values(keys).reset_index(drop=True)[sorted(expected.columns)],
            check_dtype=False, check_exact=False, rtol=1e-11, atol=1e-10)
    except AssertionError as error:
        raise ValueError(label + ': saved rows differ: ' + str(error)[:350]) from error


def expected_registries(calendar, study):
    require(calendar == sorted(set(calendar)), 'Calendar is not unique and ordered')
    positions = {date: index for index, date in enumerate(calendar)}
    rows, recent = [], []
    def add(kind, start, destination):
        index = positions[start]
        require(index > 0 and index + 24 <= len(calendar), 'Incomplete registered window: ' + start)
        end = calendar[index + 23]
        split = next((key for key, (lo, hi) in study['splits'].items()
                      if lo <= start <= end <= hi),
                     'purged' if '2010-01-01' <= start <= '2024-12-31' else 'outside')
        destination.append(dict(episode_id=('recent' if kind == 'recent_monthly' else kind) + '_' + start,
            kind=kind, start=start, end=end, split=split, session_count=24,
            prior_session_date=calendar[index - 1]))
    for month in pd.period_range('2010-01', '2024-12', freq='M').astype(str):
        found = [day for day in calendar if day.startswith(month)]
        require(bool(found), 'Missing primary month: ' + month)
        add('monthly', found[0], rows)
    for year in range(2010, study['seasonal_end_year'] + 1):
        found = [day for day in calendar if f'{year}-10-26' <= day <= f'{year}-12-31']
        require(bool(found), 'Missing seasonal year: ' + str(year))
        add('oct_nov', found[0], rows)
    for day in calendar:
        if '2010-01-01' <= day <= '2024-12-31':
            add('rolling', day, rows)
    for month in sorted({day[:7] for day in calendar if day >= '2025-01-01'}):
        start = next(day for day in calendar if day.startswith(month))
        if positions[start] + 24 <= len(calendar):
            add('recent_monthly', start, recent)
    return pd.DataFrame(rows), pd.DataFrame(recent)


def ranking(rows):
    """Recompute selection statistics from the complete attempt denominator."""
    records = []
    for candidate, frame in rows.groupby('candidate_id'):
        require(frame.measured_pass.isin([True, False]).all(), 'Invalid measured pass values')
        require(frame.complete_period.isin([True, False]).all(), 'Invalid completion values')
        valid = frame[frame.measured_pass & frame.complete_period & frame.episode_return.notna()]
        records.append(dict(candidate_id=candidate, compliance_pass_rate=frame.measured_pass.mean(),
            valid_episode_rate=frame.complete_period.mean(), median_24d_return=valid.episode_return.median(),
            p25_24d_return=valid.episode_return.quantile(.25), median_mdd=valid.episode_max_drawdown.median(),
            mean_24d_return=valid.episode_return.mean(), median_turnover=valid.episode_turnover.median()))
    return pd.DataFrame(records).sort_values(
        ['compliance_pass_rate', 'valid_episode_rate', 'median_24d_return', 'p25_24d_return',
         'median_mdd', 'mean_24d_return', 'median_turnover', 'candidate_id'],
        ascending=[False, False, False, False, True, False, True, True], na_position='last').reset_index(drop=True)


def verify_summary(saved, rows):
    expected = ranking(rows)
    same_frame(saved[list(expected.columns)], expected, ['candidate_id'], 'Summary ranking statistics')
    indexed = saved.set_index('candidate_id')
    for candidate, group in rows.groupby('candidate_id'):
        record = indexed.loc[candidate]
        valid = group.measured_pass & group.complete_period & group.episode_return.notna()
        for field, value in dict(attempted=len(group), passed=group.measured_pass.sum(),
                                 completed=group.complete_period.sum(), failed=(~valid).sum()).items():
            require(record[field] == value, 'Summary denominator differs: ' + field)
        require(record.failed_episode_penalty_return == -1., 'Failure penalty changed')
        penalized = group.episode_return.where(valid, -1.)
        for field, value in dict(penalized_mean_return=penalized.mean(),
                                 penalized_median_return=penalized.median(),
                                 penalized_p25_return=penalized.quantile(.25)).items():
            require(abs(record[field] - value) < 1e-11, 'Summary penalty differs: ' + field)


def verify_freeze(root, output, selection, registry, candidates, development, validation):
    started = read_json(output / 'holdout_started.json')
    require(started['frozen_selection_sha256'] == sha256(output / 'final_selection.json'),
            'Holdout selection digest differs')
    provenance = audit_selection_provenance(selection, registry, candidates, root=root,
                                          holdout_started_at=started['started_at'])
    require(set(selection['selection_episode_ids']) == set(development.episode_id) | set(validation.episode_id),
            'Selection episode provenance omits or adds attempts')
    monthly = registry[registry.kind.eq('monthly')]
    require(set(selection['holdout_episode_ids']) == set(monthly.loc[monthly.split.eq('holdout'), 'episode_id']),
            'Frozen holdout episode set differs')
    require(selection['number_of_candidates_evaluated'] == len(candidates), 'Frozen candidate count differs')
    required_inputs = {str((output / name).relative_to(root)) for name in
                       ['development.csv', 'validation.csv', 'candidates.csv', 'episodes.csv']}
    require(required_inputs <= set(selection['selection_input_hashes']), 'Missing frozen selection input hashes')
    dev_rank, val_rank = ranking(development), ranking(validation)
    eligible = set(dev_rank.loc[dev_rank.compliance_pass_rate.eq(1), 'candidate_id']) & set(
        val_rank.loc[val_rank.compliance_pass_rate.eq(1), 'candidate_id'])
    admissible = val_rank[val_rank.candidate_id.isin(eligible)]
    baseline = read_json(output / 'study_manifest.json')['guard']['study']['baseline_id']
    expected = str(admissible.iloc[0].candidate_id) if len(admissible) else baseline
    require(selection['candidate_id'] == expected, 'Frozen candidate violates selection ranking/gate')
    require(selection['decision'] == ('FROZEN_MEASURED_CANDIDATE' if len(admissible) else 'NO_ELIGIBLE_CANDIDATE'),
            'Frozen admissibility decision differs')
    best = str(val_rank.iloc[0].candidate_id)
    require(selection['research_best_candidate_id'] == best, 'Research challenger ranking differs')
    comparisons = list(dict.fromkeys([baseline, expected] + ([] if len(admissible) else [best])))
    require(selection['comparison_candidate_ids'] == comparisons, 'Frozen comparison membership differs')
    selected = output / 'configs' / (expected + '.json')
    require(sha256(selected) == selection['candidate_config_sha256'], 'Central selected config differs')
    frozen = dict(read_json(selected), selection_provenance=selection)
    require(read_json(output / 'frozen_candidate.json') == frozen, 'Frozen candidate content differs')
    if output.resolve() == (root / 'outputs/24d').resolve():
        require(read_json(root / 'configs/competition_24d_final.json') == frozen,
                'Canonical final candidate differs from immutable freeze')
    return provenance, pd.Timestamp(started['started_at'])


def verify_group(output, relative, receipt, expected_episodes, candidate, phase, guard_sha):
    folder = inside(output, relative).parent
    require(read_json(folder / 'receipt.json') == receipt, 'Manifest receipt differs: ' + relative)
    require(receipt['candidate_id'] == candidate and receipt['phase'] == phase, 'Receipt identity differs')
    require(receipt['episodes'] == len(expected_episodes), 'Receipt attempt count differs')
    require(set(receipt['artifact_sha256']) == ARTIFACTS, 'Receipt omits required artifacts')
    verify_hash_map(folder, receipt['artifact_sha256'])
    config = read_json(folder / 'config.json')
    require(config == read_json(output / 'configs' / (candidate + '.json')), 'Central/group config differs')
    require(receipt['input_sha256'] == digest(dict(study=guard_sha, phase=phase, config=config,
                                                episodes=expected_episodes.to_dict('records'))),
            'Group input digest differs: ' + relative)
    rows = read_csv(folder / 'metrics.csv')
    audit_attempt_coverage(rows, expected_episodes.episode_id, [candidate])
    require(rows.phase.eq(phase).all(), 'Group phase differs')
    same_frame(rows[list(expected_episodes.columns)], expected_episodes, ['episode_id'], 'Group registry')
    for name in ['audit.csv', 'serialized_audit.csv']:
        audited = read_csv(folder / name)
        require(audited.episode_id.is_unique and set(audited.episode_id) == set(rows.episode_id),
                'Serialized audit omits or duplicates attempts')
        require(audited.independent_audit.isin(AUDIT_STATUSES).all(), 'Saved independent audit failed')
        for column, value in [('active_share_status', 'ACTIVE_SHARE_NOT_VERIFIED'),
                              ('ready_status', 'BLOCK_READY')]:
            require(audited[column].eq(value).all(), 'Saved audit falsely certifies ' + column)
        common = ['episode_id', 'episode_return', 'measured_pass', 'complete_period', 'independent_audit']
        same_frame(audited[common], rows[common], ['episode_id'], name)
        normal = audited.independent_audit.eq('PASS_INTERNAL_ACCOUNTING')
        if normal.any():
            require(audited.loc[normal, 'official_compliance'].eq('UNKNOWN_BLOCK_SUBMISSION').all(),
                    'Saved audit falsely certifies official compliance')
            economic = ['episode_id', 'forensic_partial_return', 'episode_max_drawdown', 'episode_turnover']
            same_frame(audited.loc[normal, economic], rows.loc[rows.episode_id.isin(audited.loc[normal, 'episode_id']), economic],
                       ['episode_id'], name + ' accounting')
    return rows, config


def verify_receipt_set(output, receipts, expected):
    on_disk = {str(path.relative_to(output)) for path in (output / 'ledgers').glob('*/*/receipt.json')}
    require(set(receipts) == set(expected), 'Missing or unexpected completed groups in manifest')
    require(on_disk == set(expected), 'Missing or unexpected completed groups on disk')


def verify_delivery(root, output):
    path = output / 'delivery_manifest.json'
    if not path.exists():
        return
    delivery = read_json(path)
    require(delivery['schema_version'] == 1, 'Unsupported delivery manifest schema')
    require(delivery['run_manifest_sha256'] == sha256(output / 'run_manifest.json'),
            'Delivery manifest refers to a different completed study')
    require(str(path.relative_to(root)) not in delivery['files'], 'Delivery manifest cannot hash itself')
    require(DELIVERY_REQUIRED <= set(delivery['files']), 'Delivery seal omits required reports or verification code')
    verify_hash_map(root, delivery['files'])


def final_metadata(root, output):
    """Deterministic companion metadata; never edits the frozen strategy JSON."""
    root, output = Path(root), Path(output)
    require((output / 'run_manifest.json').is_file(), 'Final metadata requires completed main study')
    launch = read_json(output / 'study_manifest.json')
    cache = local_cache(root, launch['cache'])
    snapshot = read_json(cache / 'metadata.json')
    calendar = read_json(cache / 'calendar_v2.json')['calendar']
    config_path = root / 'configs/competition_24d_final.json'
    config = read_json(config_path)
    selection = read_json(output / 'final_selection.json')
    require(config['selection_provenance'] == selection, 'Final config provenance differs')
    return dict(schema_version=1,
        frozen_config_path=str(config_path.relative_to(root)), frozen_config_sha256=sha256(config_path),
        final_selection_sha256=sha256(output / 'final_selection.json'),
        run_manifest_sha256=sha256(output / 'run_manifest.json'), source_guard=launch['guard_sha256'],
        data_snapshot_metadata_sha256=sha256(cache / 'metadata.json'),
        nominal_daily_sha256=snapshot['artifact_sha256'][snapshot['derived_file']],
        actual_data_cutoff=max(calendar), requested_data_end_exclusive=snapshot['request']['end'],
        selection_frozen_at=selection['selection_frozen_at'], selection_decision=selection['decision'],
        frozen_candidate_id=selection['candidate_id'],
        adopted_candidate_id=(selection['candidate_id'] if selection['decision'] == 'FROZEN_MEASURED_CANDIDATE' else None),
        inherited_template_dates=dict(start=config['start'], end=config['end'],
            interpretation='INACTIVE_TEMPLATE_DATES: each episode overrides start/end from its registered session interval; these are not the data cutoff or live execution bounds.'),
        inherited_study_policy=dict(value=config['study_policy'],
            interpretation='INACTIVE_LEGACY_V2_METADATA: the actual 24D selection uses final_selection.json and the frozen study ranking/splits below; this legacy book-NAV-return criterion is not used.'),
        actual_selection_ranking=launch['guard']['study']['ranking'],
        actual_selection_splits=launch['guard']['study']['splits'],
        four_hour_policy=dict(use_4h=config['use_4h'], match_4h_coverage=config['match_4h_coverage'],
            active_top_level_mode=config['four_hour_mode'], active_feature_mode=config['feature_spec']['four_hour_mode'],
            inherited_nested_modes={key: config.get(key, {}).get('four_hour_mode')
                for key in ['tuning_params', 'deep_feature_params', 'full_tuning_params']},
            interpretation='Nested coverage_only values preserve the legacy parameter record; use_4h=False, match_4h_coverage=False and active disabled modes govern 24D execution.'),
        execution_assumptions={key: config[key] for key in ['execution', 'execution_assumption',
            'initial_cash', 'commission', 'sell_tax', 'slippage_bps', 'dividend_cash_policy']},
        active_share_status=selection['active_share_status'], ready_status=selection['ready_status'],
        submission_status=selection['submission_status'],
        operational_status='RESEARCH_ONLY: this companion clarifies frozen metadata; it does not authorize live submission or parameter changes.')


def verify_final_metadata(root, output):
    path = Path(root) / 'configs/competition_24d_final_metadata.json'
    require(path.is_file(), 'Final configuration metadata companion is missing')
    require(read_json(path) == final_metadata(root, output), 'Final configuration metadata differs from frozen evidence')


def diagnostic_registry(calendar, kind, start, end):
    first = {}
    for day in calendar:
        first.setdefault(day[:7], day)
    rows = []
    for index, day in enumerate(calendar):
        if not (start <= day <= end and index and index + 24 <= len(calendar)):
            continue
        if kind == 'monthly' and first[day[:7]] != day:
            continue
        rows.append(dict(episode_id=kind+'_'+day, kind=kind, start=day,
            end=calendar[index+23], prior_session_date=calendar[index-1], session_count=24,
            split='post_freeze_diagnostic', analysis_status='POST_FREEZE_DIAGNOSTIC_NEVER_SELECTION_INPUT'))
    return pd.DataFrame(rows)


def verify_cold_rows(folder, enriched):
    """Cheap independent derivation from already audited end-of-day books."""
    equity = pd.read_parquet(folder / 'equity.parquet')
    compliance = pd.read_parquet(folder / 'compliance_daily.parquet')
    books = {key: frame for key, frame in equity.groupby('episode_id', sort=False)}
    checks = {key: frame.set_index('date').warning_today for key, frame in compliance.groupby('episode_id', sort=False)}
    values = []
    for episode in enriched.episode_id:
        book = books.get(episode, equity.iloc[:0])
        record = dict(episode_id=episode)
        for day in [1, 3, 5]:
            record[f'day_{day}_invested_fraction'] = 1-float(book.iloc[day-1].cash_ratio) if len(book) >= day else None
            record[f'day_{day}_holding_count'] = int(book.iloc[day-1].holdings) if len(book) >= day else None
        valid = ((book.holdings.between(20, 30) & book.cash_ratio.ge(0) & book.cash_ratio.lt(.25)
                  & book.violations.fillna('').eq('') & book.stale_count.eq(0) & book.odd_residual_names.eq(0))
                 if len(book) else pd.Series(dtype=bool))
        if len(book) and episode in checks:
            valid &= ~book.date.map(checks[episode]).fillna(True).astype(bool)
        reached = next((index+1 for index, good in enumerate(valid) if good), None)
        record.update(days_to_valid_portfolio=reached, valid_portfolio_reached=reached is not None)
        values.append(record)
    expected = pd.DataFrame(values)
    same_frame(enriched[list(expected.columns)], expected, ['episode_id'], 'Cold-start evidence')


def verify_extended_summary(summary, rows):
    verify_summary(summary, rows)
    expected = []
    for candidate, frame in rows.groupby('candidate_id'):
        valid = frame[frame.measured_pass & frame.complete_period & frame.episode_return.notna()]
        returns = valid.episode_return.sort_values()
        count = len(returns)
        # Explicit integer order-statistic trimming; small groups can exceed 5%.
        trim = min((count+19)//20, max(0, (count-1)//2))
        cold = frame.days_to_valid_portfolio.dropna()
        record = dict(candidate_id=candidate, best_return=returns.max(),
            p90_MDD=valid.episode_max_drawdown.quantile(.9), median_trade_count=valid.trades.median(),
            valid_episode_count=count, mean_return_excluding_top_5pct=returns.iloc[:count-trim].mean(),
            median_return_without_tail_extremes=returns.iloc[trim:count-trim].median(),
            trimmed_each_tail_count=trim, median_days_to_valid_portfolio=cold.median(),
            valid_portfolio_reached_count=len(cold), valid_portfolio_unreached_count=len(frame)-len(cold))
        for day in [1, 3, 5]:
            for suffix in ['invested_fraction', 'holding_count']:
                key = f'day_{day}_{suffix}'
                record['median_'+key] = frame[key].median()
                record['observed_'+key+'_count'] = frame[key].notna().sum()
        expected.append(record)
    expected = pd.DataFrame(expected)
    same_frame(summary[list(expected.columns)], expected, ['candidate_id'], 'Extended summary')


def verify_supplement(root, main, registry, calendar, selection, parent_manifest, rebuild_context=None):
    out = root / 'outputs/24d_supplement'
    label = 'POST_FREEZE_DIAGNOSTIC_NEVER_SELECTION_INPUT'
    seal = read_json(out / 'supplement_manifest.json')
    audit = read_json(out / 'supplement_audit.json')
    guard = seal['guard']
    require(digest(guard) == seal['guard_sha256'], 'Supplement guard digest differs')
    launch = read_json(main / 'study_manifest.json')
    require(guard['parent_guard_sha256'] == launch['guard_sha256'], 'Supplement parent guard differs')
    require(guard['selection_sha256'] == sha256(main / 'final_selection.json'), 'Supplement freeze identity differs')
    require(guard['calendar_sha256'] == digest(calendar) and guard['raw_artifact_sha256'] == launch['guard']['artifact_sha256'],
            'Supplement raw inputs differ')
    verify_hash_map(root, guard['implementation_sha256'])
    require(set(guard['implementation_sha256']) == {'scripts/supplement_24d.py', 'tests/test_24d_supplement.py'},
            'Supplement implementation seal omits source/tests')
    require(guard['analysis_status'] == label and guard['adoption_allowed'] is False, 'Supplement entered selection')
    require(pd.Timestamp(seal['created_at']) > pd.Timestamp(selection['selection_frozen_at']), 'Supplement predates freeze')
    require(audit['parent_run_manifest_sha256'] == sha256(main / 'run_manifest.json'), 'Supplement parent output seal differs')
    require(audit['status'] == 'PASS_INTERNAL_ACCOUNTING' and audit['source_accuracy_verified'] is False
            and audit['active_share_status'] == 'ACTIVE_SHARE_NOT_VERIFIED' and audit['ready_status'] == 'BLOCK_READY'
            and audit['analysis_status'] == label, 'Supplement falsely certifies official/source validity')
    require(read_json(out / 'status.json')['status'] == 'COMPLETE', 'Supplement is unfinished')
    verify_hash_map(out, audit['output_sha256'])
    required = {'supplement_manifest.json', 'diagnostic_design.json', 'rolling_episodes_2025_2026.csv',
        'walk_forward_2025_decision.json', 'walk_forward_2025.csv', 'enriched_episodes.csv',
        'extended_summary.csv', 'monthly_comparison_2010_2026.csv', 'rolling_comparison_2010_2026.csv',
        'requested_split_diagnostics.csv', 'frozen_config_metadata.json', 'long_horizon/receipt.json'}
    require(required <= set(audit['output_sha256']), 'Supplement output seal omits required artifacts')
    design = read_json(out / 'diagnostic_design.json')
    require(design['analysis_status'] == label and design['adoption_allowed'] is False and
            design['parent_selection_sha256'] == guard['selection_sha256'], 'Supplement design changes adoption/provenance')
    require(pd.Timestamp(selection['selection_frozen_at']) < pd.Timestamp(design['created_at']) <= pd.Timestamp(audit['completed_at']),
            'Supplement design chronology differs')
    baseline = launch['guard']['study']['baseline_id']
    base_config = read_json(main / 'configs' / (baseline+'.json'))
    require(design['replacement_2_config']['full_tuning_params'] ==
            dict(base_config['full_tuning_params'], max_replacements_per_day=2), 'Replacement diagnostic changes unrelated parameters')
    monthly = registry[registry.kind.eq('monthly')]
    recent_rolling = diagnostic_registry(calendar, 'rolling', '2025-01-01', '2026-08-31')
    same_frame(read_csv(out / 'rolling_episodes_2025_2026.csv'), recent_rolling, ['episode_id'], 'Recent rolling registry')
    family = read_csv(main / 'walk_forward_family.csv')
    training = family[family.end.lt('2025-01-01')]
    ranked = ranking(training)
    eligible = ranked[ranked.compliance_pass_rate.eq(1)]
    chosen = str(eligible.iloc[0].candidate_id) if len(eligible) else baseline
    expected_decision = dict(candidate_id=chosen, training_start='2010-01-01', training_end='2024-12-31',
        test_year=2025, selection_rule='ORIGINAL_FROZEN_RANKING_100PCT_GATE', analysis_status=label,
        decision='ELIGIBLE_WALK_FORWARD' if len(eligible) else 'NO_ELIGIBLE_BASELINE_DIAGNOSTIC',
        training_artifact_sha256=sha256(main / 'walk_forward_family.csv'))
    require(read_json(out / 'walk_forward_2025_decision.json') == expected_decision, '2025 walk-forward decision differs')
    wf_test = diagnostic_registry(calendar, 'monthly', '2025-01-01', '2025-12-31')
    wf_test = wf_test[wf_test.end.le('2025-12-31')]
    expected, phase_rows = {}, {}
    for split in ['development', 'validation']:
        phase = 'replacement_'+split
        expected[f'ledgers/{phase}/diagnostic_replacement_2/receipt.json'] = (
            phase, 'diagnostic_replacement_2', monthly[monthly.split.eq(split)].assign(analysis_status=label))
    for year, episodes in recent_rolling.groupby(recent_rolling.start.str[:4]):
        for identifier in selection['comparison_candidate_ids']:
            phase = 'rolling_'+year
            expected[f'ledgers/{phase}/{identifier}/receipt.json'] = (phase, identifier, episodes)
    expected[f'ledgers/walk_forward_2025/{chosen}/receipt.json'] = ('walk_forward_2025', chosen, wf_test)
    receipts = audit['group_receipts']
    verify_receipt_set(out, receipts, expected)
    for path, (phase, identifier, episodes) in expected.items():
        receipt = receipts[path]
        require_receipt_sealed(path, receipt, audit['output_sha256'])
        rows, config = verify_group(out, path, receipt, episodes, identifier, phase, seal['guard_sha256'])
        require(pd.Timestamp(receipt['completed_at']) >= pd.Timestamp(design['created_at']), 'Supplement results predate design')
        if identifier == 'diagnostic_replacement_2':
            require(config['full_tuning_params'] == dict(base_config['full_tuning_params'], max_replacements_per_day=2),
                    'Replacement saved config changes unrelated parameters')
        else:
            require(config == read_json(main / 'configs' / (identifier+'.json')), 'Supplement changes frozen reference config')
        phase_rows.setdefault('rolling' if phase.startswith('rolling_') else phase, []).append(rows)
        if rebuild_context is not None:
            rebuilt = audit_saved_group(out / Path(path).parent, rows, {identifier: config}, rebuild_context, episodes)
            same_frame(rebuilt, read_csv(out / Path(path).parent / 'serialized_audit.csv'), ['episode_id'], 'Supplement rebuilt ledger')
    for phase, parts in phase_rows.items():
        rows = pd.concat(parts, ignore_index=True)
        same_frame(read_csv(out / (phase+'.csv')), rows, ['candidate_id', 'episode_id'], 'Supplement phase')
        verify_summary(read_csv(out / (phase+'_summary.csv')), rows)
    long_receipt = audit['long_horizon_receipt']
    require_receipt_sealed('long_horizon/receipt.json', long_receipt, audit['output_sha256'])
    folder = out / 'long_horizon'
    require(read_json(folder / 'receipt.json') == long_receipt and long_receipt['input_guard'] == seal['guard_sha256'],
            'Continuous-book receipt differs')
    require(set(long_receipt['artifact_sha256']) == {name+'.parquet' for name in TABLES} | {'config.json', 'metrics.json', 'audit.json'},
            'Continuous-book receipt omits evidence')
    verify_hash_map(folder, long_receipt['artifact_sha256'])
    long_metrics, long_config = read_json(folder / 'metrics.json'), read_json(folder / 'config.json')
    dates = [day for day in calendar if day >= '2010-01-01']
    expected_config = dict(base_config, start=dates[0], end=dates[-1], prior_session_date=calendar[calendar.index(dates[0])-1])
    require(long_config == expected_config, 'Continuous-book config differs from frozen baseline')
    require(long_metrics['requested_sessions'] == len(dates) and long_metrics['requested_start'] == dates[0]
            and long_metrics['requested_end'] == dates[-1], 'Continuous-book requested interval differs')
    if not long_metrics['complete_period']:
        require(long_metrics['long_horizon_return'] is None and long_metrics['episode_return'] is None,
                'Partial continuous book represented as full return')
    else:
        require(long_metrics['long_horizon_return'] == long_metrics['episode_return'], 'Continuous-book return differs')
    if rebuild_context is not None:
        from scripts.audit_24d import _audit_ledger
        result = {name: pd.read_parquet(folder / (name+'.parquet')) for name in TABLES}
        result.update(config=long_config, metrics=long_metrics)
        rebuilt = _audit_ledger(result, rebuild_context)
        saved = read_json(folder / 'audit.json')
        # JSON null and pandas NaN are semantically identical missing diagnostics.
        same_frame(pd.DataFrame([rebuilt]), pd.DataFrame([saved]), ['sessions'], 'Continuous-book rebuild')
    enriched = read_csv(out / 'enriched_episodes.csv')
    keys = ['source_study', 'phase', 'candidate_id', 'episode_id']
    require(not enriched.duplicated(keys).any() and len(enriched) == audit['enriched_rows'], 'Enriched attempt population differs')
    total = 0
    for source, directory, receipt_map in [('original', main, parent_manifest['group_receipts']), ('supplement', out, receipts)]:
        for path, receipt in receipt_map.items():
            folder = directory / Path(path).parent
            sample = enriched[enriched.source_study.eq(source) & enriched.phase.eq(receipt['phase'])
                              & enriched.candidate_id.eq(receipt['candidate_id'])]
            native = read_csv(folder / 'metrics.csv')
            same_frame(sample[list(native.columns)], native, ['episode_id'], 'Enriched source metrics')
            verify_cold_rows(folder, sample)
            require(sample.execution_model.eq('DAILY_OPEN_RESEARCH_PROXY').all() and sample.starting_cash.eq(1e9).all(),
                    'Enriched execution/cash assumptions differ')
            nav = native.get('final_economic_nav', native.get('final_nav'))
            expected_nav = native[['episode_id']].assign(terminal_NAV=nav.where(native.complete_period),
                forensic_partial_NAV=nav.where(~native.complete_period))
            same_frame(sample[list(expected_nav.columns)], expected_nav, ['episode_id'], 'Enriched terminal NAV')
            total += len(native)
    require(total == len(enriched), 'Enriched table omits/adds groups')
    require(enriched.analysis_status.eq(label).all(), 'Enriched diagnostics relabeled as selection evidence')
    summaries = read_csv(out / 'extended_summary.csv')
    for (source, phase), sample in enriched.groupby(['source_study', 'phase']):
        summary = summaries[summaries.source_study.eq(source) & summaries.phase.eq(phase)]
        verify_extended_summary(summary, sample)
    require(len(summaries) == enriched.groupby(['source_study','phase','candidate_id']).ngroups, 'Extra extended summaries')
    comparison_ids = selection['comparison_candidate_ids']
    expected_monthly = enriched[enriched.source_study.eq('original') & enriched.phase.isin(['monthly_comparison','recent_stress'])
                                & enriched.candidate_id.isin(comparison_ids) & enriched.start.le('2026-08-31')]
    monthly_full = read_csv(out / 'monthly_comparison_2010_2026.csv')
    same_frame(monthly_full, expected_monthly, ['candidate_id','start'], 'Full monthly comparison')
    starts = diagnostic_registry(calendar, 'monthly', '2010-01-01', '2026-08-31').start
    require(set(zip(monthly_full.candidate_id, monthly_full.start)) == {(candidate, start) for candidate in comparison_ids for start in starts},
            'Full monthly denominator differs')
    expected_rolling = enriched[enriched.phase.str.startswith('rolling_') & enriched.candidate_id.isin(comparison_ids)]
    rolling_full = read_csv(out / 'rolling_comparison_2010_2026.csv')
    same_frame(rolling_full, expected_rolling, ['candidate_id','start'], 'Full rolling comparison')
    starts = diagnostic_registry(calendar, 'rolling', '2010-01-01', '2026-08-31').start
    require(set(zip(rolling_full.candidate_id, rolling_full.start)) == {(candidate, start) for candidate in comparison_ids for start in starts},
            'Full rolling denominator differs')
    def descriptive_split(row):
        for name, lo, hi in [('development','2010-01-01','2018-12-31'),('validation','2019-01-01','2022-12-31'),
                             ('holdout','2023-01-01','2024-12-31'),('recent','2025-01-01','2026-09-30')]:
            if lo <= row.start <= row.end <= hi:
                return name
        return 'purged'
    require(enriched.requested_spec_split.tolist() == [descriptive_split(row) for row in enriched.itertuples()],
            'Descriptive split crossing interval boundaries')
    descriptive = read_csv(out / 'requested_split_diagnostics.csv')
    require(descriptive.selection_provenance.eq('POST_FREEZE_DESCRIPTIVE_ONLY_2019_WAS_DEVELOPMENT').all(),
            'Descriptive 2019 validation falsely claims untouched data')
    for split, sample in monthly_full.groupby('requested_spec_split'):
        verify_extended_summary(descriptive[descriptive.requested_spec_split.eq(split)], sample)
    return dict(status='PASS_SUPPLEMENT_INDEPENDENT_VERIFICATION', groups=len(receipts),
        audited_attempts=sum(item['episodes'] for item in receipts.values()),
        enriched_attempts=len(enriched), monthly_windows_per_candidate=200,
        rolling_windows_per_candidate=len(starts), adoption_allowed=False)


def verify_family_diagnostics(root, main, calendar, selection, rebuild_context=None):
    out, supplement = root / 'outputs/24d_diagnostics', root / 'outputs/24d_supplement'
    label = 'POST_FREEZE_DIAGNOSTIC_NEVER_SELECTION_INPUT'
    manifest, audit = read_json(out / 'diagnostics_manifest.json'), read_json(out / 'diagnostics_audit.json')
    guard = manifest['guard']
    require(digest(guard) == manifest['guard_sha256'], 'Family diagnostic input digest differs')
    require(manifest['adoption_allowed'] is False and audit['adoption_allowed'] is False
            and guard['analysis_status'] == label and audit['analysis_status'] == label,
            'Family diagnostics claim adoption')
    require(audit['source_accuracy_verified'] is False and audit['active_share_status'] == 'ACTIVE_SHARE_NOT_VERIFIED'
            and audit['ready_status'] == 'BLOCK_READY', 'Family diagnostics falsely certify validity')
    for key, path in [('main_run_manifest_sha256', main / 'run_manifest.json'),
                      ('supplement_audit_sha256', supplement / 'supplement_audit.json')]:
        require(guard[key] == audit[key] == sha256(path), 'Family diagnostic parent seal differs')
    require(guard['frozen_selection_sha256'] == sha256(main / 'final_selection.json'), 'Family diagnostics changed freeze')
    require(set(guard['implementation_sha256']) == {'scripts/complete_24d_diagnostics.py', 'tests/test_complete_24d_diagnostics.py'},
            'Family diagnostic implementation seal omits source/tests')
    verify_hash_map(root, guard['implementation_sha256'])
    verify_hash_map(out, audit['output_sha256'])
    require(read_json(out / 'status.json')['status'] == 'COMPLETE', 'Family diagnostics unfinished')
    require(pd.Timestamp(manifest['created_at']) > pd.Timestamp(selection['selection_frozen_at']), 'Family diagnostic chronology differs')
    required = {'launch_manifest.json', 'diagnostics_manifest.json', 'monthly_registry.csv',
        'long_horizon.csv', 'monthly_all_candidates.csv', 'full_period_summary.csv', 'candidate_scopes.csv'}
    require(required <= set(audit['output_sha256']), 'Family output seal omits required artifacts')
    configs = {identifier: read_json(main / 'configs' / (identifier+'.json'))
               for identifier in read_csv(main / 'candidates.csv').candidate_id}
    configs['simple_momentum_reference'] = read_json(main / 'configs/simple_momentum_reference.json')
    configs['diagnostic_replacement_2'] = read_json(supplement / 'configs/diagnostic_replacement_2.json')
    require(guard['configs'] == configs, 'Family diagnostics change reference config or membership')
    count = len(configs)
    require(manifest['candidate_count'] == audit['candidate_count'] == count and audit['parameter_set_count'] == count-1
            and audit['sanity_reference_count'] == 1, 'Fixed-set candidate denominator differs')
    scopes = read_csv(out / 'candidate_scopes.csv')
    expected_scopes = pd.DataFrame([dict(candidate_id=identifier, source=(
        'SANITY_REFERENCE' if identifier == 'simple_momentum_reference' else
        'POST_FREEZE_REPLACEMENT_DIAGNOSTIC' if identifier == 'diagnostic_replacement_2' else
        'PRE_FREEZE_SEARCHED_PARAMETER_SET')) for identifier in configs])
    same_frame(scopes, expected_scopes, ['candidate_id'], 'Candidate scope labels')
    episodes = diagnostic_registry(calendar, 'monthly', '2010-01-01', '2026-08-31')
    same_frame(read_csv(out / 'monthly_registry.csv'), episodes, ['episode_id'], 'Family monthly registry')
    rows = read_csv(out / 'monthly_all_candidates.csv')
    audit_attempt_coverage(rows, episodes.episode_id, configs)
    require(len(rows) == audit['monthly_attempts'] == count*len(episodes) and audit['monthly_per_candidate'] == len(episodes),
            'Family monthly audit counts differ')
    require(rows.analysis_status.eq(label).all() and rows.execution_model.eq('DAILY_OPEN_RESEARCH_PROXY').all(),
            'Family monthly results lose diagnostic/execution labels')
    for identifier, sample in rows.groupby('candidate_id'):
        same_frame(sample[['episode_id','start','end']], episodes[['episode_id','start','end']], ['episode_id'], 'Family intervals')
    enriched = read_csv(supplement / 'enriched_episodes.csv')
    keep = (enriched.source_study.eq('original') & enriched.phase.isin(
        ['development_coarse','walk_forward_later','recent_stress','sanity_monthly']))
    keep |= enriched.source_study.eq('supplement') & enriched.phase.isin(['replacement_development','replacement_validation'])
    reused = enriched[keep & enriched.candidate_id.isin(configs)].copy()
    reused['source_episode_id'], reused['source_phase'] = reused.episode_id, reused.phase
    reused['episode_id'], reused['kind'] = 'monthly_'+reused.start, 'monthly'
    reused['analysis_status'], reused['execution_model'] = label, 'DAILY_OPEN_RESEARCH_PROXY'
    actual_reused = rows[rows.source_study.isin(['original','supplement'])]
    same_frame(actual_reused[list(reused.columns)], reused, ['candidate_id','episode_id'], 'Reused monthly lineage')
    absent_sets = {}
    for identifier in configs:
        done = set(reused.loc[reused.candidate_id.eq(identifier), 'episode_id'])
        missing = tuple(episodes.loc[~episodes.episode_id.isin(done), 'episode_id'])
        if missing:
            absent_sets.setdefault(missing, []).append(identifier)
    expected_receipts, new_rows = {}, []
    for index, (missing, identifiers) in enumerate(absent_sets.items(), 1):
        phase = f'remaining_monthly_{index:03d}'
        requested = episodes[episodes.episode_id.isin(missing)]
        phase_rows = []
        for identifier in identifiers:
            relative = f'ledgers/{phase}/{identifier}/receipt.json'
            receipt = read_json(out / relative)
            require_receipt_sealed(relative, receipt, audit['output_sha256'])
            expected_receipts[relative] = receipt
            metrics, saved_config = verify_group(out, relative, receipt, requested, identifier, phase, manifest['guard_sha256'])
            require(saved_config == configs[identifier], 'Missing-cell execution changes reference config')
            require(pd.Timestamp(receipt['completed_at']) >= pd.Timestamp(manifest['created_at']), 'Missing-cell result predates design')
            sample = rows[rows.source_study.eq('family_diagnostics') & rows.candidate_id.eq(identifier) & rows.phase.eq(phase)]
            same_frame(sample[list(metrics.columns)], metrics, ['episode_id'], 'New monthly lineage')
            require(sample.source_episode_id.eq(sample.episode_id).all() and sample.source_phase.eq(phase).all(),
                    'New monthly source identity differs')
            verify_cold_rows(out / Path(relative).parent, sample)
            if rebuild_context is not None:
                rebuilt = audit_saved_group(out / Path(relative).parent, metrics, {identifier: saved_config}, rebuild_context, requested)
                same_frame(rebuilt, read_csv(out / Path(relative).parent / 'serialized_audit.csv'), ['episode_id'], 'Family missing-cell rebuild')
            phase_rows.append(metrics)
            new_rows.append(sample)
        combined = pd.concat(phase_rows, ignore_index=True)
        same_frame(read_csv(out / (phase+'.csv')), combined, ['candidate_id','episode_id'], 'Missing-cell phase')
        verify_summary(read_csv(out / (phase+'_summary.csv')), combined)
    verify_receipt_set(out, expected_receipts, expected_receipts)
    require(len(actual_reused) + sum(len(frame) for frame in new_rows) == len(rows), 'Family lineage omits/adds attempts')
    verify_extended_summary(read_csv(out / 'full_period_summary.csv'), rows)
    continuous = read_csv(out / 'long_horizon.csv')
    require(continuous.candidate_id.is_unique and set(continuous.candidate_id) == set(configs)
            and len(continuous) == audit['long_horizon_attempts'], 'Continuous-book candidate denominator differs')
    require({path.parent.name for path in (out / 'continuous').glob('*/receipt.json')} == set(configs),
            'Continuous-book receipts omit/add fixed sets')
    dates = [day for day in calendar if day >= '2010-01-01']
    for identifier, config in configs.items():
        folder = out / 'continuous' / identifier
        receipt = read_json(folder / 'receipt.json')
        require_receipt_sealed('continuous/'+identifier+'/receipt.json', receipt, audit['output_sha256'])
        require(receipt['input_guard'] == digest(dict(input_guard=manifest['guard_sha256'], candidate_id=identifier, config=config)),
                'Continuous-book input digest differs')
        require(set(receipt['artifact_sha256']) == {name+'.parquet' for name in TABLES} | {'config.json','metrics.json','audit.json','row.json'},
                'Continuous-book receipt omits required artifacts')
        verify_hash_map(folder, receipt['artifact_sha256'])
        actual_config, metrics, row = read_json(folder / 'config.json'), read_json(folder / 'metrics.json'), read_json(folder / 'row.json')
        expected_config = dict(config, start=dates[0], end=dates[-1], prior_session_date=calendar[calendar.index(dates[0])-1])
        require(actual_config == expected_config, 'Continuous book changes fixed parameters')
        same_frame(continuous[continuous.candidate_id.eq(identifier)], pd.DataFrame([row]), ['candidate_id'], 'Continuous-book summary row')
        require(row['source'] == expected_scopes.set_index('candidate_id').at[identifier,'source'], 'Continuous scope mislabeled')
        require(row['requested_start'] == dates[0] and row['requested_end'] == dates[-1]
                and row['requested_sessions'] == metrics['requested_sessions'] == len(dates), 'Continuous interval differs')
        require(row['complete_period'] == metrics['complete_period'] and row['disqualified'] == metrics['disqualified']
                and row['observed_sessions'] == metrics['observed_sessions'], 'Continuous completion differs')
        require(row['analysis_status'] == label and row['execution_model'] == 'DAILY_OPEN_RESEARCH_PROXY', 'Continuous result loses diagnostic labels')
        equity = pd.read_parquet(folder / 'equity.parquet')
        require(len(equity) == row['observed_sessions'] and equity.date.astype(str).tolist() == dates[:len(equity)],
                'Continuous observed calendar has gaps or restarts')
        require(abs(float(equity.economic_nav.iloc[-1])-row['final_economic_nav']) < 1e-5, 'Continuous terminal wealth differs')
        economic_return = float(equity.economic_nav.iloc[-1])/config['initial_cash']-1
        if row['complete_period']:
            require(len(equity) == len(dates) and row['forensic_partial_return'] is None
                    and abs(row['long_horizon_return']-economic_return) < 1e-12, 'Full continuous return differs')
        else:
            require(row['long_horizon_return'] is None and metrics['episode_return'] is None
                    and abs(row['forensic_partial_return']-economic_return) < 1e-12, 'Partial continuous book is misrepresented')
        if rebuild_context is not None:
            from scripts.audit_24d import _audit_ledger
            result = {name: pd.read_parquet(folder / (name+'.parquet')) for name in TABLES}
            result.update(config=actual_config, metrics=metrics)
            checked = _audit_ledger(result, rebuild_context)
            same_frame(pd.DataFrame([checked]), pd.DataFrame([read_json(folder / 'audit.json')]), ['sessions'], 'Continuous independent rebuild')
    return dict(status='PASS_FIXED_SET_DIAGNOSTICS_VERIFICATION', candidate_count=count,
        monthly_attempts=len(rows), long_horizon_attempts=len(continuous), adoption_allowed=False)


def verify_study(root=ROOT, output=None, rebuild=False, rebuild_groups=()):
    root = Path(root).resolve()
    output = Path(output or root / 'outputs/24d').resolve()
    require(output.is_relative_to(root), 'Study output must be inside repository')
    require((output / 'run_manifest.json').is_file(), 'Completed run_manifest.json is missing; study is unfinished')
    verify_delivery(root, output)
    if output == root / 'outputs/24d':
        verify_final_metadata(root, output)
    manifest, launch = read_json(output / 'run_manifest.json'), read_json(output / 'study_manifest.json')
    guard = launch['guard']
    require(digest(guard) == launch['guard_sha256'] == manifest['source_guard'], 'Study guard digest differs')
    verify_hash_map(root, guard['source_sha256'])
    mandatory_sources = {str(path.relative_to(root)) for path in (root / 'src').glob('*.py')}
    mandatory_sources |= {'scripts/run_24d.py', 'scripts/audit_24d.py', 'tests/test_strategy_24d.py',
        'tests/test_audit_24d.py', 'tests/test_24d_study.py', 'config/strategy_v2.json',
        'outputs/best_v2/official_ex_post/config.json', 'data/reference/universe_competition_20260731.csv',
        'docs/v3_spec.md', 'docs/v2_double_check_rules.md'}
    require(mandatory_sources <= set(guard['source_sha256']), 'Study guard omits required source hashes')
    cache = local_cache(root, launch['cache'])
    verify_hash_map(cache, {'metadata.json': guard['metadata_sha256'],
                          'calendar_v2.json': guard['calendar_amendment_sha256']})
    metadata, amendment = read_json(cache / 'metadata.json'), read_json(cache / 'calendar_v2.json')
    require(metadata['universe_sha256'] == guard['source_sha256']['data/reference/universe_competition_20260731.csv'],
            'Snapshot universe differs from frozen study universe')
    require(metadata['artifact_sha256'] == guard['artifact_sha256'], 'Data artifact maps differ')
    verify_hash_map(cache, guard['artifact_sha256'])
    calendar = amendment['calendar']
    require(digest(calendar) == guard['calendar_sha256'] == amendment['amended_calendar_sha256'], 'Calendar digest differs')
    require(amendment['source_metadata_sha256'] == guard['metadata_sha256'] and
            amendment['source_derived_sha256'] == guard['artifact_sha256'][metadata['derived_file']],
            'Calendar source lineage differs')
    verify_hash_map(output, manifest['output_sha256'])
    environment = read_json(output / 'environment.json')
    runtime_differences = {}
    for package, expected_version in environment.items():
        try:
            actual_version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            actual_version = None
        if actual_version != expected_version:
            runtime_differences[package] = dict(recorded=expected_version, current=actual_version)
    require(all(launch[package] == environment[package] for package in ['pandas', 'numpy']),
            'Launch and environment numerical versions disagree')
    if launch['python'] != sys.version:
        runtime_differences['python'] = dict(recorded=launch['python'], current=sys.version)
    require(not rebuild or not ({'python', 'numpy', 'pandas', 'pyarrow'} & set(runtime_differences)),
            'Ledger rebuild requires recorded Python/numpy/pandas/pyarrow versions')
    study = guard['study']
    require(study['required_measured_pass_rate'] == 1 and study['episode_length'] == 24, 'Study gates relaxed')
    registry, recent = expected_registries(calendar, study)
    same_frame(read_csv(output / 'episodes.csv'), registry, ['episode_id'], 'Primary registry')
    same_frame(read_csv(output / 'recent_episodes.csv'), recent, ['episode_id'], 'Recent registry')
    registry_audit = audit_episode_registry(registry, calendar)
    candidates = read_csv(output / 'candidates.csv')
    require(candidates.candidate_id.is_unique, 'Duplicate candidate identifiers')
    coarse = {study['baseline_id']: {}}
    for field, values in study['coarse_neighbors'].items():
        for value in values:
            coarse[f'coarse_{len(coarse):03d}_{field}_{str(value).replace(".", "p")}'] = {field: value}
    actual_coarse = candidates[candidates.source.isin(['baseline', 'coarse'])]
    require(dict(zip(actual_coarse.candidate_id, actual_coarse.params_json.map(json.loads))) == coarse,
            'Predeclared coarse candidate family differs')
    design = read_json(output / 'refinement_design.json')
    require(design['source'] == 'DEVELOPMENT_ONLY' and design['input_sha256'] == sha256(output / 'development_coarse.csv'),
            'Refinement input lineage differs')
    local = candidates[candidates.source.eq('local_refinement')]
    require(len(local) <= study['local_refinement_limit'], 'Refinement budget exceeded')
    require(set(local.candidate_id) == {entry['candidate_id'] for entry in design['parameters']}, 'Local family differs')
    require(set(candidates.candidate_id) == set(coarse) | set(local.candidate_id), 'Unregistered candidate family')
    for entry in design['parameters']:
        found = local.set_index('candidate_id').loc[entry['candidate_id']]
        require(json.loads(found.params_json) == entry['params'] and found.parent_candidate_id == entry['parent_candidate_id'],
                'Local candidate design differs')
    baseline_parameters = read_json(output / 'configs' / (study['baseline_id'] + '.json'))['full_tuning_params']
    for row in candidates.to_dict('records'):
        config = read_json(output / 'configs' / (row['candidate_id'] + '.json'))
        require(config['candidate_id'] == row['candidate_id'] and
                config['full_tuning_params'] == {**baseline_parameters, **json.loads(row['params_json'])},
                'Saved candidate parameters differ from declared family')
    monthly = registry[registry.kind.eq('monthly')]
    dev, val = monthly[monthly.split.eq('development')], monthly[monthly.split.eq('validation')]
    development, validation = read_csv(output / 'development.csv'), read_csv(output / 'validation.csv')
    audit_attempt_coverage(development, dev.episode_id, candidates.candidate_id)
    shortlist = set(ranking(development).candidate_id.head(study['validation_shortlist'])) | {study['baseline_id']}
    audit_attempt_coverage(validation, val.episode_id, shortlist)
    coarse_rank = ranking(read_csv(output / 'development_coarse.csv'))
    eligible_parents = set(coarse_rank[coarse_rank.compliance_pass_rate.eq(1)].candidate_id.head(3)) - {study['baseline_id']}
    require(set(local.parent_candidate_id) <= eligible_parents, 'Local search used ineligible development parents')
    selection = read_json(output / 'final_selection.json')
    provenance, started = verify_freeze(root, output, selection, registry, candidates, development, validation)
    require(set(guard['source_sha256']) <= set(selection['selection_input_hashes']), 'Freeze omits source hashes')
    comparisons, baseline, sanity = selection['comparison_candidate_ids'], [study['baseline_id']], ['simple_momentum_reference']
    phases = {'baseline_development': (baseline, dev), 'development_coarse': (list(coarse), dev),
        'validation': (sorted(shortlist), val), 'holdout': (comparisons, monthly[monthly.split.eq('holdout')]),
        'monthly_comparison': (comparisons, monthly), 'oct_nov': (comparisons, registry[registry.kind.eq('oct_nov')]),
        'rolling': (comparisons, registry[registry.kind.eq('rolling')]), 'sanity_monthly': (sanity, monthly),
        'sanity_oct_nov': (sanity, registry[registry.kind.eq('oct_nov')]),
        'walk_forward_later': (list(coarse), monthly[~monthly.episode_id.isin(dev.episode_id)])}
    if len(local):
        phases['development_local'] = (local.candidate_id.tolist(), dev)
    if len(recent):
        phases['recent_stress'] = (comparisons + sanity, recent)
    expected = {}
    for phase, (identifiers, episodes) in phases.items():
        shards = [(phase + '_' + year, part) for year, part in episodes.groupby(episodes.start.str[:4])] if phase == 'rolling' else [(phase, episodes)]
        for shard, part in shards:
            for identifier in identifiers:
                expected[f'ledgers/{shard}/{identifier}/receipt.json'] = (phase, shard, identifier, part)
    receipts = manifest['group_receipts']
    verify_receipt_set(output, receipts, expected)
    phase_rows, configs, rebuilt = {}, {}, []
    require(not rebuild_groups or rebuild, '--group requires --rebuild')
    require(set(rebuild_groups) <= {str(Path(name).parent.relative_to('ledgers')) for name in expected}, 'Unknown rebuild group')
    ctx = None
    for relative, (phase, shard, identifier, episodes) in expected.items():
        receipt = receipts[relative]
        rows, config = verify_group(output, relative, receipt, episodes, identifier, shard, launch['guard_sha256'])
        completed = pd.Timestamp(receipt['completed_at'])
        if phase in ['baseline_development', 'development_coarse', 'development_local', 'validation']:
            require(completed <= pd.Timestamp(selection['selection_frozen_at']), 'Selection group completed after freeze')
        else:
            require(completed >= started, 'Evaluation group predates holdout-start receipt')
        if phase == 'development_coarse':
            require(completed <= pd.Timestamp(design['created_at']), 'Refinement design predates coarse evidence')
        if phase in ['development_local', 'validation']:
            require(completed >= pd.Timestamp(design['created_at']), 'Refinement design follows dependent evaluation')
        require(completed <= pd.Timestamp(manifest['completed_at']), 'Manifest predates completed group')
        phase_rows.setdefault(phase, []).append(rows)
        configs[identifier] = config
        group = str(Path(relative).parent.relative_to('ledgers'))
        if rebuild and (not rebuild_groups or group in rebuild_groups):
            if ctx is None:
                daily = pd.read_parquet(cache / metadata['derived_file'])
                daily = daily[daily.symbol.ne('0050.TW')].copy()
                universe = read_csv(root / 'data/reference/universe_competition_20260731.csv')
                universe['symbol'], universe['known_at'] = universe.yahoo_symbol.astype(str), universe.attachment_created_at
                ctx = dict(daily=daily, universe=universe, session_dates=calendar)
            folder = inside(output, relative).parent
            audit = audit_saved_group(folder, rows, {identifier: config}, ctx, episodes)
            same_frame(audit, read_csv(folder / 'serialized_audit.csv'), ['episode_id'], 'Rebuilt audit')
            rebuilt.append(group)
    for phase, parts in phase_rows.items():
        assembled = pd.concat(parts, ignore_index=True)
        same_frame(read_csv(output / (phase + '.csv')), assembled,
                   ['candidate_id', 'episode_id'], 'Phase ' + phase)
        verify_summary(read_csv(output / (phase + '_summary.csv')), assembled)
    combined = pd.concat(phase_rows['development_coarse'] + phase_rows.get('development_local', []), ignore_index=True)
    same_frame(development, combined, ['candidate_id', 'episode_id'], 'Development population')
    verify_summary(read_csv(output / 'development_summary.csv'), development)
    family = read_csv(output / 'walk_forward_family.csv')
    expected_family = pd.concat([pd.concat(phase_rows['development_coarse']).assign(source_phase='development_coarse'),
                                pd.concat(phase_rows['walk_forward_later']).assign(source_phase='walk_forward_later')])
    same_frame(family, expected_family, ['candidate_id', 'episode_id'], 'Walk-forward family')
    audit_attempt_coverage(family, monthly.episode_id, coarse)
    decisions, picks = [], []
    for year in range(study['walk_forward_first_test_year'], study['walk_forward_last_test_year'] + 1):
        train = family[family.end.lt(f'{year}-01-01')]
        test = family[family.start.ge(f'{year}-01-01') & family.end.le(f'{year}-12-31')]
        eligible = ranking(train).query('compliance_pass_rate == 1')
        identifier = eligible.iloc[0].candidate_id if len(eligible) else baseline[0]
        decisions.append(dict(test_year=year, candidate_id=identifier, training_end=f'{year-1}-12-31',
            train_episodes=train.episode_id.nunique(), test_episodes=test.episode_id.nunique(),
            decision='MEASURED_ELIGIBLE' if len(eligible) else 'NO_ELIGIBLE_BASELINE_DIAGNOSTIC'))
        picks.append(test[test.candidate_id.eq(identifier)].assign(test_year=year))
    same_frame(read_csv(output / 'walk_forward_decisions.csv'), pd.DataFrame(decisions), ['test_year'], 'Walk-forward decisions')
    same_frame(read_csv(output / 'walk_forward.csv'), pd.concat(picks), ['candidate_id', 'episode_id'], 'Walk-forward picks')
    gate, audit = read_json(output / 'test_gate.json'), read_json(output / 'audit.json')
    require(gate['returncode'] == 0 and gate['source_guard'] == launch['guard_sha256'], 'Frozen test gate failed')
    require(audit['registry'] == registry_audit and audit['selection'] == provenance, 'Final audit provenance differs')
    attempts = sum(receipt['episodes'] for receipt in receipts.values())
    require(audit['groups'] == len(expected) and audit['audited_attempts'] == attempts, 'Audit counts differ')
    require(audit['group_audits'] == 'EVERY_ATTEMPT_RECONSTRUCTED_BEFORE_SAVE' and
            audit['active_share_status'] == 'ACTIVE_SHARE_NOT_VERIFIED' and audit['ready_status'] == 'BLOCK_READY' and
            audit['source_accuracy_verified'] is False and audit['rolling_windows_independent'] is False,
            'Final audit overstates certification')
    required_outputs = {'study_manifest.json', 'episodes.csv', 'recent_episodes.csv', 'candidates.csv',
        'refinement_design.json', 'development.csv', 'development_summary.csv', 'final_selection.json',
        'frozen_candidate.json', 'holdout_started.json', 'audit.json', 'test_gate.json',
        'walk_forward_family.csv', 'walk_forward_decisions.csv', 'walk_forward.csv', 'environment.json'}
    required_outputs |= {phase + suffix for phase in phases for suffix in ['.csv', '_summary.csv']}
    require(required_outputs <= set(manifest['output_sha256']), 'Run manifest omits required output hashes')
    supplement = None
    if (root / 'outputs/24d_supplement/supplement_audit.json').exists():
        supplement = verify_supplement(root, output, registry, calendar, selection, manifest,
                                       rebuild_context=ctx if rebuild and not rebuild_groups else None)
    elif (output / 'delivery_manifest.json').exists():
        raise ValueError('Final delivery requires completed independent supplement verification')
    family = None
    if (root / 'outputs/24d_diagnostics/diagnostics_audit.json').exists():
        require(supplement is not None, 'Fixed-set diagnostics require independently verified supplement')
        family = verify_family_diagnostics(root, output, calendar, selection,
                                          rebuild_context=ctx if rebuild and not rebuild_groups else None)
    elif (output / 'delivery_manifest.json').exists():
        raise ValueError('Final delivery requires completed fixed-set diagnostic verification')
    return dict(status='PASS_OFFLINE_ARTIFACT_VERIFICATION', groups=len(expected), audited_attempts=attempts,
        primary_monthly_episodes=len(monthly), candidates=len(candidates), rebuilt_groups=rebuilt,
        source_accuracy_verified=False, active_share_status='ACTIVE_SHARE_NOT_VERIFIED',
        ready_status='BLOCK_READY', submission_status='BLOCK_SUBMISSION',
        verification_runtime_differences=runtime_differences,
        supplement=supplement,
        fixed_set_diagnostics=family,
        verification_scope='artifact consistency, declared chronology, complete denominators; no vendor accuracy certification')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/24d')
    parser.add_argument('--rebuild', action='store_true', help='Reconstruct saved ledgers without running strategies')
    parser.add_argument('--group', action='append', default=[], help='Limit rebuild to phase/candidate; integrity still checks all groups')
    args = parser.parse_args()
    try:
        result = verify_study(output=args.output, rebuild=args.rebuild, rebuild_groups=args.group)
    except (ValueError, AssertionError, OSError, KeyError) as error:
        print(json.dumps(dict(status='FAIL_OFFLINE_VERIFICATION', error=str(error))), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
