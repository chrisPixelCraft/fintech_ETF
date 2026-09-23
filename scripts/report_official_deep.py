"""Verified three-way comparison, complete monthly tables and tuning diagnostics."""
from pathlib import Path
import json, sys, hashlib
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import markdown
from scripts.run_v2_tuning import sha, dump

OUT=ROOT/'outputs/full_tuned_v2'; ASSETS=ROOT/'reports/full_tuned_v2_assets'
MODELS=['full_tuned_v2','v1_matched','0050']
LABELS={'full_tuned_v2':'Full tuned v2','v1_matched':'v1','0050':'0050'}
COLORS={'full_tuned_v2':'#007f7f','v1_matched':'#d17825','0050':'#7a75ce'}
TRACKS={'official_ex_post':'正式 150 檔事後模擬','historical_pit':'歷史股票池敏感度'}


def trusted():
    audit=json.loads((OUT/'audit.json').read_text())
    if audit['status']!='PASS':raise ValueError('Independent audit required')
    if audit['auditor_sha256']!=sha(ROOT/'scripts/audit_official_deep.py'):
        raise ValueError('Auditor changed')
    for rel,h in audit['artifact_hashes'].items():
        if sha(OUT/rel)!=h:raise ValueError('Audited artifact changed '+rel)
    for rel,h in audit['input_hashes'].items():
        if sha(ROOT/rel)!=h:raise ValueError('Frozen dependency changed '+rel)
    return audit


def pct(x):return f'{float(x)*100:.2f}%'


def table(frame):
    """Render the small report tables without an optional pandas dependency."""
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', '<br>')
    rows = [list(frame.columns), ['---'] * len(frame.columns)]
    rows.extend(frame.itertuples(index=False, name=None))
    return '\n'.join('| ' + ' | '.join(cell(v) for v in row) + ' |' for row in rows)


