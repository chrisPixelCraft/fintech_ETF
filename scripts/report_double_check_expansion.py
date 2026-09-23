"""Write a concise, pre-release research report for the isolated expansion."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from src.double_check_tuning import ZERO_COUNTS, choose, eligible

OUTPUT = ROOT / 'outputs/v2_double_check_expansion'
PARENT = ROOT / 'outputs/v2_double_check_fintuned'
REPORT = ROOT / 'reports/v2_double_check_expansion_report.md'
TRACKS = ('official_ex_post', 'historical_pit')
FINAL = 'expansion_selected'


def read(path):
    return json.loads(Path(path).read_text())


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pct(value):
    return f'{float(value):.2%}'


def annual(equity_path):
    equity = pd.read_csv(equity_path, usecols=['date', 'economic_nav'])
    require(len(equity) == 417 and equity.date.iloc[0] == '2025-01-02'
            and equity.date.iloc[-1] == '2026-09-21',
            'Unexpected annual-comparison date range')
    ending_2025 = float(equity.loc[equity.date.str.startswith('2025'), 'economic_nav'].iloc[-1])
    ending_2026 = float(equity.economic_nav.iloc[-1])
    require(ending_2025 > 0 and ending_2026 > 0, 'Nonpositive economic NAV')
    return ending_2025 / 1e9 - 1, ending_2026 / ending_2025 - 1


def checked_final(root, track, selection):
    folder = root / track / 'final' / FINAL
    receipt = read(folder / 'receipt.json')
    require({'equity.csv', 'metrics.json', 'config.json', 'independent_audit.json'} <= set(receipt),
            'Selected final receipt incomplete')
    for name, digest in receipt.items():
        require(sha(folder / name) == digest, 'Selected final hash mismatch: ' + track + '/' + name)
    audit = read(folder / 'independent_audit.json')
    metrics = read(folder / 'metrics.json')
    config = read(folder / 'config.json')
    require(audit.get('independent_audit') == 'PASS', 'Selected final independent audit failed')
    require(metrics.get('official_compliance') == 'UNKNOWN_BLOCK_SUBMISSION',
            'Selected final claims unsupported official approval')
    require(config.get('full_tuning_params') == selection['params'],
            'Selected final config differs from selection')
    return folder, metrics


def make_report(root=OUTPUT, report=REPORT):
    root, report = Path(root), Path(report)
    require(not report.exists(), 'Preserve existing report: ' + str(report))
    require(not (root / 'failure.json').exists(), 'Expansion has failure marker')
    manifest = read(root / 'manifest.json')
    audit = read(root / 'study_audit.json')
    study = read(ROOT / 'config/v2_double_check_expansion.json')
    grid = read(root / 'grid.json')
    selection = read(root / 'selection.json')
    previous_audit = read(PARENT / 'audit.json')
    require(manifest.get('outputs_complete') is True and manifest.get('study') == study,
            'Expansion output incomplete or design changed')
    require(manifest.get('study_sha256') == sha(ROOT / 'config/v2_double_check_expansion.json'),
            'Expansion design hash changed')
    require(audit.get('status') == 'PASS' and audit.get('verified_trials') == 1146,
            'All new trials require per-trial independent accounting checks')
    require(audit.get('submission_status') == selection.get('submission_status') ==
            'BLOCK_SUBMISSION' and selection.get('official_compliance') ==
            'UNKNOWN_BLOCK_SUBMISSION', 'Formal submission must remain blocked')
    require(previous_audit.get('status') == 'PASS' and
            sha(PARENT / 'audit.json') == study['parent_audit_sha256'],
            'Audited d0034 baseline changed')
    require(grid.get('raw_grid_combinations') == len(grid.get('grid', [])) == 576
            and grid.get('reused_parent_count') == 3
            and grid.get('new_candidate_count') == 573,
            'Declared local grid is incomplete')
    require(manifest.get('completed_trials') == manifest.get('expected_trials') == 1146,
            'Expansion trial count incomplete')
    require(selection.get('selected_on') == 'official_ex_post'
            and selection.get('scope') == 'EX_POST_DEVELOPMENT_ONLY',
            'Selection scope differs')

    trials = pd.read_csv(root / 'trials.csv')
    require(len(trials) == 1146 and set(trials.track) == set(TRACKS),
            'Expansion trial table incomplete')
    by_track = {}
    for track in TRACKS:
        rows = trials.loc[trials.track.eq(track)].to_dict('records')
        require(len(rows) == 573 and len({r['candidate_id'] for r in rows}) == 573,
                'Duplicate or missing new trial')
        require(all(r['status'] == 'COMPLETE' and eligible(r) == bool(r['eligible'])
                    and bool(r['research_eligible']) == bool(r['eligible']) for r in rows),
                'Trial eligibility or completion differs from strict gate')
        by_track[track] = rows
    parent_rows = pd.read_csv(PARENT / 'trials.csv')
    parent_row = parent_rows.loc[(parent_rows.track == 'official_ex_post') &
                                 (parent_rows.candidate_id == 'd0034')].to_dict('records')
    require(len(parent_row) == 1 and eligible(parent_row[0]),
            'Audited d0034 is not a valid research baseline')
    winner = choose([*by_track['official_ex_post'], parent_row[0]])
    require(winner is not None and winner['candidate_id'] == selection['candidate_id'],
            'Reported selection differs from strict ranking')
    require(selection['source'] == ('prior' if winner['candidate_id'] == 'd0034' else 'new'),
            'Selection source differs from candidate ID')
    final = {track: checked_final(root, track, selection) for track in TRACKS}
    require(all(audit['final'][track].get('independent_audit') == 'PASS' for track in TRACKS),
            'Selected replay audit incomplete')
    comparison = pd.read_csv(root / 'comparison.csv')
    monthly = pd.read_csv(root / 'monthly.csv')
    require(len(comparison) == 4 and set(comparison.model) ==
            {'expansion_selected', 'parent_d0034'} and set(comparison.track) == set(TRACKS),
            'Comparison table incomplete')
    require(set(monthly.track) == set(TRACKS) and set(monthly.model) == {'expansion_selected'},
            'Monthly table incomplete')
    for track in TRACKS:
        matched = comparison.loc[(comparison.track == track) &
                                 (comparison.model == 'expansion_selected')]
        require(len(matched) == 1 and abs(float(matched.total_return.iloc[0]) -
                float(final[track][1]['total_return'])) < 1e-12,
                'Comparison differs from selected final metrics')
        months = monthly.loc[monthly.track == track].sort_values('month')
        eq = pd.read_csv(final[track][0] / 'equity.csv', usecols=['economic_nav'])
        require(len(months) == 21 and months.month.iloc[-1] == '2026-09' and
                abs(float(months.ending_economic_nav.iloc[-1]) -
                    float(eq.economic_nav.iloc[-1])) < 1e-4,
                'Monthly economic NAV differs from final ledger')

    # Read the same saved final NAV series that will be included in the release.
    annual_rows = {
        '擴搜獲選 v2': annual(final['official_ex_post'][0] / 'equity.csv'),
        '原 d0034': annual(PARENT / 'official_ex_post/final/v2_double_check_fintuned/equity.csv'),
        'v1': annual(ROOT / 'outputs/full_tuned_v2/official_ex_post/final/v1_matched/equity.csv'),
        '0050': annual(ROOT / 'outputs/full_tuned_v2/official_ex_post/final/0050/equity.csv'),
    }
    all_eligible = sorted((r for r in by_track['official_ex_post'] if eligible(r)),
                          key=lambda r: (-r['total_return'], r['max_drawdown'],
                                         r['turnover_two_way'], r['candidate_id']))
    improved = selection['source'] == 'new'
    lines = [
        '# v2 double-check：第二輪局部擴搜結果', '',
        '**研究候選；正式 policy 仍為 `BLOCK_SUBMISSION`。** 本報告由完整試驗與逐次帳務核對產生；'
        '最終可發行狀態仍須以[獨立驗證](../outputs/v2_double_check_expansion/audit.json)為準。更多參數搜尋無法補齊 Active Share、'
        '官方帳本及平台收件證據。', '',
        f'本輪完整覆蓋六軸局部網格 576 個位置，其中 3 個沿用已稽核舊設定；'
        f'573 個新候選各在兩池回放，共 1,146 次新試驗；連同舊版累計 1,409 組不同設定。'
        f'官方事後池有 '
        f'{len(all_eligible)}／573 組新候選通過全部已量測門檻。'
        f'本輪選擇 `{selection["candidate_id"]}`（{"新候選" if improved else "維持原 d0034"}）。', '',
        '## 與舊候選比較', '',
        '| 股票池 | 候選 | 帳面總報酬 | 最大回撤 | 已量測門檻合格 | 超過當日總成交量的成交 | 最大成交量參與率 |',
        '|---|---|---:|---:|---|---:|---:|',
    ]
    previous_track_rows = parent_rows.loc[parent_rows.candidate_id.eq('d0034')]
    for track in TRACKS:
        old = previous_track_rows.loc[previous_track_rows.track.eq(track)].iloc[0]
        new = final[track][1]
        for label, row in (('原 d0034', old), (selection['candidate_id'], new)):
            checked = dict(row, status='COMPLETE', candidate_id=label)
            lines.append(f'| {track} | `{label}` | {pct(row["total_return"])} | '
                         f'{pct(row["max_drawdown"])} | '
                         f'{"是" if eligible(checked) else "否"} | '
                         f'{int(row["trades_above_100pct_daily_volume"])} | '
                         f'{float(row["max_daily_volume_participation"]):.2f}× |')
    lines += ['', '官方事後池只用來選一組參數；歷史池以同一組參數重播，並非第二次選冠軍。'
              '若選擇維持 `d0034`，表內相同數字是重播一致性，不是新績效。'
              '成交量參與率不是官方明定門檻，但超過整日量的成交無法當作真實可成交收益。', '',
              '## 年度經濟淨值比較', '',
              '依各日 `economic_nav` 計算；2026 年只到 9 月 21 日，並非全年或年化報酬。', '',
              '| 期間 | 擴搜獲選 v2 | 原 d0034 | v1 | 0050 |',
              '|---|---:|---:|---:|---:|']
    for index, period in enumerate(('2025 年', '2026 年截至 9 月 21 日')):
        lines.append('| ' + period + ' | ' + ' | '.join(pct(values[index]) for values in
                     annual_rows.values()) + ' |')
    lines += ['', 'v1 有已量測違規；0050 是單一 ETF，不符合競賽個股及持股檔數限制。'
              '兩者僅供相同期間的研究對照，不能據此宣稱本輪策略已正式合規。', '',
              '## 新候選合格表', '',
              '| 排名 | 候選 | 報酬 | 最大回撤 | 換手率 |', '|---:|---|---:|---:|---:|']
    for index, row in enumerate(all_eligible[:10], start=1):
        lines.append(f'| {index} | `{row["candidate_id"]}` | {pct(row["total_return"])} | '
                     f'{pct(row["max_drawdown"])} | {float(row["turnover_two_way"]):.2f} |')
    if not all_eligible:
        lines.append('| — | 無新合格候選 | — | — | — |')
    if len(all_eligible) > 10:
        lines.append(f'\n僅列前 10 組；全部 {len(all_eligible)} 組可在試驗表查核。')
    failed = {field: sum(float(row[field]) > 0 for row in by_track['official_ex_post'])
              for field in ZERO_COUNTS}
    lines += ['', '不合格原因可重疊。以下為官方事後池新候選的逐項問題數：', '',
              '| 已量測門檻 | 有問題候選數 |', '|---|---:|']
    lines += [f'| `{key}` | {value} |' for key, value in failed.items()]
    selected = selection['params']
    axes = study['axes']
    lines += ['', '## 獲選參數與範圍', '', '| 本輪調整軸 | 獲選值 |', '|---|---|']
    for axis in axes:
        value = ([selected['macd_fast'], selected['macd_slow'], selected['macd_signal']]
                 if axis == 'macd_tuple' else selected[axis])
        lines.append(f'| `{axis}` | `{value}` |')
    lines += ['',
              '本輪只有[六軸局部網格](../docs/v2_double_check_expansion_protocol.md)完整覆蓋；'
              '其餘參數固定於原 `d0034`。選參維持八項零計數、完整期間、無失格及有限數值。'
              '更高的失敗候選不得因報酬而入選。', '',
              '2025-01-02～2026-09-21 的 417 日均已參與開發。2026 年公布的官方股票池'
              '回填至 2025 年有成分股前視偏誤；歷史池亦非未見測試。'
              '均價成交模型沒有滑價或市場衝擊。即使找到更高數字，也只能視為開發期觀察。', '',
              '[試驗表](../outputs/v2_double_check_expansion/trials.csv) · '
              '[網格位置](../outputs/v2_double_check_expansion/grid.json) · '
              '[獲選設定](../outputs/v2_double_check_expansion/selection.json) · '
              '[月度淨值](../outputs/v2_double_check_expansion/monthly.csv) · '
              '[逐條規則](../docs/v2_double_check_rules.md)', '',
    ]
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open('x') as stream:
        stream.write('\n'.join(lines))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--report', type=Path, default=REPORT)
    args = parser.parse_args()
    print(make_report(args.output, args.report))


if __name__ == '__main__':
    main()
