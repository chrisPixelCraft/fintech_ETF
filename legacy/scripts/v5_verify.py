#!/usr/bin/env python3
"""Independent V5 study audit (docs/v5_spec.md §6, §9).

Checks, without importing strategy, planner, ledger or episode code:
  1. manifest: every path repo-relative, every output and input hash matches
  2. event chain: predecessor links, hashes, monotone clock, stage order, single freeze
  3. freeze: content hashes; holdout/diagnostics only after the freeze, only frozen configs
  4. completeness: every declared (candidate, episode) has exactly one sealed record; phases cover their splits
  5. accounting: for a seeded sample of episodes, rebuild cash, fees, tax, lots, caps, NAV and return from ledger CSVs
  6. prediction timing: every decision's observed date is strictly before its decision date, inside the episode

Writes <output>/verification.json with status PASS or FAIL (fail closed).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PHASE_SPLITS = {'development': {'development'}, 's2': {'development'}, 'validation': {'validation'},
                'holdout': {'holdout_retrospective'}, 'diagnostics': {'diagnostic_seasonal', 'diagnostic_recent'}}
STAGE_ORDER = ['study_start', 'candidates_declared', 'development_start', 's1_complete', 's2_declared', 's2_start',
               's2_complete', 'validation_start', 'validation_complete', 'freeze', 'holdout_start',
               'holdout_complete', 'diagnostics_start', 'diagnostics_complete', 'manifest']
POST_FREEZE = {'holdout_retrospective', 'diagnostic_seasonal', 'diagnostic_recent'}


class Audit:
    def __init__(self):
        self.failures, self.checks = [], {}

    def require(self, condition, message):
        if not condition:
            self.failures.append(message)
        return bool(condition)


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def _clean(value):
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def object_hash(value):
    text = json.dumps(_clean(value), sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(text.encode()).hexdigest()


def relative_ok(path):
    p = Path(str(path))
    return not p.is_absolute() and '..' not in p.parts and not str(path).startswith('~')


def repo(path):
    return ROOT / path


# ------------------------------------------------------------ 1. manifest
def check_manifest(audit, directory, manifest):
    bad_paths = [k for k in [*manifest['outputs'], *manifest['inputs'], *[a['path'] for a in manifest['attempts']],
                             *[v['path'] for v in manifest.get('scores', {}).values()]] if not relative_ok(k)]
    audit.require(not bad_paths, f'Non-relative manifest paths: {bad_paths[:5]}')
    audit.require(relative_ok(manifest.get('study', '')), 'Study path not relative')
    text = (directory / 'manifest.json').read_text()
    audit.require(str(ROOT) not in text, 'Absolute repository path embedded in manifest')
    mismatched = [k for k, v in manifest['outputs'].items() if not repo(k).is_file() or file_hash(repo(k)) != v]
    audit.require(not mismatched, f'Output hash mismatch: {mismatched[:5]}')
    inputs = [k for k, v in manifest['inputs'].items() if not repo(k).is_file() or file_hash(repo(k)) != v]
    audit.require(not inputs, f'Input hash mismatch (code/data changed after the run): {inputs[:5]}')
    scores = [k for k, v in manifest.get('scores', {}).items()
              if not repo(v['path']).is_file() or file_hash(repo(v['path'])) != v['sha256']]
    audit.require(not scores, f'Score table hash mismatch: {scores[:5]}')
    features = manifest.get('features', {})
    for name in features.get('inputs', {}):
        audit.require(relative_ok(name), 'Feature manifest path not relative: ' + name)
    audit.checks['manifest'] = dict(outputs=len(manifest['outputs']), inputs=len(manifest['inputs']),
                                    scores=len(manifest.get('scores', {})))


# --------------------------------------------------------- 2/3. events
def check_events(audit, directory, freeze, attempts):
    events = [json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines() if line.strip()]
    previous, previous_time = None, None
    for index, row in enumerate(events):
        audit.require(row.get('previous_hash') == previous, f'Event chain broken at {index}')
        body = {k: v for k, v in row.items() if k != 'hash'}
        audit.require(row.get('hash') == object_hash(body), f'Event hash mismatch at {index}')
        stamp = pd.Timestamp(row['time'])
        audit.require(previous_time is None or stamp >= previous_time, f'Event clock reverses at {index}')
        previous, previous_time = row.get('hash'), stamp
    first = {}
    for index, row in enumerate(events):
        first.setdefault(row['event'], index)
    missing = [name for name in STAGE_ORDER if name not in first]
    audit.require(not missing, f'Missing stage events: {missing}')
    present = [first[name] for name in STAGE_ORDER if name in first]
    audit.require(present == sorted(present), 'Stage events out of order')
    freezes = [e for e in events if e['event'] == 'freeze']
    audit.require(len(freezes) == 1, 'Expected exactly one freeze event')
    freeze_time = None
    if freezes:
        freeze_time = pd.Timestamp(freezes[0]['time'])
        audit.require(freezes[0]['payload'].get('freeze_sha256') == file_hash(directory / 'freeze.json'),
                      'freeze.json differs from the chained freeze event')
        audit.require(pd.Timestamp(freeze['frozen_at']) <= freeze_time, 'Freeze event predates the frozen file')
    manifests = [e for e in events if e['event'] == 'manifest']
    audit.require(bool(manifests) and manifests[-1]['payload'].get('manifest_sha256') == file_hash(directory / 'manifest.json'),
                  'manifest.json differs from the chained manifest event')
    # Post-freeze runs happen only after the freeze and only for frozen configs.
    late = [a for a in attempts if a['split'] in POST_FREEZE
            and (freeze_time is None or pd.Timestamp(a['run_started_at']) < freeze_time)]
    audit.require(not late, f'{len(late)} holdout/diagnostic episodes ran before the freeze')
    # Planned denominators from phase-start events.
    planned = {}
    for e in events:
        if e['event'].endswith('_start') and 'candidates' in e['payload']:
            phase = e['event'][:-len('_start')]
            planned.setdefault(phase, set()).update((c, x) for c in e['payload']['candidates'] for x in e['payload']['episodes'])
    audit.checks['events'] = dict(count=len(events), freeze_time=str(freeze_time), post_freeze_runs=sum(
        a['split'] in POST_FREEZE for a in attempts))
    return events, planned


def check_freeze(audit, directory, freeze, records):
    for family, config in freeze['candidates'].items():
        audit.require(object_hash(config) == freeze['config_hashes'][family], f'Frozen config hash mismatch: {family}')
    for name, digest in freeze['evidence'].items():
        audit.require((directory / name).is_file() and file_hash(directory / name) == digest,
                      f'Frozen evidence changed: {name}')
    frozen = {c['id']: c for c in freeze['candidates'].values()}
    allowed = set(frozen) | {freeze['baseline_id']}
    post = records.loc[records.split.isin(POST_FREEZE)]
    audit.require(set(post.candidate) <= allowed, 'Unfrozen candidate evaluated on holdout/diagnostics')
    for cid in allowed:
        rows = post.loc[post.candidate.eq(cid)]
        audit.require(set(rows.split) >= {'holdout_retrospective'}, f'Frozen candidate {cid} lacks holdout records')
    for row in post.itertuples():
        if row.candidate in frozen:
            config = json.loads((repo(row.path) / 'strategy_config.json').read_text())
            audit.require(config == _clean(frozen[row.candidate]), f'Post-freeze config differs: {row.candidate}/{row.episode}')
    audit.require(freeze.get('preferred') is None or freeze['preferred'] in frozen, 'Preferred candidate not frozen')


# ---------------------------------------------------- 4. completeness
def check_completeness(audit, directory, records, planned, registry):
    by_split = {}
    for e in registry:
        by_split.setdefault(e['split'], set()).add(e['episode_id'])
    audit.require(not records.duplicated(['candidate', 'episode']).any(), 'Duplicate (candidate, episode) records')
    actual = set(zip(records.candidate, records.episode))
    union = set().union(*planned.values()) if planned else set()
    audit.require(actual == union, f'Record set differs from declared denominators '
                                   f'(missing {len(union - actual)}, undeclared {len(actual - union)})')
    for phase, pairs in planned.items():
        episodes = {x for _, x in pairs}
        expected = set().union(*[by_split.get(s, set()) for s in PHASE_SPLITS.get(phase, set())])
        audit.require(episodes == expected, f'Phase {phase} does not cover its registered split exactly')
    split_of = {e['episode_id']: e['split'] for e in registry}
    wrong = [r for r in records.itertuples() if split_of.get(r.episode) != r.split]
    audit.require(not wrong, f'{len(wrong)} records carry a split different from the registry')
    bad = 0
    for row in records.itertuples():
        location = repo(row.path)
        if not relative_ok(row.path) or not (location / 'complete.json').is_file():
            bad += 1
            continue
        sealed = json.loads((location / 'complete.json').read_text())
        if any(not (location / n).is_file() or file_hash(location / n) != h for n, h in sealed['hashes'].items()):
            bad += 1
            continue
        config = json.loads((location / 'strategy_config.json').read_text())
        if config.get('id') != row.candidate or sealed['candidate_hash'] != object_hash(config):
            bad += 1
    audit.require(bad == 0, f'{bad} episode records fail their sealed hashes or identity')
    audit.checks['completeness'] = dict(records=len(records), phases={k: len(v) for k, v in planned.items()})


# ------------------------------------------------------ 5. accounting
def _read(path):
    return pd.read_csv(path) if path.is_file() and path.stat().st_size else pd.DataFrame()


def reconstruct(location, record, tolerance=1.0):
    """Rebuild the book from trades and closes; return a list of discrepancy strings."""
    errors = []
    config = json.loads((location / 'config.json').read_text())
    lot = float(config.get('lot_size', 1000))
    commission, tax_rate = float(config['commission']), float(config['sell_tax'])
    initial = float(config['initial_cash'])
    cap, tsmc = float(config.get('max_weight', .10)), float(config.get('tsmc_max_weight', .25))
    equity, trades, holdings = _read(location / 'equity.csv'), _read(location / 'trades.csv'), _read(location / 'holdings.csv')
    if equity.empty:
        return [] if not record['complete'] else ['complete episode without equity']
    if len(trades):
        notional = trades.shares.abs() * trades.price
        if not np.allclose(trades.notional, notional, rtol=1e-12, atol=1e-6):
            errors.append('notional != |shares| x price')
        if not np.allclose(trades.fee, notional * commission, rtol=1e-12, atol=1e-6):
            errors.append('fee != notional x commission')
        tax = np.where(trades.shares < 0, notional * tax_rate, 0.)
        if not np.allclose(trades.tax, tax, rtol=1e-12, atol=1e-6):
            errors.append('tax differs from sell-side rate')
        odd = trades.shares.abs() % lot > 1e-6
        if odd.any():
            errors.append(f'{int(odd.sum())} trades not in board lots')
        flows = (trades.shares * trades.price + trades.fee + trades.tax).groupby(trades.date.astype(str)).sum()
        fees = trades.fee.groupby(trades.date.astype(str)).sum()
        taxes = trades.tax.groupby(trades.date.astype(str)).sum()
    else:
        flows = fees = taxes = pd.Series(dtype=float)
    cash = initial
    for row in equity.itertuples():
        day = str(row.date)
        cash -= float(flows.get(day, 0.))
        if abs(cash - float(row.cash)) > tolerance:
            errors.append(f'{day}: cash {row.cash:.2f} != rebuilt {cash:.2f}')
            break
        if float(row.cash) < -tolerance:
            errors.append(f'{day}: negative cash')
        if abs(float(row.fees) - float(fees.get(day, 0.))) > 1e-6 or abs(float(row.taxes) - float(taxes.get(day, 0.))) > 1e-6:
            errors.append(f'{day}: daily fee/tax totals differ from trades')
    if len(holdings):
        holdings['date'] = holdings.date.astype(str)
        eq = equity.assign(date=equity.date.astype(str)).set_index('date')
        for day, rows in holdings.groupby('date'):
            if day not in eq.index:
                continue
            e = eq.loc[day]
            value = float((rows.shares * rows.close).sum())
            if abs(float(e.cash) + value - float(e.nav)) > tolerance + 1e-9 * float(e.nav):
                errors.append(f'{day}: NAV != cash + marked holdings')
            if int(e.holdings) != len(rows):
                errors.append(f'{day}: holding count differs')
            odd = int((rows.shares % lot > 1e-6).sum())
            if 'odd_residual_names' in e and int(e.odd_residual_names) != odd:
                errors.append(f'{day}: odd-lot residual count differs')
            weights = rows.shares * rows.close / float(e.nav)
            caps = np.where(rows.symbol.astype(str).str.split('.').str[0].eq('2330'), tsmc, cap)
            over = rows.symbol[weights.to_numpy() > caps + 1e-9].astype(str)
            flagged = ';'.join(str(e.get(k, '') or '') for k in ('active_cap_breaches', 'passive_cap_breaches', 'violations'))
            unflagged = [s for s in over if s not in flagged]
            if unflagged:
                errors.append(f'{day}: unflagged cap breach {unflagged}')
    if record['complete'] and record['episode_return'] is not None and 'economic_nav' in equity:
        rebuilt = float(equity.economic_nav.iloc[-1]) / initial - 1
        if abs(rebuilt - float(record['episode_return'])) > 1e-9:
            errors.append('recorded episode_return differs from the ledger terminal NAV')
    return errors


def check_timing(location, episode):
    path = location / 'decisions.csv'
    if not path.is_file():
        return ['missing decisions.csv']
    errors = []
    predictions = _read(location / 'predictions.csv')
    if len(predictions) and {'signal_date', 'decision_date'} <= set(predictions):
        if not (pd.to_datetime(predictions.signal_date) < pd.to_datetime(predictions.decision_date)).all():
            errors.append('prediction signal date not strictly before decision date')
    frame = _read(path)
    if frame.empty:
        return errors
    decision = pd.to_datetime(frame.decision_date)
    observed = pd.to_datetime(frame.observed_date)
    if not (observed < decision).all():
        errors.append('observed date not strictly before decision date')
    if not decision.dt.strftime('%Y-%m-%d').isin(episode['sessions']).all():
        errors.append('decision date outside the episode sessions')
    if frame.decision_date.duplicated().any():
        errors.append('duplicate decision dates')
    return errors


def check_accounting(audit, records, registry, sample, seed):
    by_id = {e['episode_id']: e for e in registry}
    rows = records.to_dict('records')
    rng = random.Random(seed)
    chosen = rng.sample(rows, min(sample, len(rows)))
    # Always include every disqualified/incomplete record in the sample denominator check.
    keys = {(r['candidate'], r['episode']) for r in chosen}
    chosen += [r for r in rows if not r['complete'] and (r['candidate'], r['episode']) not in keys][:sample]
    problems, timing = {}, {}
    for r in chosen:
        location = repo(r['path'])
        errors = reconstruct(location, r)
        if errors:
            problems[f"{r['candidate']}/{r['episode']}"] = errors[:5]
        if r['family'] != 'V3':
            late = check_timing(location, by_id[r['episode']])
            if late:
                timing[f"{r['candidate']}/{r['episode']}"] = late
    audit.require(not problems, f'Accounting reconstruction failed for {len(problems)} sampled episodes')
    audit.require(not timing, f'Prediction timing failed for {len(timing)} sampled episodes')
    audit.checks['accounting'] = dict(sampled=len(chosen), failures=problems)
    audit.checks['prediction_timing'] = dict(sampled=sum(r['family'] != 'V3' for r in chosen), failures=timing)


# ---------------------------------------------------------------- driver
def verify(directory, sample=60, seed=20261026):
    directory = Path(directory)
    directory = directory if directory.is_absolute() else ROOT / directory
    audit = Audit()
    try:
        manifest = json.loads((directory / 'manifest.json').read_text())
        freeze = json.loads((directory / 'freeze.json').read_text())
        registry_path = next((k for k in manifest['inputs'] if k.endswith('episodes.json')), None)
        audit.require(registry_path is not None, 'Episode registry not bound in manifest inputs')
        registry = json.loads(repo(registry_path).read_text())
        # Predeclared warm-up exclusions live in the hash-bound grid file; they are
        # removed from every denominator identically for all candidates.
        start = next((json.loads(line) for line in (directory / 'events.jsonl').read_text().splitlines()
                      if line.strip() and json.loads(line).get('event') == 'study_start'), {})
        grids_path = start.get('payload', {}).get('grids')
        audit.require(grids_path in manifest['inputs'], 'Grid file not bound in manifest inputs')
        excluded = set(json.loads(repo(grids_path).read_text()).get('excluded_episodes', {}))
        registry = [e for e in registry if e['episode_id'] not in excluded]
        check_manifest(audit, directory, manifest)
        records = pd.read_csv(directory / 'all_episodes.csv').replace({np.nan: None})
        attempts = manifest['attempts']
        audit.require(set((a['candidate'], a['episode']) for a in attempts) == set(zip(records.candidate, records.episode)),
                      'Manifest attempts differ from all_episodes.csv')
        _, planned = check_events(audit, directory, freeze, attempts)
        check_freeze(audit, directory, freeze, records)
        check_completeness(audit, directory, records, planned, registry)
        check_accounting(audit, records, registry, sample, seed)
    except Exception as error:  # fail closed on any unreadable evidence
        audit.failures.append(f'{type(error).__name__}: {error}')
    status = 'PASS' if not audit.failures else 'FAIL'
    manifest_path = directory / 'manifest.json'
    result = dict(status=status, failures=audit.failures, checks=audit.checks,
                  manifest_sha256=file_hash(manifest_path) if manifest_path.exists() else None,
                  freeze_sha256=file_hash(directory / 'freeze.json') if (directory / 'freeze.json').exists() else None,
                  scope='INDEPENDENT_ACCOUNTING_PROVENANCE_AND_DECLARED_TIMING; NOT PLATFORM CERTIFICATION',
                  verified_at=pd.Timestamp.now(tz='UTC').isoformat())
    (directory / 'verification.json').write_text(json.dumps(_clean(result), indent=2, sort_keys=True, default=str) + '\n')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', default='outputs/v5/study')
    parser.add_argument('--sample', type=int, default=60)
    parser.add_argument('--seed', type=int, default=20261026)
    args = parser.parse_args(argv)
    result = verify(args.output, args.sample, args.seed)
    print(json.dumps(dict(status=result['status'], failures=result['failures'][:20]), indent=2))
    sys.exit(0 if result['status'] == 'PASS' else 1)


if __name__ == '__main__':
    main()
