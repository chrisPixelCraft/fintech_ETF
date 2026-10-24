"""Frozen benchmark batch; all writes confined to DEST and append-only holdout log."""
import sys
sys.dont_write_bytecode = True
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
DEST = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['TMPDIR'] = str(DEST / 'tmp')
(DEST / 'tmp').mkdir(exist_ok=True)
import hashlib
import importlib.util
import json
import multiprocessing as mp
import subprocess
import time
import traceback
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from competition import backtest, portfolio
from competition.data import load_market
from competition.episodes import Episode, build_episodes
from competition.rules import load_rules
from production import engine
from research.run_experiment import build_strategy

COMMAND = 'PYTHONHASHSEED=0 OPENBLAS_NUM_THREADS=1 PYTHONPATH=. .venv/bin/python research/results/benchmark_20261025/run.py'
LEGACY = ['mom20', 'mom25', 'mom10', 'mom60', 'residual20_0050', 'residual20_ew', 'residual120_h1', 'tech_composite', 'macd_combined', 'tsmc_core_equal', 'tsmc_formal', 'autots_h5', 'lgbm_D_lr0.01']
MODELS = LEGACY[-2:]
NEW = {'K2': '投信＋營收集中（正式）', 'T2': '大型股＋投信', 'C1': '台積電＋大型股', 'C3': '低波動大型股', 'S1': '營收動能', 'S2': '崩跌防護', 'T1': '投信＋營收', 'T3': '大型股＋營收'}
LOG = ROOT / 'research/holdout_access_log.jsonl'
PROGRESS, CHECKS, FALLBACKS, TIMINGS = [], {}, [], {}
START = time.monotonic()


def dump(path, obj):
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, allow_nan=False, default=str) + '\n')


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ResidualStrategy:
    def __init__(self, config):
        self.c = config
        self.p = portfolio.PortfolioConfig.from_dict(BASE['params']['portfolio'])
        self.log = []

    def decide(self, view, state):
        scores, missing = signals.score(view, self.c)
        self.log.append(dict(date=str(state.date), missing=missing, names=len(scores)))
        return portfolio.target_weights(scores, state.weights, state.sessions_remaining, RULES, self.p)


def execute(job):
    name, ep = job
    started = time.monotonic()
    config, market = BASE, MARKET
    if name in MODELS:
        config, _ = models.configuration(name)
        strategy = models.AuditedStrategy(models.construct(config, RULES))
        market = MARKET.since('2019-01-01')
    elif name.startswith('mom'):
        params = dict(window=int(name[3:]), portfolio=BASE['params']['portfolio'])
        strategy = build_strategy(dict(BASE, params=params), RULES)
    elif name.startswith('residual'):
        params = dict(kind='h1', resid_window=120 if name == 'residual120_h1' else 20,
                      beta_window=250 if name == 'residual120_h1' else 60,
                      benchmark='ew' if name == 'residual20_ew' else '0050')
        strategy = ResidualStrategy(signals.SignalConfig(**params))
    else:
        module, cls, cfg, variant, floor = TECH[name]
        strategy = cls(cfg.from_dict(dict(variant=variant, portfolio=BASE['params']['portfolio'])), RULES)
        market = MARKET.since(floor)
    result = backtest.run_episode(market, ep, strategy, RULES, engine.policy_of(config),
                                  backtest.ExecutionConfig(**config['execution']))
    problems = backtest.verify_episode(market, ep, result, RULES)
    summary = result['summary']
    folder = DEST / 'episodes' / name / ep.episode_id
    folder.mkdir(parents=True, exist_ok=True)
    dump(folder / 'summary.json', dict(summary, verification_problems=problems,
                                      runtime_seconds=time.monotonic() - started))
    assert summary['complete'] and len(result['ledger']) == 24, (name, ep.episode_id, 'incomplete')
    assert not problems, (name, ep.episode_id, problems)
    row = dict(strategy=name, label=LABELS[name], group='legacy', window=ep.episode_id,
               start=str(ep.start.date()), end=str(ep.end.date()),
               terminal_return=summary['terminal_return'], max_drawdown=summary['max_drawdown'],
               costs_pct_initial_nav=summary['costs'] / RULES.initial_capital,
               delta_vs_0050=summary['terminal_return'] - summary['benchmark_0050_return'],
               source='new_replay:' + TEMPLATE[name], verification_problem_count=len(problems),
               runtime_seconds=time.monotonic() - started)
    assert all(np.isfinite(v) for v in row.values() if isinstance(v, (float, int))), row
    return row


