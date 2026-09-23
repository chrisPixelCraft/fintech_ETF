"""Build a source-linked second-round report; no results invented for blocked tracks."""
from pathlib import Path
import html,json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

OUT=ROOT/'outputs/tuning_report_2nd_try';REPORT=ROOT/'reports/tuning_report_2nd_try'
LABELS={'historical_pit':'主要歷史回測：2024 年底股票池','official_ex_post':'正式 150 檔事後模擬：成分股前視偏誤'}
COLORS={'A':'#0b7285','B':'#1971c2','C':'#e67700','v1_matched':'#868e96','0050':'#6741d9'}

def table(frame):
    rows=[list(frame.columns), *frame.fillna('').astype(str).values.tolist()]
    clean=lambda cells: '| '+ ' | '.join(str(c).replace('|','\\|').replace('\n',' ') for c in cells)+' |'
    return '\n'.join([clean(rows[0]),clean(['---']*len(rows[0])),*[clean(r) for r in rows[1:]]])
def pct(x):return f'{x:.2%}'

def display_summary(frame):
    return pd.DataFrame([{'策略':r.model,'參數':r.candidate_id,'總報酬':pct(r.economic_total_return),'含應收MDD':pct(r.economic_max_drawdown),'帳面MDD':pct(r.max_drawdown),
        'Sharpe':f'{r.economic_sharpe_zero_rf:.3f}','平均股票曝險':pct(r.average_stock_exposure),
        '雙向換手':f'{r.turnover_two_way:.2f}×','成本NTD百萬':f'{r.costs/1e6:.2f}',
        '硬性違規日':int(r.measured_hard_breach_days) if r.model!='0050' else '不適用',
        '不可行執行日':int(r.infeasible_executed_days),'成交筆數':int(r.trade_count),'交易天數':int(r.trade_days)} for r in frame.itertuples()])

def figures(folder,comparison,monthly):
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'figure.facecolor':'white'})
    track_title='Historical reconstructed pool' if folder.name=='historical_pit' else 'Official 2026 pool (membership lookahead)'
    nav=pd.read_csv(folder/'nav_comparison.csv',index_col='date');nav.index=pd.to_datetime(nav.index)
    fig,axes=plt.subplots(2,1,figsize=(12,7),sharex=True,gridspec_kw={'height_ratios':[2,1]})
    for name in nav:
        axes[0].plot(nav.index,nav[name]/1e9,label=name,color=COLORS.get(name),lw=1.7)
        axes[1].plot(nav.index,(nav[name]/nav[name].cummax()-1)*100,color=COLORS.get(name),lw=1.3)
    axes[0].set_ylabel('Economic NAV / initial capital');axes[0].legend(ncol=5,frameon=False)
    axes[1].set_ylabel('Drawdown (%)');axes[1].grid(alpha=.15);axes[0].grid(alpha=.15)
    fig.suptitle(track_title+' | 2025-01-02 to 2026-09-21\nNet of costs; development replay; September partial',x=.08,ha='left',fontsize=14)
    fig.tight_layout();fig.savefig(folder/'nav_drawdown.png',dpi=165);plt.close(fig)
    models=comparison.model.tolist();fig,axes=plt.subplots(1,2,figsize=(12,8))
    for ax,metric,title,cmap in [(axes[0],'economic_return','Monthly economic return (%)','RdYlGn'),(axes[1],'economic_max_drawdown','Within-month economic MDD (%)','YlOrRd')]:
        pivot=monthly.pivot(index='month',columns='model',values=metric).reindex(columns=models)*100
        vals=pivot.to_numpy();lim=max(abs(vals.min()),abs(vals.max()))
        im=ax.imshow(vals,cmap=cmap,aspect='auto',vmin=-lim if metric=='economic_return' else 0,vmax=lim)
        ax.set_xticks(range(len(models)),models);ax.set_yticks(range(len(pivot)),pivot.index);ax.set_title(title)
        for i in range(len(pivot)):
            for j in range(len(models)):ax.text(j,i,f'{vals[i,j]:.1f}',ha='center',va='center',fontsize=8,color='black')
        fig.colorbar(im,ax=ax,shrink=.6)
    fig.suptitle(track_title+' | 2025-01 to 2026-09-21\nNet of costs; development replay; September partial',fontsize=12)
    fig.tight_layout();fig.savefig(folder/'monthly_return_mdd.png',dpi=165);plt.close(fig)
    fig,ax=plt.subplots(figsize=(12,3.3));ax.axis('off')
    rows=[[r.model,r.candidate_id,pct(r.economic_total_return),pct(r.economic_max_drawdown),str(int(r.measured_hard_breach_days)) if r.model!='0050' else 'N/A',str(int(r.trade_count)),f'{r.turnover_two_way:.2f}x'] for r in comparison.itertuples()]
    artist=ax.table(cellText=rows,colLabels=['Model','Candidate','Net return','Economic MDD','Hard days','Trades','Turnover'],loc='center',cellLoc='center')
    artist.auto_set_font_size(False);artist.set_fontsize(10);artist.scale(1,1.9)
    for (r,c),cell in artist.get_celld().items():
        cell.set_edgecolor('white');cell.set_facecolor('#123047' if r==0 else '#edf3f8' if r%2 else '#f8fafc')
        if r==0:cell.set_text_props(color='white',weight='bold')
    ax.set_title(track_title+' | 2025-01-02 to 2026-09-21\nNet of costs; development replay; September partial',loc='left',pad=15)
    fig.tight_layout();fig.savefig(folder/'comparison_table.png',dpi=165);plt.close(fig)


