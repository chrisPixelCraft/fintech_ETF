"""Render the bounded monthly search from authenticated, independently audited evidence."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from scripts.run_double_check import verify_hashes

OUTPUT = ROOT / 'outputs/monthly_expansion_20260923'
PARENT = ROOT / 'outputs/monthly_horizon_20260923'
REPORT = ROOT / 'reports/monthly_expansion_report.md'


def pct(value):
    return '—' if pd.isna(value) else f'{value:.2%}'


def render(root=OUTPUT):
    audit = json.loads((root / 'audit.json').read_text())
    if audit.get('status') != 'PASS':
        raise ValueError('Independent audit required before reporting')
    verify_hashes(ROOT, audit['input_sha256'])
    verify_hashes(root, audit['artifact_sha256'])
    selection = json.loads((root / 'selection.json').read_text())
    episodes = pd.read_csv(root / 'episodes.csv')
    ranks = pd.read_csv(root / 'ranking.csv')
    design = json.loads((ROOT / 'config/monthly_expansion.json').read_text())
    prior = json.loads((PARENT / 'audit.json').read_text())
    verify_hashes(ROOT, prior['input_sha256'])
    verify_hashes(PARENT, prior['artifact_sha256'])
    baseline = pd.read_csv(PARENT / 'episodes.csv')
    selected = selection['candidate_id']
    adoption = ('後續診斷未全部通過，不替換原策略' if selection['adoption'] == 'FAILED_DIAGNOSTIC_NO_ADOPTION'
                else '研究門檻通過，但官方證據未齊，暫不正式採用')
    conclusion = (f'選出固定候選 `{selected}`；{adoption}。'
                  if selected else '沒有候選通過全部 11 個訓練窗口，因此沒有新的合格冠軍，保留 `x0352` 研究入口。')
    lines = ['# 單月策略參數擴搜', '',
             f'**72 組新設定、{audit["verified_runs"]} 次回放已完成獨立稽核。{conclusion}** '
             '正式提交仍為 `BLOCK_SUBMISSION`。', '',
             '## 搜尋與結果', '',
             '以 `x0352`、`x0454` 為兩個骨架，各測 EMA 慢窗 75／100／150／200、'
             '量比下限 0.4／0.6／0.8、現金緩衝 10%／12%／14%，共 72 組。'
             '其餘參數及逐日限制固定；訓練與排序採官方事後池，每窗從 10 億元與空持倉開始，回放 25 個交易日。', '',
             '| 骨架 | 新設定 | 4 月通過 | 11 窗全通過 |', '|---|---:|---:|---:|']
    for anchor in ('x0352', 'x0454'):
        part = ranks.loc[ranks.anchor.eq(anchor)]
        ids = set(part.candidate_id)
        april = episodes.loc[episodes.candidate_id.isin(ids) & episodes.track.eq('official_ex_post')
                             & episodes.episode.eq('m25_2025-04')]
        lines.append(f'| `{anchor}` | {len(part)} | {int(april.research_eligible.sum())} | '
                     f'{int(part.training_eligible.sum())} |')
    lines += ['', f'全部設定先跑 2025-04；其中 {selection["april_passers"]} 組通過後，'
              '各跑其餘 10 個訓練窗口。訓練起點為 2025-01 至 11；十二月窗口跨至 '
              '2026-01-06，排除於訓練。只有 11 窗全部合格者，才按經濟報酬中位數、'
              '最差窗、平均換手、ID 排名。未跑窗口不補零，失敗窗口不隱藏。', '',
              '| 已執行窗口的失敗原因 | 受影響窗口數 |', '|---|---:|']
    for field, label in [('simulated_warning_days', '出現模擬警告'),
                         ('no_valid_plan_days', '無有效交易計畫'),
                         ('unfilled_orders', '未成交訂單'),
                         ('stale_held_price_days', '持股報價過期'),
                         ('hold_without_envelope_days', '續抱缺價格保護'),
                         ('execution_price_bound_breaches', '成交價格超出研究保護區間'),
                         ('raw_rule_breach_days', '原始持股／權重／現金門檻超限')]:
        count = int(episodes[field].gt(0).sum())
        if count:
            lines.append(f'| {label} | {count} |')
    lines += ['', f'其中 {int(episodes.disqualified.sum())} 個窗口因累積警告失格。'
              '原因可能重疊；價格保護是額外研究門檻，並非每個失敗都屬官方警告。'
              '「獨立稽核通過」表示帳本與結果可重建，不表示候選交易合格。', '',
              '## 與既有策略比較', '',
              '| 固定策略 | 全期帳面報酬 | 相同 11 個冷啟動訓練窗合格數 |', '|---|---:|---:|']
    for cid in ('x0352', 'x0454'):
        part = baseline.loc[baseline.track.eq('official_ex_post') & baseline.candidate_id.eq(cid)]
        full = part.loc[part.episode.eq('full')]
        training = part.loc[part.episode.isin(design['training_episodes'])]
        if len(full) != 1 or len(training) != 11:
            raise ValueError('Baseline coverage differs')
        lines.append(f'| `{cid}` | {pct(full.iloc[0].total_return)} | '
                     f'{int(training.research_eligible.sum())}/11 |')
    if selected:
        part = episodes.loc[episodes.candidate_id.eq(selected) & episodes.track.eq('official_ex_post')]
        full = part.loc[part.episode.eq('full')].iloc[0]
        lines.append(f'| `{selected}` | {pct(full.total_return) if full.research_eligible else "不合格"} | 11/11 |')
        rank = ranks.set_index('candidate_id').loc[selected]
        lines += ['', f'獲選者訓練窗中位數 {pct(rank.median_return)}、最差窗 {pct(rank.worst_return)}。'
                  f'兩池共 44 個窗口，通過 {int(episodes.loc[episodes.candidate_id.eq(selected), "research_eligible"].sum())} 個。'
                  '完整逐窗結果見下方 CSV；後續診斷不參與換冠軍。']
        trial = next(t for t in design['candidates'] if t['candidate_id'] == selected)
        lines += ['', '| 調整參數 | 原骨架 `' + trial['anchor'] + '` | `' + selected + '` |',
                  '|---|---:|---:|']
        for key, value in trial['params'].items():
            old = design['anchors'][trial['anchor']][key]
            if old != value:
                lines.append(f'| `{key}` | {old} | {value} |')
        lines += ['', '## 固定候選診斷', '',
                  '| 股票池 | 20 個冷啟動窗合格數 | 全期 | 2025 賽期類比報酬 |',
                  '|---|---:|---:|---:|']
        for track, label in [('official_ex_post', '官方事後'), ('historical_pit', '歷史股票池')]:
            group = episodes.loc[episodes.candidate_id.eq(selected) & episodes.track.eq(track)]
            resets = group.loc[group.kind.eq('reset_25_sessions')]
            full = group.loc[group.episode.eq('full')].iloc[0]
            contest = group.loc[group.episode.eq('contest_2025')].iloc[0]
            lines.append(f'| {label} | {int(resets.research_eligible.sum())}/20 | '
                         f'{"通過" if full.research_eligible else "不合格"} | '
                         f'{pct(contest.economic_total_return) if contest.research_eligible else "不合格"} |')
        lines += ['', '類比期為 2025-10-26–11-27，並非尚未發生的 2026 正式賽期。', '',
                  '| 未通過的診斷窗口 | 價格保護超界 | 無有效計畫日 | 報價過期日 |',
                  '|---|---:|---:|---:|']
        failed = episodes.loc[episodes.candidate_id.eq(selected) & ~episodes.research_eligible]
        for row in failed.itertuples():
            pool = '官方事後' if row.track == 'official_ex_post' else '歷史池'
            lines.append(f'| {pool} `{row.episode}` | {row.execution_price_bound_breaches} | '
                         f'{row.no_valid_plan_days} | {row.stale_held_price_days} |')
        lines += ['',
                  '| 月初起點（25 交易日） | `x0352` | `x0454` | `' + selected + '` |',
                  '|---|---:|---:|---:|']
        for episode in sorted(part.loc[part.kind.eq('reset_25_sessions'), 'episode']):
            rows = [baseline.loc[baseline.track.eq('official_ex_post') & baseline.candidate_id.eq(cid)
                                 & baseline.episode.eq(episode)].iloc[0] for cid in ('x0352', 'x0454')]
            rows.append(part.loc[part.episode.eq(episode)].iloc[0])
            cells = [pct(row.economic_total_return) if row.research_eligible else '不合格' for row in rows]
            lines.append(f'| {episode[4:]} | ' + ' | '.join(cells) + ' |')
        lines += ['', '上表為官方事後池；各窗獨立重設本金，可能跨月及互相重疊。'
                  '2025-12 起的窗口屬固定候選診斷。未合格的報酬不作可採用績效展示。']
    else:
        lines += ['| 本輪新設定 | —（未執行全期診斷） | 0 組達 11/11 |', '',
                  '放寬入場訊號改善了 4 月建倉可行性，仍不足以讓任何設定在所有訓練窗口合格。'
                  '因此不以違反研究門檻的高報酬設定補位，也沒有可與 309.16% 比較的新全期冠軍。']
    lines += ['', '全期期間為 2025-01-02–2026-09-21；309.16% 是整段帳面報酬，不是單月報酬。'
              '冷啟動合格數與全期持續持倉回答不同問題，不能互相替代。', '',
              '## 注意事項', '',
              '本輪針對已知失敗調參，兩池與日期都曾參與開發；不是樣本外測試，也不是每月各挑事後最佳參數。'
              '72 組為固定有限網格，不能宣稱全域最佳或未來每個月都合格；窗口可能重疊。', '',
              '官方事後股票池有前視偏誤；模擬成交未計滑價與市場衝擊，未證明容量。'
              'Active Share、正式帳本與收件仍缺證據；2010–2024 資料缺口未解決，正式提交保持阻擋。', '',
              '[固定搜尋規格](../docs/monthly_expansion_protocol.md) · '
              '[建倉診斷](../docs/monthly_expansion_design.md) · '
              '[全部參數](../config/monthly_expansion.json) · '
              '[候選資格表](../outputs/monthly_expansion_20260923/ranking.csv) · '
              '[逐窗結果](../outputs/monthly_expansion_20260923/episodes.csv) · '
              '[獨立稽核](../outputs/monthly_expansion_20260923/audit.json) · '
              '[前輪月度比較](monthly_horizon_report.md)', '',
              '驗證：`python scripts/verify_monthly_expansion.py`；'
              '報告核對：`python scripts/report_monthly_expansion.py --verify`。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    text = render()
    if args.verify:
        if REPORT.read_text() != text:
            raise ValueError('Report differs from audited evidence')
    else:
        if REPORT.exists():
            raise FileExistsError('Preserve existing report')
        REPORT.write_text(text)
    print(REPORT)


if __name__ == '__main__':
    main()