def collect(row):
    PROGRESS.append(row)
    dump(DEST / 'progress.json', PROGRESS)
    print('COMPLETE', row['strategy'], row['window'], row['terminal_return'], flush=True)


def batch(jobs, workers, timeout=None):
    started = time.monotonic()
    pool = mp.get_context('fork').Pool(workers)
    try:
        pending = [pool.apply_async(execute, (job,)) for job in jobs]
        while pending:
            for task in pending[:]:
                if task.ready():
                    collect(task.get())
                    pending.remove(task)
            if timeout and time.monotonic() - started >= timeout:
                pool.terminate()
                pool.join()
                return False
            if pending:
                time.sleep(.2)
        pool.close()
        pool.join()
        return True
    except BaseException:
        pool.terminate()
        pool.join()
        raise


def consistency(name, ep, expected, tolerance):
    row = next(r for r in PROGRESS if (r['strategy'], r['window']) == (name, ep.episode_id))
    actual = row['terminal_return']
    CHECKS[f'{name}:{ep.episode_id}'] = dict(actual=actual, expected=float(expected), tolerance=tolerance,
                                           passed=abs(actual - expected) <= tolerance)
    assert CHECKS[f'{name}:{ep.episode_id}']['passed'], CHECKS[f'{name}:{ep.episode_id}']


def protected_check():
    assert git('status', '--porcelain') == BASE_STATUS, 'Git status changed outside baseline'
    for p, h in PROTECTED.items():
        assert sha(ROOT / p) == h, f'Protected file changed: {p}'
    current = LOG.read_bytes()
    assert current.startswith(LOG_BEFORE), 'Holdout log prefix changed'
    assert current[len(LOG_BEFORE):] == LOG_APPEND, 'Unexpected holdout log append'
    return True


def report(status, blocker=''):
    elapsed = time.monotonic() - START
    summary = dict(status=status, counts=dict(legacy_expected=585, legacy_processed=len(PROGRESS),
                    legacy_fallback=len(FALLBACKS), legacy_remaining=585-len(PROGRESS)-len(FALLBACKS),
                    verification_calls=len(PROGRESS), verification_problems=sum(r['verification_problem_count'] for r in PROGRESS)),
                   elapsed_seconds=elapsed, runtimes=TIMINGS, asserts=CHECKS,
                   holdout_log_lines=LOG_APPEND.decode().splitlines() if 'LOG_APPEND' in globals() else [],
                   sources=sorted({r['source'] for r in PROGRESS + FALLBACKS}), fallback_rows=FALLBACKS,
                   workers=8, blocker=blocker)
    dump(DEST / 'summary.json', summary)
    text = ['# CODEX_REPORT', '', f'- Status: {status}.',
            '- Changed files: run.py, progress.json, summary.json, episodes/*/*/summary.json; successful completion also produces recent5.csv, latest24.csv, holdout40.csv, holdout40_summary.csv. CODEX_REPORT.md; append-only research/holdout_access_log.jsonl.',
            f'- Tests/asserts: {len(CHECKS)} consistency checks recorded; verification calls {len(PROGRESS)}, problems {summary["counts"]["verification_problems"]}. Details in summary.json.',
            f'- Items expected/processed/failed: 585 legacy episodes / {len(PROGRESS)} completed / {585-len(PROGRESS)-len(FALLBACKS)} incomplete; {len(FALLBACKS)} documented fallback rows.',
            '- Plan deviations: live production/strategy.json is now K2/T2 and lacks the old params.portfolio; legacy base read from the template mom20/config.json and checked against production/strategy_mom20.json. First attempt stopped on KeyError before completing an episode. Frozen strategy parameters unchanged; new strategies reused. No tuning or selection.',
            f'- Execution: `{COMMAND}`; main directory; {elapsed:.2f}s elapsed; up to 8 worker processes. Per-batch runtimes in summary.json.',
            '- Quota: unavailable.', f'- Blockers: {blocker or "none"}.']
    (DEST / 'CODEX_REPORT.md').write_text('\n'.join(text) + '\n')


