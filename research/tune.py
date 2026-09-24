"""Staged, resumable hyper-parameter search for the AutoTS strategy.

    PYTHONHASHSEED=0 .venv/bin/python -m research.tune --profile crazy --workers 10

Selection uses dev + validation from 2019 (2019-2024). Holdout (2025-2026/09),
the period closest to the competition, is scored once at the end as the test
and never changes the pick. Every run (baselines, candidates, test) uses the
same episodes: 24-session windows starting at month start and mid-month.
The 150-stock list is today's large caps, so earlier years carry more
look-ahead (survivor) bias; 2019 keeps the 2020 crash and the 2022 bear market.

  0  baselines   momentum / basket / no-signal AutoTS on every episode
  1  screen      baseline_autots + random configs, then 1-2 parameter mutations
                 of the leaders, each on fixed evenly spaced dev + validation
                 subsets
  2  confirm     leaders on every dev + validation episode (subset episodes are
                 reused)
  3  seeds       leaders re-run with extra AutoTS seeds; ranked by the
                 seed-averaged score. A config whose returns do not move with
                 the seed skips the rest. The best single seed is never picked.
  4  test        the pick and runner-ups, plus the baselines, on holdout, once

Score = 0.5 * mean + 0.5 * median of per-episode excess return over the
20-day momentum baseline. A config with any failed or disqualified episode
scores -inf.

Outputs:
- research/results/tune_<tag>/ (small, meant to be committed; refreshed after
  every stage): summary.md, summary.json, leaderboard.csv, best_config.json,
  log.txt
- research/runs/tune_<tag>/ (gitignored): configs/, runs/ (ledgers per
  episode), registry.csv, state.json

Rerunning the same tag resumes: finished episodes are never recomputed.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import platform
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from autots_strategy.forecaster import DEFAULT_TEMPLATE
from competition import episodes as ep
from competition.data import load_calendar
from competition.rules import load_rules
from research import compare, run_experiment
from research.run_experiment import ROOT

BASE = ROOT / 'research/configs/baseline_autots.json'
RESULTS = ROOT / 'research/results'
BASELINES = {name: ROOT / f'research/configs/baselines/{name}.json'
             for name in ('momentum_20d', 'largecap_basket', 'autots_lastvalue_naive')}
REFERENCE = 'momentum_20d'
SEEDS_BASE = 2026
SELECT = ('dev', 'validation')   # 70 + 70 episodes from 2019
TEST = 'holdout'                 # 40 episodes, scored once
EPISODES = dict(start='2019-01-01', offsets=['month_start', 'mid_month'])
CRASH = -.10                     # 0050 episode return at or below this is a crash window (report only)

ALL = {'dev': 'all', 'validation': 'all'}
PROFILES = {   # screen: episodes per split (10 + 10 = 20 = two waves of 10 workers)
    'quick': dict(screen={'dev': '2', 'validation': '2'}, confirm={'dev': '4', 'validation': '4'}, test='4',
                  n_random=3, n_local=2, leaders=2, confirm_top=2, seed_top=1, seeds=[1], final_top=1,
                  search_share=0.),
    'normal': dict(screen={'dev': '10', 'validation': '10'}, confirm=ALL, test='all', n_random=24, n_local=16,
                   leaders=4, confirm_top=8, seed_top=3, seeds=[1, 7], final_top=2, search_share=.1),
    'crazy': dict(screen={'dev': '10', 'validation': '10'}, confirm=ALL, test='all', n_random=50, n_local=30,
                  leaders=5, confirm_top=16, seed_top=4, seeds=[1, 7, 42, 1234], final_top=3, search_share=.15),
}

TEMPLATES = {   # subsets of the curated 7-model pool (see autots_strategy/forecaster.py)
    'full': None,
    'no_naive': ('ETS', 'ARIMA', 'WindowRegression'),
    'trend': ('AverageValueNaive', 'SeasonalNaive'),
    'stat': ('ETS', 'ARIMA'),
    'window_regression': ('WindowRegression',),
}
SPACE = {
    'target': ['relative_log_price/ew', 'relative_log_price/0050', 'log_price/ew', 'log_return/ew'],
    'horizon': [3, 5, 10, 20],
    'lookback': [120, 180, 240],
    'validation_windows': [3, 4, 6],
    'validation_step': [10, 24],
    'metric': ['rank_ic', 'topk_spread'],
    'template': list(TEMPLATES),
    'max_generations': [1, 2, 3],
    'score': ['z', 'mu', 'rank_blend'],
    'refit_every': [24, 12],
    'predict_every': [1, 3, 5],
    'n_holdings': [20, 22, 25, 28, 30],
    'keep_extra': [0, 5, 10, 15],
    'weighting': ['equal', 'score', 'inverse_vol'],
    'invested': [.85, .88, .92, .95],
    'cap_scale': [.8, .9, 1.],
    'rebalance_threshold': [.05, .1, .15, .2],
    'freeze_last_days': [0, 3, 5],
    'vol_window': [20, 60, 120],
}


# ---------------------------------------------------------------- configs
def base_point() -> dict:
    """The current baseline_autots settings expressed as a search-space point."""
    p = json.loads(BASE.read_text())['params']
    f, port = p['forecaster'], p['portfolio']
    return dict(mode='fixed_template', target=f"{p['target']['series']}/{p['target']['market']}",
                horizon=f['horizon'], lookback=f['lookback'], validation_windows=f['validation_windows'],
                validation_step=f['validation_step'], metric=f['metric'], template='full', max_generations=1,
                score=p['score'], refit_every=p['refit_every'], predict_every=p['predict_every'],
                n_holdings=port['n_holdings'], keep_extra=port['keep_rank'] - port['n_holdings'],
                weighting=port['weighting'], invested=port['invested'], cap_scale=port['cap_scale'],
                rebalance_threshold=port['rebalance_threshold'], freeze_last_days=port['freeze_last_days'],
                vol_window=60, seed=SEEDS_BASE)


def repair(point: dict) -> dict:
    point = dict(point)
    if point['validation_step'] < point['horizon']:
        point['validation_step'] = 24
    return point


def to_config(point: dict, name: str) -> dict:
    base = json.loads(BASE.read_text())
    series, market = point['target'].split('/')
    forecaster = dict(base['params']['forecaster'], mode=point['mode'], horizon=point['horizon'],
                      lookback=point['lookback'], validation_windows=point['validation_windows'],
                      validation_step=point['validation_step'], metric=point['metric'], random_seed=point['seed'])
    if point['mode'] == 'search':
        forecaster.update(max_generations=point['max_generations'], generation_timeout=3.)
    elif TEMPLATES[point['template']]:
        forecaster['template'] = [row for row in DEFAULT_TEMPLATE if row['Model'] in TEMPLATES[point['template']]]
    portfolio = dict(n_holdings=point['n_holdings'], keep_rank=point['n_holdings'] + point['keep_extra'],
                     weighting=point['weighting'], invested=point['invested'], cap_scale=point['cap_scale'],
                     rebalance_threshold=point['rebalance_threshold'], freeze_last_days=point['freeze_last_days'])
    params = dict(target=dict(series=series, market=market), forecaster=forecaster, score=point['score'],
                  portfolio=portfolio, refit_every=point['refit_every'], predict_every=point['predict_every'],
                  vol_window=point['vol_window'])
    return dict(name=name, description='research.tune candidate: ' + json.dumps(point, sort_keys=True),
                strategy='autots', params=params, execution=base['execution'], planner=base['planner'],
                episodes=EPISODES)


def point_id(point: dict) -> str:
    return hashlib.sha256(json.dumps(point, sort_keys=True).encode()).hexdigest()[:10]


def random_point(rng: np.random.Generator, search_share: float) -> dict:
    point = {key: values[rng.integers(len(values))] for key, values in SPACE.items()}
    point.update(mode='search' if rng.random() < search_share else 'fixed_template', seed=SEEDS_BASE)
    return repair({k: v.item() if hasattr(v, 'item') else v for k, v in point.items()})


def mutate(point: dict, rng: np.random.Generator) -> dict:
    point = dict(point)
    for key in [str(k) for k in rng.choice(sorted(SPACE), size=int(rng.integers(1, 3)), replace=False)]:
        options = [v for v in SPACE[key] if v != point[key]]
        value = options[rng.integers(len(options))]
        point[key] = value.item() if hasattr(value, 'item') else value
    return repair(point)


_RULES = None


def valid(point: dict) -> bool:
    """True when every nested strategy key passes the strategy's own validation."""
    global _RULES
    _RULES = _RULES or load_rules()
    try:
        run_experiment.build_strategy(to_config(point, 'probe'), _RULES)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- scoring
