"""Generate the comparison report strictly from independently audited outputs."""
from pathlib import Path
import json
import sys
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/backtest_v2_2025_to_now'
MODELS=['v1_matched','A','B','C','D','0050']
LABELS={'v1_matched':'v1 同口徑','A':'A 動能局部','B':'B 板塊','C':'C 隔夜','D':'D 多訊號','0050':'0050'}

def pct(x):return f'{x*100:+.2f}%'
def magnitude(x):return f'{x*100:.2f}%'
def table(headers,rows):return '\n'.join(['| '+' | '.join(map(str,headers))+' |','|'+'---|'*len(headers),*['| '+' | '.join(map(str,row))+' |' for row in rows]])

def main():
    audit=json.loads((OUT/'audit.json').read_text())
    if audit['status']!='PASS':raise ValueError('Do not publish unverified metrics')
    summary=pd.read_csv(OUT/'summary.csv').set_index('model')
    monthly=pd.read_csv(OUT/'monthly_comparison.csv')
    yearly=pd.read_csv(OUT/'annual_comparison.csv')
    comparisons=pd.read_csv(OUT/'economic_nav_comparison.csv',index_col=0,parse_dates=True)
    first=summary.loc['A','start'];last=summary.loc['A','end']
    config=json.loads((OUT/'A/config.json').read_text())
    extras={};failure_rows=[];drawdown_rows=[];data_rows=[]
    for name in MODELS:
        eq=pd.read_csv(OUT/name/'equity.csv').fillna('')
        trades=pd.read_csv(OUT/name/'trades.csv')
        warnings=pd.read_csv(OUT/name/'warnings.csv')
        sn=pd.read_csv(OUT/name/'snapshots.csv')
        raw=eq.violations.ne('').sum()
        extra=dict(trade_days=trades.date.nunique(),unfilled=int(warnings.issue.str.startswith('UNFILLED').sum()) if len(warnings) else 0,
                   overdue=int(eq.overdue_passive_caps.ne('').sum()),active_caps=int(eq.active_cap_breaches.ne('').sum()))
        extras[name]=extra
        failure_rows.append([LABELS[name],int(raw),int(summary.loc[name,'infeasible_signal_days']),extra['unfilled'],extra['overdue']])
        nav=comparisons[name];dd=nav/nav.cummax()-1;trough=dd.idxmin();peak=nav.loc[:trough].idxmax()
        recovery=nav.loc[trough:];recovery=recovery[recovery>=nav.loc[peak]]
        drawdown_rows.append([LABELS[name],str(peak.date()),str(trough.date()),str(recovery.index[0].date()) if len(recovery) else '尚未恢復'])
        if name in ['B','C','D']:
            metas=[json.loads(x) for x in sn.iloc[:-1].metadata]
            partial_sector=sum(m.get('sector_status')!='AVAILABLE' for m in metas)
            degraded=sum(m.get('overall_data_status','READY')!='READY' for m in metas)
            data_rows.append([LABELS[name],len(metas),partial_sector,degraded])
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    colors=['#80868D','#216B8C','#139E87','#A55C26','#8061AA','#293B4C']
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(2,1,figsize=(10,7),sharex=True,gridspec_kw={'height_ratios':[2,1]},constrained_layout=True)
    for name,color in zip(MODELS,colors):
        s=comparisons[name]
        axes[0].plot(s.index,(s/1e9-1)*100,label=name,color=color,lw=1.7,ls='--' if name=='v1_matched' else '-')
        axes[1].plot(s.index,(s/s.cummax()-1)*100,color=color,lw=1.4)
    axes[0].set_title('Frozen v2 parallel research | Net economic NAV',loc='left',weight='bold')
    axes[0].set_ylabel('Net return (%)');axes[1].set_ylabel('Drawdown (%)');axes[0].legend(ncol=3)
    for ax in axes:ax.grid(axis='y',alpha=.25);ax.axhline(0,color='gray',lw=.5)
    axes[1].set_xlabel('VWAP fixed-share fills; fees/tax included; research shadow, not contest certified')
    fig.savefig(OUT/'performance.png',dpi=170);plt.close(fig)
    matrix=monthly.pivot(index='month',columns='model',values='economic_return')[MODELS]*100
    fig,ax=plt.subplots(figsize=(8.5,9));im=ax.imshow(matrix.values,cmap='RdYlGn',vmin=-15,vmax=15,aspect='auto')
    ax.set_xticks(range(len(MODELS)),MODELS);ax.set_yticks(range(len(matrix)),matrix.index)
    for i in range(len(matrix)):
        for j in range(len(MODELS)):ax.text(j,i,f'{matrix.iloc[i,j]:+.1f}',ha='center',va='center',fontsize=9)
    ax.set_title('Monthly net economic returns (%)',loc='left');fig.colorbar(im,ax=ax,shrink=.5)
    fig.tight_layout();fig.savefig(OUT/'monthly_heatmap.png',dpi=160);plt.close(fig)
    cols=['指標']+[LABELS[m] for m in MODELS]
    metric_specs=[('淨累計報酬','economic_total_return',pct),('經濟 MDD','economic_max_drawdown',magnitude),('帳面 MDD','max_drawdown',magnitude),
                  ('年化報酬','calendar_cagr',pct),('年化波動','economic_annualized_volatility',magnitude),('Sharpe，RF=0','economic_sharpe_zero_rf',lambda x:f'{x:.2f}'),
                  ('期末 NAV／億','final_nav',lambda x:f'{x/1e8:.3f}'),('雙向換手／倍','turnover_two_way',lambda x:f'{x:.2f}'),
                  ('費稅／萬元','transaction_costs',lambda x:f'{x/1e4:,.1f}'),('成交筆數','trades',lambda x:str(int(x))),
                  ('平均股票曝險','average_stock_exposure',magnitude),('最高現金比例','cash_ratio_max',magnitude)]
    metrics=table(cols,[[label,*[fmt(summary.loc[m,key]) for m in MODELS]] for label,key,fmt in metric_specs])
    metrics+='\n| 有交易日數 | '+' | '.join(str(extras[m]['trade_days']) for m in MODELS)+' |'
    metrics+='\n| 最大單股權重 | '+' | '.join(magnitude(summary.loc[m,'max_single_weight']) for m in MODELS)+' |'
    metrics+='\n| 持股檔數範圍 | '+' | '.join(f"{int(summary.loc[m,'holdings_min'])}–{int(summary.loc[m,'holdings_max'])}" for m in MODELS)+' |'
    months=table(['月份',*[LABELS[m] for m in MODELS]],[[month,*[pct(matrix.loc[month,m]/100) for m in MODELS]] for month in matrix.index])
    md=monthly.pivot(index='month',columns='model',values='economic_max_drawdown')[MODELS]
    monthly_dd=table(['月份',*[LABELS[m] for m in MODELS]],[[month,*[magnitude(md.loc[month,m]) for m in MODELS]] for month in md.index])
    annual=table(['年份',*[LABELS[m] for m in MODELS]],[[year,*[pct(yearly[(yearly.model==m)&(yearly.year==year)].economic_return.iloc[0]) for m in MODELS]] for year in sorted(yearly.year.unique())])
    old=pd.read_csv(ROOT/'outputs/backtest_2025_to_now_v1/summary.csv').set_index('model')
    legacy=table(['版本','淨報酬','經濟 MDD','雙向換手'],[[name,pct(old.loc[key,'total_return']),magnitude(old.loc[key,'economic_max_drawdown']),f"{old.loc[key,'turnover_two_way']:.2f}×" if pd.notna(old.loc[key,'turnover_two_way']) else '舊輸出未記錄'] for name,key in [('舊 v1','v1'),('舊 85% 0050','0050_buy_hold_85pct')]])
    source=json.loads((ROOT/'data/v2/vendor_execution_audit.json').read_text())
    auxstatus=json.loads((ROOT/'data/v2_auxiliary/source_status.json').read_text())
    sources=table(['訊號','資料狀態'],[[k,v['status']] for k,v in auxstatus['sources'].items()])
    a,b,c,d=(summary.loc[m] for m in ['A','B','C','D'])
    comparison=table(['比較','報酬差／百分點','MDD 差／百分點','解讀'],[
        ['v1→A',f"{(a.economic_total_return-summary.loc['v1_matched','economic_total_return'])*100:+.2f}",f"{(a.economic_max_drawdown-summary.loc['v1_matched','economic_max_drawdown'])*100:+.2f}",'配置方式差異'],
        ['A→B',f'{(b.economic_total_return-a.economic_total_return)*100:+.2f}',f'{(b.economic_max_drawdown-a.economic_max_drawdown)*100:+.2f}','可用板塊層'],
        ['B→C',f'{(c.economic_total_return-b.economic_total_return)*100:+.2f}',f'{(c.economic_max_drawdown-b.economic_max_drawdown)*100:+.2f}','可用隔夜層'],
        ['C→D',f'{(d.economic_total_return-c.economic_total_return)*100:+.2f}',f'{(d.economic_max_drawdown-c.economic_max_drawdown)*100:+.2f}','多元件比較']])
    risk=table(['版本','分類覆蓋／NAV','已知板塊 HHI','最大已知板塊'],[[LABELS[m],magnitude(summary.loc[m,'mean_classified_nav_weight']),f"{summary.loc[m,'known_sector_hhi_mean']:.3f}" if pd.notna(summary.loc[m,'known_sector_hhi_mean']) else 'UNKNOWN',magnitude(summary.loc[m,'max_known_sector_weight']) if pd.notna(summary.loc[m,'max_known_sector_weight']) else 'UNKNOWN'] for m in MODELS])
    report=f'''# v2 策略與最終歷史回測報告

**四版回測及帳務驗證已完成。此報告交付研究結果；正式競賽合規仍為 `BLOCK_SUBMISSION`，資料不足的訊號按固定規則降級。不能將缺失元件也算作已驗證的完整系統。**

期間：{first} 至 {last}，共 {int(a.sessions)} 個交易日。本金新台幣 10 億元，跨年連續持倉。2026 年 9 月只計到 21 日。策略說明見 [完整 v2 規則](../docs/strategy_v2_implemented.md)，研究規格見 [原始四版文件](../docs/AI_CUP_Trading_Agent_v2.md)。

## 結果先看什麼

A／B／C／D 的淨報酬依序為 {pct(a.economic_total_return)}、{pct(b.economic_total_return)}、{pct(c.economic_total_return)}、{pct(d.economic_total_return)}；同資金預算 0050 為 {pct(summary.loc['0050','economic_total_return'])}。D 的經濟最大回撤為 {magnitude(d.economic_max_drawdown)}，0050 為 {magnitude(summary.loc['0050','economic_max_drawdown'])}。這些數字已扣委託手續費與賣出稅。

部署結論是 **HOLD**。歷史研究池不能冒充正式 150 檔，指定主動 ETF 的歷史持股與 Active Share 定義仍未認證；表現最高的研究版本不會自動變成 LIVE。若某版在資料降級下較佳，只支持該降級實作的歷史結果。

## 同口徑維度比較

{metrics}

`v1 同口徑` 保留 v1 個股訊號與整體配置意圖，改用新成交、資金預算及分批買賣流程。A 使用相同訊號但保留股不整體重配；B 加板塊門控；C 加隔夜排名；D 使用固定 Regime 權重。這張表中的 v1 不是舊報告那條曲線。

策略現金目標都是 0%，實際委託仍保留前收盤價上漲 10% 的預算與費用。0050 也用相同首次預算，之後買入持有、不再投資股利。因此它是「同預算 0050」，不是官方 100% 曝險的指數總報酬。

![共同日曆下的累計淨報酬與每日回撤](../outputs/backtest_v2_2025_to_now/performance.png)

## 每月淨報酬

月報酬以上月最後一個交易日的經濟 NAV 為分母，首月用初始本金。經濟 NAV 包含已確認但未入可用現金的股利；實際股利仍在整段期末才支付。不能將月報酬直接相加。

{months}

## 每月最大回撤

每月回撤包含上月月底 NAV 作為起點，再按當月每日路徑重新尋找高峰與低谷。它與全期 MDD 是不同指標；下表以正數表示損失幅度。

{monthly_dd}

## 跨年與全期回撤區間

{annual}

{table(['版本','高峰','谷底','恢復高峰'],drawdown_rows)}

年化報酬依實際日曆時間計算；波動及 Sharpe 使用每日經濟 NAV 報酬、252 日年化、無風險利率 0。所有 MDD 都包含初始本金；未假設期末清倉，故沒有額外終端賣出費稅。

## 哪一層帶來差異

{comparison}

MDD 差為負代表回撤較小。A→B 與 B→C 只在其他設定固定且來源覆蓋相同的範圍內解讀。D 同時改分數權重與風險成分，屬多元件比較。這是已看過資料上的固定規則研究，沒有對本輪結果再調參；不能據此宣稱長期 alpha 或推算正式 24 日競賽排名。

局部換股將成交筆數由 4,340 降至 818，但 A 仍在 393／417 日交易，雙向換手仍達 27.75 倍。它改善了整體重配造成的交易量，尚未達成「不常交易」。A 的回撤也比同口徑 v1 大 1.61 個百分點。C 相對 B 的報酬差為 −0.05 個百分點，沒有本期收益改善；D 比同預算 0050 少 1.32 個百分點，回撤則小 4.41 個百分點，不能說更穩且更賺。

獨立比較確認 A、B 的委託、成交、持股與 NAV 檔案完全相同。B 在 33 天排除過 76 筆原本可買的股票日紀錄，但都沒有改變最終交易；可執行決策窗口共 62,230 筆排名紀錄，其中 41,407 筆產業分類為 UNKNOWN，只有 50 個股票實體曾取得可靠歷史分類。因此本次沒有觀察到板塊層的增量交易效果，不能推論完整板塊輪動沒有價值。詳見[獨立解讀檢查](../outputs/backtest_v2_2025_to_now/independent_interpretation_audit.json)。

本輪決策：A 保留為研究基準；B 因介入未影響成交，暫無法評價完整機制；C 沒有採用證據；D 目前固定組合不升級。C 雖改變分數，與 A 的每日前 25 名平均仍重疊 24.70 檔；D 平均重疊降至 13.93 檔，確實改變了選股，但沒有達成更高收益與更低回撤並存。詳見[訊號獨立審查](v2_signal_review.md)。

完整非重疊 24 日區塊與末尾不足 24 日區塊分別標記於 [區塊明細](../outputs/backtest_v2_2025_to_now/blocks_24_sessions.csv)。這些是連續持倉的觀察區塊，不是每段重新給 10 億元的獨立競賽。

## 資料覆蓋與板塊風險

{table(['版本','決策期數','板塊非完整期數','輔助降級期數'],data_rows)}

{sources}

{risk}

板塊 HHI 只在已有可用歷史分類的資產內重新歸一計算。未分類曝險另行排除，因此不能把這個 HHI 當全組合分散程度；覆蓋越低，解讀限制越大。0050 是基準，未穿透成分股分類。分類來源、生效日與發布界限詳見 [產業資料來源](../data/sector/provenance.json)。

全期有成交量的股票紀錄均有成對成交金額及股數。直接交易所缺口補入 {source['repaired']:,} 筆 FinMind 紀錄，與直接交易所重疊 {source['overlap_checks']:,} 筆的均價差異上限為 {source['max_relative_difference']:.2g}。核對的是成交均價一致性，不能證明今日取得的所有歷史資料都是原始發布版本。

日線、4H 與公司行動仍沿用已揭露的[資料限制](../docs/data_audit_2025_to_now.md)：5371、6589、2888 部分早期小時線現在不可得，因此無法進場不一定代表當時沒有訊號；5880、2880、2885、6669 的四筆公司事件分解仍未取得完整直接明細，而且部分實際持股跨越了這些事件。這些是會影響結果的資料偏差與不確定性，時序測試通過也不會消除它們。

US 收盤採美東時間 16:30 作保守可用界限；匯率是臺灣銀行即期買賣中價代理，不是全球外匯收盤。台指夜盤取已完成夜盤成交量最大合約的收盤／開盤報酬，以交易量歸屬日 06:00 作可用界限；[期交所夜盤日期定義](https://www.taifex.com.tw/cht/3/futContractsDateAhView)支持交易日歸屬，精確發布時點仍是保守假設。法人使用外資與投信五日淨買超／同期成交股數，未納入自營商；財報因缺少可信公告時點而停用。

## 違規、攔截與失敗

{table(['版本','原始超限日','不可行決策','缺價未成交','被動超限逾期日'],failure_rows)}

原始超限日記錄帳面檔數、現金或權重超界；不是官方警告次數。價格造成的被動權重超限另追蹤五個交易日；Active Share 仍為 UNKNOWN。研究 shadow 帳務持續計算，沒有實作主辦方警告、作廢交易與淘汰後停止計分。因此任何出現原始超限的績效，都不能宣稱是零違規比賽路徑。0050 的單一 ETF 持有只作基準，不符合比賽檔數規則。

0050 表中的五個不可行日，是分割停牌時缺少可交易報價的每日計畫標記；它始終保留既有部位，並沒有五筆失敗買單。A／B／C 的原始超限為七日現金超限、兩日被動權重超限；D 為四日現金與四日被動權重超限。全部版本實際現金均未負值。

第一輪工程回放曾因「多檔退出整批攔截」而凍結交易；該候選隔離於 `tmp/v2_baseline_smoke`。最終程式以分批退出／回補處理，沒有因舊候選的報酬高低修改技術指標。交易容量不阻擋本比賽模型；真實市場是否能承接 10 億元是另一個研究問題。

## 舊 v1 結果保留

{legacy}

這兩條舊曲線使用 15% 策略現金、次日開盤加 5 bps；與主表的均價及近滿倉預算不同。不能將主表與舊表的報酬差全部歸因於板塊輪動。舊報告與結果均保留，沒有覆寫。

## 驗證與重現

獨立帳務審核為 `{audit['status']}`，詳細逐項證據在 [audit.json](../outputs/backtest_v2_2025_to_now/audit.json)。驗證範圍包含固定股數委託、均價、費稅、公司行動、現金、股數、股利應收、月報酬連乘及含初始本金的 MDD。模型完整性與官方合規不由帳務 PASS 取代。

55 項單元測試通過。另將日線、小時線、板塊與輔助資料實際刪除 2025-12-31 後的紀錄，使用凍結程式重跑 A、D：243 個交易日、510／577 筆成交均與完整研究同期一致，經濟 NAV 最大誤差小於 0.000001 元。截斷日的股利現金結算會不同，因此比較經濟 NAV 而非只比現金。這支持已測路徑不依賴未來資料，不能保證來源沒有歷史修訂偏差。詳細[資料與截斷審查](v2_data_review.md)。

每日 paper 命令產生 14 份文件；相同輸入重跑檔案完全一致，週末日期會被拒絕。證據見 [paper_export_audit.json](../outputs/backtest_v2_2025_to_now/paper_export_audit.json)。它驗證的是回放輸出契約，沒有驗證平台接受提交、即時資料擷取或零漏交。

輸入和程式快照保存於 `outputs/backtest_v2_2025_to_now/input_snapshot`，SHA-256 與固定設定見 [provenance.json](../outputs/backtest_v2_2025_to_now/provenance.json)。日淨值、委託、成交、持股、全部分數及降級紀錄逐版保存。

重跑指令：`python3 scripts/run_v2_study.py --output outputs/backtest_v2_reproduction`。驗證指令：`python3 scripts/audit_v2_study.py --output outputs/backtest_v2_reproduction`。每日歷史 paper 包：`python3 run_daily.py --date 2026-09-21 --mode paper`。

本次交付完成策略、連續歷史回測、比較報告與可重現 paper 包。尚未完成的是正式來源的全部時點覆蓋與官方提交認證；因此沒有部署 LIVE 或送出交易。

研究解讀亦參照 alphaXiv 核對的 [The Probability of Backtest Overfitting](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)：從已看過的多個回測選冠軍會帶來選擇偏差。本研究未估計 PBO，也未以文獻為策略收益背書。
'''
    (ROOT/'reports/backtest_v2_final.md').write_text(report)
    print(ROOT/'reports/backtest_v2_final.md')

if __name__=='__main__':main()
