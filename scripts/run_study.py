"""Run frozen v1 and predeclared controls; never choose a winning configuration."""
import argparse
import copy
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from src.backtest import aggregate_four_hour, file_hash, run_backtest, save_result
from src.benchmarks import run_buy_hold


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--daily', default='data/processed/daily_canonical.csv')
    parser.add_argument('--hourly', default='data/processed/hourly_canonical.csv')
    parser.add_argument('--universe', default='data/processed/universe_20251231.csv')
    parser.add_argument('--config', default='config/strategy_v1.json')
    parser.add_argument('--execution', choices=['vwap', 'next_open'], default='next_open')
    parser.add_argument('--output', default='outputs/backtest_2026_v1')
    args = parser.parse_args()
    paths = {key: ROOT / getattr(args, key) for key in ['daily', 'hourly', 'universe', 'config']}
    config = json.loads(paths['config'].read_text())
    config['execution'] = args.execution
    if args.execution == 'vwap':
        config['slippage_bps'] = 0.
    daily = pd.read_csv(paths['daily'], dtype={'symbol': str})
    hourly = pd.read_csv(paths['hourly'], dtype={'symbol': str})
    universe = pd.read_csv(paths['universe'], dtype={'symbol': str})
    if 'known_at' not in universe and 'known_at_assumption' in universe:
        universe['known_at'] = universe['known_at_assumption']
    bars = aggregate_four_hour(hourly)
    output = ROOT / args.output
    output.mkdir(parents=True, exist_ok=True)
    provenance = dict(run_started_at=datetime.now(timezone.utc).isoformat(),
                      command=sys.argv, python=platform.python_version(), pandas=pd.__version__, numpy=np.__version__,
                      hashes={str(p.relative_to(ROOT)): file_hash(p) for p in [*paths.values(), ROOT / 'src/backtest.py', ROOT / 'src/benchmarks.py', Path(__file__)]},
                      trust='CANDIDATE_PENDING_INDEPENDENT_AUDIT',
                      limitations=['Historical reconstruction, not point-in-time archive of data releases.',
                                   'Competition September-published universe not applied to January.',
                                   'No competition warning rollback/disqualification emulation.',
                                   'Historical Active Share unknown.'])
    (output / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    bars.groupby('symbol').agg(first=('date', 'min'), last=('date', 'max'), complete_4h_bars=('date', 'size')).to_csv(output / 'four_hour_coverage.csv')
    summary = []
    variants = [('v1', {}), ('without_4h_direction', {'use_4h': False, 'match_4h_coverage': True}),
                ('without_turnover_margin', {'replacement_margin': 0.})]
    for name, changes in variants:
        print(f'Running {name}', flush=True)
        run_config = {**copy.deepcopy(config), **changes}
        result = run_backtest(daily, universe, run_config, bars)
        save_result(result, output / name)
        summary.append(dict(model=name, **result['metrics']))
    for name, symbols in [('universe_buy_hold_85pct', universe.symbol.tolist()), ('0050_buy_hold_85pct', ['0050.TW'])]:
        print(f'Running {name}', flush=True)
        result = run_buy_hold(daily, symbols, copy.deepcopy(config))
        save_result(result, output / name)
        summary.append(dict(model=name, **result['metrics']))
    summary = pd.DataFrame(summary)
    summary.to_csv(output / 'summary.csv', index=False)
    equity = pd.concat([pd.read_csv(output / name / 'equity.csv', usecols=['date', 'nav']).set_index('date').rename(columns={'nav': name}) for name in summary.model], axis=1)
    equity.to_csv(output / 'equity_comparison.csv')
    first_base = pd.DataFrame({name: [config['initial_cash']] for name in summary.model}, index=['2025-12-31'])
    monthly = pd.concat([first_base, equity])
    monthly.index = pd.to_datetime(monthly.index)
    monthly = monthly.resample('M').last().pct_change(fill_method=None).dropna(how='all')
    monthly.to_csv(output / 'monthly_returns.csv')
    provenance['run_completed_at'] = datetime.now(timezone.utc).isoformat()
    provenance['outputs_complete'] = True
    (output / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(summary[['model', 'total_return', 'max_drawdown', 'transaction_costs']].to_string(index=False), flush=True)


if __name__ == '__main__':
    main()
