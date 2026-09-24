"""Render the nine v3 research reports from saved episode-level evidence.

No return is imputed for incomplete episodes. Return statistics are explicitly
conditional on measured-valid complete episodes; all requested episodes remain
in coverage and measured-rule denominators. Formal compliance is never inferred
from a producer PASS or an arithmetic audit PASS.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASELINE = 'x0352_daily_baseline'
PARTITIONS = ('development', 'validation', 'holdout')
METRICS = ('episode_return', 'episode_max_drawdown', 'episode_turnover')


def truth(series):
    def parse(value):
        if value is True or value == 1 or str(value).strip().lower() == 'true':
            return True
        if value is False or value == 0 or str(value).strip().lower() == 'false':
            return False
        raise ValueError(f'Unrecognized boolean in episode evidence: {value!r}')
    return series.map(parse).astype(bool)


def episodes(path, optional=False):
    path = Path(path)
    if not path.exists():
        if optional:
            return pd.DataFrame()
        raise FileNotFoundError(f'Required research evidence missing: {path}')
    try:
        frame = pd.read_csv(path, float_precision='round_trip')
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    if frame.empty:
        return frame
    required = {'candidate_id', 'complete_period', 'measured_pass', *METRICS}
    if required - set(frame):
        raise ValueError(f'{path}: missing episode fields {sorted(required - set(frame))}')
    if 'episode_id' in frame and frame.duplicated(['candidate_id', 'episode_id']).any():
        raise ValueError(f'{path}: duplicate candidate/episode evidence would double-count results')
    for column in ('complete_period', 'measured_pass'):
        frame[column] = truth(frame[column])
    for column in METRICS:
        frame[column] = pd.to_numeric(frame[column], errors='raise')
        if np.isinf(frame[column]).any():
            raise ValueError(f'{path}: nonfinite {column}')
    if frame.loc[~frame.complete_period, 'episode_return'].notna().any():
        raise ValueError(f'{path}: incomplete episodes must not expose a full 24D return')
    if frame.loc[frame.measured_pass, list(METRICS)].isna().any().any():
        raise ValueError(f'{path}: measured-valid episode has missing metrics')
    if (frame.measured_pass & ~frame.complete_period).any():
        raise ValueError(f'{path}: incomplete episode falsely marked measured-valid')
    if 'observed_sessions' in frame and frame.loc[frame.complete_period, 'observed_sessions'].ne(24).any():
        raise ValueError(f'{path}: complete episode does not contain 24 observed sessions')
    return frame


def stats(frame):
    count = len(frame)
    if not count:
        complete = valid = frame
    else:
        complete = frame.loc[frame.complete_period & frame.episode_return.notna()]
        valid = complete.loc[complete.measured_pass]
    returns = valid['episode_return'] if len(valid) else pd.Series(dtype=float)
    result = dict(requested=count, complete=len(complete), valid=len(valid),
                  incomplete=count-len(complete), failed=count-len(valid),
                  measured_pass_rate=len(valid)/count if count else None,
                  formal_confirmed=0, formal_unknown=count)
    for label, value in [('median', returns.median()), ('mean', returns.mean()),
                         ('p25', returns.quantile(.25)), ('p10', returns.quantile(.10)),
                         ('worst', returns.min()),
                         ('best', returns.max()), ('p75', returns.quantile(.75)),
                         ('positive', returns.gt(0).mean() if len(returns) else None),
                         ('loss', returns.lt(0).mean() if len(returns) else None),
                         ('median_mdd', valid.episode_max_drawdown.median() if len(valid) else None),
                         ('worst_mdd', valid.episode_max_drawdown.max() if len(valid) else None),
                         ('p90_mdd', valid.episode_max_drawdown.quantile(.9) if len(valid) else None),
                         ('trade_count', valid.trades.median() if len(valid) and 'trades' in valid else None),
                         ('turnover', valid.episode_turnover.median() if len(valid) else None),
                         ('all_complete_median', complete.episode_return.median() if len(complete) else None),
                         ('all_complete_mean', complete.episode_return.mean() if len(complete) else None)]:
        result[label] = float(value) if value is not None and pd.notna(value) else None
    ordered = returns.sort_values()
    trim = min(int(np.ceil(len(ordered)*.05)), max(0, (len(ordered)-1)//2))
    result['trimmed_each_tail_count'] = trim
    result['mean_excluding_top5'] = float(ordered.iloc[:len(ordered)-trim].mean()) if len(ordered) else None
    result['symmetric_trimmed_median'] = float(ordered.iloc[trim:len(ordered)-trim].median()) if len(ordered) else None
    reached = frame.days_to_valid_portfolio.dropna() if count and 'days_to_valid_portfolio' in frame else pd.Series(dtype=float)
    result['days_to_valid'] = float(reached.median()) if len(reached) else None
    result['valid_portfolio_reached'] = len(reached) if 'days_to_valid_portfolio' in frame else None
    return result


def percent(value):
    return 'N/A' if value is None or not math.isfinite(float(value)) else f'{100*value:.2f}%'


def number(value):
    return 'N/A' if value is None or not math.isfinite(float(value)) else f'{float(value):g}'


def table(headers, rows):
    def safe(value):
        return str(value).replace('|', '/').replace('\n', ' ')
    return '\n'.join(['| ' + ' | '.join(map(safe, headers)) + ' |',
                      '| ' + ' | '.join('---' for _ in headers) + ' |'] +
                     ['| ' + ' | '.join(map(safe, row)) + ' |' for row in rows])


def subset(frame, candidate):
    return frame.loc[frame.candidate_id.eq(candidate)] if len(frame) else frame


def metric_table(frames, selected, additional=()):
    rows = []
    names = list(dict.fromkeys([BASELINE, selected, *additional]))
    for split, frame in frames.items():
        for candidate in names:
            s = stats(subset(frame, candidate))
            rows.append([split, candidate, f'{s["valid"]}/{s["requested"]}',
                         s['incomplete'], percent(s['median']), percent(s['mean']),
                         percent(s['p25']), percent(s['p10']), percent(s['worst'])])
    return table(['Set', 'Candidate', 'Measured valid / requested', 'Incomplete', 'Median', 'Mean', 'P25', 'P10', 'Worst'], rows)


def risk_table(frames, selected, additional=()):
    rows = []
    for split, frame in frames.items():
        for candidate in dict.fromkeys([BASELINE, selected, *additional]):
            s = stats(subset(frame, candidate))
            rows.append([split, candidate, percent(s['positive']), percent(s['loss']),
                         percent(s['median_mdd']), percent(s['worst_mdd']),
                         percent(s['turnover']), percent(s['all_complete_median'])])
    return table(['Set', 'Candidate', 'Positive', 'Loss', 'Median MDD', 'Worst MDD', 'Median turnover', 'All-complete median¹'], rows)


def comparison(frame, selected, diagnostic=None):
    names = list(dict.fromkeys([BASELINE, selected] + ([diagnostic] if diagnostic else [])))
    summaries = [stats(subset(frame, name)) for name in names]
    fields = [('Median 24D return', 'median'), ('Mean 24D return', 'mean'),
              ('P25', 'p25'), ('P10', 'p10'), ('Worst', 'worst'),
              ('Positive episodes', 'positive'), ('Loss episodes', 'loss'),
              ('Median MDD', 'median_mdd'), ('Worst MDD', 'worst_mdd'),
              ('Best return', 'best'), ('P90 MDD', 'p90_mdd'),
              ('Median turnover', 'turnover'), ('Measured rule pass / requested', 'measured_pass_rate')]
    rows = [[label, *[percent(s[key]) for s in summaries]] for label, key in fields]
    rows += [['Median trade count', *[number(s['trade_count']) for s in summaries]],
             ['Median days to valid portfolio (among reached)', *[number(s['days_to_valid']) for s in summaries]]]
    rows += [['Measured valid / requested', *[f'{s["valid"]}/{s["requested"]}' for s in summaries]],
             ['Incomplete episodes', *[s['incomplete'] for s in summaries]],
             ['Formal compliance confirmed', *[f'0/{s["requested"]}; UNKNOWN' for s in summaries]]]
    labels = [name + (' (diagnostic; not adopted)' if name == diagnostic and name != selected else '') for name in names]
    return table(['Metric', *labels], rows)


def distribution_table(frames, selected, additional=()):
    rows = []
    for phase, frame in frames.items():
        for candidate in dict.fromkeys([BASELINE, selected, *additional]):
            group = subset(frame, candidate)
            if group.empty:
                continue
            s = stats(group)
            rows.append([phase, candidate, f'{s["valid"]}/{s["requested"]}', percent(s['best']),
                         percent(s['p75']), percent(s['p90_mdd']), number(s['trade_count']),
                         percent(s['mean_excluding_top5']), percent(s['symmetric_trimmed_median']), s['trimmed_each_tail_count']])
    return table(['Set', 'Candidate', 'Valid / requested', 'Best', 'P75', 'P90 MDD', 'Median trades',
                  'Mean excluding top 5%', 'Symmetric trimmed median', 'Removed per tail'], rows)


def cold_start_table(frames, selected, additional=()):
    rows = []
    for phase, frame in frames.items():
        for candidate in dict.fromkeys([BASELINE, selected, *additional]):
            group = subset(frame, candidate)
            if group.empty:
                continue
            invested, holdings, counts = [], [], []
            for day in [1, 3, 5]:
                value = group.get(f'day_{day}_invested_fraction', pd.Series(dtype=float)).dropna()
                quantity = group.get(f'day_{day}_holding_count', pd.Series(dtype=float)).dropna()
                invested.append(percent(value.median() if len(value) else None))
                holdings.append(number(quantity.median() if len(quantity) else None))
                counts.append(str(len(value)))
            s = stats(group)
            rows.append([phase, candidate, *invested, ' / '.join(holdings), ' / '.join(counts),
                         f'{number(s["valid_portfolio_reached"])}/{s["requested"]}', number(s['days_to_valid'])])
    return table(['Set', 'Candidate', 'D1 invested', 'D3 invested', 'D5 invested', 'Holdings D1 / D3 / D5',
                  'Observed D1 / D3 / D5', 'Reached valid / requested', 'MEDIAN_DAYS_TO_VALID_PORTFOLIO'], rows)


def add_cold_fields(frame, enriched, phases):
    if frame.empty:
        return frame
    selected = enriched.loc[enriched.source_study.eq('original') & enriched.phase.isin(phases)]
    fields = [c for c in selected if c.startswith('day_') or c in {'days_to_valid_portfolio', 'valid_portfolio_reached'}]
    fields = [c for c in fields if c not in frame]
    if not fields:
        return frame
    right = selected[['candidate_id', 'episode_id', *fields]]
    if right.duplicated(['candidate_id', 'episode_id']).any():
        raise ValueError('Cold-start enrichment duplicates an original candidate/episode')
    result = frame.merge(right, on=['candidate_id', 'episode_id'], how='left', validate='one_to_one', indicator=True)
    if result._merge.ne('both').any():
        raise ValueError('Cold-start enrichment omits original candidate/episode evidence')
    return result.drop(columns='_merge')


def replacement_comparison(frames, candidates, supplement):
    one = []
    for row in candidates.to_dict('records'):
        params = json.loads(row.get('params_json', '{}'))
        if params == {'max_replacements_per_day': 1}:
            one.append(row['candidate_id'])
    identities = [(0, BASELINE)] + [(1, name) for name in one] + [(2, 'diagnostic_replacement_2')]
    enriched = supplement['enriched']
    rows, evidence = [], {}
    for phase in ['development', 'validation']:
        for replacements, candidate in identities:
            if replacements == 2:
                group = enriched.loc[enriched.source_study.eq('supplement')
                    & enriched.phase.eq('replacement_' + phase) & enriched.candidate_id.eq(candidate)]
            else:
                group = subset(frames[phase], candidate)
            s = stats(group)
            evidence[(phase, replacements)] = s
            valid = group.loc[group.measured_pass & group.complete_period] if len(group) else group
            fee = valid.transaction_costs.median() / 1e9 if len(valid) and 'transaction_costs' in valid else None
            rows.append([phase, replacements, 'not evaluated' if group.empty else f'{s["valid"]}/{s["requested"]}',
                         percent(s['median']), percent(s['p25']), percent(s['median_mdd']), percent(s['turnover']), percent(fee)])
    text = table(['Set', 'Max replacements/day', 'Valid / requested', 'Median return', 'P25', 'Median MDD', 'Median turnover', 'Median fees + tax / initial cash'], rows)
    text += '\n\nReplacement 2 is an additive post-freeze diagnostic, not a revision of the frozen candidate search. '
    text += 'Return, risk and fee medians condition on each candidate’s valid subset; coverage is compared on all requested windows. '
    if evidence.get(('validation', 1), {}).get('requested', 0) == 0:
        text += 'Replacement 1 was not evaluated on validation, so no 1-versus-2 validation claim is available. '
    b, r = evidence.get(('validation', 0)), evidence.get(('validation', 2))
    if b and r and b['requested'] and r['requested'] and b['median'] is not None and r['median'] is not None:
        if r['valid'] <= b['valid'] and r['median'] <= b['median']:
            text += 'Two replacements did not improve validation coverage or its conditional median relative to the baseline; it is not adopted.'
    return text


def year_column(frame):
    for column in ['start', 'start_date', 'episode_start']:
        if column in frame:
            return pd.to_datetime(frame[column]).dt.year
    if 'year' in frame:
        return pd.to_numeric(frame.year)
    raise ValueError('Episode evidence missing a start date/year column')


def yearly_table(frame, selected, additional=()):
    if not len(frame):
        return 'No saved episodes.'
    rows = []
    for candidate in dict.fromkeys([BASELINE, selected, *additional]):
        sample = subset(frame, candidate).copy()
        if sample.empty:
            continue
        sample['_year'] = year_column(sample)
        for year, group in sample.groupby('_year'):
            s = stats(group)
            rows.append([int(year), candidate, f'{s["valid"]}/{s["requested"]}',
                         s['incomplete'], percent(s['median']), percent(s['p25']),
                         percent(s['worst']), percent(s['median_mdd'])])
    return table(['Start year', 'Candidate', 'Measured valid / requested', 'Incomplete', 'Median', 'P25', 'Worst', 'Median MDD'], rows)


def failure_table(frames, selected, additional=()):
    rows = []
    for split, frame in frames.items():
        for candidate in dict.fromkeys([BASELINE, selected, *additional]):
            sample = subset(frame, candidate)
            failures = Counter()
            if len(sample):
                for row in sample.loc[~sample.measured_pass].to_dict('records'):
                    value = row.get('failure_reasons')
                    if value is None or pd.isna(value) or not str(value).strip():
                        value = row.get('episode_status', 'FAIL_OTHER')
                    for reason in set(str(value).split(';')):
                        failures[reason] += 1
            for reason, count in sorted(failures.items()):
                rows.append([split, candidate, reason, count, len(sample)])
    return table(['Set', 'Candidate', 'Failure reason', 'Episodes', 'Requested denominator'], rows) if rows else 'No measured failures were recorded; formal compliance remains unverified.'


def selection_id(selection):
    for key in ['selected_candidate_id', 'selected_candidate', 'candidate_id', 'selected',
                'research_best_candidate_id', 'diagnostic_candidate_id']:
        value = selection.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return selection_id(value)
    raise ValueError('final_selection.json has no selected candidate identity')


def date_range_label(frame):
    if frame.empty:
        return 'no saved episodes'
    start = next((c for c in ['start', 'start_date', 'episode_start'] if c in frame), None)
    end = next((c for c in ['end', 'end_date', 'episode_end'] if c in frame), None)
    if start is None:
        return 'date range unavailable'
    starts = pd.to_datetime(frame[start])
    label = f'{starts.min().date()}–{starts.max().date()} starts'
    if end is not None:
        label += f'; last episode ends {pd.to_datetime(frame[end]).max().date()}'
    return label


def penalty_table(output_dir, frames, selected, additional=()):
    """Display and independently check the producer's predeclared failure score."""
    rows = []
    for phase, frame in frames.items():
        path = Path(output_dir) / f'{phase}_summary.csv'
        if not path.exists() or frame.empty:
            continue
        saved = pd.read_csv(path)
        fields = {'candidate_id', 'failed_episode_penalty_return', 'penalized_mean_return', 'penalized_median_return'}
        if not fields.issubset(saved):
            continue
        for candidate in dict.fromkeys([BASELINE, selected, *additional]):
            evidence = saved.loc[saved.candidate_id.eq(candidate)]
            sample = subset(frame, candidate)
            if not len(evidence) or sample.empty:
                continue
            if len(evidence) != 1:
                raise ValueError(f'{path}: duplicate candidate summary')
            record = evidence.iloc[0]
            valid = sample.measured_pass & sample.complete_period & sample.episode_return.notna()
            values = sample.episode_return.where(valid, float(record.failed_episode_penalty_return))
            if not np.isclose(values.mean(), record.penalized_mean_return) or not np.isclose(values.median(), record.penalized_median_return):
                raise ValueError(f'{path}: saved penalty score does not match episode evidence')
            rows.append([phase, candidate, len(sample), percent(record.failed_episode_penalty_return),
                         percent(record.penalized_mean_return), percent(record.penalized_median_return)])
    return table(['Set', 'Candidate', 'All requested', 'Score assigned per failure', 'Mean score', 'Median score'], rows) if rows else ''