def main():
    global BASE_STATUS, LOG_BEFORE, LOG_APPEND, PROTECTED, BASE, RULES, MARKET, models, signals, TECH, LABELS, TEMPLATE
    BASE_STATUS = git('status', '--porcelain')
    LOG_BEFORE, LOG_APPEND = LOG.read_bytes(), b''
    paths = git('ls-files', '-z').split(b'\0') + git('ls-files', '--others', '--exclude-standard', '-z').split(b'\0')
    PROTECTED = {os.fsdecode(p): sha(ROOT / os.fsdecode(p)) for p in paths if p
                 and not os.fsdecode(p).startswith('research/results/benchmark_20261025/')
                 and os.fsdecode(p) != 'research/holdout_access_log.jsonl' and (ROOT / os.fsdecode(p)).is_file()}
    dump(DEST / 'baseline.json', dict(git_status=BASE_STATUS.decode(), protected_sha256=PROTECTED,
                                     holdout_log_sha256=hashlib.sha256(LOG_BEFORE).hexdigest(), holdout_log_bytes=len(LOG_BEFORE)))
    # The current production strategy is now K2/T2; use the exact saved legacy
    # template configuration instead of silently executing the new production kind.
    BASE = json.loads((data_path := ROOT / 'production_runs/preflight_20261026/strategy_retest/momentum/mom20/config.json').read_text())
    BASE['strategy'] = 'momentum'
    frozen_mom = json.loads((ROOT / 'production/strategy_mom20.json').read_text())
    assert all(BASE[k] == frozen_mom[k] for k in ('params', 'execution', 'planner'))
    CHECKS['frozen_legacy_config_matches_saved_mom20'] = True
    RULES = load_rules()
    data = ROOT / 'production_runs/preflight_20261026'
    MARKET = load_market(yahoo_path=data / 'strategy_retest/corrected_nominal_daily.parquet',
                         official_path=data / 'official_combined.csv', calendar_path=data / 'calendar_v2.json')
    dump(DEST / 'market_provenance.json', MARKET.provenance)
    comparison = pd.read_csv(DEST.parent / 'recent24_strategies/comparison.csv', float_precision='round_trip')
    LABELS = {r.method: r.strategy for r in comparison.itertuples()}
    assert all(n in LABELS for n in LEGACY)
    LABELS.update(NEW)
    LABELS['0050'] = '0050'
    TEMPLATE = {n: 'production_runs/preflight_20261026/strategy_retest/' +
                ('models/run_models.py' if n in MODELS else 'momentum/run.py' if n in LEGACY[:7] else 'technical/run_retest.py') for n in LEGACY}
    models = load('benchmark_models', data / 'strategy_retest/models/run_models.py')
    from momv2 import signals as signal_module
    signals = signal_module
    tech = load('benchmark_technical', ROOT / 'legacy/ml/momv2/technical.py')
    macd = load('benchmark_macd', ROOT / 'legacy/ml/momv2/macd.py')
    tsmc = load('benchmark_tsmc', ROOT / 'legacy/ml/momv2/tsmc.py')
    tech._PANELS.update(tech.indicators(MARKET.since(tech.DATA_START)))
    macd._PANELS.update(macd.normalized_macd(MARKET.since(macd.DATA_START)))
    TECH = dict(tech_composite=(tech, tech.TechStrategy, tech.TechConfig, 'composite', tech.DATA_START),
                macd_combined=(macd, macd.MacdStrategy, macd.MacdConfig, 'combined', macd.DATA_START),
                tsmc_core_equal=(tsmc, tsmc.TsmcStrategy, tsmc.TsmcConfig, 'core_equal', '2014-01-01'),
                tsmc_formal=(tsmc, tsmc.TsmcStrategy, tsmc.TsmcConfig, 'formal', '2014-01-01'))
    refs = pd.read_csv(DEST.parent / 'recent5_v3/results.csv', float_precision='round_trip')
    conc = pd.read_csv(DEST.parent / 'conc_tournament/results.csv', float_precision='round_trip')
    end = MARKET.position('2026-10-22') + 1
    recent = []
    for i in range(5):
        days = tuple(MARKET.calendar[end-120+i*24:end-96+i*24])
        ep = Episode(f'W{i+1}', 'recent_diagnostic', MARKET.calendar[MARKET.position(days[0])-1], days)
        assert len(days) == 24
        matching = refs[refs.window == ep.episode_id]
        assert len(matching) == 5 and all(pd.Timestamp(r.start) == ep.start and pd.Timestamp(r.end) == ep.end for r in matching.itertuples())
        recent.append(ep)
    assert str(recent[-1].start.date()) == '2026-09-16' and str(recent[-1].end.date()) == '2026-10-22'
    holdout = build_episodes(MARKET.calendar, 'holdout', offsets=('month_start', 'mid_month'), data_end='2026-10-22')
    assert len(holdout) == 40
    historical = conc[(conc['set'] == 'historical') & (conc.split == 'holdout')]
    for name in ['K2', 'T2', 'Mom20']:
        match = historical[historical.strategy == name]
        assert len(match) == 40
        assert {(r.window_id, r.start, r.end) for r in match.itertuples()} == {(e.episode_id, str(e.start.date()), str(e.end.date())) for e in holdout}
    CHECKS['window_dates_and_ids'] = True
    started = time.monotonic()
    batch([(n, recent[-1]) for n in LEGACY], 8)
    TIMINGS['W5'] = time.monotonic() - started
    for n in LEGACY:
        consistency(n, recent[-1], comparison.loc[comparison.method == n, 'net_return'].item(), 1e-9)
    started = time.monotonic()
    batch([(n, e) for e in recent[:-1] for n in LEGACY], 8)
    TIMINGS['W1_W4'] = time.monotonic() - started
    for e in recent:
        consistency('mom20', e, refs.loc[(refs.strategy == 'Mom20') & (refs.window == e.episode_id), 'terminal_return'].item(), 1e-12)
    stamp = datetime.now(timezone.utc).isoformat()
    entries = [dict(utc=stamp, run_id=n+'__holdout_benchmark_20261025', config=str(DEST / 'run.py'),
                    config_hash=sha(DEST / 'run.py'), git_commit=git('rev-parse', 'HEAD').decode().strip(),
                    git_dirty=True, argv=sys.argv) for n in LEGACY]
    LOG_APPEND = ''.join(json.dumps(x)+'\n' for x in entries).encode()
    with LOG.open('ab') as f:
        f.write(LOG_APPEND)
    # Fail fast on the historical reference before starting all other holdout strategies.
    started = time.monotonic()
    batch([('mom20', e) for e in holdout], 8)
    for e in holdout:
        consistency('mom20', e, historical.loc[(historical.strategy == 'Mom20') & (historical.window_id == e.episode_id), 'terminal_return'].item(), 1e-12)
    batch([(n, e) for n in LEGACY[1:-2] for e in holdout], 8)
    TIMINGS['holdout_nonmodels'] = time.monotonic() - started
    for n in MODELS:
        started = time.monotonic()
        complete = batch([(n, e) for e in holdout], 8, timeout=3600)
        TIMINGS['holdout_' + n] = time.monotonic() - started
        if not complete:
            # Only the prescribed timeout permits archived returns. Auxiliary historical
            # metrics must be available rather than invented; otherwise stop and report.
            archive = pd.read_csv(DEST.parent / 'final_test/episode_returns.csv', float_precision='round_trip')
            for e in holdout:
                if any(r['strategy'] == n and r['window'] == e.episode_id for r in PROGRESS):
                    continue
                old = archive.loc[archive.episode == e.episode_id]
                assert len(old) == 1, ('fallback missing', n, e.episode_id)
                files = list((ROOT / 'research/runs/final_test').rglob(e.episode_id + '/summary.json'))
                summaries = [json.loads(p.read_text()) for p in files if n in str(p)]
                assert len(summaries) == 1, ('fallback auxiliary metrics unavailable', n, e.episode_id)
                s = summaries[0]
                bench = backtest._benchmarks(MARKET, e)['benchmark_0050_return']
                value = float(old[n].item())
                FALLBACKS.append(dict(strategy=n, label=LABELS[n], group='legacy', window=e.episode_id,
                     start=str(e.start.date()), end=str(e.end.date()), terminal_return=value,
                     max_drawdown=s['max_drawdown'], costs_pct_initial_nav=s['costs']/RULES.initial_capital,
                     delta_vs_0050=value-bench, source='fallback:research/results/final_test/episode_returns.csv'))
    rows = [{k: v for k, v in r.items() if k not in ('runtime_seconds', 'verification_problem_count')} for r in PROGRESS] + FALLBACKS
    for filename, names, is_conc in [('conc_tournament', ['K2', 'T2'], True), ('recent5_new', ['C1', 'C3', 'S1', 'S2'], False), ('recent5_v3', ['T1', 'T3'], False)]:
        table = pd.read_csv(DEST.parent / filename / 'results.csv', float_precision='round_trip')
        for e in (recent+holdout if is_conc else recent):
            for n in names:
                subset = table[(table.strategy == n) & (table['window_id' if is_conc else 'window'] == e.episode_id)]
                assert len(subset) == 1
                s = subset.iloc[0]
                assert s.start == str(e.start.date()) and s.end == str(e.end.date())
                assert bool(s.complete) and s['verify_problem_count' if is_conc else 'verification_problem_count'] == 0
                bench = backtest._benchmarks(MARKET, e)['benchmark_0050_return']
                assert abs(s.benchmark_0050_return-bench) <= 1e-12
                rows.append(dict(strategy=n, label=LABELS[n], group='new', window=e.episode_id,
                    start=s.start, end=s.end, terminal_return=float(s.terminal_return), max_drawdown=float(s.max_drawdown),
                    costs_pct_initial_nav=float(s.costs)/RULES.initial_capital, delta_vs_0050=float(s.terminal_return)-bench,
                    source=f'reuse:research/results/{filename}/results.csv'))
    for e in recent+holdout:
        lo, hi = MARKET.position(e.start), MARKET.position(e.end)+1
        growth = np.r_[1., (1+MARKET.benchmark_ret.iloc[lo:hi].fillna(0.)).cumprod().to_numpy()]
        bench = backtest._benchmarks(MARKET, e)['benchmark_0050_return']
        rows.append(dict(strategy='0050', label='0050', group='benchmark', window=e.episode_id,
                    start=str(e.start.date()), end=str(e.end.date()), terminal_return=bench,
                    max_drawdown=float(np.max(1-growth/np.maximum.accumulate(growth))), costs_pct_initial_nav=0.,
                    delta_vs_0050=0., source='competition.backtest._benchmarks:repaired_market'))
    table = pd.DataFrame(rows).sort_values(['window', 'group', 'strategy'])
    assert not table.duplicated(['strategy', 'window']).any()
    assert not table.isna().any().any() and np.isfinite(table.select_dtypes('number')).all().all()
    recent_table = table[table.window.str.startswith('W')]
    historical_table = table[table.window.str.startswith('holdout_')]
    assert len(recent_table) == 110 and (recent_table.groupby('strategy').size() == 5).all()
    assert len(historical_table) == 640 and (historical_table.groupby('strategy').size() == 40).all()
    assert set(historical_table.strategy) == set(LEGACY + ['K2', 'T2', '0050'])
    summary_rows = []
    mom = historical_table[historical_table.strategy == 'mom20'].set_index('window').terminal_return
    for n, t in historical_table.groupby('strategy'):
        delta = t.terminal_return.to_numpy() - mom.loc[t.window].to_numpy()
        summary_rows.append(dict(strategy=n, n=len(t), mean_return=t.terminal_return.mean(),
             median_return=t.terminal_return.median(), mean_delta_vs_0050=t.delta_vs_0050.mean(),
             win_rate_vs_0050=(t.delta_vs_0050 > 0).mean(), mean_delta_vs_mom20=delta.mean(), source=';'.join(sorted(set(t.source)))))
    recent_table.to_csv(DEST / 'recent5.csv', index=False)
    recent_table[recent_table.window == 'W5'].to_csv(DEST / 'latest24.csv', index=False)
    historical_table.to_csv(DEST / 'holdout40.csv', index=False)
    pd.DataFrame(summary_rows).to_csv(DEST / 'holdout40_summary.csv', index=False)
    CHECKS.update(finite_results=True, recent_rows=110, latest_rows=22, holdout_rows=640,
                  legacy_accounted_for=len(PROGRESS)+len(FALLBACKS)==585,
                  protected_inputs_and_git_status_unchanged=protected_check(), holdout_log_append_only=True)
    report('COMPLETE')


if __name__ == '__main__':
    try:
        main()
    except BaseException:
        error = traceback.format_exc()
        if 'PROTECTED' in globals():
            try:
                CHECKS['protected_inputs_and_git_status_unchanged'] = protected_check()
            except BaseException:
                error += '\nProtection check: ' + traceback.format_exc()
        report('STOPPED', error)
        raise
