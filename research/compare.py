"""Paired comparison of two runs over their common completed episodes.

    .venv/bin/python -m research.compare research/runs/A research/runs/B [--metric terminal_return]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def load_summaries(run: Path | str) -> dict:
    out = {}
    for path in sorted(Path(run).glob('episodes/*/summary.json')):
        s = json.loads(path.read_text())
        if s.get('status') == 'COMPLETE':
            out[s['episode_id']] = s
    return out


def compare(run_a, run_b, metric: str = 'terminal_return', n_boot: int = 10000, seed: int = 0) -> dict:
    """Differences are A - B per common episode; CI is a percentile bootstrap of the mean over episodes."""
    a, b = load_summaries(run_a), load_summaries(run_b)
    common = sorted(set(a) & set(b))
    if not common:
        raise ValueError('No common completed episodes')
    d = np.array([a[k][metric] - b[k][metric] for k in common])
    rng = np.random.default_rng(seed)
    boot = d[rng.integers(0, len(d), size=(n_boot, len(d)))].mean(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return dict(metric=metric, n_common=len(common), only_a=len(set(a) - set(b)), only_b=len(set(b) - set(a)),
                mean_a=float(np.mean([a[k][metric] for k in common])),
                mean_b=float(np.mean([b[k][metric] for k in common])),
                mean_diff=float(d.mean()), median_diff=float(np.median(d)), win_rate=float((d > 0).mean()),
                ci95=[float(lo), float(hi)])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('run_a')
    parser.add_argument('run_b')
    parser.add_argument('--metric', default='terminal_return')
    parser.add_argument('--n-boot', type=int, default=10000)
    args = parser.parse_args(argv)
    result = compare(args.run_a, args.run_b, args.metric, args.n_boot)
    print(json.dumps(result, indent=1))
    return result


if __name__ == '__main__':
    main()
