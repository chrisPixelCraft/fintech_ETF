#!/usr/bin/env python3
"""Render concise reports only from verified tuning artifacts."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.verify_24d_tuning import verify,read,csv
from scripts.run_24d import aggregate,atomic_frame,atomic_json,file_hash
from scripts.supplement_24d import cold_start_metrics


def pct(value):return '—' if pd.isna(value) else f'{100*value:.2f}%'

def comparison(out):
    selection=read(out/'final_selection.json')
    labels={'x0352 baseline':'x0352_daily_baseline',**{k+' 診斷代表':v for k,v in selection['diagnostic_method_leaders'].items()}}
    records=[]; deployment=[]
    for phase in ['development','validation','holdout','recent','seasonal','rolling_recent']:
        rows=csv(out/(phase+'.csv'));summaries=aggregate(rows).set_index('candidate_id')
        for label,candidate in labels.items():
            if candidate in summaries.index:records.append(dict(period=phase,strategy=label,**summaries.loc[candidate].to_dict(),candidate_id=candidate))
        if phase=='recent':
            last_six=rows[rows.start.ge('2026-03-01')]
            six=aggregate(last_six).set_index('candidate_id')
            for label,candidate in labels.items():
                if candidate in six.index:records.append(dict(period='recent_6_calendar_months',strategy=label,**six.loc[candidate].to_dict(),candidate_id=candidate))
        for candidate in sorted(set(labels.values())):
            folder=out/'ledgers'/phase/candidate
            if not folder.exists():continue
            equity=pd.read_parquet(folder/'equity.parquet'); compliance=pd.read_parquet(folder/'compliance_daily.parquet')
            for episode in rows.loc[rows.candidate_id.eq(candidate),'episode_id']:
                eq=equity[equity.episode_id.eq(episode)].sort_values('date'); co=compliance[compliance.episode_id.eq(episode)]
                deployment.append(dict(phase=phase,candidate_id=candidate,episode_id=episode,**cold_start_metrics(eq,co)))
    return pd.DataFrame(records),pd.DataFrame(deployment)


def render(out):
    verified=verify(out)
    selection=read(out/'final_selection.json');table,cold=comparison(out)
    members=csv(out/'phase_memberships.csv');screen=csv(out/'screen_summary.csv'); specs={s['candidate_id']:s for s in read(out/'candidates.json')}
    lines=['# v3：24 交易日擴充調參','',
        f'本輪搜尋 **{len(specs)} 組不重複參數**，結論為 **`{selection["status"]}`**。正式使用維持 `BLOCK_SUBMISSION`。表中的 staged／local／global 是各方法的診斷代表，不代表已取得競賽合格資格。','',
        '依 [調參規格](../docs/v3_tuning.md)執行 A–F 與有界隨機搜尋。每次從 10 億元、零持股開始，持續 24 個交易日；D−1 決策、次日開盤價近似成交，保留雙邊手續費 0.1425% 與賣出稅 0.3%。沿用[逐日規則](../docs/v2_double_check_rules.md)，未放寬持股、現金、權重、整張、禁止超賣或當沖等檢查。','',
        '## 比較結果','',
        '報酬、回撤及正報酬比例只計算「完整且已量測通過」窗口。Valid 是完整跑完的比例；通過率另外列出。失敗窗口保留在分母，失格後的部分淨值不當作 24 日報酬。不同策略的通過窗口可能不同，因此條件式報酬也不是同一批樣本上的勝負證據，更不能解讀為可直接投資的平均成果。','']
    names=dict(development='開發：2010–2018',validation='驗證：2019–2022',holdout='凍結後歷史評估：2023–2024',recent='近期壓力：2025–2026/09',seasonal='10–11 月類比：2010–2025')
    for phase,title in names.items():
        lines += [f'**{title}**','', '| 策略 | Median 24D | P25 | P10 | Worst | Positive | Median MDD | Valid | 通過／全部 |',
            '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
        for r in table[table.period.eq(phase)].itertuples():
            lines.append(f'| {r.strategy} | {pct(r.median_24d_return)} | {pct(r.p25_24d_return)} | {pct(r.p10_24d_return)} | {pct(r.worst_24d_return)} | {pct(r.positive_episode_rate)} | {pct(r.median_mdd)} | {pct(r.valid_episode_rate)} | {r.passed}/{r.attempted} |')
        lines.append('')
    lines += ['三方法代表依完整開發期排名確定，再列出相同驗證窗口的結果；staged 代表仍是基準本身。全部入圍者見[驗證排名](../outputs/24d_tuning/validation_ranking.csv)。完整 mean、worst MDD、turnover、達到 20 檔的天數及各期分母見[比較 CSV](24d_tuning_comparison.csv)。','',
        '## 搜尋與停止理由','',
        '先重現全部 154 個開發／驗證窗口，與既有結果逐項一致，再開始搜尋。所有候選共用 12 個等距開發期月初窗口；依時間排列、不隨機打散。篩選不合格者仍可作搜尋方向的診斷種子，採用門檻則始終要求完整開發與驗證 **100% 已量測通過**。','',
        '| 階段 | 參數組數 | 篩選全通過 | 搜尋方式 |','|---|---:|---:|---|']
    descriptions=dict(A='20 組持股數×每日換股數',B='報酬視窗網格、EMA／MACD 粗搜與結構交互探測',C='換股數×有效 margin',D='42 組 m×q',E='五類風險篩選的單因素搜尋',F='前五區域的局部 Cartesian 網格',G='固定 seed 的 48 組有界隨機搜尋')
    for phase in 'ABCDEFG':
        ids=set(members[members.phase.eq(phase)].candidate_id);s=screen[screen.candidate_id.isin(ids)]
        lines.append(f'| {phase} | {len(ids)} | {int((s.compliance_pass_rate.eq(1)&s.valid_episode_rate.eq(1)).sum())} | {descriptions[phase]} |')
    lines += ['',f'各階段可能重複同一組參數；全域去重後為 {len(specs)} 組。每組篩選皆跑完相同 12 個窗口，僅入圍者完整重跑開發與驗證，沒有宣稱窮舉全部交互組合。完整驗證共 {csv(out/"validation.csv").candidate_id.nunique()} 組（含基準）。排名依可行性、median、P25、P10、MDD、mean、turnover；同值時優先接近基準。', '',
        f'停止原因：`{selection["stop_reason"]}`。沒有合格候選時，不繼續用驗證或保留期間追逐報酬，也不產生假定合格的 winner；穩定性測試狀態為 `{read(out/"stability.json")["status"]}`。','',
        '## 近期與冷啟動','',
        '| 範圍／策略 | Median 24D | P25 | 最差 | 通過／全部 |','|---|---:|---:|---:|---:|']
    for r in table[table.period.isin(['recent_6_calendar_months','rolling_recent'])].itertuples():
        label='2026/03–08' if r.period=='recent_6_calendar_months' else '最近 126 個滾動起點'
        lines.append(f'| {label}／{r.strategy} | {pct(r.median_24d_return)} | {pct(r.p25_24d_return)} | {pct(r.worst_24d_return)} | {r.passed}/{r.attempted} |')
    lines += ['', '資料截止 2026-09-23；9 月月初窗口尚不足 24 個交易日，未納入完整月份。滾動窗口只作近期壓力測試，重疊窗口不視為獨立樣本。[冷啟動明細](24d_tuning_cold_start.csv)包含第 1／3／5 日持股與投入比例，以及首次形成有效投資組合的天數；失敗與缺日不補值。','',
        '## 固定參數與限制','',
        '| 方法 | 參數識別碼 |','|---|---|']
    for label,candidate in selection['diagnostic_method_leaders'].items():
        lines.append(f'| {label} 診斷代表 | [`{candidate}`](../outputs/24d_tuning/configs/{candidate}.json) |')
    lines += ['',f'凍結時間：`{selection["frozen_at"]}`。[本輪凍結紀錄](../outputs/24d_tuning/final_selection.json)保存切分、候選數、選參輸入與程式 SHA256（`code_commit` 是開跑前 HEAD，新增實作以 SHA256 識別）；[本輪設定](../outputs/24d_tuning/competition_24d_candidate.json)保存參數。若無合格替代者，[canonical 設定](../configs/competition_24d_final.json)保留既有基準原位元組，不把診斷代表冒充正式候選。','',
        '2023–2024 與近期資料已被上一輪研究觀察過。本輪不讓它們回流選參，但不能稱作首次未見 holdout；原基準也曾由後期資料選出。2026 白名單回套歷史存在存活與成分前視偏誤。','',
        'Active Share、官方結算、公司行動與平台收件仍缺證據。Yahoo 修訂及日線開盤成交近似值也不等於官方日成交均價，10 億元委託沒有市場衝擊模型。因此即使帳務稽核通過，也不能保證正式比賽每天完全合規。失敗帳本用於診斷，不輸出可提交 D-Plan。','',
        '## 重現','', '新入口以 `tuning_protocol` 定義搜尋預算。共用設定繼承的 `local_refinement_limit`、`refinement_requires_development_pass_rate` 與 `walk_forward_*` 未被本入口使用；實際每階段組數以上表與收據為準。','', '```bash','python scripts/tune_24d.py --workers 4 --output outputs/24d_tuning_replay',
        'python scripts/verify_24d_tuning.py --rebuild','python scripts/report_24d_tuning.py --verify','```','',
        f'本輪驗證涵蓋 {verified["groups"]} 組帳本、{verified["attempts"]} 次窗口嘗試。詳見[驗證](../outputs/24d_tuning/verification.json)、[搜尋設定](../config/24d_tuning_study.json)及[輸出 SHA256](../outputs/24d_tuning/result_manifest.json)。上一輪報告與帳本保留，[舊交付檔案](../outputs/24d_tuning/previous_delivery/archive.json)可核對 README 更新前的位元組。','']
    failures=['## 驗證期的失敗證據','',
        '| 策略 | 原始規則超限日 | 警告日 | 無可行計畫日 | 成交包絡超界 | 零股殘餘窗口 |',
        '|---|---:|---:|---:|---:|---:|']
    validation=csv(out/'validation.csv')
    for label,candidate in {'x0352 baseline':'x0352_daily_baseline',**selection['diagnostic_method_leaders']}.items():
        g=validation[validation.candidate_id.eq(candidate)]
        failures.append(f'| {label} | {int(g.raw_rule_breach_days.sum())} | {int(g.simulated_warning_days.sum())} | {int(g.no_valid_plan_days.sum())} | {int(g.execution_price_bound_breaches.sum())} | {int(g.failure_reasons.fillna("").str.contains("FAIL_ROUND_LOT").sum())} |')
    failures += ['', '以上是各窗口累計，重疊日期可能重複；各類可同時發生，不能相加成獨立違規數。±10% 成交包絡是較嚴格的研究門檻，不是官方承諾的成交範圍。零股殘餘可能由公司行動產生，不等於下了零股委託；其官方處理契約未明，本地採保守不通過。進場池不足另保留在各階段逐窗口的 `episode_status` 與 `failure_reasons`。','']
    screens=pd.concat([csv(out/('screen_'+p+'.csv')) for p in 'ABCDEFG']).drop_duplicates(['candidate_id','episode_id'])
    failures += ['共同篩選中最難的三個窗口：','', '| 窗口起點 | 通過參數／全部 |','|---|---:|']
    for episode,g in screens.groupby('episode_id',sort=True):
        if episode in ['monthly_2011-08-01','monthly_2015-08-03','monthly_2018-11-01']:
            failures.append(f'| {episode.removeprefix("monthly_")} | {int(g.measured_pass.sum())}/{len(g)} |')
    failures += ['', '這只證明本輪已測設定未跨過門檻，不能推論所有可能參數都不合格。下一步應先釐清失敗窗口的公司行動／持股可行性，再考慮擴大報酬搜尋。','']
    index=lines.index('## 近期與冷啟動');lines[index:index]=failures
    return '\n'.join(lines),table,cold


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=ROOT/'outputs/24d_tuning');p.add_argument('--verify',action='store_true');a=p.parse_args()
    prose,table,cold=render(a.output)
    targets={ROOT/'reports/24d_tuning.md':prose,ROOT/'reports/24d_tuning_comparison.csv':table.to_csv(index=False),ROOT/'reports/24d_tuning_cold_start.csv':cold.to_csv(index=False)}
    for path,content in targets.items():
        if a.verify:
            if path.read_text()!=content:raise ValueError('Report mismatch: '+str(path))
        else:path.write_text(content)
    print(json.dumps(dict(status='PASS',mode='verify' if a.verify else 'write',files={str(p.relative_to(ROOT)):file_hash(p) for p in targets}),indent=2))
if __name__=='__main__':main()
