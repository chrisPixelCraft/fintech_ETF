"""Run one config over a split's episodes: parallel, resumable, provenance-logged.

    PYTHONHASHSEED=0 .venv/bin/python -m research.run_experiment \\
        --config research/configs/baseline_autots.json --split dev --episodes 6 --workers 6

Outputs research/runs/<run_id>/ (gitignored): manifest.json, config.json and
episodes/<episode_id>/{ledger,trades,orders,holdings,issues}.csv + summary.json
+ strategy_log.json. One row per invocation is appended to research/registry.csv.
The holdout split needs --i-understand-holdout and is logged to
research/holdout_access_log.jsonl.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from competition import backtest, episodes as ep
from competition.data import MarketData, load_market
from competition.planner import PlannerPolicy
from competition.rules import ROOT, RULES_PATH, CompetitionRules, load_rules
from research import registry

RUNS = ROOT / 'research/runs'
HOLDOUT_LOG = ROOT / 'research/holdout_access_log.jsonl'
UPSTREAM = ROOT / 'third_party/autots/UPSTREAM.md'
CONFIG_KEYS = {'name', 'description', 'strategy', 'params', 'execution', 'planner', 'episodes'}

_MARKET: MarketData | None = None
_RULES: CompetitionRules | None = None


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def config_hash(config: dict) -> str:
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()


def load_config(path) -> dict:
    config = json.loads(Path(path).read_text())
    unknown = set(config) - CONFIG_KEYS
    if unknown or 'name' not in config or 'strategy' not in config:
        raise ValueError(f'Config needs name/strategy; unknown keys {sorted(unknown)}')
    build_strategy(config, load_rules())   # validate every nested key before any work
    backtest.ExecutionConfig(**config.get('execution', {}))
    PlannerPolicy(**config.get('planner', {}))
    episodes = config.get('episodes', {})
    unknown = (set(episodes) - {'offsets', 'start'}) | (set(episodes.get('offsets', [])) - set(ep.OFFSETS))
    if unknown:
        raise ValueError(f'Unknown episodes settings {sorted(unknown)}')
    return config


def build_strategy(config: dict, rules: CompetitionRules):
    params = config.get('params', {})
    kind = config['strategy']
    if kind == 'autots':
        from autots_strategy.strategy import AutoTSStrategy, AutoTSStrategyConfig
        return AutoTSStrategy(AutoTSStrategyConfig.from_dict(params), rules)
    if kind == 'lgbm':
        from lgbm_strategy.strategy import LightGBMStrategy, LightGBMStrategyConfig
        return LightGBMStrategy(LightGBMStrategyConfig.from_dict(params), rules)
    if kind == 'lgbm':
        from lgbm_strategy.strategy import LightGBMStrategy, LightGBMStrategyConfig
        return LightGBMStrategy(LightGBMStrategyConfig.from_dict(params), rules)
    from research.baselines import BasketConfig, BasketStrategy, MomentumConfig, MomentumStrategy
    if kind == 'momentum':
        return MomentumStrategy(MomentumConfig.from_dict(params), rules)
    if kind == 'basket':
        return BasketStrategy(BasketConfig.from_dict(params), rules)
    raise ValueError(f'Unknown strategy {kind}')


def git_state() -> dict:
    def run(*args):
        return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return dict(commit=run('rev-parse', 'HEAD'), dirty=bool(run('status', '--porcelain')))


def autots_provenance() -> dict:
    text = UPSTREAM.read_text()
    commit = re.search(r'\| Commit \| `([0-9a-f]{40})`', text)
    patches = re.findall(r'^\| (P\d+) \| `([^`]+)`', text, flags=re.M)
    import autots
    from autots_strategy.forecaster import RUNTIME_OVERRIDES
    return dict(upstream_commit=commit.group(1) if commit else None, version=autots.__version__,
                local_patches=[f'{pid}: {path}' for pid, path in patches], runtime_overrides=list(RUNTIME_OVERRIDES),
                path=str(Path(autots.__file__).resolve().parent.relative_to(ROOT)))


def _init_worker():
    global _MARKET, _RULES
    _MARKET, _RULES = load_market(), load_rules()


def run_one(config: dict, episode: ep.Episode, out: Path) -> dict:
    """Run, verify and persist one episode; never raises."""
    folder = out / 'episodes' / episode.episode_id
    folder.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        strategy = build_strategy(config, _RULES)
        execution = backtest.ExecutionConfig(**config.get('execution', {}))
        policy = PlannerPolicy(**config.get('planner', {}))
        result = backtest.run_episode(_MARKET, episode, strategy, _RULES, policy, execution)
        problems = backtest.verify_episode(_MARKET, episode, result, _RULES)
        for name in ('ledger', 'trades', 'orders', 'holdings', 'issues'):
            result[name].to_csv(folder / f'{name}.csv', index=False)
        (folder / 'strategy_log.json').write_text(json.dumps(getattr(strategy, 'log', []), indent=1, default=str))
        summary = dict(result['summary'], status='COMPLETE' if result['summary']['complete'] and not problems
                       else 'FAILED', verification_problems=problems, execution=vars(execution))
    except Exception as error:  # recorded, the run continues with other episodes
        (folder / 'error.txt').write_text(traceback.format_exc())
        summary = dict(episode_id=episode.episode_id, split=episode.split, status='FAILED', error=repr(error))
    summary['runtime_seconds'] = time.perf_counter() - started
    (folder / 'summary.json').write_text(json.dumps(summary, indent=1, default=str))
    return summary


def completed(out: Path, episode_id: str) -> dict | None:
    path = out / 'episodes' / episode_id / 'summary.json'
    if path.exists():
        summary = json.loads(path.read_text())
        if summary.get('status') == 'COMPLETE':
            return summary
    return None


def main(argv=None, progress=None) -> dict:
    """``progress(done, total)``, when given, is called once before any episode runs (done = already
    complete) and again after every finished episode."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--config', required=True)
    parser.add_argument('--split', required=True, choices=sorted(ep.SPLITS))
    parser.add_argument('--episodes', default='all', help='N evenly spaced episodes, or all')
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--out', help='run folder (default research/runs/<name>__<split>__<n>__<hash8>)')
    parser.add_argument('--registry', default=str(registry.REGISTRY), help=argparse.SUPPRESS)
    parser.add_argument('--i-understand-holdout', action='store_true',
                        help='required for --split holdout; every access is logged')
    args = parser.parse_args(argv)
    if args.split == 'holdout' and not args.i_understand_holdout:
        parser.error('--split holdout is veto-only; pass --i-understand-holdout to run it')

    config = load_config(args.config)
    chash = config_hash(config)
    n = None if args.episodes == 'all' else int(args.episodes)
    run_id = Path(args.out).name if args.out else f"{config['name']}__{args.split}__{args.episodes}__{chash[:8]}"
    out = Path(args.out) if args.out else RUNS / run_id
    manifest_path = out / 'manifest.json'
    previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
    if previous and previous['config_hash'] != chash:
        raise SystemExit(f'{out} holds a different config ({previous["config_hash"][:8]}); use a new --out')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'config.json').write_text(json.dumps(config, indent=1, sort_keys=True))
    git = git_state()
    if args.split == 'holdout':
        with HOLDOUT_LOG.open('a') as log:
            log.write(json.dumps(dict(utc=now(), run_id=run_id, config=args.config, config_hash=chash,
                                      git_commit=git['commit'], git_dirty=git['dirty'], argv=sys.argv)) + '\n')

    started = time.perf_counter()
    _init_worker()
    data_end = _MARKET.close.dropna(how='all').index[-1]
    offsets = tuple(config.get('episodes', {}).get('offsets', ['month_start']))
    first = config.get('episodes', {}).get('start')
    chosen = ep.select(ep.build_episodes(_MARKET.calendar, args.split, _RULES.episode_sessions, offsets, data_end,
                                         first), n)
    summaries = {e.episode_id: completed(out, e.episode_id) for e in chosen}
    todo = [e for e in chosen if summaries[e.episode_id] is None]
    print(f'{run_id}: {len(chosen)} episodes, {len(chosen) - len(todo)} already complete, running {len(todo)}',
          flush=True)
    finished = len(chosen) - len(todo)
    if progress:
        progress(finished, len(chosen))
    if args.workers <= 1:
        for e in todo:
            summaries[e.episode_id] = run_one(config, e, out)
            print(f"  {e.episode_id} {summaries[e.episode_id]['status']}", flush=True)
            finished += 1
            if progress:
                progress(finished, len(chosen))
    else:
        with ProcessPoolExecutor(max_workers=args.workers, initializer=_init_worker) as pool:
            futures = {pool.submit(run_one, config, e, out): e for e in todo}
            for future in as_completed(futures):
                e = futures[future]
                summaries[e.episode_id] = future.result()
                print(f"  {e.episode_id} {summaries[e.episode_id]['status']} "
                      f"{summaries[e.episode_id]['runtime_seconds']:.0f}s", flush=True)
                finished += 1
                if progress:
                    progress(finished, len(chosen))

    done = [s for s in summaries.values() if s and s['status'] == 'COMPLETE']
    status = 'COMPLETE' if len(done) == len(chosen) else 'PARTIAL' if done else 'FAILED'
    runtimes = [s['runtime_seconds'] for s in done]
    manifest = dict(
        run_id=run_id, status=status, created_utc=previous['created_utc'] if previous else now(), updated_utc=now(),
        split=args.split, episodes_arg=args.episodes, offsets=list(offsets), start=first, episode_ids=[e.episode_id for e in chosen],
        n_complete=len(done), n_failed=len(chosen) - len(done),
        failed=sorted(k for k, s in summaries.items() if not s or s['status'] != 'COMPLETE'),
        config_path=str(args.config), config_name=config['name'], config_hash=chash,
        git=dict(git, history=(previous or {}).get('git', {}).get('history', []) + [git['commit']]),
        autots=autots_provenance(), data=dict(files=_MARKET.provenance, rules=str(RULES_PATH.relative_to(ROOT)),
                                              data_end=str(data_end.date()),
                                              cutoff=str(max(e.end for e in chosen).date()) if chosen else None),
        execution=config.get('execution', {}), workers=args.workers,
        runtime_seconds=time.perf_counter() - started,
        episode_runtime_seconds=dict(mean=float(np.mean(runtimes)), median=float(np.median(runtimes)),
                                     max=float(np.max(runtimes))) if runtimes else None,
        environment=dict(python=platform.python_version(), pandas=pd.__version__, numpy=np.__version__,
                         PYTHONHASHSEED=os.environ.get('PYTHONHASHSEED')),
        argv=sys.argv)
    manifest_path.write_text(json.dumps(manifest, indent=1))
    row = registry.append(manifest, [s for s in summaries.values() if s], Path(args.registry))
    print(json.dumps({k: row[k] for k in ('run_id', 'status', 'n_episodes', 'mean_return', 'median_return',
                                           'hit_rate_vs_ew', 'mean_max_drawdown', 'warnings_total')}), flush=True)
    return manifest


if __name__ == '__main__':
    main()