def episode_returns(runs) -> dict:
    """COMPLETE episode summaries of one or more run folders, keyed by episode id."""
    out = {}
    for run in ([runs] if isinstance(runs, Path) else runs):
        out.update(compare.load_summaries(run))
    return out


def score_runs(runs: list[Path], reference: dict) -> dict:
    """Score the episodes these runs were asked for (their manifests), paired with the reference."""
    wanted = set()
    for run in runs:
        if (run / 'manifest.json').exists():
            wanted |= set(json.loads((run / 'manifest.json').read_text())['episode_ids'])
    done = {k: s for k, s in episode_returns(runs).items() if k in wanted}
    ret = pd.Series({k: s['terminal_return'] for k, s in done.items()}, dtype=float)
    excess = (ret - pd.Series({k: s['terminal_return'] for k, s in reference.items()}, dtype=float)).dropna()
    broken = len(done) < len(wanted) or any(s.get('disqualified') for s in done.values()) or excess.empty
    return dict(
        n=len(done), failed=len(wanted) - len(done),
        score=-np.inf if broken else float(.5 * excess.mean() + .5 * excess.median()),
        mean_excess=float(excess.mean()) if len(excess) else np.nan,
        median_excess=float(excess.median()) if len(excess) else np.nan,
        win_rate=float((excess > 0).mean()) if len(excess) else np.nan,
        mean_return=float(ret.mean()) if len(ret) else np.nan,
        median_return=float(ret.median()) if len(ret) else np.nan,
        p25_return=float(ret.quantile(.25)) if len(ret) else np.nan,
        mean_max_drawdown=float(np.mean([s['max_drawdown'] for s in done.values()])) if done else np.nan,
        mean_turnover=float(np.mean([s['turnover'] for s in done.values()])) if done else np.nan,
        warning_days=int(sum(s.get('warning_days', 0) for s in done.values())),
        disqualified=int(sum(bool(s.get('disqualified')) for s in done.values())))