def local_snapshot(selection, study_manifest, root=None):
    """Resolve recorded snapshot identity inside this checkout, never an old workspace."""
    root = Path(root or ROOT)
    recorded = selection.get('cache_dir') or study_manifest.get('cache')
    expected = study_manifest.get('guard', {}).get('metadata_sha256')
    if not recorded or not expected:
        raise ValueError('Report requires recorded snapshot identity and metadata SHA256')
    name = Path(recorded).name
    if name in {'', '.', '..'}:
        raise ValueError('Invalid recorded snapshot identity')
    local_root = (root / 'data/yahoo_daily').resolve()
    local = (local_root / name).resolve()
    if not local.is_relative_to(local_root):
        raise ValueError('Report snapshot resolves outside the current checkout')
    metadata_path = local / 'metadata.json'
    if not metadata_path.exists():
        raise FileNotFoundError(f'Required snapshot is absent from this checkout: {metadata_path}')
    if hashlib.sha256(metadata_path.read_bytes()).hexdigest() != expected:
        raise ValueError('Local snapshot metadata hash differs from the recorded study')
    return local


def verified_supplement(output_dir):
    folder = Path(output_dir).parent / (Path(output_dir).name + '_supplement')
    status_path, audit_path = folder / 'status.json', folder / 'supplement_audit.json'
    if not status_path.exists() or not audit_path.exists():
        return None
    if json.loads(status_path.read_text()).get('status') != 'COMPLETE':
        return None
    main_seal = Path(output_dir) / 'run_manifest.json'
    if not main_seal.exists():
        raise ValueError('Supplement cannot be reported before the main run is sealed')
    audit = json.loads(audit_path.read_text())
    if audit.get('status') != 'PASS_INTERNAL_ACCOUNTING':
        raise ValueError('Supplement accounting audit did not pass')
    if hashlib.sha256(main_seal.read_bytes()).hexdigest() != audit['parent_run_manifest_sha256']:
        raise ValueError('Supplement refers to a different parent run seal')
    required = {'enriched_episodes.csv', 'extended_summary.csv',
                'monthly_comparison_2010_2026.csv', 'rolling_comparison_2010_2026.csv'}
    hashes = audit.get('output_sha256', {})
    if not required.issubset(hashes):
        raise ValueError('Supplement seal lacks required output hashes')
    for relative, digest in hashes.items():
        path = (folder / relative).resolve()
        if not path.is_relative_to(folder.resolve()):
            raise ValueError('Supplement artifact resolves outside its output folder')
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Supplement output hash mismatch: {relative}')
    result = {'folder': folder, 'audit': audit}
    for key, filename in [('enriched', 'enriched_episodes.csv'), ('summary', 'extended_summary.csv'),
                          ('monthly', 'monthly_comparison_2010_2026.csv'), ('rolling', 'rolling_comparison_2010_2026.csv')]:
        frame = pd.read_csv(folder / filename, float_precision='round_trip')
        for column in ['measured_pass', 'complete_period', 'valid_portfolio_reached']:
            if column in frame:
                frame[column] = truth(frame[column])
        result[key] = frame
    return result