def main():
    audit=trusted();ASSETS.mkdir(parents=True,exist_ok=True)
    comp=pd.read_csv(OUT/'comparison.csv');monthly=pd.read_csv(OUT/'monthly.csv')
    trials=pd.read_csv(OUT/'trials.csv');selection=json.loads((OUT/'selection.json').read_text())
    study=json.loads((OUT/'manifest.json').read_text())['study']
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':11,'axes.spines.top':False,
        'axes.spines.right':False,'axes.grid':True,'grid.alpha':.15,'figure.facecolor':'white','savefig.facecolor':'white'})
    presentation=dict(selection=selection,study=study,comparison=comp.astype(object).where(pd.notna(comp),None).to_dict('records'),tracks={})
    sections=[]
    for track,title in TRACKS.items():
        nav=pd.read_csv(OUT/track/'nav.csv',index_col='date');nav.index=pd.to_datetime(nav.index)
        nav=pd.concat([pd.DataFrame({m:[1e9] for m in MODELS},index=[pd.Timestamp('2024-12-31')]),nav])
        drawdown=nav/nav.cummax()-1
        for what,df,ylabel in [('nav',nav/1e9,'Book NAV / initial capital'),('drawdown',drawdown*100,'Drawdown (%)')]:
            fig,ax=plt.subplots(figsize=(12.5,5.4))
            for model in MODELS:ax.plot(df.index,df[model],label=LABELS[model],color=COLORS[model],lw=2.2 if model=='full_tuned_v2' else 1.6)
            ax.set_title(('Official 150: retrospective universe' if track=='official_ex_post' else 'Historical universe: same tuned parameters')+'\n2025-01-02 to 2026-09-21',loc='left',fontweight='bold',pad=14)
            ax.set_ylabel(ylabel);ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3));ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax.legend(frameon=False,ncol=3,loc='upper left');fig.tight_layout();fig.savefig(ASSETS/f'{track}_{what}.png',dpi=170);plt.close(fig)
        mm=monthly[monthly.track.eq(track)].copy()
        for what,field,title_en in [('monthly_return','book_return','Monthly book-NAV return (%)'),('monthly_mdd','book_max_drawdown','Within-month maximum drawdown (%)')]:
            pivot=mm.pivot(index='month',columns='model',values=field)[MODELS]*100
            fig,ax=plt.subplots(figsize=(14,5.8));x=np.arange(len(pivot));width=.25
            for i,model in enumerate(MODELS):ax.bar(x+(i-1)*width,pivot[model],width,color=COLORS[model],label=LABELS[model])
            ax.set_xticks(x);ax.set_xticklabels(pivot.index,rotation=55,ha='right');ax.set_ylabel(title_en);ax.axhline(0,color='#64748b',lw=.8)
            ax.set_title(('Official 150 retrospective' if track=='official_ex_post' else 'Historical universe')+'; September 2026 through Sep 21',loc='left',fontweight='bold');ax.legend(frameon=False,ncol=3)
            fig.tight_layout();fig.savefig(ASSETS/f'{track}_{what}.png',dpi=160);plt.close(fig)
        rows=[]
        for m in MODELS:
            r=comp[comp.track.eq(track)&comp.model.eq(m)].iloc[0]
            rows.append({'策略':LABELS[m],'總報酬':pct(r.total_return),'最大回撤':pct(r.max_drawdown),
                '換手倍數':f'{r.turnover_two_way:.2f}×','交易筆數':int(r.trade_count),
                '費稅／百萬元':f'{r.transaction_costs/1e6:.2f}',
                '已量測超限日':'不適用' if m=='0050' else str(int(r.measured_hard_breach_days))})
        returns=mm.pivot(index='month',columns='model',values='book_return');mdds=mm.pivot(index='month',columns='model',values='book_max_drawdown')
        mt=pd.DataFrame({'月份':returns.index})
        for m in MODELS:
            mt[LABELS[m]+' 報酬']=[pct(x) for x in returns[m]]
            mt[LABELS[m]+' 回撤']=[pct(x) for x in mdds[m]]
        section=f'## {title}\n\n'+table(pd.DataFrame(rows))+'\n\n'
        if track=='official_ex_post':section+='此池符合現在的競賽白名單，但其歷史成分來自 2026 年名單，不能解讀成 2025 年已知股票池。v1 有已量測違規，0050 也不是合規競賽組合；兩者只作固定研究對照。\n\n'
        else:section+='這一軌使用同一組獲選參數，沒有重新挑冠軍。股票池以 2024 年底可重建資訊為準，但參數仍用已見期間開發，因此也不是未見測試。\n\n'
        section+=f'![NAV](full_tuned_v2_assets/{track}_nav.png)\n\n![Drawdown](full_tuned_v2_assets/{track}_drawdown.png)\n\n'
        section+=f'![Monthly returns](full_tuned_v2_assets/{track}_monthly_return.png)\n\n![Monthly MDD](full_tuned_v2_assets/{track}_monthly_mdd.png)\n\n'
        section+='\n<details markdown="1"><summary>展開全部月份：報酬與回撤</summary>\n\n'+table(mt)+'\n\n</details>\n\n'
        sections.append(section)
        monthly_nav=nav.resample('M').last()
        presentation['tracks'][track]=dict(monthly_nav={'months':monthly_nav.index.strftime('%y/%m').tolist(),
            'series':{m:(monthly_nav[m]/1e9).tolist() for m in MODELS}},
            monthly=mm.to_dict('records'),summary=rows,
            month_table=mt.to_dict('records'))
    official=trials[trials.track.eq('official_ex_post')].copy()
    fig,ax=plt.subplots(figsize=(10.5,6))
    for valid,color,label in [(False,'#c5cbd1','Excluded by measured gates'),(True,'#007f7f','Eligible research candidates')]:
        frame=official[official.eligible.eq(valid)];ax.scatter(frame.max_drawdown*100,frame.total_return*100,c=color,label=label,s=27,alpha=.65)
    win=official[official.candidate_id.eq(selection['candidate_id'])].iloc[0]
    pit=comp[comp.track.eq('historical_pit')&comp.model.eq('full_tuned_v2')].iloc[0]
    pit_v1=comp[comp.track.eq('historical_pit')&comp.model.eq('v1_matched')].iloc[0]
    ax.scatter([win.max_drawdown*100],[win.total_return*100],marker='*',s=240,color='#bd5f21',label='Selected fixed candidate')
    ax.set_xlabel('Book-NAV maximum drawdown (%)');ax.set_ylabel('Net total return (%)');ax.set_title('Official-universe tuning: development evidence',loc='left',fontweight='bold');ax.legend(frameon=False);fig.tight_layout();fig.savefig(ASSETS/'tuning_frontier.png',dpi=170);plt.close(fig)
    eligible=official[official.eligible].sort_values(['total_return','max_drawdown','turnover_two_way','candidate_id'],ascending=[False,True,True,True])
    columns=['candidate_id','phase','total_return','max_drawdown','turnover_two_way','no_valid_plan_days','hold_without_envelope_days','four_hour_mode']
    elite=eligible[columns].head(20).copy()
    for key in ['total_return','max_drawdown']:elite[key]=elite[key].map(pct)
    elite['turnover_two_way']=elite['turnover_two_way'].map(lambda x:f'{x:.2f}×')
    params=pd.DataFrame([{'參數':k,'固定值':format(v,'.8g') if isinstance(v,float) else str(v)} for k,v in selection['params'].items()])
    status=pd.DataFrame([{'股票池':TRACKS[t],'完成':len(trials[trials.track.eq(t)]),'通過研究門檻':int(trials[trials.track.eq(t)].eligible.sum())} for t in TRACKS])
    text=f'''# Full tuned v2：策略、調參與最終比較

**本輪固定候選：{selection['candidate_id']}。正式 150 檔事後模擬淨報酬 {pct(win.total_return)}，最大回撤 {pct(win.max_drawdown)}。**

同參數在歷史股票池的報酬為 **{pct(pit.total_return)}**，回撤 **{pct(pit.max_drawdown)}**；v1 對照為 {pct(pit_v1.total_return)}／{pct(pit_v1.max_drawdown)}。請同時閱讀兩軌，不能只取正式事後池的高數字。v1 的已量測違規另列於表中。

期間為 2025-01-02 至 2026-09-21，共 417 個交易日。初始本金 10 億元，前日訊號決定股數，次日以官方成交均價撮合，已扣手續費與證交稅。

**研究稽核通過，正式送件仍受證據門檻阻擋。** 本輪沒有取得平台成功回執，也沒有把缺少的 Active Share 算法、歷史 ETF 權重與除權訂單語意當成已確認。這份報告不宣稱未來每筆成交必然合規。

[固定策略程式](../v2_offcial_best_deep_tuning.py)　[策略簡報](full_tuned_v2_deck/full_tuned_v2_strategy.pptx)　[每日流程](../daily_auto/full_tuned_workflow.md)　[全部 810 次結果](../outputs/full_tuned_v2/trials.csv)　[逐月數據](../outputs/full_tuned_v2/monthly.csv)　[獨立稽核](../outputs/full_tuned_v2/audit.json)

## 讀數字前的三個口徑

主圖與主表採帳面 NAV。現金股利在期末一次加入，會使最後一個月帳面報酬跳升；CSV 另外列經濟 NAV 月報酬與回撤。9 月只到 21 日，不是完整月份。

月回撤由上月底 NAV 加上當月每日 NAV 計算；全期最大回撤則沿用全期最高點，兩者不能混用。

0050 是同本金、同費稅及價格預算的一次買入版本，初始預留現金，並非百分之百投資的官方總報酬指數。v1 未得到本輪相同調參預算，因此這裡只比較交付版本的結果。

'''
    text+='\n'.join(sections)
    text+='## 本輪調參完成度\n\n'+table(status)+'\n\n341 組探索加 64 組局部深化，兩池共 810 次。搜尋涵蓋 18 個設定、兩個 Sobol seed 與配對 4H 模式。這是明確範圍內的有界搜尋，不能稱全域最優。\n\n'
    text+='![Tuning frontier](full_tuned_v2_assets/tuning_frontier.png)\n\n'
    text+='先排除已量測硬超限、無有效方案或未成交的候選，再以正式池帳面淨報酬排序，以回撤及換手率破同分。歷史池全程使用同參數，沒有另挑最高收益。所有歷史都已參與開發，搜尋 seed 不是獨立市場樣本。\n\n'
    text+=f'<details markdown="1"><summary>全部 {len(elite)} 個合格候選</summary>\n\n'+table(elite)+'\n\n</details>\n\n'
    text+='## 固定參數與交易策略\n\n'+table(params)+'\n\n'
    cfg=json.loads((OUT/'official_ex_post/final/full_tuned_v2/config.json').read_text())
    text+=f"分數由短長期報酬百分位、量比百分位、MACD 方向、短期 EMA 趨勢與長期 EMA 趨勢組成。權重依序為 `{list(cfg['score_weights'].values())}`。進場還要通過暖機、慢 EMA、量比、追高及波動篩選。\n\n"
    text+='跌破慢 EMA，或短期動能與 MACD 同弱，會提出退出。一般換股需超過分差門檻，並受每日替換數限制。保留股原則上不整體重配，只有規則修正可調整；0% 是策略現金目標，實際仍需保留費用與未知成交價預算。\n\n'
    if selection['params']['max_replacements_per_day']==0:
        text+='本輪獲選組合的每日一般替換數為 0，因此排名分差門檻不生效。趨勢退出、現金及權重修正仍會交易；不能把「不按排名替換」解讀成低交易頻率。\n\n'
    text+='`strict` 需要 4H 方向同意，`coverage_only` 只匹配可用資料與成熟條件，沒有 4H 多頭確認。長 EMA 100/200、4H 指標、暖機與量比／波動計算窗固定，Fibonacci、板塊與美股因子未加入本輪 A 核心。\n\n'
    text+='獲選量比下限 0.2 位於本輪搜尋邊界，不能由此推斷範圍外更好或更差。最佳兩組的交易路徑相同，也不是兩次獨立市場支持。精確浮點設定保存在 JSON，表格採可讀的小數格式。\n\n'
    diag=[]
    for track in TRACKS:
        r=comp[comp.track.eq(track)&comp.model.eq('full_tuned_v2')].iloc[0]
        diag.append({'股票池':TRACKS[track],'重規劃日':int(r.replanning_days),'修正成交日':int(r.recovered_trade_days),
            '驗證續抱日':int(r.validated_hold_days),'僅當下通過':int(r.hold_without_envelope_days),'無有效方案日':int(r.no_valid_plan_days)})
    text+='## 不可行日與每日工作流\n\n'+table(pd.DataFrame(diag))+'\n\n'
    text+='規劃失敗時先擴充合法修正候選，再重驗續抱。表中「僅當下通過」代表上一日帳本合規，但不能覆蓋所有 ±10% 價格情境；不等於明日保證。持倉缺價不會被補造成新行情。正式流程另受官方帳本、AS、公司事件與回執限制。\n\n'
    text+='每日先取得並核對官方帳本，再以固定參數更新訊號、產生 D-Plan 與委託。空單也產出完整報告。檔案、狀態、訊號、程式與設定保存雜湊，送件成功只能由日期／隊伍／檔案完全對應的官方回執認定。\n\n'
    text+='買賣階段由前一日已接受的 D-Plan、前後已結算官方帳本及實際股數變化恢復。持有股票卻缺少可信前態時，不能每天重設成初始策略。日 K 必須在收盤後可得，小時 K 必須已完成；等價時區的重複資料也會被攔截。\n\n'
    text+='被動超限依連續官方交易日與官方估值重算，缺日、缺估值或交易原因不明時不補造寬限。持股與現金不變只是保守的價格變動推論，不是主辦方原因認證。官方警告次數只接受官方欄位，缺失為 UNKNOWN，不能把本機違規或未知次數寫成官方零警告。\n\n'
    text+='| 優先項目 | 本輪完成 | 外部缺口 |\n|---|---|---|\n| Active Share | 30 檔逐項覆蓋表、真實來源與日期 | 00980A 有明確日期權重；00982A 日期語意未定；28 檔未取得。完整算法待確認 |\n| 官方帳本 | 傳輸來源、結算狀態及現金／股數核對 | 尚無正式登入及實際賽事帳本 |\n| 提交回執 | 隊伍、日期、檔名、精確內容及接收狀態驗證 | 尚無平台成功回執，未啟用上傳 |\n| 配股與零股 | 原股數保留、未確認事件阻擋、詢問稿 | 主辦方尚未回覆 SELL_ALL／除權委託語意 |\n| 不可行日 | 重規劃與續抱重驗，固定候選兩池零無效方案 | 不保證未來每個市場日可行 |\n| 自主紀錄 | 固定程式、原始資料、設定、決策與委託封存 | 不接受手工選股、改單或偽造模型呼叫 |\n\n'
    text+='[daily_auto 操作入口](../daily_auto/README.md)列出執行指令。[官方證據狀態](../daily_auto/official_evidence_status.md)列出取得情況與待確認事項。常駐排程和正式送件尚未啟用，缺少介接規格不能自動填成成功。\n\n'
    text+='## 證據、限制與採用判斷\n\n'
    text+='採用固定候選作下一階段每日計畫的策略來源，保持正式提交 gate。較高開發期報酬只能說明這份資料中的結果；同期間多次選參數、正式池成分前視與公司事件假設都限制可外推性。新的未參與開發期間，才可提供真正向前驗證。\n\n'
    text+='本輪凍結十份官方文件、來源資料、執行程式與搜尋設定。稽核檢查全部宣告測試的完整度、選參規則、固定候選重播、帳本與費稅、公式還原及刪除未來資料後的前綴一致性。完整證據見 [audit.json](../outputs/full_tuned_v2/audit.json) 和 [protocol](../docs/official_deep_protocol.md)。\n\n'
    text+='研究固定股數在配股日可能不等於官方 SELL_ALL 的實際語意。相關交易不能憑帳務一致就認證；正式每日輸入有未確認公司行動時必須停止送件。AS 官方算法與完整資料、平台實際結算及接受狀態，同樣不能由回測推定。\n'
    text+='\n正式池 full tuned v2 有 1 筆成交均價超出以前日收盤為基礎的 ±10% 預算範圍；實際帳本重算仍零已量測超限，但該範圍不是所有事件日的保證。除權與其他改變參考價的事件須使用當日官方契約。\n'
    text+='\n容量依比賽全額成交規則不作淘汰條件。正式池仍有 98 筆超過當日日量的 10%，其中 11 筆超過全日量，最高 9.59 倍；這不是 10 億元實盤可完全成交的證據。\n'
    path=ROOT/'reports/full_tuned_v2_report.md';path.write_text(text)
    body=markdown.markdown(text,extensions=['tables','fenced_code','md_in_html'])
    html='''<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Full tuned v2 最終比較</title><style>
    body{font:17px/1.8 -apple-system,BlinkMacSystemFont,"Noto Sans CJK TC",sans-serif;color:#173044;background:#f4f6f8;margin:0}main{max-width:1180px;margin:auto;background:white;padding:44px}h1{font-size:34px;line-height:1.35}h2{font-size:26px;margin-top:52px;border-top:1px solid #dce3e8;padding-top:25px}a{color:#007f7f}img{width:100%;height:auto;margin:12px 0}table{width:100%;border-collapse:collapse;font-size:14px;font-variant-numeric:tabular-nums;display:block;overflow-x:auto}th{background:#edf3f5;text-align:left}th,td{padding:10px 12px;border-bottom:1px solid #e3e8eb;white-space:nowrap}details{padding:16px;background:#f5f8fa}summary{cursor:pointer;font-weight:600}code{font-size:14px;overflow-wrap:anywhere}strong{color:#0b5360}@media(max-width:700px){main{padding:20px}h1{font-size:28px}}
    </style><main>'''+body+'</main></html>'
    (ROOT/'reports/full_tuned_v2_report.html').write_text(html)
    dump(ASSETS/'presentation_data.json',presentation)
    dump(ASSETS/'provenance.json',dict(audit_sha256=sha(OUT/'audit.json'),generator_sha256=sha(Path(__file__)),
        inputs={str(p.relative_to(ROOT)):sha(p) for p in [OUT/'comparison.csv',OUT/'monthly.csv',OUT/'trials.csv',OUT/'selection.json']},
        outputs={str(p.relative_to(ROOT)):sha(p) for p in ASSETS.iterdir() if p.is_file() and p.name!='provenance.json'}))
    print('Verified report and presentation data generated')


if __name__=='__main__':main()