# ---------------------------------------------------------------- progress
def duration(seconds: float) -> str:
    minutes = int(seconds // 60)
    return f'{minutes // 60}h{minutes % 60:02d}m' if minutes >= 60 else f'{minutes}m{int(seconds % 60):02d}s'


class Progress:
    """One live status line: stage, config, per-split episodes, overall bar and ETA.

    The overall bar counts AutoTS episodes the search still has to compute
    (episodes reused from an earlier stage are not counted); the three fast
    baselines are shown but not counted. ETA uses this session's pace.
    The same line is written to ``path`` so a second terminal can follow it.
    """
    LABELS = {'dev': 'train', 'validation': 'val', 'holdout': 'test'}

    def __init__(self, total: int, done: int, path: Path, stream=None):
        self.total, self.done, self.path = total, done, path
        self.stream = stream or sys.stderr
        self.tty = self.stream.isatty()
        self.started, self.executed = time.time(), 0
        self.stage, self.config, self.splits = '', '', {}
        self.shown, self.last_plain, self.last_file = False, 0., 0.

    def start(self, stage: str, config: str, splits):
        self.stage, self.config = stage, config
        self.splits = {split: [0, 0] for split in splits}
        self.draw()

    def tracker(self, split: str, counted: bool = True):
        """Callback for run_experiment.main: (done, total) per finished episode."""
        state = {'seen': None}

        def update(done: int, total: int):
            if counted and state['seen'] is not None and done > state['seen']:
                self.done += done - state['seen']
                self.executed += done - state['seen']
            state['seen'] = done
            self.splits[split] = [done, total]
            self.draw()
        return update

    def line(self) -> str:
        total = max(self.total, self.done, 1)
        fraction = self.done / total
        bar = '█' * int(fraction * 20) + '░' * (20 - int(fraction * 20))
        splits = ' '.join(f'{self.LABELS.get(s, s)} {d}/{t}' for s, (d, t) in self.splits.items())
        elapsed = time.time() - self.started
        eta = (total - self.done) * elapsed / self.executed if self.executed else None
        return (f'[{self.stage}] {self.config} | {splits} | {bar} {fraction:.0%} {self.done}/{total} | '
                f'{duration(elapsed)}' + (f', ETA ~{duration(eta)}' if eta is not None else ''))

    def draw(self, force: bool = False):
        line, now_ = self.line(), time.time()
        if self.tty:
            width = shutil.get_terminal_size((160, 20)).columns - 1
            self.stream.write('\r\x1b[2K' + line[:width])
            self.stream.flush()
            self.shown = True
        elif force or now_ - self.last_plain >= 60:
            self.stream.write(line + '\n')
            self.stream.flush()
            self.last_plain = now_
        if force or now_ - self.last_file >= 5:
            self.path.write_text(f'{time.strftime("%Y-%m-%d %H:%M:%S")} {line}\n')
            self.last_file = now_

    def clear(self):
        if self.tty and self.shown:
            self.stream.write('\r\x1b[2K')
            self.stream.flush()


# ---------------------------------------------------------------- driver
def now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def pct(x) -> str:
    return 'n/a' if x is None or not np.isfinite(x) else f'{x:+.2%}'


class Tuner:
    def __init__(self, tag: str, profile: str, workers: int, seed: int, runs_dir: Path | None = None,
                 results_dir: Path | None = None):
        self.p, self.workers, self.tag = PROFILES[profile], workers, tag
        self.root = Path(runs_dir or ROOT / 'research/runs') / f'tune_{tag}'
        self.results = Path(results_dir or RESULTS) / f'tune_{tag}'
        for folder in (self.root / 'configs', self.root / 'runs', self.results):
            folder.mkdir(parents=True, exist_ok=True)
        self.rng = np.random.default_rng(seed)                 # random screen
        self.local_rng = np.random.default_rng([seed, 1])      # local search, independent of resumes
        self.reference: dict = {}
        self.started = time.time()
        self.state_path = self.root / 'state.json'
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else \
            dict(profile=profile, search_seed=seed, episodes=EPISODES, points={}, stages={}, scores={},
                 seed_groups={}, started_utc=now(), elapsed_seconds=0., done=[])
        episodes = self.state.get('episodes', dict(offsets=['month_start']))   # before EPISODES existed
        if self.state['profile'] != profile or self.state['search_seed'] != seed or episodes != EPISODES:
            raise SystemExit(f'{self.root} was started with profile={self.state["profile"]} '
                             f'seed={self.state["search_seed"]} episodes={episodes}; use a new --tag')
        self.elapsed_before = self.state.get('elapsed_seconds', 0.)
        self.progress: Progress | None = None
        self.stage = ''

    def plan(self) -> int:
        """Upper bound of AutoTS episodes this profile computes (seeds assumed to matter)."""
        p, calendar, rules = self.p, load_calendar(), load_rules()
        sizes = {s: len(ep.build_episodes(calendar, s, rules.episode_sessions, tuple(EPISODES['offsets']),
                                          start=EPISODES['start'])) for s in (*SELECT, TEST)}
        count = lambda plan: sum(sizes[s] if n == 'all' else min(int(n), sizes[s]) for s, n in plan.items())
        screen, confirm, test = count(p['screen']), count(p['confirm']), count({TEST: p['test']})
        new_confirm = max(confirm - screen, 0)
        per_leader = screen + new_confirm + (len(p['seeds']) - 1) * confirm
        self.seed_saving = per_leader - screen   # removed from the plan when a seed changes nothing
        insensitive = sum(g.get('sensitive') is False for g in self.state['seed_groups'].values())
        return ((1 + p['n_random'] + p['n_local']) * screen + p['confirm_top'] * new_confirm
                + p['seed_top'] * per_leader + p['final_top'] * test - insensitive * self.seed_saving)

    def computed_episodes(self) -> int:
        """AutoTS episodes already on disk (any status), so a resumed search starts its bar where it stopped."""
        return sum(1 for run in (self.root / 'runs').glob('*') if not run.name.startswith('baseline_')
                   for _ in run.glob('episodes/*/summary.json'))

    def start_progress(self, stream=None):
        self.progress = Progress(self.plan(), self.computed_episodes(), self.results / 'progress.txt', stream)

    def save(self):
        self.state['elapsed_seconds'] = self.elapsed_before + time.time() - self.started
        self.state_path.write_text(json.dumps(self.state, indent=1, sort_keys=True, default=float))

    def log(self, message: str):
        line = f'[{time.strftime("%Y-%m-%d %H:%M:%S")}] {message}'
        if self.progress:
            self.progress.clear()
        print(line, flush=True)
        with (self.results / 'log.txt').open('a') as handle:
            handle.write(line + '\n')
        if self.progress and self.progress.config:
            self.progress.draw()

    def stage_done(self, name: str):
        if name not in self.state['done']:
            self.state['done'].append(name)
        self.save()
        self.write_outputs()

    def add(self, point: dict) -> str:
        pid = point_id(point)
        if pid not in self.state['points']:
            self.state['points'][pid] = point
            (self.root / 'configs' / f'{pid}.json').write_text(json.dumps(to_config(point, f'tune_{pid}'), indent=1))
        return pid

    def run(self, config_path: Path, out: Path, split: str, episodes: str, counted: bool = True):
        argv = ['--config', str(config_path), '--split', split, '--episodes', str(episodes),
                '--workers', str(self.workers), '--out', str(out), '--registry', str(self.root / 'registry.csv')]
        if split == TEST:
            argv.append('--i-understand-holdout')   # logged to research/holdout_access_log.jsonl
        callback = self.progress.tracker(split, counted) if self.progress else None
        with contextlib.redirect_stdout(io.StringIO()):
            run_experiment.main(argv, progress=callback)

    def run_point(self, pid: str, episodes: dict) -> dict:
        """Run one config on {split: episodes} and score the union of those episodes."""
        started = time.time()
        label = ' + '.join(f'{split}:{n}' for split, n in episodes.items())
        self.log(f'  {pid} {label} running ...')
        if self.progress:
            self.progress.start(self.stage, pid, episodes)
        runs = []
        for split, n in episodes.items():
            runs.append(self.root / 'runs' / f'{pid}__{split}')
            self.run(self.root / 'configs' / f'{pid}.json', runs[-1], split, n)
        result = score_runs(runs, self.reference)
        self.log(f'  {pid} {label} score={result["score"]:+.4f} mean_ret={result["mean_return"]:+.4f} '
                 f'n={result["n"]} ({time.time() - started:.0f}s)')
        return result

    def baselines(self, splits):
        self.log(f'stage 0 baselines on {", ".join(splits)}')
        for split in splits:
            for name, source in BASELINES.items():
                path = self.root / 'configs' / f'baseline_{name}.json'   # same episodes as the candidates
                path.write_text(json.dumps(dict(json.loads(source.read_text()), episodes=EPISODES), indent=1))
                out = self.root / 'runs' / f'baseline_{name}__{split}'
                if self.progress:
                    self.progress.start('0/4 baselines' if split != TEST else '4/4 test baselines', name, [split])
                self.run(path, out, split, 'all', counted=False)
                if name == REFERENCE:
                    self.reference.update(episode_returns(out))
        self.log(f'  reference {REFERENCE}: {len(self.reference)} episodes')

    def screen(self):
        p = self.p
        stage = self.state['stages'].setdefault('screen', [])
        if not stage:
            stage.append(self.add(base_point()))
            while len(stage) < 1 + p['n_random']:
                point = random_point(self.rng, p['search_share'])
                if point_id(point) not in self.state['points'] and valid(point):
                    stage.append(self.add(point))
            self.save()
        self.log(f'stage 1a screen: {len(stage)} configs x {p["screen"]}')
        for i, pid in enumerate(stage, 1):
            self.stage = f'1/4 screen {i}/{len(stage)}'
            self.state['scores'][pid] = self.run_point(pid, p['screen'])
            self.save()
        local = self.state['stages'].setdefault('local', [])
        if not local:
            leaders = self.top(stage, p['leaders'])
            while leaders and len(local) < p['n_local']:
                point = mutate(self.state['points'][leaders[len(local) % len(leaders)]], self.local_rng)
                if point_id(point) not in self.state['points'] and valid(point):
                    local.append(self.add(point))
            self.save()
        self.log(f'stage 1b local search around the leaders: {len(local)} configs')
        for i, pid in enumerate(local, 1):
            self.stage = f'1/4 local {i}/{len(local)}'
            self.state['scores'][pid] = self.run_point(pid, p['screen'])
            self.save()
        self.stage_done('screen')

    def top(self, pids: list[str], k: int) -> list[str]:
        ranked = sorted(pids, key=lambda x: self.state['scores'].get(x, {}).get('score', -np.inf), reverse=True)
        return [x for x in ranked if np.isfinite(self.state['scores'].get(x, {}).get('score', -np.inf))][:k]

    def confirm(self):
        screened = self.state['stages']['screen'] + self.state['stages']['local']
        chosen = self.state['stages'].setdefault('confirm', self.top(screened, self.p['confirm_top']))
        self.log(f'stage 2 confirm: {len(chosen)} configs on {self.p["confirm"]}')
        for i, pid in enumerate(chosen, 1):
            self.stage = f'2/4 confirm {i}/{len(chosen)}'
            self.state['scores'][f'{pid}@full'] = self.run_point(pid, self.p['confirm'])
            self.save()
        self.stage_done('confirm')

    def seeds(self):
        full = {pid: self.state['scores'][f'{pid}@full'] for pid in self.state['stages']['confirm']}
        leaders = sorted(full, key=lambda x: full[x]['score'], reverse=True)[:self.p['seed_top']]
        leaders = [x for x in leaders if np.isfinite(full[x]['score'])]
        self.log(f'stage 3 seed robustness: {len(leaders)} leaders x seeds {self.p["seeds"]}')
        for i, pid in enumerate(leaders, 1):
            group = self.state['seed_groups'].setdefault(pid, dict(members=[pid], sensitive=None))
            for j, seed in enumerate(self.p['seeds'], 1):
                self.stage = f'3/4 seeds {i}/{len(leaders)} seed {j}/{len(self.p["seeds"])}'
                variant = self.add(dict(self.state['points'][pid], seed=seed))
                if variant not in group['members']:
                    group['members'].append(variant)
                if group['sensitive'] is None:   # one cheap check on the screen subset first
                    self.run_point(variant, self.p['screen'])
                    a = episode_returns([self.root / 'runs' / f'{pid}__{s}' for s in SELECT])
                    b = episode_returns([self.root / 'runs' / f'{variant}__{s}' for s in SELECT])
                    group['sensitive'] = any(abs(a[k]['terminal_return'] - b[k]['terminal_return']) > 1e-12
                                             for k in set(a) & set(b))
                    self.log(f'  {pid}: seed {"changes" if group["sensitive"] else "does not change"} results')
                    if not group['sensitive'] and self.progress:
                        self.progress.total -= self.seed_saving
                    self.save()
                if not group['sensitive']:
                    break
                self.state['scores'][f'{variant}@full'] = self.run_point(variant, self.p['confirm'])
                self.save()
            scores = [self.state['scores'][f'{m}@full']['score'] for m in group['members']
                      if f'{m}@full' in self.state['scores']]
            group.update(seed_mean=float(np.mean(scores)), seed_std=float(np.std(scores)), n_seeds=len(scores))
            self.save()
        self.stage_done('seeds')

    def test(self):
        groups = self.state['seed_groups']
        ranked = sorted(groups, key=lambda x: groups[x]['seed_mean'], reverse=True)
        if not ranked:
            self.log('No config finished stage 3 without failures; see leaderboard.csv')
            return
        self.state['pick'] = ranked[0]
        self.save()
        finalists = ranked[:self.p['final_top']]
        self.log(f'stage 4 test on {TEST} (score only, the pick {ranked[0]} is already fixed): {finalists}')
        self.baselines([TEST])
        for i, pid in enumerate(finalists, 1):
            self.stage = f'4/4 test {i}/{len(finalists)}'
            self.state['scores'][f'{pid}@test'] = self.run_point(pid, {TEST: self.p['test']})
            self.save()
        self.stage_done('test')

    # ------------------------------------------------------------ result files
    def baseline_scores(self) -> dict:
        out = {}
        for name in BASELINES:
            for phase, splits in (('select', SELECT), ('test', (TEST,))):
                runs = [self.root / 'runs' / f'baseline_{name}__{s}' for s in splits]
                if all((r / 'manifest.json').exists() for r in runs):
                    out.setdefault(name, {})[phase] = score_runs(runs, self.reference)
        return out

    def crash_rows(self, pid: str, splits) -> list[dict]:
        """Episodes ``pid`` ran in ``splits`` where 0050 fell by CRASH or more: 0050, momentum, ``pid`` returns."""
        mine = episode_returns([self.root / 'runs' / f'{pid}__{s}' for s in splits])
        return [dict(episode=k, benchmark=ref['benchmark_0050_return'], momentum=ref['terminal_return'],
                     config=mine[k]['terminal_return'])
                for k, ref in sorted(self.reference.items(), key=lambda kv: kv[1].get('start', kv[0]))
                if k in mine and ref.get('split') in splits and ref.get('benchmark_0050_return', 0.) <= CRASH]

    def write_outputs(self):
        """Refresh the committed result files in research/results/tune_<tag>/."""
        st, p = self.state, self.p
        rows = []
        for key, s in st['scores'].items():
            pid, _, phase = key.partition('@')
            rows.append(dict(config=pid, phase=phase or 'screen', **s,
                             **{f'p.{k}': v for k, v in st['points'][pid].items()}))
        board = pd.DataFrame(rows)
        if len(board):
            board = board.sort_values(['phase', 'score'], ascending=[True, False])
            board.to_csv(self.results / 'leaderboard.csv', index=False)
        base = self.baseline_scores()
        pick = st.get('pick')
        best = dict(config=pick) if pick else None
        if pick:
            config = to_config(st['points'][pick], f'tuned_autots_{self.tag}')
            (self.results / 'best_config.json').write_text(json.dumps(config, indent=1))
            test = st['scores'].get(f'{pick}@test', {})
            best.update(params=st['points'][pick], select=st['scores'][f'{pick}@full'],
                        seeds={k: v for k, v in st['seed_groups'][pick].items() if k != 'members'}, test=test,
                        verdict=None if not test else 'PASS' if test['mean_excess'] >= 0 else 'FAIL',
                        crashes=dict(select=self.crash_rows(pick, SELECT),
                                     test=self.crash_rows(pick, (TEST,)) if test else []))
        stages = ['screen', 'confirm', 'seeds', 'test']
        status = 'COMPLETE' if 'test' in st['done'] else \
            f'RUNNING (done: {", ".join(st["done"]) or "none"})'
        git = run_experiment.git_state()
        meta = dict(tag=self.tag, status=status, profile=st['profile'], workers=self.workers,
                    started=st['started_utc'], updated=now(), elapsed_hours=round(st['elapsed_seconds'] / 3600, 2),
                    configs_tried=len(st['points']), git_commit=git['commit'][:8], git_dirty=git['dirty'],
                    python=platform.python_version(), machine=f'{platform.system()} {platform.machine()}',
                    select=f'dev + validation from {EPISODES["start"]} (2019-2024)', test='holdout (2025-2026/09)',
                    episodes=EPISODES, n_select=sum(s.get('split') in SELECT for s in self.reference.values()),
                    n_test=sum(s.get('split') == TEST for s in self.reference.values()),
                    score=f'0.5*mean + 0.5*median of per-episode excess return over {REFERENCE}')
        (self.results / 'summary.json').write_text(json.dumps(
            dict(meta=meta, best=best, baselines=base), indent=1, default=float))
        (self.results / 'summary.md').write_text(self.summary_md(meta, best, base, board, stages))

    def summary_md(self, meta, best, base, board, stages) -> str:
        cols = ['target', 'horizon', 'lookback', 'template', 'mode', 'score', 'n_holdings', 'weighting']
        def table(frame, n=10):
            if frame is None or not len(frame):
                return ['（還沒有結果）']
            out = ['| 設定 | 分數 | 平均報酬 | 中位數報酬 | 勝過動能 | 平均回撤 | 窗口數 | 主要參數 |',
                   '|---|---:|---:|---:|---:|---:|---:|---|']
            for r in frame.head(n).to_dict('records'):   # columns 'p.<name>' are not attribute-safe
                params = ', '.join(f'{c}={r.get("p." + c, "")}' for c in cols)
                out.append(f'| `{r["config"]}` | {pct(r["score"])} | {pct(r["mean_return"])} | '
                           f'{pct(r["median_return"])} | {r["win_rate"]:.0%} | {r["mean_max_drawdown"]:.2%} | '
                           f'{r["n"]} | {params} |')
            return out
        phase = lambda name: board[board.phase == name] if len(board) else None
        lines = [f'# AutoTS 調參結果：`{meta["tag"]}`', '',
                 f'- **狀態**：{meta["status"]}',
                 f'- **時間**：{meta["started"]} 開始，更新於 {meta["updated"]}，累計 {meta["elapsed_hours"]} 小時',
                 f'- **環境**：profile `{meta["profile"]}`、{meta["workers"]} workers、commit `{meta["git_commit"]}`'
                 f'{"（有未提交修改）" if meta["git_dirty"] else ""}、Python {meta["python"]}、{meta["machine"]}',
                 f'- **資料**：挑參數用 2019–2024（{meta["n_select"]} 個窗口）；測試用 2025–2026/9（{meta["n_test"] or "約 40"} 個窗口）；'
                 '每月月初、月中各一個起點',
                 f'- **評分**：每個 24 日窗口對 {REFERENCE} 的超額報酬，平均和中位數各半；有窗口被取消資格就是 -inf',
                 f'- **試過的設定**：{meta["configs_tried"]} 組', '', '## 結論', '']
        if best:
            sel, seeds, test = best['select'], best['seeds'], best['test']
            lines += [f'- **最佳設定**：`{best["config"]}` → `research/results/tune_{meta["tag"]}/best_config.json`',
                      f'- **2019–2024**：平均報酬 {pct(sel["mean_return"])}，對動能超額 {pct(sel["mean_excess"])}，'
                      f'勝過動能 {sel["win_rate"]:.0%}（{sel["n"]} 個窗口）',
                      f'- **seed 穩定度**：{seeds.get("n_seeds", 1)} 個 seed，分數 {pct(seeds.get("seed_mean"))} ± '
                      f'{seeds.get("seed_std", 0):.2%}' + ('' if seeds.get('sensitive') else '（seed 不影響這組設定）')]
            if test:
                lines.append(f'- **2025–2026 測試**：平均報酬 {pct(test["mean_return"])}，對動能超額 '
                             f'{pct(test["mean_excess"])}，勝過動能 {test["win_rate"]:.0%}（{test["n"]} 個窗口）'
                             f' → **{best["verdict"]}**')
            else:
                lines.append('- **2025–2026 測試**：還沒跑')
        else:
            lines.append('- 還沒選出最佳設定（跑完 seed 階段後才會有）')
        lines += ['', '## 簡單基準', '', '| 基準 | 2019–2024 平均報酬 | 2025–2026 平均報酬 |', '|---|---:|---:|']
        for name, phases in base.items():
            lines.append(f'| {name} | {pct(phases.get("select", {}).get("mean_return"))} | '
                         f'{pct(phases.get("test", {}).get("mean_return"))} |')
        lines += ['', '## 2025–2026 測試（前幾名）', '', *table(phase('test')),
                  '', '## 2019–2024 全部窗口排行', '', *table(phase('full')),
                  '', '## 粗篩排行（20 個窗口）', '', *table(phase('screen')),
                  '', f'## 大跌窗口表現（0050 跌 {-CRASH:.0%} 以上，只供觀察，不影響選擇）', '',
                  *self.crash_md(best), '', '## 最佳參數', '']
        lines += ['```json', json.dumps(best['params'], indent=1, sort_keys=True), '```'] if best else ['（還沒有）']
        lines += ['', '## 注意', '',
                  f'- 最佳設定是從 {meta["configs_tried"]} 組裡挑出來的，2019–2024 的分數偏樂觀；2025–2026 測試才是比較客觀的數字',
                  '- 看完 2025–2026 測試後不要再回頭調參，否則這個分數就不再客觀',
                  '- 2024 以前的成交價是代理價（高低收平均），2024 之後才是官方成交均價',
                  '- 150 檔是 2026 年的名單，回測有存活偏誤：絕對報酬偏高，策略間比較影響較小；'
                  '從 2019 開始挑參數就是為了減少這個偏誤',
                  '- 月初和月中起點的窗口有重疊，相鄰窗口不是獨立樣本', '']
        return '\n'.join(lines)

    @staticmethod
    def crash_md(best) -> list[str]:
        if not best:
            return ['（選出最佳設定後才有）']
        out = []
        for phase, label in (('select', '2019–2024'), ('test', '2025–2026 測試')):
            rows = best['crashes'][phase]
            if not rows:
                out += [f'- {label}：' + ('跑過的窗口裡沒有大跌' if phase == 'select' or best['test'] else '還沒跑'), '']
                continue
            diff = [r['config'] - r['momentum'] for r in rows]
            out += [f'**{label}**：{len(rows)} 個窗口，最佳設定平均 {pct(np.mean([r["config"] for r in rows]))}，'
                    f'動能平均 {pct(np.mean([r["momentum"] for r in rows]))}，'
                    f'跌得比動能少的窗口 {np.mean([d > 0 for d in diff]):.0%}', '',
                    '| 窗口 | 0050 | 動能 | 最佳設定 | 差距 |', '|---|---:|---:|---:|---:|']
            out += [f'| {r["episode"]} | {pct(r["benchmark"])} | {pct(r["momentum"])} | {pct(r["config"])} | '
                    f'{pct(r["config"] - r["momentum"])} |' for r in rows] + ['']
        return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--profile', choices=sorted(PROFILES), default='crazy')
    parser.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument('--tag', help='output folders research/{runs,results}/tune_<tag> (default: the profile name)')
    parser.add_argument('--search-seed', type=int, default=20261026, help='RNG for sampling configs')
    args = parser.parse_args(argv)
    if os.environ.get('PYTHONHASHSEED') != '0':
        raise SystemExit('Set PYTHONHASHSEED=0 (research/finetune.sh does this)')
    tuner = Tuner(args.tag or args.profile, args.profile, args.workers, args.search_seed)
    p = tuner.p
    tuner.log(f'profile {args.profile}: {1 + p["n_random"]} random + {p["n_local"]} local configs on {p["screen"]}, '
              f'top {p["confirm_top"]} on {p["confirm"]}, seeds {p["seeds"]} for top {p["seed_top"]}, '
              f'test {TEST}:{p["test"]}, {args.workers} workers -> {tuner.results}')
    tuner.start_progress()
    tuner.log(f'plan: about {tuner.progress.total} AutoTS episodes (upper bound), '
              f'{tuner.progress.done} already computed; live progress: {tuner.results / "progress.txt"}')
    tuner.baselines(SELECT)
    tuner.screen()
    tuner.confirm()
    tuner.seeds()
    tuner.test()
    tuner.write_outputs()
    tuner.progress.stage, tuner.progress.config = 'done', ''
    tuner.progress.draw(force=True)
    tuner.progress.clear()
    tuner.log(f'done: {tuner.results / "summary.md"}')


if __name__ == '__main__':
    main()
