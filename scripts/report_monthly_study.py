"""Render a concise report only from independently verified monthly artifacts."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from scripts.run_double_check import verify_hashes

OUTPUT = ROOT / 'outputs/monthly_horizon_20260923'
REPORT = ROOT / 'reports/monthly_horizon_report.md'


def pct(value):
    return '—' if value is None or pd.isna(value) else f'{value:.2%}'


def render(root=OUTPUT):
    read = lambda name: json.loads((root / name).read_text())
    audit = read('audit.json')
    if audit.get('status') != 'PASS':
        raise ValueError('Independent study verification must pass before reporting')
    verify_hashes(root, audit['artifact_sha256'])
    verify_hashes(ROOT, audit['input_sha256'])
    selected = read('selection.json')['candidate_id']
    summary = read('summary.json')
    months = pd.read_csv(root / 'monthly.csv')
    episodes = pd.read_csv(root / 'episodes.csv')
    history = pd.read_csv(root / 'history_monthly.csv')
    ranking = pd.read_csv(root / 'ranking.csv').set_index('candidate_id')
    if len(history) != 180 or history.return_net.notna().any():
        raise ValueError('Historical availability changed; reassess report')
    ids = list(dict.fromkeys(['x0352', selected]))
    official = {r['candidate_id']: r for r in summary['candidates'] if r['track'] == 'official_ex_post'}
    base, candidate = official['x0352'], official[selected]
    get = lambda track, cid, episode: episodes.loc[
        episodes.track.eq(track) & episodes.candidate_id.eq(cid) & episodes.episode.eq(episode)].iloc[0]
    show = lambda r: pct(r.economic_total_return) if bool(r.research_eligible) else '不合格'
    lines = ['# 一個月持有期：固定月度候選與 x0352', '',
        f'**月度目標選出 `{selected}`；不能認定它每月都優於 `x0352`。** '
        '2010–2024 的 180 個月皆缺完整必要證據，未產生歷史報酬。'
        '以下實測為 2025-01-02–2026-09-21，正式提交仍為 `BLOCK_SUBMISSION`。', '',
        '## 主要比較', '',
        '使用官方事後股票池與同一帳本。從既有 48 組全期合格設定，按 2025 '
        '完整月經濟報酬中位數、最差月、2025 換手、ID 選一組固定參數；之後不按月換參。'
        '這是有限動能候選的目標重排名，未測新的波動風控或反轉模型。', '',
        f'選參所用的 2025 月中位數：`{selected}` 為 {pct(ranking.at[selected, "median_2025"])}，'
        f'`x0352` 為 {pct(ranking.at["x0352", "median_2025"])}。下表中位數涵蓋全部 20 個完整月，'
        '期間不同，不能混為同一排名。', '',
        '| 固定策略 | 全期帳面報酬 | 2025 年 | 2026 年至 9/21 | 完整月中位數 | 最差月 | 虧損月 |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for cid in ids:
        r = official[cid]
        lines.append(f'| `{cid}` | {pct(r["book_total_return"])} | {pct(r["annual_2025"])} | '
                     f'{pct(r["ytd_2026"])} | {pct(r["median_month"])} | {pct(r["worst_month"])} | '
                     f'{r["negative_months"]}/{r["complete_months"]} |')
    archived = {r['candidate_id']: r['params'] for r in read('candidates.json')}
    lines += ['', '全期帳面欄對齊原 309.16%；年度與月度欄採股利歸屬後的 `economic_nav`。'
              '完整月統計共 20 月，排除 2026-09 部分月。', '',
              '| 參數差異 | `x0352` | 月度候選 `' + selected + '` |', '|---|---:|---:|']
    for key in archived['x0352']:
        if archived['x0352'][key] != archived[selected][key]:
            lines.append(f'| `{key}` | {archived["x0352"][key]} | {archived[selected][key]} |')
    lines += ['',
              '## 逐月連續帳本', '',
              '| 月份 | `x0352` | 月度候選 `' + selected + '` |', '|---|---:|---:|']
    official_months = months.loc[months.track.eq('official_ex_post')]
    wins = ties = count = 0
    for month in sorted(official_months.month.unique()):
        b = official_months.loc[official_months.month.eq(month) & official_months.candidate_id.eq('x0352')].iloc[0]
        c = official_months.loc[official_months.month.eq(month) & official_months.candidate_id.eq(selected)].iloc[0]
        label = month + ('（至 9/21）' if bool(b.partial_period) else '')
        lines.append(f'| {label} | {pct(b.return_net)} | {pct(c.return_net)} |')
        if not bool(b.partial_period):
            count += 1
            wins += c.return_net > b.return_net + 1e-12
            ties += abs(c.return_net - b.return_net) <= 1e-12
    lines += ['', f'月度候選在 {count} 個完整月中勝過基準 {wins} 月、平手 {ties} 月。'
              '此表沿用既有持倉，不等同每個月重新投入本金。', '',
              '## 重新建倉的 25 交易日', '',
              '每窗從 10 億元與空持倉開始，保留先前暖機；起點為各月首個交易日，'
              '結束日可能跨月。下表只對完整、未失格且八項已量測門檻全零的窗口顯示報酬。', '',
              '| 起始月份 | `x0352` | 月度候選 `' + selected + '` |', '|---|---:|---:|']
    for episode in sorted(episodes.loc[episodes.kind.eq('reset_25_sessions'), 'episode'].unique()):
        b, c = (get('official_ex_post', cid, episode) for cid in ('x0352', selected))
        lines.append(f'| {episode[4:]} | {show(b)} | {show(c)} |')
    lines += ['', '| 股票池／策略 | 合格窗口 | 合格窗中位數 | 合格窗最差報酬 |', '|---|---:|---:|---:|']
    for r in summary['candidates']:
        label = '官方事後' if r['track'] == 'official_ex_post' else '歷史股票池'
        lines.append(f'| {label} `{r["candidate_id"]}` | {r["reset_eligible"]}/{r["reset_count"]} | '
                     f'{pct(r["eligible_reset_median"])} | {pct(r["eligible_reset_worst"])} |')
    for pair in summary['paired_resets']:
        label = '官方事後池' if pair['track'] == 'official_ex_post' else '歷史池'
        lines += ['', f'{label}雙方共同合格 {pair["paired_eligible"]} 窗，月度候選勝 '
                  f'{pair["selected_wins"]} 窗、平手 {pair["ties"]} 窗；平均報酬差 '
                  f'{pair["mean_difference"] * 100:.2f} 個百分點。不同策略的合格子集可能不同，不能只比各自中位數。'
                  if pair['mean_difference'] is not None else
                  f'{label}沒有雙方共同合格的窗口，無法配對比較。']
    lines += ['', '不合格窗口保留於分母與[逐窗稽核表](../outputs/monthly_horizon_20260923/episodes.csv)。'
              '窗口可能重疊，未將它們當獨立樣本推算顯著性。', '',
              '2025-04 重新建倉時，兩策略在兩池均累積三次模擬警告而失格；其餘失敗包含'
              '成交價格超出研究保護區間、未成交或持股報價過期。價格保護區間是額外研究門檻，'
              '不能把所有此類失敗都說成官方警告。`x0454` 在歷史池的全期回放也有一次價格保護超界，'
              '因此未同時通過兩池全期門檻。', '',
              '## 10/26–11/27 歷史類比', '',
              '2026 正式賽期尚未發生。下表只重播 2025-10-26–11-27，實際首個交易日為 10/27；'
              '僅一個類比期，不能用來預測比賽勝率。', '',
              '| 股票池 | `x0352` | 月度候選 `' + selected + '` |', '|---|---:|---:|']
    for track, label in [('official_ex_post', '官方事後'), ('historical_pit', '歷史股票池')]:
        b, c = (get(track, cid, 'contest_2025') for cid in ('x0352', selected))
        lines.append(f'| {label} | {show(b)} | {show(c)} |')
    lines += ['', '## 2010–2024 每月覆蓋', '',
              '下列「—」表示資料不足，非 0% 或已執行回測。2010–2023 缺日線與日內歷史；'
              '2024 年 1–9 月缺四小時觀測，晚期月份仍缺月初當時可知股票池。', '',
              '<details>', '<summary>展開 180 個月份</summary>', '',
              '| 年度 | 1月 | 2月 | 3月 | 4月 | 5月 | 6月 | 7月 | 8月 | 9月 | 10月 | 11月 | 12月 |',
              '|---|' + '---:|' * 12]
    for year in range(2010, 2025):
        lines.append(f'| {year} | ' + ' | '.join(['—'] * 12) + ' |')
    lines += ['', '</details>', '',
              '逐月原因見[180 月覆蓋表](../outputs/monthly_horizon_20260923/history_monthly.csv)；'
              '來源限制見[歷史查核](x0352_2010_2024_report.md)及[資料與文獻研究](../docs/monthly_strategy_research.md)。', '',
              '## 結論與注意事項', '',
              f'月度目標候選的全期帳面報酬為 {pct(candidate["book_total_return"])}，基準為 '
              f'{pct(base["book_total_return"])}；逐月、最差月與重新建倉結果必須一起比較。'
              '目前不足以認定存在每月都最佳且每日都合格的策略，正式採用判定為 `HOLD`。', '',
              '48 組資格預篩已看過 2026，加上先前多輪選參與官方事後名單的前視偏誤，'
              '本輪不是未見資料測試。模擬均價成交無滑價／市場衝擊，容量限制也未獲證明。'
              'Active Share、正式結算與平台收件仍缺證據；研究帳本稽核通過不等於官方全部規則通過。', '',
              '[研究與來源](../docs/monthly_strategy_research.md) · '
              '[實驗規格](../docs/monthly_strategy_protocol.md) · '
              '[獨立驗證](../outputs/monthly_horizon_20260923/audit.json) · '
              '[固定參數](../outputs/monthly_horizon_20260923/selection.json) · '
              '[月報酬 CSV](../outputs/monthly_horizon_20260923/monthly.csv)', '',
              '重跑：`python scripts/run_monthly_study.py --output outputs/my_monthly_study`；'
              '驗證封存：`python scripts/verify_monthly_study.py`。', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    text = render()
    if args.verify:
        if REPORT.read_text() != text:
            raise ValueError('Report differs from verified evidence')
    else:
        if REPORT.exists():
            raise FileExistsError('Preserve existing monthly report')
        REPORT.write_text(text)
    print(REPORT)


if __name__ == '__main__':
    main()
