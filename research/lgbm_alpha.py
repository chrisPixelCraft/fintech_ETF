"""Relative-alpha LightGBM vs raw-return LightGBM vs momentum_20d vs AutoTS (docs/jpx#5_alpha_spec.md).

    PYTHONHASHSEED=0 .venv/bin/python -m research.lgbm_alpha --workers 10

Stage 1 parity: raw_return LightGBM on the 6 smoke episodes must reproduce the
JPX2 run in research/runs/lgbm_jpx2/lgbm_jpx2__smoke (returns, trades,
holdings, model log) exactly, else stop. Stage 2 smoke and stage 3 full dev
run all four strategies on the JPX2 episode set, paired by episode. Only
PASS_ALPHA_DEV unlocks validation (2022-2024); only a validation pass unlocks
the one holdout run, which is report-only. Runs are resumable
(research/runs/lgbm_alpha/, gitignored). Results: research/results/lgbm_alpha/.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
from pathlib import Path

import numpy as np
import pandas as pd

from competition.rules import ROOT
from research import compare, run_experiment
from research.lgbm_jpx2 import episode_rows, metrics, pct

RUNS = ROOT / 'research/runs/lgbm_alpha'
RESULTS = ROOT / 'research/results/lgbm_alpha'
JPX2_SMOKE = ROOT / 'research/runs/lgbm_jpx2/lgbm_jpx2__smoke'
CONFIGS = ROOT / 'research/configs'
STRATEGIES = {   # report order; every run uses the alpha config's episode set
    'momentum_20d': CONFIGS / 'baselines/momentum_20d.json',
    'lgbm_raw': CONFIGS / 'baseline_lgbm_jpx2.json',
    'lgbm_alpha': CONFIGS / 'baseline_lgbm_alpha.json',
    'autots': CONFIGS / 'baseline_autots.json',
}
LABELS = {'momentum_20d': 'Momentum', 'lgbm_raw': 'Raw LGBM', 'lgbm_alpha': 'Alpha LGBM', 'autots': 'AutoTS'}
REFERENCE, CANDIDATE, RAW = 'momentum_20d', 'lgbm_alpha', 'lgbm_raw'
LGBM = (RAW, CANDIDATE)
STAGES = {'smoke': ('dev', '6'), 'full': ('dev', 'all'), 'validation': ('validation', 'all'),
          'holdout': ('holdout', 'all')}
PARITY_FILES = ('trades.csv', 'holdings.csv', 'orders.csv')
DIAGNOSTIC_KEYS = ('best_iteration', 'validation_pearson', 'train_pearson', 'target_std', 'prediction_std',
                   'n_train_rows', 'n_validation_rows', 'n_symbols', 'latest_label_end', 'raw_target_mean',
                   'raw_target_std', 'alpha_target_mean', 'alpha_target_std', 'alpha_cross_section_mean_error')


def stage_config(name: str) -> Path:
    episodes = json.loads(STRATEGIES[CANDIDATE].read_text())['episodes']
    path = RUNS / 'configs' / f'{name}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(json.loads(STRATEGIES[name].read_text()), episodes=episodes), indent=1))
    return path


def run_dir(name: str, stage: str) -> Path:
    return RUNS / f'{name}__{stage}'


def run(name: str, stage: str, workers: int) -> dict:
    split, n = STAGES[stage]
    argv = ['--config', str(stage_config(name)), '--split', split, '--episodes', n, '--workers', str(workers),
            '--out', str(run_dir(name, stage))]
    if split == 'holdout':
        argv.append('--i-understand-holdout')
    print(f'== {stage}: {name}', flush=True)
    with contextlib.redirect_stdout(io.StringIO()):
        manifest = run_experiment.main(argv)
    print(f'   {manifest["status"]}: {manifest["n_complete"]}/{len(manifest["episode_ids"])} episodes, '
          f'{manifest["runtime_seconds"]:.0f}s', flush=True)
    return manifest


def run_stage(stage: str, names, workers: int) -> dict:
    return {name: run(name, stage, workers) for name in names}


# ---------------------------------------------------------------- parity

def parity_problems(new: Path, old: Path) -> list[str]:
    """Differences between a raw_return rerun and the JPX2 run on their common episodes."""
    if not old.is_dir():
        raise SystemExit(f'Parity reference {old} is missing; rerun research.lgbm_jpx2 first')
    problems, episodes = [], sorted(p.name for p in (old / 'episodes').iterdir())
    for e in episodes:
        a, b = old / 'episodes' / e, new / 'episodes' / e
        ra, rb = json.loads((a / 'summary.json').read_text()), json.loads((b / 'summary.json').read_text())
        if ra['terminal_return'] != rb['terminal_return']:
            problems.append(f'{e}: return {ra["terminal_return"]} != {rb["terminal_return"]}')
        problems += [f'{e}: {f} differs' for f in PARITY_FILES if (a / f).read_text() != (b / f).read_text()]
        la, lb = json.loads((a / 'strategy_log.json').read_text()), json.loads((b / 'strategy_log.json').read_text())
        if len(la) != len(lb) or any(x[k] != y.get(k) for x, y in zip(la, lb) for k in x):
            problems.append(f'{e}: model/prediction log differs')
    return problems


# ---------------------------------------------------------------- evaluation

def log_entries(run: Path) -> list[tuple[str, dict]]:
    return [(p.parent.name, e) for p in sorted(run.glob('episodes/*/strategy_log.json'))
            for e in json.loads(p.read_text())]


def turnover_diagnostics(run: Path) -> dict:
    """Mean daily rank stability and target turnover (diagnostic only)."""
    days = pd.DataFrame([e for _, e in log_entries(run)])
    if days.empty or 'top25_overlap' not in days:
        return {}
    return dict(top25_overlap=float(days.top25_overlap.dropna().mean()),
                top35_overlap=float(days.top35_overlap.dropna().mean()),
                entries_per_day=float(days.entries.mean()), exits_per_day=float(days.exits.mean()),
                target_one_way_turnover_per_day=float(days.one_way_turnover.mean()))


def refit_rows(name: str, stage: str) -> list[dict]:
    return [dict(strategy=name, stage=stage, episode=episode, date=e['date'], **{k: e.get(k) for k in DIAGNOSTIC_KEYS})
            for episode, e in log_entries(run_dir(name, stage)) if e.get('refit')]


def model_summary(rows: list[dict]) -> dict:
    frame = pd.DataFrame(rows)
    if frame.empty:
        return {}
    return dict(refits=len(frame), best_iteration_median=float(frame.best_iteration.median()),
                best_iteration_p10=float(frame.best_iteration.quantile(.1)),
                validation_pearson_mean=float(frame.validation_pearson.mean()),
                prediction_std_median=float(frame.prediction_std.median()),
                target_std_median=float(frame.target_std.median()),
                alpha_cross_section_mean_error_max=float(frame.alpha_cross_section_mean_error.max()))


def paired(a: str, b: str, stage: str) -> dict:
    result = compare.compare(run_dir(a, stage), run_dir(b, stage))
    return dict(n=result['n_common'], mean_delta=result['mean_diff'], median_delta=result['median_diff'],
                win_rate=result['win_rate'], ci95=result['ci95'])


def evaluate(stage: str, names) -> dict:
    rows = {name: episode_rows(run_dir(name, stage)) for name in names}
    reference = {k: s for k, s in rows[REFERENCE].items() if s.get('status') == 'COMPLETE'}
    table = {name: metrics(rows[name], reference) for name in names}
    pairs = {f'{CANDIDATE}_vs_{b}': paired(CANDIDATE, b, stage) for b in names if b != CANDIDATE}
    if RAW in names:
        pairs[f'{RAW}_vs_{REFERENCE}'] = paired(RAW, REFERENCE, stage)
    refits = [r for name in LGBM if name in names for r in refit_rows(name, stage)]
    return dict(table=table, pairs=pairs, refits=refits,
                models={name: model_summary([r for r in refits if r['strategy'] == name]) for name in LGBM
                        if name in names},
                turnover={name: turnover_diagnostics(run_dir(name, stage)) for name in LGBM if name in names},
                episodes={k: {name: rows[name].get(k, {}).get('terminal_return') for name in names}
                          for k in sorted(rows[REFERENCE], key=lambda k: rows[REFERENCE][k].get('start', k))})


def stage_problems(manifests: dict, table: dict) -> list[str]:
    problems = [f'{name}: {m["n_failed"]} failed episodes {m["failed"]}' for name, m in manifests.items()
                if m['n_failed']]
    return problems + [f'{name}: {row["disqualified"]} disqualified' for name, row in table.items()
                       if row['disqualified']]


def beats_momentum(body: dict) -> bool:
    """Spec §38: paired mean and median delta vs momentum > 0 and no higher disqualification rate."""
    t, p = body['table'], body['pairs'][f'{CANDIDATE}_vs_{REFERENCE}']
    return p['mean_delta'] > 0 and p['median_delta'] > 0 \
        and t[CANDIDATE]['disqualification_rate'] <= t[REFERENCE]['disqualification_rate']


def decide(body: dict) -> str:
    """PASS_ALPHA_DEV, else ALPHA_SIGNAL_PROMISING_COST_BLOCKED (alpha significantly beats raw, CI > 0, while
    costing more than momentum), else STOP_ALPHA_LGBM."""
    if beats_momentum(body):
        return 'PASS_ALPHA_DEV'
    vs_raw, t = body['pairs'][f'{CANDIDATE}_vs_{RAW}'], body['table']
    better = vs_raw['mean_delta'] > 0 and vs_raw['median_delta'] > 0 and vs_raw['ci95'][0] > 0
    if better and t[CANDIDATE]['cost_pct'] > t[REFERENCE]['cost_pct']:
        return 'ALPHA_SIGNAL_PROMISING_COST_BLOCKED'
    return 'STOP_ALPHA_LGBM'


# ---------------------------------------------------------------- report

NEXT_ACTION = {
    'PASS_ALPHA_DEV': '已依 spec 跑 validation（見下），通過才跑一次 holdout',
    'ALPHA_SIGNAL_PROMISING_COST_BLOCKED': 'alpha 訊號比 raw 好但成本吃掉優勢；下一版 spec 研究 turnover，本輪不改',
    'STOP_ALPHA_LGBM': '停止 alpha LightGBM；不調參、不加特徵、不換 horizon、不改 portfolio',
}


def main_table(body: dict) -> list[str]:
    lines = ['| 策略 | 平均 | 中位數 | 配對 Δ vs 動能 | 勝率 | P10 | 最差 | MDD | 周轉 | 成本 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for name, m in body['table'].items():
        ref = name == REFERENCE
        lines.append(f'| {LABELS[name]} | {pct(m["mean"])} | {pct(m["median"])} | '
                     f'{"—" if ref else pct(m["paired_mean_delta"])} | '
                     f'{"—" if ref else format(m["paired_win_rate"], ".0%")} | {pct(m["p10"])} | '
                     f'{pct(m["worst"])} | {m["mdd"]:.2%} | {m["turnover"]:.2f} | {m["cost_pct"]:.2%} |')
    return lines


def pair_line(label: str, p: dict) -> str:
    return (f'- {label}：平均 Δ {pct(p["mean_delta"])}，中位數 Δ {pct(p["median_delta"])}，勝率 {p["win_rate"]:.0%}，'
            f'95% CI {pct(p["ci95"][0])} ~ {pct(p["ci95"][1])}（{p["n"]} 窗口）')


def diagnosis(body: dict, decision: str) -> list[str]:
    p, t, turn = body['pairs'], body['table'], body['turnover']
    vs_raw = p[f'{CANDIDATE}_vs_{RAW}']
    rank = lambda n: f'{turn.get(n, {}).get("top25_overlap", float("nan")):.0%}'
    return [f'1. **Target effect**：alpha − raw 配對平均 {pct(vs_raw["mean_delta"])}、中位數 '
            f'{pct(vs_raw["median_delta"])}（95% CI {pct(vs_raw["ci95"][0])} ~ {pct(vs_raw["ci95"][1])}）；'
            f'Top25 日重疊 raw {rank(RAW)} → alpha {rank(CANDIDATE)}',
            f'2. **Turnover effect**：周轉 動能 {t[REFERENCE]["turnover"]:.2f} / raw {t[RAW]["turnover"]:.2f} / '
            f'alpha {t[CANDIDATE]["turnover"]:.2f}；成本 {t[REFERENCE]["cost_pct"]:.2%} / {t[RAW]["cost_pct"]:.2%} / '
            f'{t[CANDIDATE]["cost_pct"]:.2%}（沒有做無成本反事實，不能推論「沒成本就會贏」）',
            f'3. **Next action**：{NEXT_ACTION[decision]}']


def summary_md(result: dict) -> str:
    lines = ['# LightGBM relative-alpha baseline', '',
             f'- **資料**：dev {result["episodes"]}，四個策略同一組窗口逐窗口配對；只改 target，模型、特徵、組合、執行都不變',
             f'- **Parity**：{result["parity"]}', '']
    full = result['stages'].get('full')
    if not full or not result['decision']:
        return '\n'.join(lines + ['Full dev 還沒跑完。', ''])
    lines += ['## Result', '', f'**{result["decision"]}**', '', '## Main table（70 個 dev 窗口）', '',
              *main_table(full), '', '## 配對比較', '',
              pair_line('Alpha − Momentum', full['pairs'][f'{CANDIDATE}_vs_{REFERENCE}']),
              pair_line('Alpha − Raw LGBM', full['pairs'][f'{CANDIDATE}_vs_{RAW}']),
              pair_line('Alpha − AutoTS', full['pairs'][f'{CANDIDATE}_vs_autots']),
              pair_line('Raw LGBM − Momentum', full['pairs'][f'{RAW}_vs_{REFERENCE}']), '',
              '## Diagnosis', '', *diagnosis(full, result['decision']), '']
    for stage, title in (('validation', 'Validation 2022–2024'), ('holdout', 'Holdout 2025–2026/09（report only）')):
        if stage in result['stages']:
            body = result['stages'][stage]
            lines += [f'## {title}', '', *main_table(body), '',
                      pair_line('Alpha − Momentum', body['pairs'][f'{CANDIDATE}_vs_{REFERENCE}']), '']
    lines += [f'- Validation：{result["validation"]}；Holdout：{result["holdout"]}', '']
    return '\n'.join(lines)


def failures_md(result: dict) -> str:
    lines = ['# Failures', '']
    for stage, problems in result['problems'].items():
        lines += [f'## {stage}', ''] + ([f'- {p}' for p in problems] or ['- 無']) + ['']
    return '\n'.join(lines)


def write_csv(path: Path, rows: list[dict]):
    if not rows:
        return
    fields = list(dict.fromkeys(k for r in rows for k in r))   # rows may carry extra (diagnostic) columns
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({k: round(v, 6) if isinstance(v, float) else v for k, v in r.items()} for r in rows)


def write_outputs(result: dict):
    RESULTS.mkdir(parents=True, exist_ok=True)
    stages = result['stages']
    write_csv(RESULTS / 'model_diagnostics.csv', [r for body in stages.values() for r in body['refits']])
    write_csv(RESULTS / 'comparison.csv', [dict(stage=s, strategy=n, **m, **stages[s]['turnover'].get(n, {}))
                                           for s in stages for n, m in stages[s]['table'].items()])
    compact = {s: {k: v for k, v in body.items() if k != 'refits'} for s, body in stages.items()}
    (RESULTS / 'summary.json').write_text(json.dumps(dict(result, stages=compact), indent=1, default=float))
    (RESULTS / 'summary.md').write_text(summary_md(result))
    (RESULTS / 'failures.md').write_text(failures_md(result))


def run_and_check(result: dict, stage: str, workers: int) -> bool:
    manifests = run_stage(stage, STRATEGIES, workers)
    result['stages'][stage] = evaluate(stage, list(STRATEGIES))
    result['problems'][stage] = stage_problems(manifests, result['stages'][stage]['table'])
    write_outputs(result)
    return not result['problems'][stage]


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    episodes = json.loads(STRATEGIES[CANDIDATE].read_text())['episodes']
    result = dict(episodes=f'{episodes["start"]} 起，{" + ".join(episodes["offsets"])}', stages={}, problems={},
                  parity=None, decision=None, validation='未執行（dev gate 未通過）', holdout='未執行')

    run(RAW, 'smoke', args.workers)
    problems = parity_problems(run_dir(RAW, 'smoke'), JPX2_SMOKE)
    result['parity'] = 'PASS（6 窗口的報酬、交易、持股、委託、模型 log 與 JPX2 完全相同）' if not problems \
        else 'FAIL: ' + '; '.join(problems)
    result['problems']['parity'] = problems
    write_outputs(result)
    if problems:
        raise SystemExit('Parity failed: ' + '; '.join(problems))

    if not run_and_check(result, 'smoke', args.workers):
        raise SystemExit('Smoke failed: ' + '; '.join(result['problems']['smoke']))
    run_and_check(result, 'full', args.workers)
    result['decision'] = decide(result['stages']['full'])
    write_outputs(result)
    if result['decision'] == 'PASS_ALPHA_DEV':
        run_and_check(result, 'validation', args.workers)
        passed = beats_momentum(result['stages']['validation']) and not result['problems']['validation']
        result['validation'] = 'PASS（設定凍結）' if passed else 'FAIL（不跑 holdout）'
        if passed:
            run_and_check(result, 'holdout', args.workers)
            result['holdout'] = '已跑一次，只報告'
        write_outputs(result)
    print(f'Decision: {result["decision"]} -> {RESULTS / "summary.md"}', flush=True)
    return result


if __name__ == '__main__':
    main()
