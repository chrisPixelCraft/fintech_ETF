#!/usr/bin/env python3
"""Render a verified feasibility study without inventing unrun period returns."""
from pathlib import Path
import argparse
import json
import sys
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_24d import file_hash


def read(path):return json.loads(path.read_text())
def csv(path):return pd.read_csv(path,float_precision='round_trip')


def render(out):
    from scripts.verify_24d_round4 import verify
    verified=verify(out)
    decision=read(out/'final_selection.json');candidates=read(out/'candidates.json')
    members=csv(out/'phase_memberships.csv');hard=csv(out/'bottleneck.csv')
    rows=members.merge(hard,on='candidate_id',validate='one_to_one');records=[]
    names={'24d_tuning':'第一輪開發代表鄰域','24d_expansion':'第二輪開發代表鄰域','24d_round3':'第三輪開發代表鄰域'}
    odd_total=0
    for family,group in rows.groupby('family',sort=False):
        odd=0
        for candidate in group.candidate_id:
            folder=out/'ledgers/bottleneck'/candidate
            trades=pd.read_parquet(folder/'trades.parquet',columns=['shares'])
            holdings=pd.read_parquet(folder/'holdings.parquet',columns=['shares'])
            if not trades.shares.mod(1000).eq(0).all():raise ValueError('Non-round-lot execution '+candidate)
            odd+=int(holdings.shares.mod(1000).ne(0).any())
        odd_total+=odd
        records.append(dict(family=family,parent_id=group.parent_id.iloc[0],attempted=len(group),completed=int(group.complete_period.sum()),
            no_raw_breach=int(group.raw_rule_breach_days.eq(0).sum()),measured_pass=int(group.measured_pass.sum()),odd_residual=odd))
    summary=pd.DataFrame(records)
    lines=['# v3：第四輪可行性搜尋','',f'新增 **{len(candidates)} 組參數**，排除前三輪 1,087 組，四輪合計 **{1087+len(candidates):,} 組**。凍結結果為 **`{decision["status"]}`**，正式提交仍為 **`BLOCK_SUBMISSION`**。','',
        '這輪測試三個開發期代表的訊號與換股參數鄰域；每個來源128組。先重現154個基準窗口，再測必要開發窗口 **2015-08-03 起的24個交易日**。若此窗口失敗，就不可能達到全部開發窗口100%通過的原採用門檻。','',
        '## 實際結果','', '| 參數來源 | 新組數 | 完成24日 | 無原始超限 | 全項通過 | 零股持倉 |','|---|---:|---:|---:|---:|---:|']
    for row in summary.itertuples():lines.append(f'| {names[row.family]} | {row.attempted} | {row.completed} | {row.no_raw_breach} | {row.measured_pass} | {row.odd_residual} |')
    lines+=['','無原始超限不等於通過全部門檻；零股持倉欄與其他欄可能重疊，不能相加。[彙整 CSV](24d_round4_summary.csv)與[逐窗口資料](../outputs/24d_round4/bottleneck.csv)可核對全部嘗試。','',
        '| 原因組合 | 新參數組數 |','|---|---:|']
    reason_names={'FAIL_ROUND_LOT':'僅零股持倉門檻','FAIL_CASH;FAIL_HOLDING_COUNT':'現金、持股數','FAIL_CASH;FAIL_HOLDING_COUNT;FAIL_ROUND_LOT':'現金、持股數、零股持倉','':'無失敗原因'}
    for reason,count in rows.failure_reasons.fillna('').value_counts().sort_index().items():lines.append(f'| {reason_names.get(reason,reason)} | {count} |')
    lines+=['',f'所有新組合的成交股數均為1,000股倍數；{odd_total}組出現零股持倉。公司行動後的零股持倉不等於送出零股委託；官方處理契約仍不明，本輪保留既有保守門檻，不能稱作已確認的官方違規。','']
    if decision['downstream_evaluation']=='NOT_RUN':
        if bool(rows.measured_pass.any()):raise ValueError('Pruned a passing candidate')
        lines+=['## 結論與未執行項目','',f'新增{len(candidates)}組全數未通過必要開發窗口，依[規格的 early pruning](../docs/v3_tuning.md)提前停止。本轮沒有可採用的新候選，保留原設定；這是已完成的可行性篩選，不是完整期間的報酬排名。','',
            '新參數的完整開發期、驗證期、2023–2024歷史評估、近期與季節窗口均 **NOT_RUN**；不替它們填入中位數、年度或累積報酬。154個基準重現包含既有開發／驗證資料，只用於確認計算口徑。','',
            '最近一次完整期間的比較仍見[第三輪報告](24d_round3.md)及[比較 CSV](24d_round3_comparison.csv)，不是本輪新參數的成績。單一必要窗口足以否定100%通過資格，卻不足以推論整體報酬，也不代表全部參數空間均無解。','']
    else:
        from scripts.report_24d_round3 import comparison,result_tables
        labels={'基準':'x0352_daily_baseline','前輪開發代表':decision['prior_leader_id'],'本輪開發代表':decision['diagnostic_leader_id']}
        if decision['candidate_id'] and decision['candidate_id'] not in labels.values():labels['研究候選']=decision['candidate_id']
        table,common=comparison(out,labels)
        lines+=['## 完整期間檢查','','只統計完整且通過窗口的報酬，失敗保留在全部嘗試分母；不同策略通過樣本可能不同，不能只看條件式報酬判勝。','']+result_tables(table)
    lines+=['## 搜尋邊界與限制','','只調整 `return_short`、`return_long`、`momentum_weight`、`long_return_fraction`、`max_replacements_per_day`、`replacement_margin`，其餘參數在各來源內固定。Seed為2409202604，範圍遵守原規格。三個來源各自只按開發期排名選出，不使用驗證或近期報酬決定新參數。','',
        '這是多參數共同變動的局部抽樣，不能歸因為單一參數效果，也不是窮舉。開發2010–2018、驗證2019–2022的邊界不變；既有驗證與後期結果已在前輪看過，不能再宣稱首次未見 holdout。2026白名單回套歷史及後期選出的基準參數亦有前視／選擇偏誤。','',
        'Yahoo快照截至2026-09-23；每窗口從10億元零持股開始，D−1決策、次日開盤價近似成交，雙邊手續費0.1425%及賣出稅0.3%。未建模市場衝擊；Active Share、官方均價結算、公司行動與平台收件證據仍缺。本地稽核不是正式合規認證。309.16%的固定v2長期結果不變，不能與單一24日窗口直接比較。','',
        '後續優先釐清公司行動及零股處理契約，再決定是否值得擴大搜尋；不能靠移除門檻製造合格候選。','',
        '## 證據與重現','',f'已驗證{verified["groups"]}組帳本、{verified["attempts"]}次窗口嘗試。查看[設定](../config/24d_round4_study.json)、[凍結紀錄](../outputs/24d_round4/final_selection.json)、[驗證結果](../outputs/24d_round4/verification.json)及[結果雜湊](../outputs/24d_round4/result_manifest.json)。','',
        '```bash','python scripts/verify_24d_round4.py --rebuild','python scripts/report_24d_round4.py --verify','```','',
        '另建輸出重跑；同一命令可接續未完成組別：','','```bash','python scripts/expand_24d_round4.py --workers 4 --output outputs/24d_round4_replay','python scripts/verify_24d_round4.py --output outputs/24d_round4_replay --rebuild','```','']
    return '\n'.join(lines).replace('本轮','本輪'),summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=ROOT/'outputs/24d_round4');p.add_argument('--verify',action='store_true');a=p.parse_args()
    prose,summary=render(a.output)
    targets={ROOT/'reports/24d_round4.md':prose,ROOT/'reports/24d_round4_summary.csv':summary.to_csv(index=False)}
    for path,content in targets.items():
        if a.verify:
            if path.read_text()!=content:raise ValueError('Report mismatch: '+str(path))
        else:path.write_text(content)
    print(json.dumps(dict(status='PASS',mode='verify' if a.verify else 'write',files={str(p.relative_to(ROOT)):file_hash(p) for p in targets}),indent=2))
if __name__=='__main__':main()
