"""Physically truncate and perturb future market inputs for the selected release."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.tuning_a_deep import context
from tests.test_double_check_causality import verify_selected_config


def choose_prefix_window(cfg, daily, universe, future_sessions=15):
    """Keep fixed membership, original start/end, and genuine future sessions."""
    dates = pd.to_datetime(daily.date)
    end = pd.Timestamp(cfg['end'])
    calendar = sorted(dates[(dates >= pd.Timestamp(cfg['start'])) & (dates <= end)].unique())
    if len(calendar) <= future_sessions:
        raise ValueError('Insufficient market sessions for a meaningful future window')
    available = daily.assign(date=dates)
    first = available[available.symbol.isin(set(universe.symbol))].groupby('symbol').date.min()
    if set(first.index) != set(universe.symbol):
        raise ValueError('Fixed universe missing source history')
    cutoff = max(pd.Timestamp(calendar[-future_sessions-1]), first.max())
    if cutoff >= pd.Timestamp(calendar[-1]):
        raise ValueError('Cannot preserve full universe and leave any future session')
    return dict(start=cfg['start'], cutoff=str(cutoff.date()), end=cfg['end'])


def main(root):
    output = root / 'prefix_audit.json'
    if output.exists():
        raise FileExistsError(output)
    manifest = json.loads((root / 'manifest.json').read_text())
    if not manifest['outputs_complete']:
        raise ValueError('Completed study required')
    selected = json.loads((root / 'selection.json').read_text())
    results = {}
    for track in ('official_ex_post', 'historical_pit'):
        cfg_path = root / track / 'final/v2_double_check_fintuned/config.json'
        cfg = json.loads(cfg_path.read_text())
        if cfg['full_tuning_params'] != selected['params']:
            raise ValueError('Selected parameters mismatch')
        ctx = context(track)
        window = choose_prefix_window(cfg, ctx['daily'], ctx['universe'])
        print('PREFIX ' + track + ' ' + json.dumps(window), flush=True)
        result = verify_selected_config(cfg, ctx['daily'], ctx['universe'], ctx['bars'],
                                        **window)
        result.update(candidate_id=selected['candidate_id'],
                      config_sha256=hashlib.sha256(cfg_path.read_bytes()).hexdigest())
        results[track] = result
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(results, indent=2, allow_nan=False) + '\n')
    temporary.rename(output)
    print(json.dumps(results, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'outputs/v2_double_check_fintuned')
    args = parser.parse_args()
    main(args.output)
