"""Final test on 2025-01..2026-09: 21 settings of five methods, walk-forward, trained on 2019+ data.

    PYTHONHASHSEED=0 .venv/bin/python -m research.final_test --workers 10

Every run sees market data from 2019-01-01 only and trades every holdout
episode (month start and mid-month). Models retrain walk-forward: at each
decision (episode start, then every 5 sessions) on all matured 2019+ data
through D-1. Methods and settings:
- momentum_20d family: lookback 10 / 20 / 60 sessions.
- AutoTS (baseline_autots): forecast horizon 5 / 10 / 20 sessions.
- LightGBM raw-return, learning rate 0.05 / 0.01 / 0.005 / 0.001 / 0.0005, three ways:
  control (train on the earlier 80%, early-stop on the latest 20%),
  C (same tree count, refit on 100%), D (no split, 100% of rows, fixed trees =
  the per-rate median chosen by early stopping on 2020-2024, research.tree_calibration).
All share one portfolio layer (top 25 equal weight, invested 95%) and the same
planner, execution and costs. Runs are resumable (research/runs/final_test/,
gitignored); each holdout access is logged by research.run_experiment.
Results: research/results/final_test/.
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
from research.lgbm_alpha import log_entries, pair_line, stage_problems, write_csv
from research.lgbm_jpx2 import episode_rows, metrics, pct

RUNS = ROOT / 'research/runs/final_test'
RESULTS = ROOT / 'research/results/final_test'
CONFIGS = ROOT / 'research/configs'
DATA = {'start': '2019-01-01'}
EPISODES = {'offsets': ['month_start', 'mid_month']}
INVESTED = .95
ALL_HISTORY = 100_000                       # lookback in sessions: every matured date since the data floor
RATES = (.05, .01, .005, .001, .0005)
TREES = ROOT / 'research/results/tree_calibration/trees.json'
REFERENCE = 'momentum_20'


def with_portfolio(config: dict) -> dict:
    params = dict(config['params'], portfolio=dict(config['params']['portfolio'], invested=INVESTED))
    return dict(config, params=params, data=DATA, episodes=EPISODES)


def momentum(window: int) -> dict:
    base = json.loads((CONFIGS / 'baselines/momentum_20d.json').read_text())
    return with_portfolio(dict(base, name=f'momentum_{window}', params=dict(base['params'], window=window)))


def autots(horizon: int) -> dict:
    base = json.loads((CONFIGS / 'baseline_autots.json').read_text())
    params = dict(base['params'], forecaster=dict(base['params']['forecaster'], horizon=horizon))
    return with_portfolio(dict(base, name=f'autots_h{horizon}', params=params))


def calibrated_trees() -> dict[str, int]:
    """D's fixed tree count per learning rate, from research.tree_calibration (pre-2025 data only)."""
    if not TREES.exists():
        raise SystemExit(f'{TREES} is missing; run research.tree_calibration first')
    return json.loads(TREES.read_text())['trees']


def lgbm(way: str, rate: float) -> dict:
    base = json.loads((CONFIGS / 'baseline_lgbm_raw.json').read_text())
    if way not in ('control', 'C', 'D'):
        raise ValueError(f'Unknown LightGBM way {way}')
    extra = dict(refit_on_all=True) if way == 'C' else \
        dict(fixed_trees=calibrated_trees()[f'{rate:g}']) if way == 'D' else {}
    params = dict(base['params'], lookback=ALL_HISTORY, learning_rate=rate, **extra)
    return with_portfolio(dict(base, name=f'lgbm_{way}_lr{rate:g}', description=f'final test: LightGBM {way}',
                               params=params))


def settings() -> dict[str, dict]:
    """Every configuration, fastest first (the run order)."""
    runs = [momentum(w) for w in (10, 20, 60)] + [autots(h) for h in (5, 10, 20)]
    runs += [lgbm(way, rate) for rate in RATES for way in ('control', 'C', 'D')]
    return {c['name']: c for c in runs}


def method(name: str) -> str:
    return name.split('_lr')[0] if name.startswith('lgbm') else name.rsplit('_', 1)[0]


def run_dir(name: str) -> Path:
    return RUNS / name


def run(config: dict, workers: int) -> dict:
    path = RUNS / 'configs' / f'{config["name"]}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=1))
    print(f'== {config["name"]}', flush=True)
    with contextlib.redirect_stdout(io.StringIO()):
        manifest = run_experiment.main(['--config', str(path), '--split', 'holdout', '--episodes', 'all',
                                        '--workers', str(workers), '--out', str(run_dir(config['name'])),
                                        '--i-understand-holdout'])
    print(f'   {manifest["status"]}: {manifest["n_complete"]}/{len(manifest["episode_ids"])} episodes, '
          f'{manifest["runtime_seconds"]:.0f}s', flush=True)
    return manifest


def tree_summary(name: str) -> dict:
    """Tree counts of a LightGBM run's refits (other methods have no trees)."""
    if not name.startswith('lgbm'):
        return {}
    refits = pd.DataFrame([e for _, e in log_entries(run_dir(name)) if e.get('refit')])
    if refits.empty:
        return {}
    return dict(trees_median=float(refits.best_iteration.median()), trees_p10=float(refits.best_iteration.quantile(.1)),
                train_rows_median=float(refits.get('refit_rows', refits.n_train_rows).median()))