def canonical_episode_rows(frame, source_file, source_hash):
    """Add explicit episode-contract names without losing native/partial evidence."""
    out = frame.copy()
    for field in ['complete_period', 'measured_pass']:
        out[field] = truth(out[field])
    if out.duplicated(['candidate_id', 'start']).any():
        raise ValueError('Canonical episode alias would duplicate a candidate/start')
    out['start_date'], out['end_date'] = out.start, out.end
    out['starting_cash'] = 1_000_000_000.
    if 'terminal_NAV' in out:
        out['native_terminal_NAV'] = out.terminal_NAV
    returns = pd.to_numeric(out.episode_return, errors='raise')
    if out.loc[~out.complete_period, 'episode_return'].notna().any():
        raise ValueError('Incomplete canonical episode cannot have a full-horizon return')
    out['terminal_NAV'] = (out.starting_cash * (1. + returns)).where(out.complete_period)
    out['return'] = returns.where(out.complete_period)
    for canonical, native in [('max_drawdown', 'episode_max_drawdown'), ('turnover', 'episode_turnover')]:
        if canonical in out:
            out['native_' + canonical] = out[canonical]
        out[canonical] = out[native].where(out.complete_period)
    if 'trade_count' not in out:
        out['trade_count'] = out.get('trades', pd.Series(np.nan, index=out.index))
    if 'status' in out:
        out['process_status'] = out.status
    out['status'] = out.get('episode_status', pd.Series('UNKNOWN', index=out.index))
    if 'execution_model' in out and out.execution_model.dropna().ne('DAILY_OPEN_RESEARCH_PROXY').any():
        raise ValueError('Canonical alias combines different execution assumptions')
    out['execution_model'] = 'DAILY_OPEN_RESEARCH_PROXY'
    out['formal_compliance_status'] = 'UNKNOWN_ACTIVE_SHARE_NOT_VERIFIED'
    out['alias_source_file'], out['alias_source_sha256'] = source_file, source_hash
    return out.sort_values(['candidate_id', 'start']).reset_index(drop=True)


