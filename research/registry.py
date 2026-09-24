"""Append-only experiment registry (research/registry.csv), one row per run invocation."""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import numpy as np

from competition.rules import ROOT

REGISTRY = ROOT / 'research/registry.csv'
COLUMNS = ['updated_utc', 'run_id', 'status', 'config_name', 'config_hash', 'split', 'episodes_arg', 'n_episodes',
           'mean_return', 'median_return', 'std_return', 'hit_rate_vs_0050', 'hit_rate_vs_ew', 'mean_excess_vs_ew',
           'mean_max_drawdown', 'mean_turnover', 'mean_cost_pct', 'warnings_total', 'disqualified', 'unfilled_total',
           'official_fill_share', 'fill_source_mix', 'git_commit', 'git_dirty', 'runtime_seconds']


def metrics(summaries: list[dict], initial_capital: float = 1e9) -> dict:
    done = [s for s in summaries if s.get('status') == 'COMPLETE']
    if not done:
        return dict(n_episodes=0)
    r = np.array([s['terminal_return'] for s in done])
    ew = np.array([s['equal_weight_universe_return'] for s in done])
    bench = np.array([s['benchmark_0050_return'] for s in done])
    fills = Counter()
    for s in done:
        fills.update(s['fill_sources'])
    total = sum(fills.values())
    return dict(n_episodes=len(done), mean_return=float(r.mean()), median_return=float(np.median(r)),
                std_return=float(r.std(ddof=1)) if len(r) > 1 else 0., hit_rate_vs_0050=float((r > bench).mean()),
                hit_rate_vs_ew=float((r > ew).mean()), mean_excess_vs_ew=float((r - ew).mean()),
                mean_max_drawdown=float(np.mean([s['max_drawdown'] for s in done])),
                mean_turnover=float(np.mean([s['turnover'] for s in done])),
                mean_cost_pct=float(np.mean([s['costs'] for s in done]) / initial_capital),
                warnings_total=int(sum(s['warning_days'] for s in done)),
                disqualified=int(sum(s['disqualified'] for s in done)),
                unfilled_total=int(sum(s['unfilled_orders'] for s in done)),
                official_fill_share=float(fills.get('official_vwap', 0) / total) if total else 0.,
                fill_source_mix=json.dumps(dict(sorted(fills.items()))))


def append(manifest: dict, summaries: list[dict], path: Path = REGISTRY) -> dict:
    row = dict(updated_utc=manifest['updated_utc'], run_id=manifest['run_id'], status=manifest['status'],
               config_name=manifest['config_name'], config_hash=manifest['config_hash'], split=manifest['split'],
               episodes_arg=manifest['episodes_arg'], git_commit=manifest['git']['commit'],
               git_dirty=manifest['git']['dirty'], runtime_seconds=round(manifest['runtime_seconds'], 1),
               **metrics(summaries))
    new = not path.exists()
    with path.open('a', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, restval='')
        if new:
            writer.writeheader()
        writer.writerow({k: (round(v, 6) if isinstance(v, float) else v) for k, v in row.items()})
    return row
