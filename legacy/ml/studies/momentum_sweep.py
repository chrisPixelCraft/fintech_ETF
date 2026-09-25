"""P1 momentum study (docs/production_spec.md section 9): DEV sweep -> freeze -> validation once.

    PYTHONHASHSEED=0 .venv/bin/python -m research.momentum_sweep --workers 10

DEV is every 2019-2021 dev episode (month start and mid-month); signals read
their own lookback before each window, so early-2019 windows see late-2018
prices (momentum has no training). The portfolio is frozen at the production
setting (top 25 equal weight, invested 95%, keep 35, 10% turnover gate,
freeze last 3). Every candidate is paired with momentum_20 per episode.

The DEV pick rule and the validation gate are fixed below, before any
result. At most one candidate goes to validation (2022-2024), which runs
once: a stored verdict is never recomputed with a different candidate.
Runs are resumable (research/runs/momentum_sweep/, gitignored).
Results: research/results/momentum_sweep/.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import subprocess
from pathlib import Path

from competition.rules import ROOT
from research import compare, run_experiment
from research.lgbm_alpha import write_csv
from research.lgbm_jpx2 import episode_rows, metrics, pct

RUNS = ROOT / 'research/runs/momentum_sweep'
RESULTS = ROOT / 'research/results/momentum_sweep'
FREEZE = RESULTS / 'freeze.json'
VERDICT = RESULTS / 'validation.json'
PORTFOLIO = dict(n_holdings=25, keep_rank=35, weighting='equal', invested=.95, cap_scale=.9,
                 rebalance_threshold=.10, freeze_last_days=3)
EPISODES = {'start': '2019-01-01', 'offsets': ['month_start', 'mid_month']}
REFERENCE = 'mom20'
CANDIDATES = {   # name -> MomentumConfig overrides (docs/production_spec.md section 9)
    'mom20': {},
    'mom15': dict(window=15), 'mom25': dict(window=25), 'mom30': dict(window=30),
    'mom20_skip1': dict(skip=1), 'mom20_skip3': dict(skip=3), 'mom20_skip5': dict(skip=5),
    'mom10+20': dict(extra_windows=[10]), 'mom20+60': dict(extra_windows=[60]),
    'mom10+20+60': dict(extra_windows=[10, 60]),
    'mom20_risk_adjusted': dict(risk_adjusted=True),
    'mom20+near_high': dict(quality='near_high'), 'mom20+up_ratio': dict(quality='up_ratio'),
    'mom20+volume_trend': dict(quality='volume_trend'),
}
DEV_PICK = dict(min_median_delta=0., max_turnover_ratio=1.5, min_mean_delta=0.)
GATE = dict(min_mean_delta=.005, min_median_delta=-.005, min_win_rate=.45, max_p10_drop=.01, max_worst_drop=.02,
            max_turnover_ratio=1.5)


def config(name: str) -> dict:
    base = json.loads((ROOT / 'research/configs/baselines/momentum_20d.json').read_text())
    params = dict({'window': 20, **CANDIDATES[name]}, portfolio=PORTFOLIO)
    return dict(base, name=f'sweep_{name}', description=f'P1 momentum sweep: {name}', params=params, episodes=EPISODES)


def run_dir(name: str, split: str) -> Path:
    return RUNS / f'{name}__{split}'


def run(name: str, split: str, workers: int) -> dict:
    path = RUNS / 'configs' / f'{name}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config(name), indent=1))
    with contextlib.redirect_stdout(io.StringIO()):
        manifest = run_experiment.main(['--config', str(path), '--split', split, '--episodes', 'all',
                                        '--workers', str(workers), '--out', str(run_dir(name, split))])
    print(f'{split} {name}: {manifest["status"]} {manifest["n_complete"]}/{len(manifest["episode_ids"])}', flush=True)
    return manifest


def table(names, split: str) -> dict:
    rows = {n: episode_rows(run_dir(n, split)) for n in names}
    reference = {k: s for k, s in rows[REFERENCE].items() if s.get('status') == 'COMPLETE'}
    out = {}
    for n in names:
        m = metrics(rows[n], reference)
        if n != REFERENCE:
            m['ci95'] = compare.compare(run_dir(n, split), run_dir(REFERENCE, split))['ci95']
        out[n] = m
    return out


def dev_pick(dev: dict) -> str | None:
    """Highest mean delta among candidates with median delta >= 0, turnover <= 1.5x Mom20, no extra
    disqualification and mean delta > 0; None keeps Mom20."""
    ref = dev[REFERENCE]
    ok = [n for n, m in dev.items() if n != REFERENCE and m['paired_median_delta'] >= DEV_PICK['min_median_delta']
          and m['turnover'] <= DEV_PICK['max_turnover_ratio'] * ref['turnover']
          and m['disqualification_rate'] <= ref['disqualification_rate']
          and m['paired_mean_delta'] > DEV_PICK['min_mean_delta']]
    return max(ok, key=lambda n: dev[n]['paired_mean_delta']) if ok else None


def gate(m: dict, ref: dict) -> dict:
    """Validation checks (docs/production_spec.md section 9); PASS needs all of them."""
    checks = {
        'mean_delta > +0.5%': m['paired_mean_delta'] > GATE['min_mean_delta'],
        'median_delta >= -0.5%': m['paired_median_delta'] >= GATE['min_median_delta'],
        'win_rate >= 45%': m['paired_win_rate'] >= GATE['min_win_rate'],
        'P10 drop <= 1%': m['p10'] >= ref['p10'] - GATE['max_p10_drop'],
        'worst drop <= 2%': m['worst'] >= ref['worst'] - GATE['max_worst_drop'],
        'extra cost < mean_delta': m['cost_pct'] - ref['cost_pct'] < m['paired_mean_delta'],
        'turnover <= 1.5x': m['turnover'] <= GATE['max_turnover_ratio'] * ref['turnover'],
        'no extra disqualification': m['disqualification_rate'] <= ref['disqualification_rate'],
    }
    return dict(checks=checks, verdict='PASS' if all(checks.values()) else 'FAIL')


def git_state() -> dict:
    run_git = lambda *a: subprocess.run(['git', *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return dict(commit=run_git('rev-parse', 'HEAD'), dirty=bool(run_git('status', '--porcelain')))


def summary_md(dev: dict, pick: str | None, validation: dict | None) -> str:
    lines = ['# P1 Momentum 小實驗', '',
             '- DEV：2019–2021 月初＋月中窗口；組合固定為 production 設定（投入 95%）',
             f'- 比較對象：{REFERENCE}（逐窗口配對）', '', '## DEV', '',
             '| 候選 | 平均 | 中位數 | 平均 Δ | 中位數 Δ | 95% CI | 勝率 | P10 | 最差 | 周轉 | 成本 | 警告 |',
             '|---|' + '---:|' * 11]
    for n, m in sorted(dev.items(), key=lambda kv: -kv[1]['paired_mean_delta'] if kv[0] != REFERENCE else -1e9):
        ref = n == REFERENCE
        ci = '—' if ref else f'{pct(m["ci95"][0])} ~ {pct(m["ci95"][1])}'
        lines.append(f'| {n} | {pct(m["mean"])} | {pct(m["median"])} | {"—" if ref else pct(m["paired_mean_delta"])} | '
                     f'{"—" if ref else pct(m["paired_median_delta"])} | {ci} | '
                     f'{"—" if ref else format(m["paired_win_rate"], ".0%")} | {pct(m["p10"])} | {pct(m["worst"])} | '
                     f'{m["turnover"]:.2f} | {m["cost_pct"]:.2%} | {m["warnings"]} |')
    lines += ['', '選擇規則（事先固定）：中位數 Δ ≥ 0、周轉 ≤ 1.5 倍、失格不增加、平均 Δ > 0，取平均 Δ 最高的一個。', '',
              f'**DEV 選出**：{pick or "無（Mom20 維持 production）"}', '']
    if validation:
        lines += ['## Validation 2022–2024（只跑一次）', '',
                  f'**{validation["verdict"]}**：{validation["candidate"]} vs {REFERENCE}', '']
        m = validation['table'][validation['candidate']]
        lines += [f'- 平均 Δ {pct(m["paired_mean_delta"])}，中位數 Δ {pct(m["paired_median_delta"])}，'
                  f'勝率 {m["paired_win_rate"]:.0%}，95% CI {pct(m["ci95"][0])} ~ {pct(m["ci95"][1])}', '']
        lines += [f'- {"✓" if ok else "✗"} {name}' for name, ok in validation['checks'].items()]
        lines += ['', '通過 → candidate 取代 Mom20；未通過 → Mom20 維持，停止策略搜尋。', '']
    return '\n'.join(lines)


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    RESULTS.mkdir(parents=True, exist_ok=True)
    for name in CANDIDATES:
        run(name, 'dev', args.workers)
    dev = table(list(CANDIDATES), 'dev')
    pick = dev_pick(dev)
    write_csv(RESULTS / 'dev.csv', [dict(candidate=n, **{k: v for k, v in m.items() if k != 'ci95'},
                                         ci95_low=m.get('ci95', [None])[0], ci95_high=m.get('ci95', [None, None])[1])
                                    for n, m in dev.items()])
    validation = None
    if pick is not None:
        freeze = dict(candidate=pick, config=config(pick), reference=config(REFERENCE), dev_pick_rule=DEV_PICK,
                      gate=GATE, git=git_state())
        if FREEZE.exists() and json.loads(FREEZE.read_text())['candidate'] != pick:
            raise SystemExit(f'{FREEZE} froze a different candidate; validation runs once')
        if not FREEZE.exists():
            FREEZE.write_text(json.dumps(freeze, indent=1))
        for name in (REFERENCE, pick):
            run(name, 'validation', args.workers)
        body = table([REFERENCE, pick], 'validation')
        validation = dict(candidate=pick, table=body, **gate(body[pick], body[REFERENCE]))
        if VERDICT.exists() and json.loads(VERDICT.read_text())['verdict'] != validation['verdict']:
            raise SystemExit(f'{VERDICT} already holds a different verdict')
        VERDICT.write_text(json.dumps(validation, indent=1, default=float))
    (RESULTS / 'summary.md').write_text(summary_md(dev, pick, validation))
    print(f'DEV pick: {pick}; validation: {validation["verdict"] if validation else "not run"}', flush=True)
    return dict(dev=dev, pick=pick, validation=validation)


if __name__ == '__main__':
    main()
