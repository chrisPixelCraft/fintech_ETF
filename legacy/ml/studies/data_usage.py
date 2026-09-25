"""LightGBM data-usage study on a 2019 data floor: does using the discarded rows help?

    PYTHONHASHSEED=0 .venv/bin/python -m research.data_usage --workers 10

Every strategy sees market data from 2019-01-01 only (training, features,
AutoTS history) and is scored on the dev episodes from 2020 (month start and
mid-month), after one year of history. The raw-return LightGBM control is
compared with single changes:
- relaxed_volatility: volatility_h needs 80% of its h returns observed, not all.
- refit_on_all: after early stopping picks the tree count, refit on train + validation.
- both.
Stage 0 checks that the raw config (no floor) still reproduces the JPX2 smoke
run. Runs are resumable (research/runs/data_usage/, gitignored); no
validation or holdout split is run. Results: research/results/data_usage/.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path

from competition.rules import ROOT
from research import compare, run_experiment
from research.lgbm_alpha import log_entries, pair_line, parity_problems, stage_problems, turnover_diagnostics, \
    write_csv
from research.lgbm_jpx2 import episode_rows, metrics, pct

RUNS = ROOT / 'research/runs/data_usage'
RESULTS = ROOT / 'research/results/data_usage'
JPX2_SMOKE = ROOT / 'research/runs/lgbm_jpx2/lgbm_jpx2__smoke'
CONFIGS = ROOT / 'research/configs'
DATA = {'start': '2019-01-01'}
EPISODES = {'start': '2020-01-01', 'offsets': ['month_start', 'mid_month']}
RAW_CONFIG = CONFIGS / 'baseline_lgbm_raw.json'
VARIANTS = {   # LightGBM params changed from the raw-return control
    'lgbm_control': {},
    'lgbm_relaxed_volatility': dict(min_observed_share=.8),
    'lgbm_refit_on_all': dict(refit_on_all=True),
    'lgbm_both': dict(min_observed_share=.8, refit_on_all=True),
}
STRATEGIES = ('momentum_20d', *VARIANTS, 'autots')
SOURCES = {'momentum_20d': CONFIGS / 'baselines/momentum_20d.json', 'autots': CONFIGS / 'baseline_autots.json'}
LABELS = {'momentum_20d': 'Momentum', 'lgbm_control': 'LGBM 對照組', 'lgbm_relaxed_volatility': 'B 波動度放寬',
          'lgbm_refit_on_all': 'C 驗證資料也訓練', 'lgbm_both': 'B+C', 'autots': 'AutoTS'}
REFERENCE, CONTROL = 'momentum_20d', 'lgbm_control'
STAGES = {'smoke': '6', 'full': 'all'}
MODEL_KEYS = ('best_iteration', 'validation_pearson', 'train_pearson', 'n_train_rows', 'n_validation_rows',
              'refit_rows', 'train_start', 'validation_end')


def config_for(name: str) -> dict:
    if name in VARIANTS:
        raw = json.loads(RAW_CONFIG.read_text())
        return dict(raw, name=name, description=f'raw-return LightGBM, 2019 data floor, {VARIANTS[name] or "control"}',
                    params=dict(raw['params'], **VARIANTS[name]), data=DATA, episodes=EPISODES)
    return dict(json.loads(SOURCES[name].read_text()), data=DATA, episodes=EPISODES)


def run_dir(name: str, stage: str) -> Path:
    return RUNS / f'{name}__{stage}'


def run(name: str, stage: str, workers: int, config_path: Path | None = None, split: str = 'dev') -> dict:
    if config_path is None:
        config_path = RUNS / 'configs' / f'{name}.json'
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config_for(name), indent=1))
    print(f'== {stage}: {name}', flush=True)
    with contextlib.redirect_stdout(io.StringIO()):
        manifest = run_experiment.main(['--config', str(config_path), '--split', split, '--episodes', STAGES[stage],
                                        '--workers', str(workers), '--out', str(run_dir(name, stage))])
    print(f'   {manifest["status"]}: {manifest["n_complete"]}/{len(manifest["episode_ids"])} episodes, '
          f'{manifest["runtime_seconds"]:.0f}s', flush=True)
    return manifest


def paired(a: str, b: str, stage: str) -> dict:
    r = compare.compare(run_dir(a, stage), run_dir(b, stage))
    return dict(n=r['n_common'], mean_delta=r['mean_diff'], median_delta=r['median_diff'], win_rate=r['win_rate'],
                ci95=r['ci95'])


def evaluate(stage: str) -> dict:
    rows = {name: episode_rows(run_dir(name, stage)) for name in STRATEGIES}
    reference = {k: s for k, s in rows[REFERENCE].items() if s.get('status') == 'COMPLETE'}
    variants = [v for v in VARIANTS if v != CONTROL]
    return dict(table={name: metrics(rows[name], reference) for name in STRATEGIES},
                vs_control={v: paired(v, CONTROL, stage) for v in variants},
                vs_momentum={v: paired(v, REFERENCE, stage) for v in VARIANTS},
                turnover={v: turnover_diagnostics(run_dir(v, stage)) for v in VARIANTS},
                model_rows=[r for v in VARIANTS for r in refit_rows_for(v, stage)],
                episodes={k: {name: rows[name].get(k, {}).get('terminal_return') for name in STRATEGIES}
                          for k in sorted(reference, key=lambda k: reference[k].get('start', k))})


def refit_rows_for(name: str, stage: str) -> list[dict]:
    """One row per LightGBM refit: tree count, early-stopping scores and training-set size."""
    return [dict(strategy=name, stage=stage, episode=episode, date=e['date'], **{k: e.get(k) for k in MODEL_KEYS})
            for episode, e in log_entries(run_dir(name, stage)) if e.get('refit')]


def adopted(p: dict) -> bool:
    return p['mean_delta'] > 0 and p['median_delta'] > 0


# ---------------------------------------------------------------- report

def main_table(body: dict) -> list[str]:
    lines = ['| 策略 | 平均 | 中位數 | Δ vs 動能 | 勝率 | P10 | 最差 | MDD | 周轉 | 成本 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for name, m in body['table'].items():
        ref = name == REFERENCE
        lines.append(f'| {LABELS[name]} | {pct(m["mean"])} | {pct(m["median"])} | '
                     f'{"—" if ref else pct(m["paired_mean_delta"])} | '
                     f'{"—" if ref else format(m["paired_win_rate"], ".0%")} | {pct(m["p10"])} | {pct(m["worst"])} | '
                     f'{m["mdd"]:.2%} | {m["turnover"]:.2f} | {m["cost_pct"]:.2%} |')
    return lines


def summary_md(result: dict) -> str:
    lines = ['# LightGBM 資料使用研究（資料從 2019 年起）', '',
             f'- **資料**：所有策略只看 2019-01-01 以後的資料；dev 窗口從 2020 起（月初＋月中）',
             f'- **Parity**：{result["parity"]}', '']
    full = result['stages'].get('full')
    if not full:
        return '\n'.join(lines + ['Full dev 還沒跑完。', ''])
    n = full['table'][CONTROL]['paired_n']
    lines += [f'## 主表（{n} 個 dev 窗口）', '', *main_table(full), '', '## 各改動 vs 對照組', '']
    lines += [pair_line(LABELS[v], p) + ('　→ **採用**' if adopted(p) else '　→ 不採用')
              for v, p in full['vs_control'].items()]
    lines += ['', '## LightGBM vs 動能', '']
    lines += [pair_line(LABELS[v], p) for v, p in full['vs_momentum'].items()]
    lines += ['', '採用規則：對 LGBM 對照組的配對平均 Δ > 0 且中位數 Δ > 0。沒有跑 validation／holdout。', '']
    return '\n'.join(lines)


def write_outputs(result: dict):
    RESULTS.mkdir(parents=True, exist_ok=True)
    stages = result['stages']
    write_csv(RESULTS / 'model_diagnostics.csv', [r for b in stages.values() for r in b['model_rows']])
    write_csv(RESULTS / 'comparison.csv', [dict(stage=s, strategy=n, **m, **stages[s]['turnover'].get(n, {}))
                                           for s in stages for n, m in stages[s]['table'].items()])
    compact = {s: {k: v for k, v in b.items() if k != 'model_rows'} for s, b in stages.items()}
    (RESULTS / 'summary.json').write_text(json.dumps(dict(result, stages=compact), indent=1, default=float))
    (RESULTS / 'summary.md').write_text(summary_md(result))
    (RESULTS / 'failures.md').write_text('\n'.join(['# Failures', ''] + [
        line for stage, problems in result['problems'].items()
        for line in (f'## {stage}', '', *([f'- {p}' for p in problems] or ['- 無']), '')]))


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    result = dict(stages={}, problems={}, parity=None)
    run('parity_raw', 'smoke', args.workers, config_path=RAW_CONFIG)
    problems = parity_problems(run_dir('parity_raw', 'smoke'), JPX2_SMOKE)
    result['problems']['parity'] = problems
    result['parity'] = 'PASS（原始設定、不截資料時，6 窗口與 JPX2 完全相同）' if not problems else 'FAIL: ' + '; '.join(problems)
    write_outputs(result)
    if problems:
        raise SystemExit('Parity failed: ' + '; '.join(problems))
    for stage in STAGES:
        manifests = {name: run(name, stage, args.workers) for name in STRATEGIES}
        result['stages'][stage] = evaluate(stage)
        result['problems'][stage] = stage_problems(manifests, result['stages'][stage]['table'])
        write_outputs(result)
        if stage == 'smoke' and result['problems'][stage]:
            raise SystemExit('Smoke failed: ' + '; '.join(result['problems'][stage]))
    print(f'Done -> {RESULTS / "summary.md"}', flush=True)
    return result


if __name__ == '__main__':
    main()