def run():
    from scripts.run_v2_tuning import sha
    pins=json.loads((OUT/'pinned_entrypoints.json').read_text())
    for relative,digest in pins['hashes'].items():
        if sha(ROOT/relative)!=digest:raise ValueError('Pinned entrypoint changed: '+relative)
    for track in LABELS:
        path=OUT/track/'manifest.json'
        if not path.exists() or not json.loads(path.read_text()).get('outputs_complete'):
            if not (OUT/track/'blocked.json').exists():raise ValueError('Final report requires completed or explicitly blocked track: '+track)
    primary=OUT/'historical_pit/manifest.json'
    if not primary.exists() or not json.loads(primary.read_text()).get('outputs_complete'):
        raise ValueError('Primary historical track must be complete for this final report')
    completed={track for track in LABELS if (OUT/track/'manifest.json').exists() and json.loads((OUT/track/'manifest.json').read_text()).get('outputs_complete')}
    if set(pins['selections']) != completed:raise ValueError('Pinned universe coverage differs from completed audited tracks')
    md=['# tuning_report_2nd_try｜第二輪規則優先調參',
        '正式搜尋為每個股票池 A/B/C 各 64 組，兩份共 **384 次**。另外 24 次工程 pilot 已隔離保存，不混入正式候選表，也不當成獨立驗證樣本。部分板塊／隔夜維度對 A 或 B 不生效，64 組完整設定不代表 64 條不同交易軌跡。',
        '回測期間 **2025-01-02–2026-09-21**，417 個完整交易日，初始本金 **10 億 NTD**。2026 年 9 月只計至 9 月 21 日。所有摘要以扣手續費與賣出稅、含應收股息的 economic NAV 計算。',
        '**最佳的定義：先要求全期零已觀測硬性違規，再從合格候選中選總報酬最高者。Active Share 缺乏歷史資料，仍為 UNKNOWN，三支程式均禁止正式送單。**',
        '比賽帳面 NAV 在期末才加入股息，economic NAV 在除息時列入應收股息。本回測沿用整段研究期間末結算，沒有每個自然月重設一場比賽。兩者全期末值相同，但月報酬、回撤與 Sharpe 可能不同；下方主圖與主月表採 economic NAV，另提供帳面回撤及帳面月報，不能把研究診斷直接稱為官方賽事成績。',
        '主要歷史股票池的 known-at 採 2024-12-31 19:30 的研究假設，並非逐筆證明的原始發布時間；因此仍保留重建資料限制。',
        '兩個股票池分開選參與報告。主要歷史回測沿用當時股票池；正式名單事後模擬有成分股前視偏誤。整段歷史已用於開發，選出的績效不是未見測試結果。',
        '防止交易時序前視：以決策時點已知資料產生訊號與固定整張股數，下一交易日的官方均價只用於成交結算，不能反過來決定當天股數。指標窗口使用已完成資料；隔夜訊號遵守可用時間。這不消除全期選參的事後偏誤，也不消除正式名單事後情境的成分股前視。',
        '[實驗規格](../docs/tuning_2nd_protocol.md) · [資料與規則稽核](../docs/tuning_2nd_data_readiness.md) · [全部候選合併總表 CSV](../outputs/tuning_report_2nd_try/all_candidate_results.csv)']
    html_parts=[];all_rows=[]
    for track,label in LABELS.items():
        folder=OUT/track
        if not (folder/'manifest.json').exists() or not json.loads((folder/'manifest.json').read_text()).get('outputs_complete'):
            block=json.loads((folder/'blocked.json').read_text()) if (folder/'blocked.json').exists() else {'reason':'尚未完成，不提供部分績效'}
            md += ['## '+label,'**BLOCKED**：'+str(block.get('reason',block))];continue
        audit=json.loads((folder/'audit.json').read_text())
        if audit.get('status')!='PASS':raise ValueError('Unverified artifacts cannot enter report: '+track)
        for relative,digest in audit['artifact_hashes'].items():
            if sha(folder/relative)!=digest:raise ValueError('Audited artifact changed: '+relative)
        trials=pd.read_csv(folder/'trial_summary.csv');comparison=pd.read_csv(folder/'comparison.csv');monthly=pd.read_csv(folder/'monthly_comparison.csv')
        selected=json.loads((folder/'selection.json').read_text())
        if pins['selections'][track] != selected:raise ValueError('Pinned selection differs from audited selection: '+track)
        replay_path=OUT/'entrypoint_replay'/track/'replay_audit.json'
        replay=json.loads(replay_path.read_text())
        if replay.get('status')!='PASS' or replay['source_audit_sha256']!=sha(folder/'audit.json'):
            raise ValueError('Public entrypoint replay not verified: '+track)
        for relative,digest in replay['code_hashes'].items():
            if sha(ROOT/relative)!=digest:raise ValueError('Replayed entrypoint dependency changed: '+relative)
        study=json.loads((folder/'manifest.json').read_text())['study']
        figures(folder,comparison,monthly)
        bookrows=[]
        for name in comparison.model:
            eq=pd.read_csv(folder/'final'/name/'equity.csv');previous=1e9
            for month,g in eq.groupby(pd.to_datetime(eq.date).dt.to_period('M')):
                values=np.r_[previous,g.nav.to_numpy(float)]
                bookrows.append(dict(model=name,month=str(month),book_return=values[-1]/values[0]-1,
                    book_max_drawdown=float(-(values/np.maximum.accumulate(values)-1).min())))
                previous=values[-1]
        book=pd.DataFrame(bookrows);book.to_csv(folder/'book_monthly_comparison.csv',index=False)
        all_rows.append(trials.assign(universe_track=track))
        md += ['## '+label,
            f"完成 **{len(trials)}/192** 次正式候選回測；固定壓力情境現金再投入觸發值 **{study['cash_guard_ratio']:.0%}**。股票目標現金 0% 不代表實際無現金，整張與固定股數成交必然留下餘額。",
            table(display_summary(comparison)),
            f'![五方比較](../outputs/tuning_report_2nd_try/{track}/comparison_table.png)',
            f'![淨值與最大回撤](../outputs/tuning_report_2nd_try/{track}/nav_drawdown.png)']
        arow=comparison[comparison.model.eq('A')]
        if len(arow):
            ar=arow.iloc[0]
            md += [f"A 仍在 {int(ar.trade_days)}/417 天成交，共 {int(ar.trade_count):,} 筆。零量測硬性違規並不代表低頻或低換手，現金與權重修正仍可能頻繁交易。"]
        checks=[]
        for r in comparison[comparison.model.isin(['A','B','C'])].itertuples():
            checks.append({'策略':r.model,'持股數範圍':f'{int(r.holdings_min)}–{int(r.holdings_max)}',
                '現金比例範圍':pct(r.cash_ratio_min)+'–'+pct(r.cash_ratio_max),
                '負現金日':int(r.negative_cash_days),'池外持股日':int(r.whitelist_breach_days),
                '主動權重超限日':int(r.active_cap_breach_days),'被動超限逾期日':int(r.overdue_passive_cap_days),'Active Share':'UNKNOWN'})
        md += ['**已觀測規則檢查**（池外檢查只對本情境的股票池；歷史池並非正式競賽名單。）',table(pd.DataFrame(checks))]
        elig=[];paramrows=[]
        for strategy in ['A','B','C']:
            pool=trials[trials.strategy.eq(strategy)];win=selected[strategy]
            elig.append({'策略':strategy,'已完成':len(pool),'零硬性違規候選':int(pool.measured_hard_breach_days.eq(0).sum()),'選擇':win['candidate_id'] or 'NO_ELIGIBLE_WINNER','Active Share':'UNKNOWN'})
            if win['candidate_id']:
                cfg=json.loads((folder/'final'/strategy/'config.json').read_text())
                paramrows.append({'策略':strategy,'候選':win['candidate_id'],**cfg['tuning_params']})
        if all(selected[x]['candidate_id'] for x in ['A','B']):
            ta=pd.read_csv(folder/'final/A/trades.csv');tb=pd.read_csv(folder/'final/B/trades.csv')
            if ta[['date','symbol','shares','price']].equals(tb[['date','symbol','shares','price']]):
                md += ['這份選中 A/B 的逐筆交易完全相同，沒有證據顯示板塊門控增加了績效。']
            else:
                md += ['A/B 使用各自選出的完整參數組，差異不能單獨歸因於板塊門控；若要辨識板塊的獨立效果，需要其餘參數固定的消融比較。']
        if selected['C']['candidate_id']:
            cp=next(r for r in paramrows if r['策略']=='C')
            if cp['c_alpha']==0:
                md += ['C 選中的 c_alpha=0，表示這個最佳歷史組合實際關閉隔夜加權；不能據此宣稱美股訊號有貢獻。']
        md += [table(pd.DataFrame(elig)), '**選中參數完整表**（保存完整候選 tuple；A 的 sector_*、c_alpha 與 B 的 c_alpha 不生效。）',table(pd.DataFrame(paramrows)) if paramrows else '沒有可選候選。']
        preset_cfg=json.loads((ROOT/'config/v2_tuning_study.json').read_text())
        resolved=[]
        for pr in paramrows:
            resolved.append({'策略':pr['策略'],'報酬窗（日）':' / '.join(map(str,preset_cfg['presets']['returns'][pr['returns']])),
                'EMA（日）':' / '.join(map(str,preset_cfg['presets']['ema'][pr['ema']])),
                'MACD':' / '.join(map(str,preset_cfg['presets']['macd'][pr['macd']])),
                '4H': '只檢查資料覆蓋' if pr['four_hour_mode']=='coverage_only' else '方向確認',
                '換股分差':pr['replacement_margin'],'每日普通換股上限':pr['max_replacements_per_day']})
        # Keep behavioural differences visible: a best-return grid point can relax the original design.
        if any(pr['replacement_margin']==0 or pr['four_hour_mode']=='coverage_only' for pr in paramrows):
            md += ['**策略差異：** 換股分差為 0 的組合不要求新股明顯優於舊股；coverage_only 只檢查 4H 資料覆蓋，不要求 4H 方向確認。這些是既定搜尋空間內的最高報酬選擇，不能稱為原始低換手、嚴格 4H 確認版本。']
        md += ['**實際生效的指標窗口與換股條件**',table(pd.DataFrame(resolved)),
            '普通換股上限不包含強制退出、現金與權重修正。base 評分權重依序為短報酬／長報酬／量能／MACD／趨勢／長趨勢：40%／35%／10%／5%／5%／5%；slow 則為 15%／60%／10%／5%／5%／5%，fast 為 60%／15%／10%／5%／5%／5%。coverage_only 仍需 50 根已觀測 4H 棒，但不檢查其偏多方向。']
        md += ['**月報：每格依序為月報酬／月內最大回撤。** 月回撤包含前月最後淨值作為月初基準，不等於截至該月的全期回撤。']
        models=comparison.model.tolist();mt=[]
        for month,g in monthly.groupby('month'):
            row={'月份':month}
            for model in models:
                rr=g[g.model.eq(model)].iloc[0];row[model]=pct(rr.economic_return)+' / '+pct(rr.economic_max_drawdown)
            mt.append(row)
        md += [table(pd.DataFrame(mt)),f'![月報酬與月內回撤](../outputs/tuning_report_2nd_try/{track}/monthly_return_mdd.png)',
            f'[比賽帳面月報 CSV](../outputs/tuning_report_2nd_try/{track}/book_monthly_comparison.csv) · [192 組完整結果 CSV](../outputs/tuning_report_2nd_try/{track}/trial_summary.csv) · [月報 CSV](../outputs/tuning_report_2nd_try/{track}/monthly_comparison.csv) · [獨立稽核](../outputs/tuning_report_2nd_try/{track}/audit.json) · [程式入口重播驗證](../outputs/tuning_report_2nd_try/entrypoint_replay/{track}/replay_audit.json) · [來源雜湊](../outputs/tuning_report_2nd_try/{track}/manifest.json)']
        bt=[]
        for month,g in book.groupby('month'):
            row={'月份':month}
            for name in comparison.model:
                r=g[g.model.eq(name)].iloc[0];row[name]=pct(r.book_return)+' / '+pct(r.book_max_drawdown)
            bt.append(row)
        html_parts.append('<details><summary>'+html.escape(label)+'：比賽帳面 NAV 月報酬／月內回撤</summary><div class="scroll">'+pd.DataFrame(bt).to_html(index=False,border=0)+'</div></details>')
        # Full candidate tuples are reviewable in an expandable, searchable HTML table.
        cols=['strategy','candidate_id','economic_total_return','economic_max_drawdown','measured_hard_breach_days','infeasible_executed_days','cash_ratio_max','trade_count','turnover_two_way',*study['candidates'][0]['params']]
        full=trials[cols].copy()
        for c in ['economic_total_return','economic_max_drawdown','cash_ratio_max']:full[c]=full[c].map(pct)
        html_parts.append('<details><summary>'+html.escape(label)+'：全部 192 組參數與結果</summary><input class="filter" placeholder="篩選候選，例如 p028 或 strict"><div class="scroll">'+full.to_html(index=False,classes='trials',border=0)+'</div></details>')
    md += ['## 這輪改了什麼',
        '保留原來 A 的動能／趨勢、B 的板塊篩選、C 的板塊加隔夜訊號。執行器允許保留股因現金規則補倉；換股賣出前檢查现金及持股估值的價格壓力情境，持股已到最低檔數時先補位再退出。必要時允許不同股票同日買賣，買單最高價格加費用須由既有現金支付，不依賴同日賣款。市場普遍轉弱但規則要求至少持有 20 檔時，暫留排名較高的既有持股；這是規則優先的例外，並非所有趨勢轉弱都能立即清空。普通換股仍受原本的排名門檻及每日上限限制。',
        '兩份情境選中的 A／B／C 最大回撤都比同情境 v1 更深，較高報酬不代表風險下降。v1 保留舊版執行器及固定參數，沒有得到這輪調參機會；因此五方表是實用版本對照，不是只改一個選股因子的因果實驗。0050 是同預算、含成本的買入持有基準，保留初次固定股數估價緩衝造成的現金，並非百分之百投入的 ETF 總報酬指數。',
        '## 尚未通過的正式比賽項目',
        'Active Share 必須對當日可知的 ETF 持倉資料驗證；固定 30 檔名單中的 ETF 在 2025 年初尚未全部存在，不能以今天的持倉回填。已觀測現金／檔數／權重等通過不等於全規則認證。正式送單需補足白名單當期檢查、Active Share、報告與訂單一致性及成功提交證據。配股產生的零股殘額在回測中保留、不虛構零股成交；它與官方 target_shares／整張訂單欄位如何銜接，仍缺正式殘股處理說明。',
        '不可行執行日表示原定換股／補倉計畫未能同時滿足約束，當日可能維持既有持倉。零已觀測硬性違規不表示每天都能執行策略意圖；這個操作限制必須與績效一起評估。',
        '價格壓力測試有明確假設，停牌、公司事件、價格超出界限仍可能使計畫失效。非零違規候選只保留研究診斷；舊帳本未模擬違規日撤單與累積淘汰，其收益不能視為正式賽事成績。',
        '資料仍有範圍限制：原行情的四筆 TWSE 公司事件缺乏獨立分解明細；新增 33 家公司的板塊／法人特徵不在原凍結資料中，沿用 UNKNOWN 的中性回退。小時線与日線開盤有 15 筆差距超過 0.5%（最大 1.415%），未見系統性單位倍率錯誤。完整来源、公司事件與假設見資料準備度報告。',
        '## 使用與重現',
        '三支入口為 `v2_A_best.py`、`v2_B_best.py`、`v2_C_best.py`。預設使用主要歷史股票池的固定選中參數；`--track official_ex_post` 使用另一份獨立選擇。`--show-config` 可檢查完整參數；若表中為 NO_ELIGIBLE_WINNER，對應入口直接拒絕執行。正式交易提交始終被阻擋。',
        '```bash\npython3 v2_A_best.py --show-config\npython3 v2_B_best.py --output outputs/my_v2_B_replay\npython3 v2_C_best.py --track official_ex_post --show-config\n```',
        '表格中的「最佳」僅限這一輪已列舉候選與已使用的歷史期間；不能推定未来最佳。']
    if not all_rows:raise ValueError('No complete verified track; cannot publish a results report')
    text='\n\n'.join(md)+'\n';REPORT.with_suffix('.md').write_text(text)
    pd.concat(all_rows,ignore_index=True).to_csv(OUT/'all_candidate_results.csv',index=False)
    import markdown
    body=markdown.markdown(text,extensions=['tables','fenced_code'])
    style='''body{font:16px/1.7 -apple-system,BlinkMacSystemFont,"Noto Sans TC",sans-serif;color:#173047;background:#f4f7fa;margin:0}main{max-width:1240px;margin:auto;padding:40px 32px;background:white}h1{font-size:30px}h2{margin-top:48px;border-bottom:2px solid #dce6ee;padding-bottom:8px}table{border-collapse:collapse;font-size:13px;white-space:nowrap}td,th{padding:9px 12px;border-bottom:1px solid #dde5eb;text-align:right}th{background:#173047;color:white}tr:nth-child(even){background:#f1f5f8}img{width:100%;height:auto}a{color:#006a91}code{background:#edf2f6;padding:2px 4px}.scroll{overflow:auto;margin:20px 0}details{padding:18px;border:1px solid #dce6ee;margin:20px 0}summary{cursor:pointer;font-weight:650}input{font:inherit;margin:16px 0;padding:8px}pre{overflow:auto;background:#edf2f6;padding:16px}'''
    # Make every wide prose table scroll without requiring a browser library.
    body=body.replace('<table>','<div class="scroll"><table>').replace('</table>','</table></div>')
    script="document.querySelectorAll('.filter').forEach(i=>i.addEventListener('input',()=>{const q=i.value.toLowerCase();i.parentNode.querySelectorAll('tbody tr').forEach(r=>r.hidden=!r.textContent.toLowerCase().includes(q))}));"
    REPORT.with_suffix('.html').write_text('<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>tuning_report_2nd_try</title><style>'+style+'</style><main>'+body+'<h2>完整調參表</h2>'+''.join(html_parts)+'</main><script>'+script+'</script></html>')
    print(REPORT.with_suffix('.html'))

if __name__=='__main__':run()
