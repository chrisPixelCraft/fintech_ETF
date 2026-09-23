"""Pinned ex-post research configurations; no runtime winner selection or live orders.

The three public entrypoints share the audited engine and frozen input manifest.
A/B p005 and C p006 were selected by the predeclared rule-first ordering, not
by maximum historical return. Replaying them is development evidence only.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import platform
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from scripts import run_v2_tuning as tuning
from src.backtest import save_result
from src.tuning_features import IsolatedEngine

ROOT = Path(__file__).resolve().parents[1]
TUNING_OUTPUT = ROOT / 'outputs/v2_abc_tuning_20260922'
SELECTED_CANDIDATES = {'A': 'p005', 'B': 'p005', 'C': 'p006'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_frozen_inputs(root=ROOT):
    root = Path(root)
    manifest = json.loads((root / 'outputs/v2_abc_tuning_20260922/tuning_manifest.json').read_text())
    if not manifest.get('outputs_complete'):
        raise ValueError('The source tuning run is incomplete')
    for relative, expected in manifest['hashes'].items():
        if sha(root / relative) != expected:
            raise ValueError('Frozen dependency changed: ' + relative)
    for key, relative in [('config', 'config/strategy_v2.json'),
                          ('study', 'config/v2_tuning_study.json')]:
        if manifest[key] != json.loads((root / relative).read_text()):
            raise ValueError('Embedded manifest differs from verified ' + relative)
    return manifest


def make_config(strategy, candidate_id, parameters):
    """Validate a literal pinned choice; never inspect or rank trial performance."""
    if SELECTED_CANDIDATES.get(strategy) != candidate_id:
        raise ValueError('Unsupported pinned strategy/candidate')
    manifest = verify_frozen_inputs()
    study = manifest['study']
    trial = next(t for t in study['candidates'] if t['candidate_id'] == candidate_id)
    if dict(parameters) != trial['params']:
        raise ValueError('Entrypoint parameters differ from the frozen candidate')
    config = tuning.trial_config(manifest['config'], study, trial, strategy)
    config['study_policy'].update(parameter_search=False,
                                  selected_from_parameter_search=True,
                                  selection_scope='EX_POST_DEVELOPMENT')
    return config


def prepare_context():
    verify_frozen_inputs()
    tuning.setup_context()
    return tuning.CTX


def run_fixed(strategy, config, context=None):
    """Recalculate all signals, orders, fills and NAV for one fixed configuration."""
    context = prepare_context() if context is None else context
    parameters = config['tuning_params']
    callback = None
    if strategy in {'B', 'C'}:
        callback = context['signals'].make_callback(strategy, dict(
            target_count=config['target_count'], min_count=config['min_count'],
            sector_top_fraction=parameters['sector_top_fraction'],
            sector_short_weight=parameters['sector_short_weight'],
            sector_fallback_mode=parameters['sector_fallback_mode'],
            c_alpha=parameters['c_alpha']))
    result = IsolatedEngine(context['cache']).run_v2(
        context['daily'], context['universe'], copy.deepcopy(config), context['bars'],
        signal_transform=callback)
    if len(result['holdings'].query("symbol == '2888.TW' and date >= '2025-07-24'")):
        raise ValueError('Unsupported multi-security merger held; quarantine result')
    enrich_metrics(result, config['initial_cash'])
    result['metrics'].update(candidate_id=config['tuning_candidate_id'],
                             selection_scope='EX_POST_DEVELOPMENT')
    return result


def enrich_metrics(result, initial_cash):
    """Add diagnostics without overwriting the original book-NAV metric fields."""
    extra = tuning.period_metrics(result['equity'], initial_cash)
    for name in ['hard_breach_days', 'infeasible_executed_days', 'trade_days',
                 'costs', 'final_economic_nav']:
        result['metrics'][name] = extra[name]


def create_output(path):
    path = Path(path).resolve()
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise FileExistsError('Existing outputs preserved; choose an empty output directory')
    path.mkdir(parents=True, exist_ok=True)
    return path


def begin_provenance(output, selected):
    manifest = verify_frozen_inputs()
    files = list(manifest['hashes']) + [
        'src/v2_best.py', 'v2_A_best.py', 'v2_B_best.py', 'v2_C_best.py',
        'scripts/run_v2_best.py']
    hashes = {relative: sha(ROOT / relative) for relative in dict.fromkeys(files)}
    for relative in hashes:
        destination = output / 'input_snapshot' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    provenance = dict(
        started_at=datetime.now(timezone.utc).isoformat(), command=sys.argv,
        python=platform.python_version(), hashes=hashes, config=manifest['config'],
        selected_candidates=selected, selection_scope='EX_POST_DEVELOPMENT',
        selection_rule=manifest['study']['selection'],
        historical_period_is_development=True, parameter_search=False,
        research_shadow=True, trust='CANDIDATE_PENDING_INDEPENDENT_AUDIT',
        source_study='outputs/v2_abc_tuning_20260922', outputs_complete=False)
    tuning.dump(output / 'provenance.json', provenance)
    return provenance


def finish_provenance(output, provenance):
    for relative, expected in provenance['hashes'].items():
        if sha(ROOT / relative) != expected:
            raise ValueError('Dependency changed while running: ' + relative)
    provenance.update(outputs_complete=True, completed_at=datetime.now(timezone.utc).isoformat())
    tuning.dump(output / 'provenance.json', provenance)


def single_cli(strategy, candidate_id, parameters, argv=None):
    parser = argparse.ArgumentParser(description=f'Frozen v2 {strategy} {candidate_id} research replay')
    parser.add_argument('--output', default=f'outputs/v2_{strategy}_best')
    parser.add_argument('--show-config', action='store_true')
    args = parser.parse_args(argv)
    config = make_config(strategy, candidate_id, parameters)
    if args.show_config:
        print(json.dumps(config, indent=2, ensure_ascii=False))
        return
    output = create_output(args.output)
    provenance = begin_provenance(output, {strategy: candidate_id})
    result = run_fixed(strategy, config)
    save_result(result, output / strategy)
    pd.DataFrame(tuning.monthly_rows(result['equity'], config['initial_cash'])).to_csv(output / 'monthly.csv', index=False)
    finish_provenance(output, provenance)
    print(json.dumps(result['metrics'], indent=2, ensure_ascii=False))
