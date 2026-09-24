#!/usr/bin/env python3
"""Generate the Stage 1 execution sensitivity report from audited artifacts."""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def percentage(value):
    return 'N/A' if pd.isna(value) else f'{value * 100:.3f}%'


def render(output, destination):
    from scripts.v4_verify import verify_directory
    verification = verify_directory(output)
    rows = pd.read_csv(output / 'results.csv')
    episodes = pd.read_csv(output / 'episodes.csv')
    study = json.loads((output / 'study.json').read_text())
    modes = study['execution_modes']
    lines = ['# V4 Stage 1 — Execution comparison', '',
        '**Scope: frozen V3 signals and parameters; no V4 strategy implementation, tuning, or winner selection.**', '',
        'The paired comparison changes fills from Yahoo Open to exchange trading value divided by share volume. '
        'The two primary tracks retain the same V3 signal history, prior-close sizing/valuation basis, fees, corporate-action policy, '
        'and simulated settlement rules. This isolates the fill assumption; it does not certify a live D-Plan. The third track, `official_average_official_close`, also uses exchange closes for D-Plan sizing and marking after constructing the frozen indicators. It tests the official price pipeline but bundles two price-basis changes.', '',
        '## Evidence and coverage', '',
        f'The predeclared study attempts {len(episodes)} windows per track, each starting with NT$1B and no holdings. '
        'The dates were fixed before inspecting returns. This is a bounded diagnostic sample, not exhaustive 2010–2026 validation. '
        'Recent and seasonal windows are diagnostics only. The two latest windows overlap and are not independent samples.', '',
        f"Raw-source verification authenticated {verification['execution_provenance']['rows']:,} expected observations, "
        f"of which {verification['execution_provenance']['available_rows']:,} have usable official value/volume. "
        'Missing historical roster observations and invalid value/volume remain explicit; request success alone does not imply full coverage.', '',
        '| Episode | Split | First session | Last session |', '|---|---|---|---|']
    for r in episodes.itertuples():
        lines.append(f'| {r.episode_id} | {r.split} | {r.start} | {r.end} |')
    lines += ['', '## Results, including failures', '',
        'A complete return is reported even when measured compliance fails. Incomplete episodes have no terminal 24-day return; '
        'their partial returns remain forensic artifacts and are excluded from return summaries. Every attempt remains in the denominator.', '',
        '| Execution | Complete / attempted | Measured PASS / attempted | Official fills available / attempted |',
        '|---|---:|---:|---:|']
    for mode in modes:
        g = rows.loc[rows.execution_mode.eq(mode)]
        lines.append(f'| {mode} | {int(g.complete_period.sum())} / {len(g)} | {int(g.measured_pass.sum())} / {len(g)} | {int(g.canonical_execution_available.sum())} / {len(g)} |')
    lines += ['', '| Episode | Open return | Average return | Return difference (pp) | Open MDD | Average MDD | MDD difference (pp) |',
              '|---|---:|---:|---:|---:|---:|---:|']
    paired = []
    for episode in episodes.episode_id:
        g = rows.loc[rows.episode_id.eq(episode)].set_index('execution_mode')
        op, av = g.loc['open_proxy'], g.loc['official_average']
        delta = av.episode_return - op.episode_return if bool(av.canonical_execution_available) else np.nan
        paired.append(dict(episode_id=episode, return_difference=delta,
                           mdd_difference=av.episode_max_drawdown-op.episode_max_drawdown if bool(av.canonical_execution_available) and bool(op.complete_period) else np.nan,
                           open_turnover=op.episode_turnover, official_turnover=av.episode_turnover))
        lines.append(f'| {episode} | {percentage(op.episode_return)} | {percentage(av.episode_return)} | '
                     f'{delta * 100:.3f}' .replace('nan', 'N/A') +
                     f' | {percentage(op.episode_max_drawdown)} | {percentage(av.episode_max_drawdown)} | '
                     + (f"{paired[-1]['mdd_difference'] * 100:.3f}" if pd.notna(paired[-1]['mdd_difference']) else 'N/A') + ' |')
    lines += ['', 'MDD for incomplete episodes is a partial-path diagnostic, not a full 24-day MDD. Paired differences require complete official fills; completed but missing-price paths remain descriptive failures, not canonical comparisons.', '',
              '| Execution | Mean | Median | P25 | P10 | Worst | Positive / complete |',
              '|---|---:|---:|---:|---:|---:|---:|']
    for mode in modes:
        g = rows.loc[rows.execution_mode.eq(mode)]
        values = g.loc[g.complete_period.astype(bool), 'episode_return'].dropna()
        lines.append('| ' + mode + ' | ' + ' | '.join(percentage(x) for x in [values.mean(),values.median(),values.quantile(.25),values.quantile(.1),values.min()]) + f' | {int(values.gt(0).sum())} / {len(values)} |')
    lines += ['', 'The full official-price diagnostic also uses official prior closes and daily marks:', '',
              '| Episode | Official-price return | Canonical data status | Measured compliance |',
              '|---|---:|---|---|']
    for r in rows.loc[rows.execution_mode.eq('official_average_official_close')].itertuples():
        lines.append(f'| {r.episode_id} | {percentage(r.episode_return)} | {r.canonical_status} | {r.compliance_status} |')
    lines += ['', '| Execution | Cash violation days | Weight violation days | Holding-count days | Odd-lot days | Missing fill days | Unfilled days | No-valid-plan days |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for mode in modes:
        g = rows.loc[rows.execution_mode.eq(mode)]
        fields = ['cash_violation_days','weight_violation_days','holding_count_violation_days','odd_lot_issue_days','missing_execution_price_days','unfilled_days','no_valid_plan_days']
        lines.append('| '+mode+' | '+' | '.join(str(int(g[f].sum())) for f in fields)+' |')
    lines += ['', '| Execution | Mean turnover (initial NAV units) | Total transaction cost (NT$) | Mean day-1 invested | Mean day-3 invested | Mean day-5 invested |',
        '|---|---:|---:|---:|---:|---:|']
    for mode in modes:
        g = rows.loc[rows.execution_mode.eq(mode)]
        lines.append(f'| {mode} | {g.episode_turnover.mean():.4f} | {g.transaction_cost.sum():,.2f} | '+
            ' | '.join(percentage(g[f'day_{d}_invested_ratio'].mean()) + f" (n={g[f'day_{d}_invested_ratio'].count()})" for d in [1,3,5])+' |')
    lines += ['', 'Totals above describe observed days across all attempts, including incomplete runs; overlapping dates can be counted twice.', '', '## Paired sensitivity and ranking', '']
    pairs = pd.DataFrame(paired)
    complete = pairs.return_difference.dropna()
    if len(complete):
        lines += [f'{len(complete)} paired windows have complete returns and official fills. Mean official-minus-Open return is '
                  f'**{complete.mean()*100:.3f} percentage points**, with a range of '
                  f'**{complete.min()*100:.3f} to {complete.max()*100:.3f} points**. '
                  'This sample measures sensitivity; it cannot establish how much of every earlier V3 conclusion came from Open execution.', '']
    else:
        lines += ['No paired window has two complete returns with official fills available. The effect on terminal return is **not estimable** from these attempts.', '']
    lines += ['Strategy ranking changes are **not applicable**: only one frozen V3 configuration was tested. '
              'The following ranking is an episode-return diagnostic on the same complete pairs, not a strategy selection.', '',
              '| Episode | Open return rank | Official return rank |', '|---|---:|---:|']
    matched = rows[rows.episode_id.isin(pairs.loc[pairs.return_difference.notna(),'episode_id'])]
    rank = matched.pivot(index='episode_id',columns='execution_mode',values='episode_return').rank(ascending=False,method='min')
    for episode, r in rank.iterrows():
        lines.append(f'| {episode} | {int(r.open_proxy)} | {int(r.official_average)} |')
    trades = pd.read_csv(output / 'trade_price_comparison.csv')
    # Compare quote pairs once, not weighted twice by both independently evolving books.
    quotes = trades.drop_duplicates(['episode_id','date','symbol']).dropna(subset=['relative_difference'])
    manifest = json.loads((output / 'manifest.json').read_text())
    official = pd.read_csv(manifest['inputs']['execution_data'])
    quote_diagnostics = quotes.merge(official[['date', 'symbol', 'open']], on=['date', 'symbol'], how='left').rename(columns={'open': 'official_open'})
    quote_diagnostics['average_vs_official_open'] = quote_diagnostics.official_average_price / quote_diagnostics.official_open - 1
    quote_diagnostics['official_open_vs_vendor_open'] = quote_diagnostics.official_open / quote_diagnostics.open_price - 1
    lines += ['', f'Per-trade quote comparisons are retained in `{output}/trade_price_comparison.csv`, including signed quantity, '
              'Open, official average, absolute difference, and relative difference. '
              f'{len(quotes)} distinct episode/date/symbol quote pairs are available; mean official/Open difference is '
              f'{percentage(quotes.relative_difference.mean())}. This is an unweighted quote statistic, not portfolio attribution.', '',
              f'The mean official-average versus **official Open** quote difference is {percentage(quote_diagnostics.average_vs_official_open.mean())}; '
              f'the mean official-Open versus **Yahoo Open** difference is {percentage(quote_diagnostics.official_open_vs_vendor_open.mean())}. '
              'Consequently, the requested Open-proxy comparison includes vendor/exchange price-basis differences as well as intraday execution timing. '
              'It must not be interpreted as pure intraday timing alpha. The official-close track separately measures the full official price basis.', '',
              '## Verification and interpretation', '',
              'The Open track is compared table-by-table with the original V3 callable for equity, trades, orders, holdings, '
              'compliance, and snapshots. Independent accounting reconstruction checks prices, fees, taxes, fixed prior-close '
              'orders, corporate actions, cash, holdings, NAV, and period metrics. Source/input hashes and deterministic '
              'output hashes bind the run; the verifier rejects altered artifacts.', '',
              f'Evidence: `{output}/open_reproduction.json`, `audits.json`, `manifest.json`, '
              '`results.csv`, and the per-episode `ledgers/` tables. The execution cache retains source URLs, raw payloads, '
              'quality flags, units, and provenance. Missing official value/volume never falls back to Open or Close.', '',
              'The fixed 2026 competition universe is a retrospective stress universe, not historically known constituents. '
              'The frozen V3 parameters and historical holdout were already examined in earlier work; this is not a newly unseen test set. '
              'Yahoo corporate-action history and the observed-session calendar remain research inputs. '
              'Whole-day rollback and period-end dividend credit reproduce V3 research settlement assumptions. '
              'Neither Active Share, organizer settlement parity, corporate-action contracts, nor market impact at NT$1B '
              'is certified. All tracks retain **BLOCK_SUBMISSION**.', '',
              'Source rules: `official_docs/D-Plan_撰寫指南.md`, section ⑥; '
              '`docs/v4_master_spec.md`, sections 3–5; `docs/v4_execution_validation_spec.md`, sections 2–4, 9–10. '
              'Observed execution differences are evidence about this fixed baseline and these windows only. '
              'There is no V4 winner and no strategy parameter was tuned.', '',
              '## Reproduce', '', '```bash',
              'python scripts/v4_build_execution_data.py --dates outputs/v4/execution_requested_dates.csv',
              'python scripts/v4_run_baseline.py --output outputs/v4/stage1_replay',
              'python scripts/v4_verify.py --output outputs/v4/stage1_replay',
              'python scripts/v4_report.py --output outputs/v4/stage1_replay',
              "python -m unittest discover -s tests -p 'test_v4_*.py' -q", '```', '']
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text('\n'.join(lines))
    pairs.to_csv(destination.with_suffix('.csv'), index=False)
    quote_diagnostics.to_csv(destination.with_name('v4_trade_price_diagnostics.csv'), index=False)
    verification['manifest_sha256'] = hashlib.sha256((output / 'manifest.json').read_bytes()).hexdigest()
    verification['report_sha256'] = hashlib.sha256(destination.read_bytes()).hexdigest()
    (output / 'verification.json').write_text(json.dumps(verification, indent=2, allow_nan=False) + '\n')
    return verification


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('outputs/v4/stage1'))
    parser.add_argument('--report', type=Path, default=Path('reports/v4_execution_comparison.md'))
    args = parser.parse_args()
    render(args.output,args.report)


if __name__ == '__main__':
    main()
