"""JPX #2 LightGBM baseline vs momentum_20d vs AutoTS on the same dev episodes (docs/jpx#2_spec.md §16-18).

    PYTHONHASHSEED=0 .venv/bin/python -m research.lgbm_jpx2 --workers 10

Stage 1 (smoke): 6 dev episodes per strategy; must finish with every episode
COMPLETE and none disqualified. Stage 2 (full): every dev episode. All three
strategies use the LightGBM config's episode set (2019+ month-start and
mid-month windows), so comparisons are paired episode by episode. Runs are
resumable (research/runs/lgbm_jpx2/, gitignored); rerun the same command after
an interruption. Holdout is never touched. Results:
research/results/lgbm_jpx2/{summary.md, summary.json, comparison.csv, failures.md}.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
from pathlib import Path

import numpy as np

from competition.rules import ROOT
from research import compare, run_experiment

RUNS = ROOT / 'research/runs/lgbm_jpx2'
RESULTS = ROOT / 'research/results/lgbm_jpx2'
LGBM = ROOT / 'research/configs/baseline_lgbm_jpx2.json'
STRATEGIES = {   # name -> source config; every run uses LGBM's episode set
    'momentum_20d': ROOT / 'research/configs/baselines/momentum_20d.json',
    'autots': ROOT / 'research/configs/baseline_autots.json',
    'lgbm_jpx2': LGBM,
}
REFERENCE = 'momentum_20d'
STAGES = {'smoke': '6', 'full': 'all'}
SPLIT = 'dev'


def stage_config(name: str) -> Path:
    """The strategy's config with LGBM's episode set, written next to the runs."""
    episodes = json.loads(LGBM.read_text())['episodes']
    config = dict(json.loads(STRATEGIES[name].read_text()), episodes=episodes)
    path = RUNS / 'configs' / f'{name}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=1))
    return path


def run_dir(name: str, stage: str) -> Path:
    return RUNS / f'{name}__{stage}'


def run_stage(stage: str, workers: int) -> dict:
    """Run (or resume) every strategy on the stage's episodes; returns the manifests."""
    manifests = {}
    for name in STRATEGIES:
        print(f'== {stage}: {name}', flush=True)
        with contextlib.redirect_stdout(io.StringIO()):
            manifests[name] = run_experiment.main(['--config', str(stage_config(name)), '--split', SPLIT,
                                                   '--episodes', STAGES[stage], '--workers', str(workers),
                                                   '--out', str(run_dir(name, stage))])
        m = manifests[name]
        print(f'   {m["status"]}: {m["n_complete"]}/{len(m["episode_ids"])} episodes, '
              f'{m["runtime_seconds"]:.0f}s', flush=True)
    return manifests


def episode_rows(run: Path) -> dict:
    """Every episode summary of a run (COMPLETE or not), by episode id."""
    return {p.parent.name: json.loads(p.read_text()) for p in sorted(run.glob('episodes/*/summary.json'))}


def metrics(rows: dict, reference: dict) -> dict:
    """Return distribution, paired deltas vs the reference, costs and failures over completed episodes."""
    done = {k: s for k, s in rows.items() if s.get('status') == 'COMPLETE'}
    r = np.array([s['terminal_return'] for s in done.values()])
    common = sorted(set(done) & set(reference))
    delta = np.array([done[k]['terminal_return'] - reference[k]['terminal_return'] for k in common])
    return dict(n=len(done), n_failed=len(rows) - len(done), mean=float(r.mean()), median=float(np.median(r)),
                p25=float(np.percentile(r, 25)), p10=float(np.percentile(r, 10)), worst=float(r.min()),
                paired_n=len(common), paired_mean_delta=float(delta.mean()),
                paired_median_delta=float(np.median(delta)), paired_win_rate=float((delta > 0).mean()),
                mdd=float(np.mean([s['max_drawdown'] for s in done.values()])),
                turnover=float(np.mean([s['turnover'] for s in done.values()])),
                cost_pct=float(np.mean([s['costs'] for s in done.values()]) / 1e9),
                warnings=int(sum(s['warning_days'] for s in done.values())),
                disqualified=int(sum(s['disqualified'] for s in done.values())),
                disqualification_rate=float(np.mean([s['disqualified'] for s in done.values()])),
                runtime_seconds_per_episode=float(np.mean([s['runtime_seconds'] for s in done.values()])))


