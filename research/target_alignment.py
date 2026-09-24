"""LightGBM target alignment study: raw_return vs relative_alpha vs execution_alpha (docs/jpx#5_target_align_spec.md).

    PYTHONHASHSEED=0 .venv/bin/python -m research.target_alignment --workers 10

Stage 0 parity: raw_return on the 6 smoke episodes must reproduce the JPX2 run
(research/runs/lgbm_jpx2/lgbm_jpx2__smoke) exactly, else stop. Stage 1 smoke
and stage 2 full dev run momentum, the three LightGBM targets and AutoTS on the
JPX2 episode set, paired by episode. Only PASS_TARGET_STUDY unlocks validation
(2022-2024); only a validation pass unlocks the one holdout run (report only).
Runs are resumable (research/runs/target_alignment/, gitignored).
Results: research/results/target_alignment/.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path

import pandas as pd

from competition.rules import ROOT
from research import compare, run_experiment
from research.lgbm_alpha import log_entries, pair_line, parity_problems, stage_problems, turnover_diagnostics, \
    write_csv
from research.lgbm_jpx2 import episode_rows, metrics, pct

RUNS = ROOT / 'research/runs/target_alignment'
RESULTS = ROOT / 'research/results/target_alignment'
JPX2_SMOKE = ROOT / 'research/runs/lgbm_jpx2/lgbm_jpx2__smoke'
CONFIGS = ROOT / 'research/configs'
STRATEGIES = {   # report order; every run uses the raw config's episode set
    'momentum_20d': CONFIGS / 'baselines/momentum_20d.json',
    'lgbm_raw': CONFIGS / 'baseline_lgbm_raw.json',
    'lgbm_alpha': CONFIGS / 'baseline_lgbm_alpha.json',
    'lgbm_execution_alpha': CONFIGS / 'baseline_lgbm_execution_alpha.json',
    'autots': CONFIGS / 'baseline_autots.json',
}
LABELS = {'momentum_20d': 'Momentum', 'lgbm_raw': 'Raw LGBM', 'lgbm_alpha': 'Alpha LGBM',
          'lgbm_execution_alpha': 'Execution Alpha LGBM', 'autots': 'AutoTS'}
REFERENCE, RAW, ALPHA, EXEC = 'momentum_20d', 'lgbm_raw', 'lgbm_alpha', 'lgbm_execution_alpha'
LGBM = (RAW, ALPHA, EXEC)
PAIRS = {'q1_alpha_vs_raw': (ALPHA, RAW), 'q2_execution_vs_alpha': (EXEC, ALPHA),
         'q3_execution_vs_momentum': (EXEC, REFERENCE), 'execution_vs_raw': (EXEC, RAW),
         'raw_vs_momentum': (RAW, REFERENCE), 'alpha_vs_momentum': (ALPHA, REFERENCE),
         'execution_vs_autots': (EXEC, 'autots')}
STAGES = {'smoke': ('dev', '6'), 'full': ('dev', 'all'), 'validation': ('validation', 'all'),
          'holdout': ('holdout', 'all')}
TARGET_KEYS = ('target_mode', 'target_mean', 'target_std', 'cross_section_count', 'label_start', 'latest_label_end',
               'official_vwap_share', 'proxy_hlc3_share', 'alpha_cross_section_mean_error')
MODEL_KEYS = ('best_iteration', 'train_pearson', 'validation_pearson', 'prediction_std', 'n_train_rows',
              'n_validation_rows', 'n_symbols')


def run_dir(name: str, stage: str) -> Path:
    return RUNS / f'{name}__{stage}'


def stage_config(name: str) -> Path:
    episodes = json.loads(STRATEGIES[RAW].read_text())['episodes']
    path = RUNS / 'configs' / f'{name}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(json.loads(STRATEGIES[name].read_text()), episodes=episodes), indent=1))
    return path


def run(name: str, stage: str, workers: int) -> dict:
    split, n = STAGES[stage]
    argv = ['--config', str(stage_config(name)), '--split', split, '--episodes', n, '--workers', str(workers),
            '--out', str(run_dir(name, stage))] + (['--i-understand-holdout'] if split == 'holdout' else [])
    print(f'== {stage}: {name}', flush=True)
    with contextlib.redirect_stdout(io.StringIO()):
        manifest = run_experiment.main(argv)
    print(f'   {manifest["status"]}: {manifest["n_complete"]}/{len(manifest["episode_ids"])} episodes, '
          f'{manifest["runtime_seconds"]:.0f}s', flush=True)
    return manifest


# ---------------------------------------------------------------- evaluation

def refit_rows(name: str, stage: str, keys) -> list[dict]:
    return [dict(strategy=name, stage=stage, episode=episode, date=e['date'], **{k: e.get(k) for k in keys})
            for episode, e in log_entries(run_dir(name, stage)) if e.get('refit')]


def signal_summary(name: str, stage: str) -> dict:
    """Validation Pearson, best iteration and official-price share over the refits (diagnostic only)."""
    frame = pd.DataFrame(refit_rows(name, stage, (*MODEL_KEYS, 'official_vwap_share')))
    if frame.empty:
        return {}
    share = frame.official_vwap_share.dropna()
    return dict(refits=len(frame), validation_pearson_mean=float(frame.validation_pearson.mean()),
                best_iteration_median=float(frame.best_iteration.median()),
                prediction_std_median=float(frame.prediction_std.median()),
                official_label_share=float(share.mean()) if len(share) else None)


def paired(a: str, b: str, stage: str) -> dict:
    r = compare.compare(run_dir(a, stage), run_dir(b, stage))
    return dict(n=r['n_common'], mean_delta=r['mean_diff'], median_delta=r['median_diff'], win_rate=r['win_rate'],
                ci95=r['ci95'])


def evaluate(stage: str) -> dict:
    rows = {name: episode_rows(run_dir(name, stage)) for name in STRATEGIES}
    reference = {k: s for k, s in rows[REFERENCE].items() if s.get('status') == 'COMPLETE'}
    return dict(table={name: metrics(rows[name], reference) for name in STRATEGIES},
                pairs={key: paired(a, b, stage) for key, (a, b) in PAIRS.items()},
                signal={name: signal_summary(name, stage) for name in LGBM},
                turnover={name: turnover_diagnostics(run_dir(name, stage)) for name in LGBM},
                target_rows=[r for name in LGBM for r in refit_rows(name, stage, TARGET_KEYS)],
                model_rows=[r for name in LGBM for r in refit_rows(name, stage, MODEL_KEYS)],
                episodes={k: {name: rows[name].get(k, {}).get('terminal_return') for name in STRATEGIES}
                          for k in sorted(reference, key=lambda k: reference[k].get('start', k))})


def positive(p: dict) -> bool:
    return p['mean_delta'] > 0 and p['median_delta'] > 0


def best_target(body: dict) -> str:
    """The LightGBM target with the highest paired mean delta vs momentum (median breaks ties)."""
    t = body['table']
    return max(LGBM, key=lambda n: (t[n]['paired_mean_delta'], t[n]['paired_median_delta']))


def beats_momentum(body: dict, name: str) -> bool:
    """Spec §38: paired mean and median delta vs momentum > 0 and no worse disqualification rate."""
    t = body['table']
    return t[name]['paired_mean_delta'] > 0 and t[name]['paired_median_delta'] > 0 \
        and t[name]['disqualification_rate'] <= t[REFERENCE]['disqualification_rate']


def decide(body: dict) -> dict:
    p, best = body['pairs'], best_target(body)
    flags = [flag for flag, ok in (('RELATIVE_ALPHA_SUPPORTED', positive(p['q1_alpha_vs_raw'])),
                                   ('EXECUTION_ALIGNMENT_SUPPORTED', positive(p['q2_execution_vs_alpha']))) if ok]
    if beats_momentum(body, best):
        decision = 'PASS_TARGET_STUDY'
    elif positive(p['execution_vs_raw']):
        decision = 'TARGET_IMPROVED_MODEL_STILL_WEAK'
    else:
        decision = 'STOP_TARGET_FORMULATION'
    return dict(decision=decision, flags=flags, best_target=best)


# ---------------------------------------------------------------- report

NEXT_ACTION = {
    'PASS_TARGET_STUDY': ['最佳 target 已凍結進 validation（見下）；validation 通過才跑一次 holdout'],
    'TARGET_IMPROVED_MODEL_STILL_WEAK': ['target 對齊有幫助但仍輸動能；下一階段才研究 JPX5 features 或 learning-to-rank',
                                         '不調參、不改 portfolio'],
    'STOP_TARGET_FORMULATION': ['三種 target 都沒有明顯改善：問題不只在 target',
                                '下一個方向：JPX5 behavioral features 或直接 ranking objective',
                                '不調參、不改 portfolio、不換 horizon'],
}


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


def target_table(body: dict) -> list[str]:
    s, turn, t = body['signal'], body['turnover'], body['table']
    lines = ['| Target | Validation Pearson | Top25 日重疊 | 進場/日 | 周轉 | 成本 | 官方 VWAP label 比例 |',
             '|---|---:|---:|---:|---:|---:|---:|']
    for name in LGBM:
        share = s[name].get('official_label_share')
        lines.append(f'| {LABELS[name]} | {s[name]["validation_pearson_mean"]:.3f} | '
                     f'{turn[name]["top25_overlap"]:.0%} | {turn[name]["entries_per_day"]:.2f} | '
                     f'{t[name]["turnover"]:.2f} | {t[name]["cost_pct"]:.2%} | '
                     f'{"—" if share is None else format(share, ".0%")} |')
    return lines


def summary_md(result: dict) -> str:
    lines = ['# LightGBM target alignment study', '',
             f'- **資料**：dev {result["episodes"]}，五個策略同一組窗口逐窗口配對；三個 LightGBM 只差 target',
             f'- **Parity**：{result["parity"]}',
             '- **Execution price**：2019–2021 dev 完全沒有官方 VWAP，execution_alpha 用的是 HLC3 proxy；'
             '這裡驗證的是「與 simulator 成交語意對齊」，不是官方 VWAP target', '']
    full, d = result['stages'].get('full'), result['decision']
    if not full or not d:
        return '\n'.join(lines + ['Full dev 還沒跑完。', ''])
    lines += ['## Decision', '', f'**{d["decision"]}**' + (f'（{", ".join(d["flags"])}）' if d['flags'] else '')
              + f'；三個 target 中對動能最好的是 {LABELS[d["best_target"]]}', '',
              '## Main Table（70 個 dev 窗口）', '', *main_table(full), '',
              '## Target Comparison', '',
              pair_line('Raw → Alpha（Q1）', full['pairs']['q1_alpha_vs_raw']),
              pair_line('Alpha → Execution Alpha（Q2）', full['pairs']['q2_execution_vs_alpha']),
              pair_line('Execution Alpha − Momentum（Q3）', full['pairs']['q3_execution_vs_momentum']),
              pair_line('Execution Alpha − Raw', full['pairs']['execution_vs_raw']), '',
              *target_table(full), '',
              '成本差距不代表「沒成本就會贏」：沒有跑無成本反事實。', '',
              '## Next Action', '', *[f'- {x}' for x in NEXT_ACTION[d['decision']]], '']
    for stage, title in (('validation', 'Validation 2022–2024'), ('holdout', 'Holdout 2025–2026/09（report only）')):
        if stage in result['stages']:
            body = result['stages'][stage]
            best = d['best_target']
            share = body['signal'][best].get('official_label_share')
            lines += [f'## {title}', '', *main_table(body), '',
                      pair_line(f'{LABELS[best]} − Momentum', body['pairs'][
                          {EXEC: 'q3_execution_vs_momentum', ALPHA: 'alpha_vs_momentum', RAW: 'raw_vs_momentum'}[best]]),
                      f'- 官方 VWAP execution label 比例：{"—" if share is None else format(share, ".0%")}', '']
    lines += [f'- Validation：{result["validation"]}；Holdout：{result["holdout"]}', '']
    return '\n'.join(lines)


def failures_md(result: dict) -> str:
    lines = ['# Failures', '']
    for stage, problems in result['problems'].items():
        lines += [f'## {stage}', ''] + ([f'- {p}' for p in problems] or ['- 無']) + ['']
    return '\n'.join(lines)


def write_outputs(result: dict):
    RESULTS.mkdir(parents=True, exist_ok=True)
    stages = result['stages']
    write_csv(RESULTS / 'target_diagnostics.csv', [r for b in stages.values() for r in b['target_rows']])
    write_csv(RESULTS / 'model_diagnostics.csv', [r for b in stages.values() for r in b['model_rows']])
    write_csv(RESULTS / 'comparison.csv', [dict(stage=s, strategy=n, **m, **stages[s]['turnover'].get(n, {}))
                                           for s in stages for n, m in stages[s]['table'].items()])
    compact = {s: {k: v for k, v in b.items() if k not in ('target_rows', 'model_rows')} for s, b in stages.items()}
    (RESULTS / 'summary.json').write_text(json.dumps(dict(result, stages=compact), indent=1, default=float))
    (RESULTS / 'summary.md').write_text(summary_md(result))
    (RESULTS / 'failures.md').write_text(failures_md(result))


def run_stage(result: dict, stage: str, workers: int) -> bool:
    manifests = {name: run(name, stage, workers) for name in STRATEGIES}
    result['stages'][stage] = evaluate(stage)
    result['problems'][stage] = stage_problems(manifests, result['stages'][stage]['table'])
    write_outputs(result)
    return not result['problems'][stage]


def check_parity(result: dict, workers: int):
    run(RAW, 'smoke', workers)
    problems = parity_problems(run_dir(RAW, 'smoke'), JPX2_SMOKE)
    result['problems']['parity'] = problems
    result['parity'] = ('PASS（6 窗口的報酬、交易、持股、委託、訓練資料 audit、模型與預測 log 與 JPX2 完全相同）'
                        if not problems else 'FAIL: ' + '; '.join(problems))
    write_outputs(result)
    if problems:
        raise SystemExit('Parity failed: ' + '; '.join(problems))


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    episodes = json.loads(STRATEGIES[RAW].read_text())['episodes']
    result = dict(episodes=f'{episodes["start"]} 起，{" + ".join(episodes["offsets"])}', stages={}, problems={},
                  parity=None, decision=None, validation='未執行（dev gate 未通過）', holdout='未執行')
    check_parity(result, args.workers)
    if not run_stage(result, 'smoke', args.workers):
        raise SystemExit('Smoke failed: ' + '; '.join(result['problems']['smoke']))
    run_stage(result, 'full', args.workers)
    result['decision'] = decide(result['stages']['full'])
    write_outputs(result)
    if result['decision']['decision'] == 'PASS_TARGET_STUDY':
        run_stage(result, 'validation', args.workers)
        best = result['decision']['best_target']
        passed = beats_momentum(result['stages']['validation'], best) and not result['problems']['validation']
        result['validation'] = f'PASS（{LABELS[best]} 凍結）' if passed else 'FAIL（不跑 holdout）'
        write_outputs(result)
        if passed:
            run_stage(result, 'holdout', args.workers)
            result['holdout'] = '已跑一次，只報告'
            write_outputs(result)
    print(f'Decision: {result["decision"]["decision"]} -> {RESULTS / "summary.md"}', flush=True)
    return result


if __name__ == '__main__':
    main()