def evaluate(names: list[str]) -> dict:
    rows = {name: episode_rows(run_dir(name)) for name in names}
    reference = {k: s for k, s in rows[REFERENCE].items() if s.get('status') == 'COMPLETE'}
    table = {}
    for name in names:
        row = dict(method=method(name), **metrics(rows[name], reference), **tree_summary(name))
        if name != REFERENCE:
            ci = compare.compare(run_dir(name), run_dir(REFERENCE))['ci95']
            row.update(ci95_low=ci[0], ci95_high=ci[1])
        table[name] = row
    order = sorted(reference, key=lambda k: reference[k]['start'])
    episodes = {k: dict(start=reference[k]['start'], end=reference[k]['end'],
                        benchmark_0050=reference[k]['benchmark_0050_return'],
                        equal_weight_150=reference[k]['equal_weight_universe_return'],
                        **{n: rows[n].get(k, {}).get('terminal_return') for n in names}) for k in order}
    return dict(table=table, episodes=episodes)


def tree_table() -> list[str]:
    """Stage 1 (research.tree_calibration): D's fixed tree count per learning rate and the spread behind it."""
    calibration = json.loads(TREES.read_text())
    lines = [f'## 第一階段：D 的樹數（{calibration["first"]} ～ {calibration["last"]} 共 {calibration["decisions"]} 個決策日，'
             f'提早停止選到的樹數；標籤最晚結束於 {calibration["latest_label_end"]}）', '',
             '| 學習率 | D 的樹數（中位數） | 第 10 百分位 | 第 90 百分位 |', '|---:|---:|---:|---:|']
    for rate, trees in calibration['trees'].items():
        spread = calibration['spread'][rate]
        lines.append(f'| {rate} | {trees} | {spread["10%"]:.0f} | {spread["90%"]:.0f} |')
    return lines


def episode_table(body: dict) -> list[str]:
    """One row per test window: 0050, the equal-weight universe and every setting's 24-session return."""
    names = list(body['table'])
    lines = ['| 窗口 | 期間 | 0050 | 150 檔等權 | ' + ' | '.join(names) + ' |', '|---|---|' + '---:|' * (len(names) + 2)]
    for k, e in body['episodes'].items():
        lines.append(f'| {k} | {e["start"]} ~ {e["end"]} | {pct(e["benchmark_0050"])} | {pct(e["equal_weight_150"])} | '
                     + ' | '.join(pct(e[n]) for n in names) + ' |')
    return lines


def summary_md(result: dict) -> str:
    body = result['evaluation']
    n = len(body['episodes'])
    lines = ['# 最終測試：2025-01 ～ 2026-09', '',
             f'- **資料**：2019-01 起；每個決策用 2019 到前一天的全部資料滾動重訓；{n} 個 24 日窗口（月初＋月中）',
             f'- **共用**：前 25 檔等權重，目標投入 {INVESTED:.0%}；同一套規劃、成交、成本',
             f'- **配對比較對象**：{REFERENCE}（20 日動能）', '',
             '| 設定 | 平均 | 中位數 | Δ vs 動能20 | 95% CI | 勝率 | P10 | 最差 | MDD | 周轉 | 成本 | 警告 | 失格 | 樹數中位數 |',
             '|---|' + '---:|' * 13]
    for name, m in body['table'].items():
        ref = name == REFERENCE
        ci = '—' if ref else f'{pct(m["ci95_low"])} ~ {pct(m["ci95_high"])}'
        trees = m.get('trees_median')
        lines.append(f'| {name} | {pct(m["mean"])} | {pct(m["median"])} | '
                     f'{"—" if ref else pct(m["paired_mean_delta"])} | {ci} | '
                     f'{"—" if ref else format(m["paired_win_rate"], ".0%")} | {pct(m["p10"])} | {pct(m["worst"])} | '
                     f'{m["mdd"]:.2%} | {m["turnover"]:.2f} | {m["cost_pct"]:.2%} | {m["warnings"]} | '
                     f'{m["disqualified"]} | {"—" if trees is None else f"{trees:g}"} |')
    lines += ['', f'- {len(body["table"])} 組都在同一段測試期比較；挑其中最好的一組會偏樂觀（總有運氣好的）',
              '- 月初和月中的窗口有重疊，實際獨立樣本比窗口數少',
              '- 已知資料問題：2025-08-01 有 28 檔（上櫃 26 檔）沒有任何價格資料，前後交易日都正常。'
              '從這天開始的窗口，第一天建倉若成交不到 20 檔（規定下限）→ 當天交易全部作廢並記 1 次警告，'
              '隔天正常建倉。表中「警告」欄的 1 次都是這件事；autots_h5、autots_h20 當天挑到的缺價股較少'
              '（成交 22、21 檔），所以沒有警告。影響只有這一個窗口的第一天', '',
              *tree_table(), '',
              '## 逐窗口報酬（24 個交易日，扣成本後）', '', *episode_table(body), '']
    return '\n'.join(lines)


def write_outputs(result: dict):
    RESULTS.mkdir(parents=True, exist_ok=True)
    write_csv(RESULTS / 'comparison.csv', [dict(setting=n, **m) for n, m in result['evaluation']['table'].items()])
    write_csv(RESULTS / 'episode_returns.csv', [dict(episode=k, **e) for k, e in result['evaluation']['episodes'].items()])
    (RESULTS / 'summary.json').write_text(json.dumps(result, indent=1, default=float))
    (RESULTS / 'summary.md').write_text(summary_md(result))
    (RESULTS / 'failures.md').write_text('\n'.join(['# Failures', ''] + ([f'- {p}' for p in result['problems']]
                                                                         or ['- 無']) + ['']))


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    configs = settings()
    manifests = {}
    for name, config in configs.items():
        manifests[name] = run(config, args.workers)
        if REFERENCE not in manifests:
            continue
        done = list(manifests)
        result = dict(settings=list(configs), finished=done, evaluation=evaluate(done),
                      problems=stage_problems(manifests, {}))
        write_outputs(result)      # refreshed after every run, so partial results are readable
    print(f'Done -> {RESULTS / "summary.md"}', flush=True)
    return result


if __name__ == '__main__':
    main()