def model_diagnostics(run: Path) -> dict:
    """LightGBM refit statistics from the strategy logs (never used for the decision)."""
    refits = [e for p in sorted(run.glob('episodes/*/strategy_log.json'))
              for e in json.loads(p.read_text()) if e.get('refit')]
    if not refits:
        return {}
    best = np.array([e['best_iteration'] for e in refits])
    return dict(refits=len(refits), best_iteration_median=float(np.median(best)),
                best_iteration_p10=float(np.percentile(best, 10)), best_iteration_max=int(best.max()),
                validation_pearson_mean=float(np.mean([e['validation_pearson'] for e in refits])),
                train_rows_median=float(np.median([e['n_train_rows'] for e in refits])),
                eligible_median=float(np.median([e['eligible'] for p in sorted(run.glob('episodes/*/strategy_log.json'))
                                                 for e in json.loads(p.read_text())])))


def smoke_problems(manifests: dict, table: dict) -> list[str]:
    problems = [f'{name}: {m["n_failed"]} failed episodes {m["failed"]}' for name, m in manifests.items()
                if m['n_failed']]
    problems += [f'{name}: {row["disqualified"]} disqualified' for name, row in table.items() if row['disqualified']]
    return problems


def decide(table: dict) -> str:
    """Spec §18: paired median and mean delta vs momentum > 0 and no higher disqualification rate."""
    lgbm, ref = table['lgbm_jpx2'], table[REFERENCE]
    ok = lgbm['paired_median_delta'] > 0 and lgbm['paired_mean_delta'] > 0 \
        and lgbm['disqualification_rate'] <= ref['disqualification_rate']
    return 'PASS_TO_STAGE_2' if ok else 'STOP_LGBM_BASELINE'


def evaluate(stage: str) -> dict:
    rows = {name: episode_rows(run_dir(name, stage)) for name in STRATEGIES}
    reference = {k: s for k, s in rows[REFERENCE].items() if s.get('status') == 'COMPLETE'}
    table = {name: metrics(r, reference) for name, r in rows.items()}
    ci = compare.compare(run_dir('lgbm_jpx2', stage), run_dir(REFERENCE, stage))['ci95']
    vs_autots = compare.compare(run_dir('lgbm_jpx2', stage), run_dir('autots', stage))
    return dict(table=table, paired_mean_delta_ci95=ci,
                vs_autots=dict(mean_delta=vs_autots['mean_diff'], median_delta=vs_autots['median_diff'],
                               win_rate=vs_autots['win_rate'], ci95=vs_autots['ci95']),
                diagnostics=model_diagnostics(run_dir('lgbm_jpx2', stage)),
                episodes={k: {name: rows[name].get(k, {}).get('terminal_return') for name in STRATEGIES}
                          for k in sorted(rows[REFERENCE], key=lambda k: rows[REFERENCE][k].get('start', k))})


def pct(x) -> str:
    return '—' if x is None else f'{x:+.2%}'


