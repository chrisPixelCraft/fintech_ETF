"""Build concise Chinese development-results report from verified study artifacts."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import markdown


def pct(value):
    return f'{float(value):.2%}'


def make_report(root):
    release = 'v2_double_check_fintuned'
    report = ROOT / 'reports' / (release + '_report.md')
    if report.exists() or report.with_suffix('.html').exists():
        raise FileExistsError('Preserve existing report: ' + str(report))
    audit = json.loads((root / 'study_audit.json').read_text())
    if audit['status'] != 'PASS':
        raise ValueError('Independent study audit required before reporting')
    manifest = json.loads((root / 'manifest.json').read_text())
    if not manifest['outputs_complete']:
        raise ValueError('Incomplete study')
    tests = json.loads((root / 'test_verification.json').read_text())
    if (tests['status'] != 'PASS'
            or hashlib.sha256((root / tests['log']).read_bytes()).hexdigest() != tests['log_sha256']):
        raise ValueError('Unit-test evidence missing or changed')
    prefix = json.loads((root / 'prefix_audit.json').read_text())
    if set(prefix) != {'official_ex_post', 'historical_pit'} or any(r['status'] != 'PASS' for r in prefix.values()):
        raise ValueError('Both physical prefix checks required')
    selection = json.loads((root / 'selection.json').read_text())
    grid = json.loads((root / 'local_grid.json').read_text())
    trials = pd.read_csv(root / 'trials.csv')
    comparison = pd.read_csv(root / 'comparison.csv')
    study = manifest['study']
    assets = ROOT / 'reports' / (release + '_assets')
    assets.mkdir(exist_ok=True)
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'axes.spines.top': False,
                         'axes.spines.right': False, 'figure.dpi': 130})
    palette = {'v2_double_check_fintuned': '#165D99', 'incumbent_f0019': '#D57B27',
               '0050': '#757575', 'v1_matched': '#7B579B'}
    for track in study['tracks']:
        fig, axes = plt.subplots(2, 1, figsize=(10, 7), sharex=True)
        for model, color in palette.items():
            folder = (root / track / 'final' / release if model == release else
                      ROOT / 'outputs/full_tuned_v2' / track / 'final' /
                      ('full_tuned_v2' if model == 'incumbent_f0019' else model))
            eq = pd.read_csv(folder / 'equity.csv')
            nav = np.r_[1e9, eq.nav.to_numpy()]
            drawdown = 100 * (nav / np.maximum.accumulate(nav) - 1)[1:]
            axes[0].plot(pd.to_datetime(eq.date), eq.nav / 1e9, label=model, color=color)
            axes[1].plot(pd.to_datetime(eq.date), drawdown, color=color)
        axes[0].set(ylabel='Book NAV / initial capital', title=track + ' | development data')
        axes[1].set(ylabel='Drawdown (%)', xlabel='Date')
        axes[0].legend(fontsize=8)
        for ax in axes:
            ax.grid(alpha=.2)
        fig.tight_layout(); fig.savefig(assets / (track + '_nav.png')); plt.close(fig)
    official = trials[trials.track.eq('official_ex_post')]
    eligible = official[official.eligible]
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.scatter(eligible.max_drawdown * 100, eligible.total_return * 100, s=16, alpha=.7,
               color='#165D99', label='All declared measured guards passed')
    best = official[official.candidate_id.eq(selection['candidate_id'])].iloc[0]
    ax.scatter([best.max_drawdown * 100], [best.total_return * 100], marker='*', s=160,
               color='#D57B27', label=selection['candidate_id'])
    ax.set(xlabel='Max drawdown (%)', ylabel='Development total return (%)', title='Search results, not out-of-sample evidence')
    ax.legend(fontsize=8); ax.grid(alpha=.2); fig.tight_layout()
    fig.savefig(assets / 'search_frontier.png'); plt.close(fig)
    reasons = ['raw_rule_breach_days', 'simulated_warning_days', 'no_valid_plan_days', 'unfilled_orders',
               'stale_held_price_days', 'hold_without_envelope_days', 'execution_price_bound_breaches']
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.barh(reasons, [int(official[key].gt(0).sum()) for key in reasons], color='#9F554E')
    ax.set(xlabel='Rejected candidate count (reasons may overlap)', title='Failure diagnostics, not performance comparisons')
    fig.tight_layout(); fig.savefig(assets / 'rejection_counts.png'); plt.close(fig)
    fields = list(selection['params'])
    fig, axes = plt.subplots(6, 3, figsize=(14, 19))
    for ax, field in zip(axes.flat, fields):
        data = eligible.copy()
        values = sorted(data[field].unique())
        if field == 'four_hour_mode':
            x = data[field].map({v: i for i, v in enumerate(values)})
            ax.set_xticks(range(len(values)), values, rotation=15)
        else:
            x = data[field]
        ax.scatter(x, data.total_return * 100, s=7, alpha=.35)
        ax.set(title=field, ylabel='Return (%)')
        ax.grid(alpha=.15)
    fig.suptitle('Eligible development candidates | correlations are not causal effects', y=.999)
    fig.tight_layout(); fig.savefig(assets / 'parameter_axes.png'); plt.close(fig)
    boundary = {}
    from scripts.prepare_double_check import PAIR_KEYS
    for axis, values in study['spaces'].items():
        p = selection['params']
        value = [p[k] for k in PAIR_KEYS[axis]] if axis in PAIR_KEYS else p[axis]
        boundary[axis] = dict(value=value, boundary=value in (values[0], values[-1]),
                              searched_values=values)
    (root / 'search_diagnostics.json').write_text(json.dumps(dict(
        winner_boundaries=boundary, total_unique_candidates=len(official),
        eligible_by_track=trials.groupby('track').eligible.sum().astype(int).to_dict(),
        fully_exhaustive_global=False, raw_global_combinations=study['raw_cartesian_combinations'],
        global_fraction=len(official) / study['raw_cartesian_combinations'],
        raw_local_combinations=grid['raw_combinations'], local_coverage_rows=len(grid['coverage']),
        search_seed=2026092301), ensure_ascii=False, indent=2) + '\n')
    lines = [f'# {release}：雙重核對結果', '',
        '**研究回測已完成；正式 policy 使用仍阻擋。** 已知每日交易限制與保護條件必須全部通過，'
        '任何違規、失格、缺價、未成交或保護失效候選都不能獲選。Active Share、官方帳本與平台接受證據不足，'
        '因此研究 PASS 不代表符合全部官方規則，也不授權送件。', '',
        f'本輪選擇 `{selection["candidate_id"]}`。資料涵蓋 2025-01-02～2026-09-21，共 417 個交易日；'
        '整段行情已參與開發。以下報酬是事後選參績效，沒有未見驗證，也不保證未來零違規。', '',
        '## 績效比較', '', '| 股票池 | 策略 | 淨報酬 | 最大回撤 |', '|---|---|---:|---:|']
    for row in comparison.itertuples():
        lines.append(f'| {row.track} | {row.model} | {pct(row.total_return)} | {pct(row.max_drawdown)} |')
    selected_rows = comparison[comparison.model.eq(release)]
    capacity = '；'.join(f'{row.track} 有 {int(row.trades_above_100pct_daily_volume)} 筆成交超過該股票當日總成交量'
                        for row in selected_rows.itertuples())
    lines += ['', '新策略在官方事後股票池選參，再用相同參數重播歷史股票池。2026 年公布的官方名單用於 '
        '2025 年會造成成分股前視偏誤；歷史池只是敏感度分析，不能當獨立測試。舊版、v1 與 0050 數字來自凍結且經驗證的原報告；'
        '它們未獲得本輪相同搜尋預算，所以此表是工程比較，不支持模型優越性的科學結論。'
        '`incumbent_replayed_new_ledger` 則是舊 f0019 參數在新帳本下的本輪實際回放。'
        '舊參數報酬較高，但官方池出現一次成交價格保護超界，因此不符合本輪嚴格門檻；本版不為保留高報酬放寬條件。', '',
        '0050 是單一 ETF 情境對照，不符合本競賽的 150 個股白名單與 20–30 檔持股要求。'
        '所有舊版對照均未因此取得新版資格，也不參與正式 policy 發布。', '',
        '模擬按文件所述均價成交，未加入滑價或市場衝擊。' + capacity + '。'
        '文件未明訂成交量上限，因此這不是已判定的官方違規；但這些報酬不能視為真實市場可成交績效。', '',
        f'![Official development NAV]({release}_assets/official_ex_post_nav.png)', '',
        f'![Historical sensitivity NAV]({release}_assets/historical_pit_nav.png)', '',
        '## 每日合規與使用資格', '',
        '逐日稽核重新計算股數、現金、費稅、成交均價、公司行動、股利、NAV、警告及回退。'
        '普通違規同日只記一次，撤銷整日成交與費稅；第三次警告停止。被動權重超限依五日寬限／第六日警告檢查。'
        '公司行動保留與期末股利時點仍是明列的研究解釋，不能冒充主辦方確認。', '',
        '官方仍有五日被動超限寬限；本版選參額外要求全期零超限，避免依賴寬限與未確認歸因。'
        '首輪產物因帳務歸因問題隔離，修正後重新執行，未混入結果。', '',
        '| 股票池 | 原始超限日 | 警告日 | 未成交 | 缺價日 | 保護失效 | 研究候選合格 |', '|---|---:|---:|---:|---:|---:|---|']
    for track in study['tracks']:
        row = trials[trials.track.eq(track) & trials.candidate_id.eq(selection['candidate_id'])].iloc[0]
        lines.append(f'| {track} | {int(row.raw_rule_breach_days)} | {int(row.simulated_warning_days)} | {int(row.unfilled_orders)} | '
                     f'{int(row.stale_held_price_days)} | {int(row.execution_price_bound_breaches)} | {bool(row.eligible)} |')
    lines += ['', '歷史敏感度池若不合格，會保留原樣揭露；它不能取得正式 policy 使用資格。'
        '正式入口 `plan` 不輸出可提交 D-Plan；`research-plan` 只輸出包裝過、標明 `NEVER_SUBMIT` 的研究草稿。'
        '阻擋送件也不是成功提交，仍可能無法滿足至少 22 日成功繳交門檻。', '',
        '完整規則與未知項目見[逐條規則表](../docs/v2_double_check_rules.md)，'
        '實驗條件見[本輪 protocol](../docs/v2_double_check_protocol.md)。', '',
        '## 搜尋實際覆蓋', '',
        f'完成 **{len(official)} 組唯一候選 × 2 池 = {len(trials)} 次回放**，每次都通過獨立帳務驗證。'
        f'第一階段 {len(study["candidates"])} 組，另新增 {len(grid["candidates"])} 組局部候選；'
        f'局部網格 {grid["raw_combinations"]} 個原始組合全部覆蓋，與前階段重複者按相同有效參數重用。', '',
        '通過全部已量測候選門檻的數量：' + '；'.join(
            f'{track} {int(group.eligible.sum())}／{len(group)} 組' for track, group in trials.groupby('track')) + '。', '',
        f'完整離散範圍有 **{study["raw_cartesian_combinations"]:,} 組**；本輪沒有全域窮舉，'
        '更沒有窮舉無限多連續參數。廣域搜尋涵蓋所有已宣告參數軸，各值逐軸測試加配對 Sobol 取樣；'
        '只有明列的七軸、每軸兩值局部網格可稱為完整搜尋。', '',
        f'![Search frontier]({release}_assets/search_frontier.png)', '',
        f'![Rejection counts]({release}_assets/rejection_counts.png)', '',
        f'![Parameter axes]({release}_assets/parameter_axes.png)', '',
        '## 固定參數', '', '| 參數 | 值 |', '|---|---|']
    lines += [f'| `{key}` | `{value}` |' for key, value in selection['params'].items()]
    edges = [axis for axis, value in boundary.items() if value['boundary']]
    lines += ['', '獲選值位於宣告範圍邊界的軸：' + '、'.join(f'`{x}`' for x in edges) + '。'
        '這些邊界尚未證明是全域最佳；本輪停在預先宣告預算，不追逐已見行情無限擴搜。', '',
        '## 驗證與重現', '',
        f'完整測試 **{tests["tests"]} 項通過**。兩池獲選參數亦通過移除未來資料與改動未來價格、量能、'
        '股利及分割的前綴檢查；既有委託與結算不變。這是因果時序驗證，不是未見行情績效驗證。', '',
        '```bash', '.venv/bin/python v2_double_check_fintuned.py --verify-release',
        '.venv/bin/python v2_double_check_fintuned.py --show-config',
        '.venv/bin/python v2_double_check_fintuned.py --output outputs/double_check_replay',
        '.venv/bin/python -m unittest discover -s tests', '```', '',
        '證據位於 [`outputs/v2_double_check_fintuned`](../outputs/v2_double_check_fintuned/)。'
        '`manifest.json` 保存程式／資料 SHA256、Git 起點、環境與搜尋規格；`trials.csv` 保存每組結果；'
        '`local_grid.json` 列明全部局部組合；`study_audit.json` 保存帳務核對；'
        '`audit.json` 綁定可發行檔案。完整中間帳本保留本機，Git 保留獲選帳本與完整彙總，重跑可重建全部候選。', '',
        '本輪學到：帳務回退補齊了舊版缺漏，但不能把缺失的官方契約變成 PASS。'
        '較高回測報酬也不能證明未來可用；正式使用的決策維持 **HOLD／BLOCK_SUBMISSION**。', '']
    report.write_text('\n'.join(lines))
    html = markdown.markdown(report.read_text(), extensions=['tables', 'fenced_code'])
    css = 'body{max-width:1100px;margin:40px auto;padding:0 24px;font:16px/1.7 system-ui;color:#203040}table{border-collapse:collapse;width:100%;font-size:14px}th,td{border:1px solid #ddd;padding:8px;text-align:left}img{max-width:100%}pre{background:#f2f5f8;padding:16px;overflow:auto}h1,h2{color:#165D99}a{color:#165D99}'
    report.with_suffix('.html').write_text('<!doctype html><html lang="zh-Hant"><meta charset="utf-8">'
        f'<title>{release}</title><style>{css}</style><body>{html}</body></html>')
    print(report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='outputs/v2_double_check_fintuned')
    args = parser.parse_args()
    make_report(ROOT / args.output)