def verified_diagnostics(output_dir, supplement):
    """Load the post-freeze family panel only after its seals and counts agree."""
    folder = Path(output_dir).parent / (Path(output_dir).name + '_diagnostics')
    audit_path, status_path = folder / 'diagnostics_audit.json', folder / 'status.json'
    if not audit_path.exists() or not status_path.exists():
        return None
    if json.loads(status_path.read_text()).get('status') != 'COMPLETE':
        return None
    if supplement is None:
        raise ValueError('Family diagnostics require the verified supplement')
    audit = json.loads(audit_path.read_text())
    if audit.get('adoption_allowed') is not False or audit.get('analysis_status') != 'POST_FREEZE_DIAGNOSTIC_NEVER_SELECTION_INPUT':
        raise ValueError('Family diagnostic scope permits adoption or lacks its post-freeze label')
    for key, path in [('main_run_manifest_sha256', Path(output_dir) / 'run_manifest.json'),
                      ('supplement_audit_sha256', supplement['folder'] / 'supplement_audit.json')]:
        if hashlib.sha256(path.read_bytes()).hexdigest() != audit.get(key):
            raise ValueError('Family diagnostic dependency hash mismatch: ' + key)
    required = {'monthly_all_candidates.csv', 'full_period_summary.csv', 'long_horizon.csv'}
    hashes = audit.get('output_sha256', {})
    if not required.issubset(hashes):
        raise ValueError('Family diagnostic seal lacks required output hashes')
    for relative, digest in hashes.items():
        path = (folder / relative).resolve()
        if not path.is_relative_to(folder.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError('Family diagnostic artifact hash mismatch: ' + relative)
    monthly = episodes(folder / 'monthly_all_candidates.csv')
    counts = monthly.groupby('candidate_id').size()
    if (len(monthly) != audit['monthly_attempts'] or len(counts) != audit['candidate_count']
            or counts.ne(audit['monthly_per_candidate']).any()
            or audit['parameter_set_count'] + audit['sanity_reference_count'] != len(counts)):
        raise ValueError('Family diagnostic monthly denominator mismatch')
    expected = set(monthly.loc[monthly.candidate_id.eq(BASELINE), 'start'])
    if not expected or any(set(group.start) != expected for _, group in monthly.groupby('candidate_id')):
        raise ValueError('Family diagnostic candidates do not share identical monthly starts')
    long_horizon = pd.read_csv(folder / 'long_horizon.csv', float_precision='round_trip')
    for column in ['complete_period', 'disqualified', 'measured_pass']:
        long_horizon[column] = truth(long_horizon[column])
    if (len(long_horizon) != audit['long_horizon_attempts'] or long_horizon.candidate_id.duplicated().any()
            or set(long_horizon.candidate_id) != set(counts.index)):
        raise ValueError('Family continuous-book denominator mismatch')
    if long_horizon.loc[~long_horizon.complete_period | long_horizon.disqualified, 'long_horizon_return'].notna().any():
        raise ValueError('Disqualified or partial book exposes a long-horizon return')
    return {'folder': folder, 'audit': audit, 'monthly': monthly, 'long_horizon': long_horizon}


def long_horizon_table(frame):
    rows = []
    for row in frame.sort_values('candidate_id').itertuples():
        rows.append([row.candidate_id, f'{row.observed_sessions}/{row.requested_sessions}',
                     row.observed_end, 'DQ' if row.disqualified else ('complete' if row.complete_period else 'incomplete'),
                     percent(row.long_horizon_return), percent(row.forensic_partial_return)])
    return table(['Candidate', 'Observed / requested sessions', 'Observed end', 'Book status',
                  'LONG_HORIZON_RETURN', 'Forensic partial return (not full horizon)'], rows)


def write_canonical_aliases(output_dir, supplement, verify=False):
    """Write new derived aliases only after both original and supplemental seals."""
    output_dir = Path(output_dir)
    if not (output_dir / 'run_manifest.json').exists():
        raise ValueError('Canonical aliases require sealed main outputs')
    enriched = supplement['enriched']
    main = enriched.loc[enriched.source_study.eq('original')]
    definitions = {
        'monthly_episodes.csv': (supplement['monthly'].loc[supplement['monthly'].candidate_id.eq(BASELINE)],
                                 supplement['folder'] / 'monthly_comparison_2010_2026.csv'),
        'rolling_episodes.csv': (supplement['rolling'], supplement['folder'] / 'rolling_comparison_2010_2026.csv'),
        'oct_nov_episodes.csv': (main.loc[main.phase.eq('oct_nov') & main.candidate_id.eq(BASELINE)],
                                 supplement['folder'] / 'enriched_episodes.csv'),
        'recent_regime.csv': (main.loc[main.phase.eq('recent_stress')], supplement['folder'] / 'enriched_episodes.csv'),
    }
    result = {}
    for name, (frame, source) in definitions.items():
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        out = canonical_episode_rows(frame, os.path.relpath(source, ROOT), digest)
        payload = out.to_csv(index=False, lineterminator='\n').encode()
        path = output_dir / name
        if path.exists():
            if path.read_bytes() != payload:
                raise ValueError(f'Existing canonical alias differs: {path}')
        elif verify:
            raise FileNotFoundError(f'Canonical alias is missing: {path}')
        else:
            with path.open('xb') as stream:
                stream.write(payload)
        result[name] = {'rows': len(out), 'distinct_starts': out.start.nunique(),
                        'sha256': hashlib.sha256(payload).hexdigest(),
                        'source_file': os.path.relpath(source, ROOT), 'source_sha256': digest}
    lineage = {'policy': 'ADDITIVE_POST_FREEZE_ALIASES_NO_SELECTION_CHANGE',
               'main_run_manifest_sha256': hashlib.sha256((output_dir / 'run_manifest.json').read_bytes()).hexdigest(),
               'supplement_audit_sha256': hashlib.sha256((supplement['folder'] / 'supplement_audit.json').read_bytes()).hexdigest(),
               'native_columns_preserved': True,
               'null_policy': 'Full-horizon return, terminal_NAV, drawdown and turnover are null for incomplete episodes; native and forensic fields remain available.',
               'status_semantics': 'Measured research episode status; formal compliance remains unknown.',
               'aliases': result}
    payload = (json.dumps(lineage, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode()
    path = output_dir / 'canonical_aliases.json'
    if path.exists() and path.read_bytes() != payload:
        raise ValueError('Canonical alias lineage differs')
    if not path.exists():
        if verify:
            raise FileNotFoundError('Canonical alias lineage is missing')
        with path.open('xb') as stream:
            stream.write(payload)
    return result


def render(output_dir, report_dir, verify=False):
    output_dir, report_dir = Path(output_dir), Path(report_dir)
    selection = json.loads((output_dir / 'final_selection.json').read_text())
    audit_path = output_dir / 'audit.json'
    if not audit_path.exists():
        raise FileNotFoundError('Final audit evidence is required before publishing the nine reports')
    run_manifest_path = output_dir / 'run_manifest.json'
    if run_manifest_path.exists():
        manifest = json.loads(run_manifest_path.read_text())
        for relative, expected in manifest['output_sha256'].items():
            if hashlib.sha256((output_dir / relative).read_bytes()).hexdigest() != expected:
                raise ValueError(f'Run output hash mismatch before reporting: {relative}')
    selected = selection_id(selection)
    diagnostic = selection.get('research_best_candidate_id') or selection.get('diagnostic_candidate_id')
    diagnostic = diagnostic if isinstance(diagnostic, str) and diagnostic != selected else None
    additional = [diagnostic] if diagnostic else []
    supplement = verified_supplement(output_dir)
    diagnostics = verified_diagnostics(output_dir, supplement)
    aliases = write_canonical_aliases(output_dir, supplement, verify=verify) if supplement else {}
    frames = {name: episodes(output_dir / f'{name}.csv') for name in PARTITIONS}
    seasonal = episodes(output_dir / 'oct_nov.csv')
    rolling = episodes(output_dir / 'rolling.csv', optional=True)
    walk = episodes(output_dir / 'walk_forward.csv', optional=True)
    recent_path = output_dir / ('recent_regime.csv' if (output_dir / 'recent_regime.csv').exists() else 'recent_stress.csv')
    recent = episodes(recent_path, optional=True)
    monthly_comparison = episodes(output_dir / 'monthly_comparison.csv', optional=True)
    sanity = episodes(output_dir / 'sanity_monthly.csv', optional=True)
    if supplement:
        for phase in frames:
            phases = ['development_coarse', 'development_local'] if phase == 'development' else [phase]
            frames[phase] = add_cold_fields(frames[phase], supplement['enriched'], phases)
        seasonal = add_cold_fields(seasonal, supplement['enriched'], ['oct_nov'])
        monthly_comparison = supplement['monthly']
        rolling = supplement['rolling']
        supplemental_walk = supplement['enriched'].loc[supplement['enriched'].source_study.eq('supplement')
            & supplement['enriched'].phase.eq('walk_forward_2025')]
        if len(supplemental_walk):
            walk = pd.concat([walk, supplemental_walk], ignore_index=True)
    candidates = pd.read_csv(output_dir / 'candidates.csv')
    candidate_count = candidates.candidate_id.nunique() if 'candidate_id' in candidates else len(candidates)
    declared_count = selection.get('number_of_candidates_evaluated', candidate_count)
    if isinstance(declared_count, dict):
        declared_count = declared_count.get('unique', declared_count.get('total'))
    if int(declared_count) < candidate_count:
        raise ValueError('Selection candidate count is smaller than saved candidate identities')
    if len(frames['holdout']) and selected not in set(frames['holdout'].candidate_id):
        raise ValueError('Selected candidate has no saved holdout rows')
    audit = json.loads(audit_path.read_text())
    audit_status = audit.get('status', audit.get('group_audits', 'NOT_AVAILABLE'))
    from src.yahoo_daily import calendar_amendment, load_metadata
    study_manifest_path = output_dir / 'study_manifest.json'
    study_manifest = json.loads(study_manifest_path.read_text()) if study_manifest_path.exists() else {}
    cache = local_snapshot(selection, study_manifest)
    metadata = load_metadata(cache)
    calendar_evidence = calendar_amendment(cache)
    source = lambda name: f'[{name}]({os.path.relpath(output_dir / name, report_dir)})'
    selected_holdout = stats(subset(frames['holdout'], selected))
    total_formal = selected_holdout['requested']
    common = (
        '**Interpretation:** `COMPETITION_UNIVERSE_STRESS_TEST`. The 2026 official 150-stock whitelist is applied '
        'retrospectively; survivorship and composition look-ahead prevent a point-in-time deployability claim. '
        'Each episode starts from TWD 1 billion and zero holdings for 24 observed trading sessions.\n\n'
        '**Metrics:** Return, drawdown, win/loss and turnover statistics below use only complete episodes passing '
        'the measured research checks. These include competition constraints plus stricter data and execution checks; '
        'a measured failure is not necessarily an official-rule violation. Coverage and pass rates retain every requested episode. Incomplete 24D returns '
        'stay null; partial forensic returns are not substitutes. ¹ All-complete median is a diagnostic that includes '
        'completed rule failures, so it is not compliant performance. Turnover is gross traded notional / initial cash.\n\n'
        '**Formal status:** `BLOCK_READY` / `BLOCK_SUBMISSION`; `ACTIVE_SHARE_NOT_VERIFIED`. Zero episodes have '
        'confirmed formal compliance. Measured PASS and arithmetic audit PASS do not verify official Active Share, '
        'platform settlement, company actions, current announcements or operational submission.\n'
    )
    brief_common = (
        '`COMPETITION_UNIVERSE_STRESS_TEST`; `BLOCK_READY` / `BLOCK_SUBMISSION`. Return statistics condition on measured-valid complete episodes; '
        'valid/requested counts retain failures and incomplete returns stay null. Formal compliance has **0 confirmed episodes**; '
        '`ACTIVE_SHARE_NOT_VERIFIED`. Full assumptions and the inherited parameter-selection confound are in the '
        '[main report](24d_strategy_summary.md).\n'
    )
    assumptions = (
        'Decisions and lot sizing use D−1 information; fills use the day’s Open as an explicit research proxy, '
        'not the official transaction-average price. Both sides include 0.1425% commission; sells also pay 0.3% tax. '
        'No market-impact or queue model establishes fill capacity; high volume participation remains a material limitation. '
        'Cash dividends are terminal NAV credits rather than reinvestable cash.\n\n'
        f'Yahoo snapshot: **{metadata["downloaded_count"]}/{metadata["requested_count"]} symbols**, '
        f'**{metadata["invalid_price_rows"]} invalid rows** and **{metadata["quality_alert_rows"]} quality-flagged rows** retained. '
        f'Calendar ends **{metadata["calendar"][-1] if metadata["calendar"] else "unavailable"}**. '
        '`auto_adjust=False`, `actions=True`, `repair=True`; raw library responses and separate nominal-share derivatives '
        'are hash-checked. Reversing vendor future split factors restores nominal units only if Yahoo’s action history '
        'is complete and consistent. Vendor repair is ex-post and can internally reconstruct from intraday data; '
        'the strategy uses daily features only, with no 4H gate. The observed weekday calendar combines the 0050 reference '
        f'with dates supported by at least 20 valid positive-volume official stocks; calendar_v2.json records {len(calendar_evidence["added_dates"])} sessions '
        'restored where the ETF had no qualifying bar. An official exchange calendar remains unverified. The 0050 series is not a verified '
        'return benchmark; its unrepaired discontinuity prevents treating it as a clean investment baseline.\n\n'
        'Observed severe price jumps are quarantined by the engine’s declared data-quality policy, with indicator '
        'warm-up restarted after the affected observation. This is a recorded research eligibility rule, not a repair '
        'or deletion of the vendor source. Missing historical names and failed episodes remain visible.\n\n'
        'The frozen legacy **309.16% LONG_HORIZON_RETURN** came from a different period and model; it is not a '
        '**24D_EPISODE_RETURN** and supplies no evidence for the selection here.\n'
    )
    assumptions += ('\n**Inherited selection confound:** Original x0352 parameters were selected using **2025–2026 data**. '
                    'The 2023–2024 holdout is excluded from this new search, but is **not a pristine prospective test of the strategy family**.\n')
    provenance = (
        f'Evidence: {source("final_selection.json")}, {source("audit.json")}, {source("episodes.csv")}, '
        f'{source("candidates.csv")}. Arithmetic audit status: **{audit_status}**.\n\n'
        'Reproduce in a fresh output directory using the three commands in [README](../README.md#重現與驗證); '
        f'they explicitly reuse `{os.path.relpath(cache, ROOT)}`. '
        'Check the delivered evidence with `.venv/bin/python scripts/verify_24d.py` and '
        '`.venv/bin/python scripts/report_24d.py --verify`.\n'
    )
    provenance += f'Calendar evidence: [calendar_v2.json]({os.path.relpath(cache / "calendar_v2.json", report_dir)}). Rules: [fixed rule coverage](../docs/v2_double_check_rules.md).\n'
    provenance += ('The organizer event page returned HTTP 403 during verification; the '
                   '[sponsor announcement](https://www.esunfhc.com/zh-tw/news-center/news-center/news/detail?id=1E8E47CF311B43E2B558F06B9CF79A55&p=C184F013F5EA4654A68BF10BF86AA6F5) '
                   'supports general mechanics only, not a verification of new detailed platform rules.\n')
    if supplement:
        provenance += f'Additive diagnostics: [supplement audit]({os.path.relpath(supplement["folder"] / "supplement_audit.json", report_dir)}). The frozen primary selection and its candidate count are unchanged.\n'
        provenance += 'Canonical episode tables: ' + ', '.join(
            f'{source(name)} ({record["rows"]} rows)' for name, record in aliases.items()) + '; '
        provenance += source('canonical_aliases.json') + ' records source hashes and null-value policy.\n'
    if diagnostics:
        provenance += f'Full-family post-freeze evidence: [diagnostic seal]({os.path.relpath(diagnostics["folder"] / "diagnostics_audit.json", report_dir)}). No adoption or retuning is permitted from these results.\n'
    brief_provenance = (f'Evidence: {source("final_selection.json")}, {source("audit.json")}. '
                        f'Audit: **{audit_status}**. [Full provenance and reproduction command](24d_strategy_summary.md).\n')
    population = f'(measured-valid {selected_holdout["valid"]}/{selected_holdout["requested"]} holdout)'
    selection_label = f'`{selected}`'
    if selection.get('decision') == 'NO_ELIGIBLE_CANDIDATE':
        selection_label += ' (diagnostic fallback; **NO_ELIGIBLE_CANDIDATE**, not adopted)'
    primary = '\n\n'.join([
        f'SELECTED STRATEGY: {selection_label}',
        f'SELECTION DATA: Development {date_range_label(frames["development"])}; validation {date_range_label(frames["validation"])}.',
        f'HOLDOUT DATA: {date_range_label(frames["holdout"])}; selection chronology and boundary purges are recorded in final_selection.json.',
        f'24D MEDIAN RETURN: {percent(selected_holdout["median"])} {population}',
        f'24D P25 RETURN: {percent(selected_holdout["p25"])} {population}',
        f'24D WORST RETURN: {percent(selected_holdout["worst"])} {population}',
        f'POSITIVE EPISODE RATE: {percent(selected_holdout["positive"])} {population}',
        f'COMPLIANCE PASS RATE: 0/{total_formal} formally confirmed; UNKNOWN. Measured rule pass: {selected_holdout["valid"]}/{total_formal} ({percent(selected_holdout["measured_pass_rate"])}).',
        f'MEDIAN MDD: {percent(selected_holdout["median_mdd"])} {population}',
    ]) + '\n'
    diagnostic_note = (f'`{diagnostic}` is a frozen **diagnostic neighbour, not adopted**. When the selection decision is '
                       '`NO_ELIGIBLE_CANDIDATE`, it **did not pass the eligibility gate**; the baseline remains frozen and blocked. '
                       'Its later-period comparison is diagnostic and cannot trigger retuning.\n\n') if diagnostic else ''
    summary = primary + '\n' + common + '\n' + diagnostic_note + '\n## Holdout comparison\n\n' + comparison(frames['holdout'], selected, diagnostic)
    if selection.get('decision') == 'NO_ELIGIBLE_CANDIDATE' and selected == BASELINE:
        summary += '\n\nBaseline versus selected strategy: **no new strategy qualified**. The single column is the unchanged baseline diagnostic fallback.\n'
    summary += '\n\n## Chronological results\n\n' + metric_table(frames, selected, additional)
    summary += '\n\n' + risk_table(frames, selected, additional) + '\n\n'
    if supplement:
        summary += 'Cold-start timing and tail-trim diagnostics are in the [baseline report](24d_x0352_baseline.md); '
        summary += 'the [recent-regime report](24d_recent_regime.md) separates 2024, 2025 and 2026.\n\n'
    if diagnostics:
        da = diagnostics['audit']
        summary += (f'The separate [family diagnostics](24d_parameter_search.md#full-family-post-freeze-diagnostics) cover '
                    f'**{da["candidate_count"]} fixed configurations × {da["monthly_per_candidate"]} monthly starts**: '
                    f'{da["parameter_set_count"]} parameter sets plus {da["sanity_reference_count"]} sanity reference. '
                    f'Only {declared_count} configurations entered the frozen search; later diagnostics cannot change adoption.\n\n')
    summary += ('Selection is restricted to the predeclared candidate neighborhood and measured feasibility; '
                'it does not establish the globally strongest strategy. Conditional returns must be read with the failed-episode counts.\n\n')
    summary += f'Recorded selection decision: **{selection.get("decision", "NOT_RECORDED")}**. '
    summary += f'Candidate above may be a diagnostic fallback when no candidate clears the recorded threshold; ready status: **{selection.get("ready_status", "BLOCK_READY")}**.\n\n'
    summary += '## Execution and data limits\n\n' + assumptions + '\n' + provenance
    summary += '\nDetails: [baseline](24d_x0352_baseline.md), [search](24d_parameter_search.md), [validation](24d_validation.md), [holdout](24d_holdout.md), [recent regime](24d_recent_regime.md), [seasonal analogues](24d_oct_nov_analogs.md), [failures](24d_failure_analysis.md), [frozen candidate](24d_final_candidate.md).\n'
    if len(sanity):
        if diagnostics:
            reference = diagnostics['monthly'].loc[diagnostics['monthly'].candidate_id.isin([BASELINE, selected, 'simple_momentum_reference', *additional])]
        else:
            shared_starts = set(sanity.start) & set(monthly_comparison.start) if len(monthly_comparison) else set()
            reference = pd.concat([monthly_comparison.loc[monthly_comparison.start.isin(shared_starts)],
                                   sanity.loc[sanity.start.isin(shared_starts)]], ignore_index=True) if shared_starts else sanity
        summary += '\n## Evaluation-only sanity reference\n\nThe simple-momentum reference uses the same dates, filters, planner and costs; it did not enter parameter selection.\n\n'
        summary += metric_table({'all_monthly': reference}, selected, additional + ['simple_momentum_reference']) + '\n'
    baseline_frames = {key: subset(value, BASELINE) for key, value in frames.items()}
    baseline = '# x0352 daily baseline\n\n' + common
    baseline += ('\nThe frozen x0352 parameters are transferred to daily indicators; the 4H availability gate is removed. '
                 'Signals retain the 10/30-day returns, EMA 10/30 and 100/200, MACD 8/21/5, volume/risk filters and portfolio rules. '
                 'The approximate score weights are 44% short return, 11% long return, 18% volume and 9% each MACD, medium trend and long trend. '
                 'These reports describe the saved engine run, not a reproduction of the legacy long-horizon return.\n\n')
    baseline += metric_table(baseline_frames, BASELINE) + '\n\n' + risk_table(baseline_frames, BASELINE)
    baseline += '\n\n## Distribution diagnostics\n\n' + distribution_table(baseline_frames, BASELINE)
    baseline += '\n\nTop-tail removal discards ceil(5% × valid episodes), capped to leave more than half the sample. '
    baseline += 'Symmetric trimming leaves the sample median unchanged by construction; it is reported for completeness, not evidence of improved robustness.\n'
    if supplement:
        baseline += '\n## Cold-start deployment\n\n' + cold_start_table(baseline_frames, BASELINE)
        baseline += '\n\nInvested fractions and holding counts are medians among observations available on each day; observed counts expose early stops. '
        baseline += 'Time to valid portfolio is conditional on reaching all measured portfolio checks, including holding count, cash, caps, stale quotes and odd shares; unreached episodes remain in the denominator. Formal Active Share remains unknown.\n'
    baseline += '\n\n## Monthly episodes by start year\n\n' + yearly_table(pd.concat(list(baseline_frames.values()), ignore_index=True), BASELINE)
    if diagnostics:
        all_monthly = subset(diagnostics['monthly'], BASELINE)
        baseline += '\n\n## Full-period monthly and continuous-book diagnostics\n\n'
        baseline += metric_table({'all_200_monthly_starts': all_monthly}, BASELINE)
        baseline += '\n\n' + distribution_table({'all_200_monthly_starts': all_monthly}, BASELINE)
        baseline += '\n\nThe following book starts once and carries its holdings forward; it is not a compound return of cash-reset episodes. '
        baseline += 'Disqualification stops the book and leaves the full-horizon return null.\n\n'
        baseline += long_horizon_table(subset(diagnostics['long_horizon'], BASELINE))
    baseline += '\n\n' + provenance
    search = '# 24D parameter search\n\n' + common
    search += f'\n**number_of_candidates_evaluated: {declared_count}.** Selected research candidate: `{selected}`. '
    search += 'The final choice is limited to this saved neighborhood; the final holdout is not a selection input.\n\n'
    search += '## Development candidates\n\n'
    rows = []
    for candidate, group in frames['development'].groupby('candidate_id'):
        s = stats(group)
        rows.append([candidate, f'{s["valid"]}/{s["requested"]}', percent(s['median']), percent(s['p25']), percent(s['p10']), percent(s['worst']), percent(s['median_mdd'])])
    search += table(['Candidate', 'Measured valid / requested', 'Median', 'P25', 'P10', 'Worst', 'Median MDD'], rows)
    shortlisted = sorted(set(frames['validation'].candidate_id) - {BASELINE, selected}) if len(frames['validation']) else []
    search += '\n\n## Validation comparison\n\n' + metric_table({'validation': frames['validation']}, selected, shortlisted)
    search += '\n\nThe validation shortlist contains diagnostic fallback entries when development eligibility fails; shortlist membership does not mean a candidate passed the development gate.\n'
    search += '\n\nThe exact candidate definitions, selection ordering, thresholds, purged episodes and recorded chronology are in '
    search += source('candidates.csv') + ' and ' + source('final_selection.json') + '. No unpublished refinement or holdout-driven parameter change is implied.\n\n'
    search += f'Recorded decision: **{selection.get("decision", "NOT_RECORDED")}**. Required measured pass threshold: '
    search += percent(selection.get('required_measured_pass_rate')) + '. '
    search += 'A diagnostic candidate is not an eligible winner when no candidate meets this threshold.\n\n'
    search += 'The frozen implementation did not use P10 as a ranking key; P10 is shown as a descriptive sensitivity metric. '
    search += 'The report preserves that recorded ordering and does not retroactively claim the recommended P10 ordering was applied.\n\n'
    if supplement:
        all_development_ids = sorted(set(frames['development'].candidate_id) - {BASELINE, selected})
        search += '## Cold-start behavior of every searched candidate\n\n'
        search += cold_start_table({'development': frames['development']}, selected, all_development_ids)
        search += '\n\nMedians use observed days; time-to-valid uses reached episodes, with reached/requested shown separately. '
        search += 'A 20-name count alone does not establish a valid portfolio.\n\n'
        search += '## Zero, one and two daily replacements\n\n' + replacement_comparison(frames, candidates, supplement) + '\n\n'
    penalties = penalty_table(output_dir, frames, selected, additional)
    if penalties:
        search += '## Full-denominator penalty diagnostic\n\nThe saved policy assigns a fixed negative score to each failed episode. '
        search += 'These penalty scores are **not realized portfolio returns**; they make failed-episode exclusions visible.\n\n' + penalties + '\n\n'
    search += '## Walk-forward evidence\n\n'
    if len(walk):
        policy = stats(walk)
        search += 'The walk-forward policy may choose different candidates by year using prior-year episodes only; this is family robustness evidence, not the fixed final strategy’s return.\n\n'
        search += table(['Measured valid / requested', 'Median', 'P25', 'P10', 'Worst', 'Median MDD'], [[
            f'{policy["valid"]}/{policy["requested"]}', percent(policy['median']), percent(policy['p25']),
            percent(policy['p10']), percent(policy['worst']), percent(policy['median_mdd'])]])
        search += '\n\n' + yearly_table(walk.assign(candidate_id='walk_forward_policy'), 'walk_forward_policy')
        decisions_path = output_dir / 'walk_forward_decisions.csv'
        if decisions_path.exists():
            decisions = pd.read_csv(decisions_path)
            fields = ['test_year', 'candidate_id', 'training_end', 'train_episodes', 'test_episodes', 'decision']
            if set(fields).issubset(decisions):
                search += '\n\n' + table(fields, decisions[fields].fillna('N/A').values.tolist())
        if supplement and (supplement['folder'] / 'walk_forward_2025_decision.json').exists():
            decision = json.loads((supplement['folder'] / 'walk_forward_2025_decision.json').read_text())
            search += '\n\nThe additive 2025 walk-forward diagnostic uses training ending ' + str(decision['training_end'])
            search += f'; frozen-family choice `{decision["candidate_id"]}`, decision **{decision["decision"]}**. '
            search += 'It is included in the policy summary above and cannot revise the fixed selection.\n'
    else:
        search += 'No saved walk-forward episode results; not claimed complete.'
    if diagnostics:
        da = diagnostics['audit']
        family = diagnostics['monthly']
        family_ids = sorted(set(family.candidate_id) - {BASELINE, selected})
        link = lambda name: f'[{name}]({os.path.relpath(diagnostics["folder"] / name, report_dir)})'
        search += '\n\n## Full-family post-freeze diagnostics\n\n'
        search += (f'**{da["candidate_count"]} configurations × {da["monthly_per_candidate"]} starts = {da["monthly_attempts"]} attempted episodes**. '
                   f'This includes {declared_count} searched configurations, the additive replacement-2 parameter set and the simple-momentum sanity reference; '
                   'it does not expand the frozen search or authorize a new winner. The periods include previously exposed development, validation and recent data.\n\n')
        search += metric_table({'2010–2026 monthly diagnostic': family}, selected, family_ids)
        search += '\n\nEvery configuration’s full mean/median/tails, best/worst, positive/loss rates, MDD, turnover, trade counts, validity and cold-start denominators are available in '
        search += link('full_period_summary.csv') + '; per-episode fees, evidence and original ledger lineage are in ' + link('monthly_all_candidates.csv') + '. '
        search += 'Top-tail removal and symmetric-trim medians remain conditional on valid episodes; a symmetric trim leaves the median unchanged by construction.\n'
        search += '\n## Continuous books across all fixed configurations\n\n'
        search += 'Each book has one initial cash allocation. DQ or incomplete books have no full-horizon return; forensic partial values do not establish long-horizon performance.\n\n'
        search += long_horizon_table(diagnostics['long_horizon'])
        search += '\n\nEvidence: ' + link('long_horizon.csv') + ' and ' + link('diagnostics_audit.json') + '.\n'
    search += '\n\n' + provenance
    validation_report = '# Validation results\n\n' + common
    validation_report += f'\nValidation uses {date_range_label(frames["validation"])}. '
    validation_report += f'The recorded decision is **{selection.get("decision", "NOT_RECORDED")}**; '
    validation_report += f'the required measured pass rate is **{percent(selection.get("required_measured_pass_rate"))}** in both development and validation. '
    validation_report += 'Validation ranks the development shortlist; no holdout result may alter that choice.\n\n'
    validation_ids = sorted(set(frames['validation'].candidate_id) - {BASELINE, selected}) if len(frames['validation']) else []
    validation_report += metric_table({'validation': frames['validation']}, selected, validation_ids)
    validation_report += '\n\n' + risk_table({'validation': frames['validation']}, selected, validation_ids)
    validation_report += '\n\n## Distribution diagnostics\n\n' + distribution_table({'validation': frames['validation']}, selected, validation_ids)
    if supplement:
        validation_report += '\n\n## Cold-start deployment\n\n' + cold_start_table({'validation': frames['validation']}, selected, validation_ids)
    validation_report += '\n\n' + failure_table({'validation': frames['validation']}, selected, validation_ids)
    validation_report += '\n\n' + provenance
    holdout = '# Holdout results\n\n' + common + '\n' + diagnostic_note + comparison(frames['holdout'], selected, diagnostic)
    registry_path = output_dir / 'episodes.csv'
    if registry_path.exists():
        registry = pd.read_csv(registry_path)
        if {'kind', 'split', 'start', 'end'}.issubset(registry):
            purged = registry.loc[registry.kind.eq('monthly') & registry.split.str.startswith('purged', na=False)]
            if len(purged):
                holdout += '\n\n## Boundary purges\n\nThese monthly windows cross partition boundaries and are excluded from selection and holdout scoring. '
                holdout += 'They remain in the full-period descriptive monthly panel.\n\n'
                holdout += table(['Start', 'End', 'Registry split'], purged[['start', 'end', 'split']].values.tolist())
    holdout += '\n\n## By start year\n\n' + yearly_table(frames['holdout'], selected, additional)
    holdout += '\n\n## Distribution diagnostics\n\n' + distribution_table({'holdout': frames['holdout']}, selected, additional)
    if supplement:
        holdout += '\n\n## Cold-start deployment\n\n' + cold_start_table({'holdout': frames['holdout']}, selected, additional)
    holdout += '\n\n## Rolling stress test\n\nOverlapping 24-session windows are dependent; their row count is not an independent sample size. '
    holdout += 'Rolling results are secondary stress evidence and must not revise the frozen selection.\n\n'
    holdout += metric_table({'rolling': rolling}, selected, additional) if len(rolling) else 'No saved rolling episode results; not claimed complete.'
    holdout += '\n\n' + provenance
    recent_report = '# Recent-regime stress test\n\n' + common
    recent_report += '\nThese post-freeze 2025–2026 episodes are stress evidence only and cannot alter the selected parameters. '
    recent_report += 'The inherited x0352 baseline was previously selected using 2025–2026 data, so this is not an untouched holdout for that baseline.\n\n'
    if len(recent):
        recent_report += f'Observed starts: {date_range_label(recent)}.\n\n'
        recent_report += metric_table({'recent_regime': recent}, selected, additional + ['simple_momentum_reference'])
        recent_report += '\n\n' + risk_table({'recent_regime': recent}, selected, additional + ['simple_momentum_reference'])
        recent_report += '\n\n' + yearly_table(recent, selected, additional + ['simple_momentum_reference'])
        if supplement:
            years = {str(year): monthly_comparison.loc[pd.to_datetime(monthly_comparison.start).dt.year.eq(year)] for year in [2024, 2025, 2026]}
            recent_report += '\n\n## Separate 2024 / 2025 / 2026 regimes\n\nThese are descriptive calendar-year groupings of all monthly comparison episodes; they do not replace the purged holdout denominator.\n\n'
            recent_report += metric_table(years, selected, additional)
            recent_report += '\n\n## Cold-start deployment\n\n' + cold_start_table({'recent_regime': recent}, selected, additional + ['simple_momentum_reference'])
        recent_report += '\n\n## Distribution diagnostics\n\n' + distribution_table({'recent_regime': recent}, selected, additional + ['simple_momentum_reference'])
        recent_report += '\n\n' + failure_table({'recent_regime': recent}, selected, additional + ['simple_momentum_reference'])
    else:
        recent_report += 'No saved recent-regime episodes are available; no performance claim is made.\n'
    recent_report += '\n\n' + provenance
    seasonal_report = '# October–November analogues\n\n`OCT_NOV_ANALOG` starts on the first observed session on or after October 26 and lasts 24 sessions. '
    seasonal_report += 'This small seasonal sample is secondary evidence, not an optimization target.\n\n' + common
    seasonal_report += '\n' + diagnostic_note + metric_table({'oct_nov': seasonal}, selected, additional) + '\n\n' + risk_table({'oct_nov': seasonal}, selected, additional)
    seasonal_report += '\n\n' + yearly_table(seasonal, selected, additional) + '\n\n' + provenance
    failures = '# Episode failure analysis\n\n' + common
    failures += '\nA single episode may have multiple failure reasons, so reason counts can exceed failed-episode counts. '
    failures += 'Failed and incomplete episodes remain in each requested denominator. These are **measured research failures**, not a count of certified official violations. '
    failures += '`FAIL_OTHER` can include a breach of the predeclared ±10% research execution-price envelope, which is not an official trading-rule limit.\n\n'
    failures += failure_table({**frames, 'oct_nov': seasonal, 'rolling': rolling, 'recent_stress': recent}, selected, additional)
    base_dev = subset(frames['development'], BASELINE)
    if {'failure_reasons', 'execution_price_bound_breaches', 'raw_rule_breach_days', 'start'}.issubset(base_dev):
        envelope = base_dev.loc[base_dev.failure_reasons.eq('FAIL_OTHER')
            & base_dev.complete_period & base_dev.execution_price_bound_breaches.gt(0)
            & base_dev.raw_rule_breach_days.eq(0)]
        if len(envelope):
            failures += '\n\nBaseline development episodes with `FAIL_OTHER`, a research price-envelope breach and no recorded raw-rule breach: '
            failures += ', '.join(envelope.start.astype(str)) + '. They are complete episodes excluded by the stricter research gate.\n'
    yearly = monthly_comparison if len(monthly_comparison) else pd.concat(list(frames.values()), ignore_index=True)
    failures += '\n\n## Monthly coverage and returns by start year\n\n' + yearly_table(yearly, selected, additional)
    failures += '\n\n## Data and execution causes\n\n' + assumptions + '\n' + provenance
    final = '# Frozen 24D research candidate\n\n' + primary + '\n' + common
    final += '\nThe selected fixed parameter set is a research artifact, not authorization to trade or submit. '
    final += 'Its current result applies only to the tested candidate family, data snapshot, execution proxy and episode definitions. '
    final += 'Daily data updates must not silently change the parameters.\n\n'
    final += '[Frozen configuration](../configs/competition_24d_final.json) · [Configuration metadata](../configs/competition_24d_final_metadata.json) · ' + source('final_selection.json') + '\n\n'
    final += 'The metadata companion records the actual data cutoff and distinguishes inactive legacy dates and selection labels from the active 24D configuration.\n\n'
    final += f'Recorded selection decision: **{selection.get("decision", "NOT_RECORDED")}**; ready status: **{selection.get("ready_status", "BLOCK_READY")}**.\n\n'
    config_path = output_dir / 'configs' / f'{selected}.json'
    if config_path.exists():
        config_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()
        if config_hash != selection.get('candidate_config_sha256'):
            raise ValueError('Frozen candidate configuration hash differs before reporting')
        config = json.loads(config_path.read_text())
        final += '## Frozen parameters\n\n'
        parameters = dict(config['full_tuning_params'])
        parameters['four_hour_mode'] = config['feature_spec']['four_hour_mode']
        final += table(['Parameter', 'Value'], sorted(parameters.items()))
        final += '\n\nThe active 4H mode is shown above. Nested `coverage_only` values preserve inactive legacy metadata; daily execution has `use_4h=False` and `match_4h_coverage=False`.\n\n'
        final += f'Base candidate configuration SHA256 (`outputs/24d/configs/{selected}.json`): `{config_hash}`. Selection freeze: `{selection.get("selection_frozen_at", "unavailable")}`.\n\n'
        frozen_path = output_dir.parent.parent / 'configs' / 'competition_24d_final.json'
        if frozen_path.exists():
            final += f'Final configuration SHA256 (`configs/competition_24d_final.json`): `{hashlib.sha256(frozen_path.read_bytes()).hexdigest()}`.\n\n'
    final += diagnostic_note + comparison(frames['holdout'], selected, diagnostic) + '\n\n' + assumptions + '\n' + provenance
    rendered = dict(strategy_summary=summary, x0352_baseline=baseline, parameter_search=search,
                    validation=validation_report, holdout=holdout, recent_regime=recent_report, oct_nov_analogs=seasonal_report,
                    failure_analysis=failures, final_candidate=final)
    for name in rendered:
        if name != 'strategy_summary':
            rendered[name] = (rendered[name].replace(common, brief_common)
                .replace(assumptions, 'Daily Open is a research fill proxy; capacity, official actions and source accuracy remain unverified. '
                         'See [the main report](24d_strategy_summary.md) for data-quality policy, calendar amendments and the 2025–2026 inherited-selection confound.\n')
                .replace(provenance, brief_provenance))
    if not verify:
        report_dir.mkdir(parents=True, exist_ok=True)
    obsolete = [report_dir / name for name in ['24d_summary.md', '24d_baseline_x0352.md', '24d_holdout_results.md']]
    for path in obsolete:
        if path.exists():
            if verify:
                raise ValueError(f'Obsolete generated report still exists: {path}')
            path.unlink()
    for name, text in rendered.items():
        path = report_dir / f'24d_{name}.md'
        payload = (text.rstrip() + '\n').encode('utf-8')
        if verify:
            if not path.exists():
                raise FileNotFoundError(f'Report is missing: {path}')
            if path.read_bytes() != payload:
                raise ValueError(f'Report differs from current verified evidence: {path}')
        else:
            path.write_bytes(payload)
    return {'reports': len(rendered), 'selected': selected, 'candidate_count': declared_count,
            'canonical_aliases': aliases, 'supplement_complete': supplement is not None,
            'family_diagnostics_complete': diagnostics is not None,
            'verification': 'PASS' if verify else 'GENERATED',
            'audit_status': audit_status}


def self_test():
    sample = pd.DataFrame({'candidate_id': ['a']*4, 'complete_period': [True, True, True, False],
                           'measured_pass': [True, True, False, False],
                           'episode_return': [.1, -.2, .9, np.nan],
                           'episode_max_drawdown': [.02, .2, .4, .1],
                           'episode_turnover': [1., 2., 3., 0.]})
    s = stats(sample)
    assert s['requested'] == 4 and s['complete'] == 3 and s['valid'] == 2
    assert s['incomplete'] == 1 and s['failed'] == 2 and s['measured_pass_rate'] == .5
    assert np.isclose(s['median'], -.05) and s['all_complete_median'] == .1
    assert s['positive'] == .5 and s['loss'] == .5 and s['formal_confirmed'] == 0
    assert stats(sample.iloc[:0])['median'] is None
    assert truth(pd.Series(['False', 'True', False, True])).tolist() == [False, True, False, True]
    import tempfile
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / 'episodes.csv'
        sample.to_csv(path, index=False)
        assert len(episodes(path)) == 4
        sample.loc[3, 'episode_return'] = .5
        sample.to_csv(path, index=False)
        try:
            episodes(path)
        except ValueError:
            pass
        else:
            raise AssertionError('Incomplete return was accepted')
    return {'self_test': 'PASS'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'outputs/24d')
    parser.add_argument('--report-dir', type=Path, default=ROOT / 'reports')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--verify', action='store_true', help='Recompute and compare reports/aliases without writing any files')
    args = parser.parse_args()
    print(json.dumps(self_test() if args.self_test else render(args.output_dir, args.report_dir, verify=args.verify), indent=2))