def summary_md(result: dict) -> str:
    full = result['stages'].get('full')
    lines = ['# JPX #2 LightGBM baseline', '',
             f'- **資料**：dev 窗口（{result["episodes"]}），三個策略跑同一組窗口，逐窗口配對比較；holdout 沒有使用',
             '- **比較對象**：momentum_20d（主要）、AutoTS baseline_autots（次要）；portfolio、planner、成本都相同', '']
    if not full:
        return '\n'.join(lines + ['Stage 2 還沒跑完。', ''])
    t = full['table']
    lines += ['## Result', '',
              f'LightGBM vs Momentum vs AutoTS（{t["lgbm_jpx2"]["paired_n"]} 個配對窗口）：**{result["decision"]}**', '',
              '## Evidence', '',
              '| 策略 | 平均 | 中位數 | 配對平均 Δ | 配對中位數 Δ | 勝過動能 | P25 | P10 | 最差 | 平均 MDD | 周轉 | 成本 | 警告 | 失格 | 秒/窗口 |',
              '|---|' + '---:|' * 14]
    for name, m in t.items():
        paired = name != REFERENCE          # the reference paired with itself is all zeros
        win = f'{m["paired_win_rate"]:.0%}' if paired else '—'
        lines.append(f'| {name} | {pct(m["mean"])} | {pct(m["median"])} | '
                     f'{pct(m["paired_mean_delta"] if paired else None)} | '
                     f'{pct(m["paired_median_delta"] if paired else None)} | {win} | {pct(m["p25"])} | '
                     f'{pct(m["p10"])} | {pct(m["worst"])} | {m["mdd"]:.2%} | {m["turnover"]:.2f} | '
                     f'{m["cost_pct"]:.2%} | {m["warnings"]} | {m["disqualified"]} | '
                     f'{m["runtime_seconds_per_episode"]:.0f} |')
    ci, va, d = full['paired_mean_delta_ci95'], full['vs_autots'], full['diagnostics']
    lines += ['', f'- LightGBM − 動能 配對平均 Δ 的 95% bootstrap 區間：{pct(ci[0])} ~ {pct(ci[1])}',
              f'- LightGBM − AutoTS：平均 Δ {pct(va["mean_delta"])}，中位數 Δ {pct(va["median_delta"])}，'
              f'勝率 {va["win_rate"]:.0%}，95% 區間 {pct(va["ci95"][0])} ~ {pct(va["ci95"][1])}',
              f'- 模型：{d.get("refits")} 次 refit，best iteration 中位數 {d.get("best_iteration_median")}'
              f'（P10 {d.get("best_iteration_p10")}，最大 {d.get("best_iteration_max")}），'
              f'validation Pearson 平均 {d.get("validation_pearson_mean", 0):.3f}（pooled，不是判斷依據）',
              f'- Smoke（6 窗口）：{result["smoke_status"]}', '',
              '## Decision', '', f'`{result["decision"]}`', '',
              '規則（spec §18）：配對中位數 Δ > 0、配對平均 Δ > 0、失格率不高於動能，三項都成立才進下一階段。', '']
    return '\n'.join(lines)


def failures_md(result: dict) -> str:
    lines = ['# Failures', '']
    for stage, problems in result['problems'].items():
        lines += [f'## {stage}', ''] + ([f'- {p}' for p in problems] or ['- 無']) + ['']
    return '\n'.join(lines)


def write_outputs(result: dict):
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / 'summary.json').write_text(json.dumps(result, indent=1, default=float))
    (RESULTS / 'summary.md').write_text(summary_md(result))
    (RESULTS / 'failures.md').write_text(failures_md(result))
    with (RESULTS / 'comparison.csv').open('w', newline='') as handle:
        writer = None
        for stage, body in result['stages'].items():
            for name, m in body['table'].items():
                row = dict(stage=stage, strategy=name, **m)
                writer = writer or csv.DictWriter(handle, fieldnames=list(row))
                if handle.tell() == 0:
                    writer.writeheader()
                writer.writerow({k: round(v, 6) if isinstance(v, float) else v for k, v in row.items()})


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    episodes = json.loads(LGBM.read_text())['episodes']
    result = dict(episodes=f'{episodes["start"]} 起，{" + ".join(episodes["offsets"])}', split=SPLIT, stages={},
                  problems={}, smoke_status=None, decision=None)
    smoke = run_stage('smoke', args.workers)
    result['stages']['smoke'] = evaluate('smoke')
    result['problems']['smoke'] = smoke_problems(smoke, result['stages']['smoke']['table'])
    result['smoke_status'] = 'PASS' if not result['problems']['smoke'] else 'FAIL'
    write_outputs(result)
    if result['problems']['smoke']:
        raise SystemExit('Smoke failed: ' + '; '.join(result['problems']['smoke']))
    full = run_stage('full', args.workers)
    result['stages']['full'] = evaluate('full')
    result['problems']['full'] = smoke_problems(full, result['stages']['full']['table'])
    result['decision'] = decide(result['stages']['full']['table'])
    write_outputs(result)
    print(f'Decision: {result["decision"]} -> {RESULTS / "summary.md"}', flush=True)
    return result


if __name__ == '__main__':
    main()
