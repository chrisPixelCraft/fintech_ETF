"""The one daily LightGBM-v2 evaluation (docs/lgbm_v2_daily_spec.md section 4): v2A every day vs Mom20.

    PYTHONHASHSEED=0 .venv/bin/python -m hybrid.evaluate_daily --workers 10
    PYTHONHASHSEED=0 .venv/bin/python -m hybrid.evaluate_daily --pure-test --workers 10   # only after PASS

Same windows, data start and portfolio layer as hybrid.evaluate; the Mom20
reference runs are the identical ones (same config, so they resume without
recomputing). The verdict is written once. Results: research/results/lgbm_v2_daily/.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json

from hybrid import evaluate as base
from research import run_experiment
from research.lgbm_alpha import write_csv
from research.lgbm_jpx2 import metrics, pct
from competition.rules import ROOT

RESULTS = ROOT / 'research/results/lgbm_v2_daily'
VERDICT = RESULTS / 'verdict.json'
NAME = 'lgbm_v2A_daily'


def config(period: str) -> dict:
    cfg = base.config('hybrid_v2A', period)
    cfg['name'] = f'eval_{NAME}'
    cfg['params'] = dict(cfg['params'], always=True,
                         predictions=f'data/hybrid/predictions_v2A_{period}_daily.parquet')
    return cfg


def run(split: str, period: str, workers: int) -> dict:
    path = base.RUNS / 'configs' / f'{NAME}_{period}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config(period), indent=1))
    argv = ['--config', str(path), '--split', split, '--episodes', 'all', '--workers', str(workers),
            '--out', str(base.run_dir(NAME, split))] + (['--i-understand-holdout'] if split == 'holdout' else [])
    with contextlib.redirect_stdout(io.StringIO()):
        manifest = run_experiment.main(argv)
    print(f'{split} {NAME}: {manifest["status"]} {manifest["n_complete"]}/{len(manifest["episode_ids"])}', flush=True)
    return manifest


def summary_md(v: dict) -> str:
    ref, m, ci = v['reference'], v['metrics'], v['ci95']
    lines = [f'# LightGBM-v2 每日排名：{v["period"]}', '',
             f'- 窗口：{v["windows"]} 個 24 日窗口，與 Mom20 逐窗口配對；規則見 docs/lgbm_v2_daily_spec.md',
             '- 2015–2024 是這段資料第二次被用來比較（見 spec 第 3 節揭露）' if v['period'] == '2015-2024' else
             '- 2025–2026 純測試：只跑一次，只報告', *([f'- **注意**：{v["note"]}'] if v.get('note') else []), '',
             f'**{v["decision"]}**（本期間通過條件）', '',
             '| 策略 | 平均 | 中位數 | 配對平均 Δ | 配對中位數 Δ | 95% CI | 勝率 | P10 | 最差 | 周轉 | 成本 | 失格 |',
             '|---|' + '---:|' * 11,
             f'| Mom20 | {pct(ref["mean"])} | {pct(ref["median"])} | — | — | — | — | {pct(ref["p10"])} | '
             f'{pct(ref["worst"])} | {ref["turnover"]:.2f} | {ref["cost_pct"]:.2%} | {ref["disqualified"]} |',
             f'| LightGBM-v2 每日 | {pct(m["mean"])} | {pct(m["median"])} | {pct(m["paired_mean_delta"])} | '
             f'{pct(m["paired_median_delta"])} | {pct(ci[0])} ~ {pct(ci[1])} | {m["paired_win_rate"]:.0%} | '
             f'{pct(m["p10"])} | {pct(m["worst"])} | {m["turnover"]:.2f} | {m["cost_pct"]:.2%} | {m["disqualified"]} |',
             '', '## 通過條件', '']
    lines += [f'- {"✓" if ok else "✗"} {k}' for k, ok in v['acceptance']['checks'].items()]
    return '\n'.join(lines + [''])


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--pure-test', action='store_true')
    parser.add_argument('--after-stop', action='store_true',
                        help='run the pure test despite a STOP verdict (spec section 7); reported as such')
    args = parser.parse_args(argv)
    RESULTS.mkdir(parents=True, exist_ok=True)
    passed = VERDICT.exists() and json.loads(VERDICT.read_text())['decision'] == 'PASS'
    if args.pure_test and not passed and not args.after_stop:
        raise SystemExit('The pure test runs only after a PASS verdict (or with --after-stop, spec section 7)')
    period, splits = ('test', base.SPLITS['test']) if args.pure_test else ('eval', base.SPLITS['eval'])
    for split in splits:
        base.run(base.REFERENCE, split, period, args.workers)
        run(split, period, args.workers)
    reference = {k: s for k, s in base.rows(base.REFERENCE, splits).items() if s.get('status') == 'COMPLETE'}
    ref = metrics(base.rows(base.REFERENCE, splits), reference)
    mine = base.rows(NAME, splits)
    m = metrics(mine, reference)
    acceptance = base.accept(m, ref)
    verdict = dict(period='2025-2026/09' if args.pure_test else '2015-2024', windows=len(reference), reference=ref,
                   metrics=m, ci95=base.paired_ci({k: s for k, s in mine.items() if s.get('status') == 'COMPLETE'},
                                                  reference),
                   acceptance=acceptance, decision='PASS' if acceptance['passed'] else 'STOP',
                   note=('run after the 2015-2024 STOP at the user\'s request (spec section 7); does not change the STOP'
                         if args.pure_test and not passed else None))
    target = RESULTS / ('pure_test.json' if args.pure_test else 'verdict.json')
    if not args.pure_test and VERDICT.exists() and json.loads(VERDICT.read_text())['decision'] != verdict['decision']:
        raise SystemExit(f'{VERDICT} already holds a different verdict; the evaluation runs once')
    target.write_text(json.dumps(verdict, indent=1, default=float))
    (RESULTS / ('pure_test.md' if args.pure_test else 'summary.md')).write_text(summary_md(verdict))
    write_csv(RESULTS / f'comparison_{period}.csv', [dict(strategy='mom20', **ref), dict(strategy=NAME, **m)])
    print(verdict['decision'], f'mean delta {m["paired_mean_delta"]:+.4f}', flush=True)
    return verdict


if __name__ == '__main__':
    main()
