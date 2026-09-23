"""Publish supplied-document v2 review only after independent audit gates pass."""
from pathlib import Path
import argparse
import hashlib
import html
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import markdown

TRACKS = ('historical_pit', 'official_ex_post')
MODELS = ('A', 'A_dplan_guard', 'v1_matched', '0050')
NAMES = {'A': '原 A 固定版', 'A_dplan_guard': 'A 公式相容版', 'v1_matched': 'v1 同口徑', '0050': '0050 費後買持'}
COLORS = {'A': '#64748b', 'A_dplan_guard': '#087e8b', 'v1_matched': '#cf8b24', '0050': '#7c3aed'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pct(value):
    return f'{float(value)*100:.2f}%'


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |',
        *['| ' + ' | '.join(map(str, row)) + ' |' for row in rows]])


def gate(root):
    hashes = {}; audits = {}
    for track in TRACKS:
        folder = root / track
        audit = json.loads((folder / 'audit.json').read_text())
        if audit.get('status') != 'PASS':
            raise ValueError('Independent audit has not passed: ' + track)
        if audit.get('auditor_sha256') != sha(ROOT / 'scripts/audit_official_v2.py'):
            raise ValueError('Auditor changed after verification')
        for filename, key in [('manifest.json', 'manifest_sha256'), ('receipt.json', 'receipt_sha256')]:
            if sha(folder / filename) != audit[key]:
                raise ValueError('Audit binding mismatch: ' + filename)
        for name, digest in audit['artifact_hashes'].items():
            if sha(folder / name) != digest:
                raise ValueError('Verified output changed: ' + name)
            hashes[str((folder / name).relative_to(ROOT))] = digest
        manifest = json.loads((folder / 'manifest.json').read_text())
        if not manifest['outputs_complete']:
            raise ValueError('Incomplete run')
        for name, digest in manifest['hashes'].items():
            if sha(ROOT / name) != digest:
                raise ValueError('Source changed: ' + name)
            hashes[name] = digest
        hashes[str((folder / 'audit.json').relative_to(ROOT))] = sha(folder / 'audit.json')
        audits[track] = audit
    return hashes, audits


def plots(folder, dest, track):
    curves = {}
    fig, axes = plt.subplots(2, 1, figsize=(11.5, 7), sharex=True, gridspec_kw={'height_ratios': [2, 1]})
    for name in MODELS:
        eq = pd.read_csv(folder / 'final' / name / 'equity.csv')
        y = np.r_[1e9, eq.nav.to_numpy(float)]
        x = pd.to_datetime(['2024-12-31', *eq.date])
        curves[name] = pd.Series(y, index=x)
        axes[0].plot(x, y / 1e9, label=name, color=COLORS[name], lw=2 if name=='A_dplan_guard' else 1.3)
        axes[1].plot(x, (y / np.maximum.accumulate(y)-1)*100, color=COLORS[name])
    axes[0].set_title(('Historical reconstructed pool' if track=='historical_pit' else 'Official 2026 list: retrospective membership')
        + '\n2025-01-02 to 2026-09-21 | Book NAV after fees/tax | Terminal dividend credit')
    axes[0].set_ylabel('NAV / initial capital'); axes[1].set_ylabel('Drawdown (%)')
    axes[0].legend(ncol=2)
    for ax in axes: ax.grid(alpha=.18)
    fig.tight_layout(); fig.savefig(dest / (track + '_nav.png'), dpi=170); plt.close(fig)
    pd.DataFrame(curves).rename_axis('date').to_csv(dest / (track + '_nav.csv'))
    monthly = pd.read_csv(folder / 'primary_nav_monthly.csv')
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 10))
    for ax, column, title, cmap in zip(axes, ('book_return','book_max_drawdown'), ('Monthly return (%)','Within-month MDD (%)'), ('RdYlGn','YlOrRd')):
        panel = monthly.pivot(index='month', columns='model', values=column)[list(MODELS)] * 100
        bound = max(float(np.abs(panel.to_numpy()).max()), 1e-8)
        im = ax.imshow(panel, aspect='auto', cmap=cmap, vmin=-bound if column=='book_return' else 0, vmax=bound)
        ax.set_title(title); ax.set_xticks(range(4), ['A original','A D-Plan','v1','0050'], rotation=30, ha='right')
        ax.set_yticks(range(len(panel)), [str(m)+('*' if str(m)=='2026-09' else '') for m in panel.index])
        for i in range(len(panel)):
            for j in range(4):
                value = panel.iloc[i,j]
                ax.text(j,i,f'{value:.1f}',ha='center',va='center',fontsize=8, color='white' if abs(value)>bound*.70 else '#14212f')
        fig.colorbar(im, ax=ax, fraction=.035, pad=.02)
    fig.suptitle('Book NAV monthly comparison | * September is partial and includes terminal dividend credit', fontsize=10)
    fig.tight_layout(rect=(0,0,1,.97)); fig.savefig(dest / (track + '_monthly.png'), dpi=170); plt.close(fig)
    monthly.to_csv(dest / (track + '_monthly.csv'), index=False)


def build(root, report):
    hashes, audits = gate(root)
    assets = report.with_name(report.name + '_assets'); assets.mkdir(parents=True, exist_ok=True)
    summary = []; monthly_all = []
    lines = ['# v2 A｜官方文件完整複核與回測', '',
        '**十份官方文件已逐一核對；固定 A 與公式相容版均已完整重跑。正式提交仍為 BLOCK，不能把本報告當成已通過比賽所有規則的成績。**', '',
        '期間為 2025-01-02 至 2026-09-21，共 417 個完整交易日，初始本金 10 億元。1 月 1 日休市；9 月 22 日執行時尚未收盤，因此不用當日不完整行情。歷史池 A 固定 p052，官方事後池固定 p049；本輪沒有再選參數，v3 不再使用。', '',
        '先看三件事：原策略績效可以重現；D-Plan 股數推導需要修正；Active Share 及正式結算接入仍缺證據。詳細操作已整理為 [每日單頁入口](../daily_auto/README.md)。', '',
        '## 結論先看', '',
        '| 項目 | 結果 |', '|---|---|',
        '| 原 A 重播 | 兩軌的 NAV、持股、成交、委託均與凍結結果完全相同 |',
        '| 公式相容版 | 只處理權重序列化與零股異動，不改動能參數 |',
        '| 帳務與時序 | 兩軌獨立稽核、實體截斷重播通過 |',
        '| 正式送件 | 尚未放行：AS、公司事件細則及平台帳本／收件仍需接通 |',
        '| v3 | 退出现行流程；原程式與結果保留 |', '',
        '## 這次改正什麼', '',
        '官方要求先從目標權重算出整張目標股數，再減掉前日實際庫存。舊程式先決定股數、再反算位於取整邊界的權重，浮點序列化可能少算一張；配股後若有零股，整張目標減現有庫存也無法變成千股倍數。', '',
        '`A_dplan_guard` 將權重放在同一整張區間內，再精確回算股數。含零股的既有股票先續抱，不對該股下無法符合字面公式的委託；其他股票繼續運作。這是保守的相容性假設，主辦方尚未提供零股和除權日待執行委託的完整細則。', '',
        '指南和 schema 要求公式一致，反例註解卻說部分不一致僅警示，因此以下只能量化「不符合字面公式」，不能把每列都算成主辦方拒件或一般違規。', '']
    contract_rows = []
    for track in TRACKS:
        for model in ('A','A_dplan_guard'):
            c = audits[track]['order_contracts'][model]
            contract_rows.append(['歷史池' if track=='historical_pit' else '官方事後池', NAMES[model], c['orders'], c['mismatch_rows'], c['float_floor_mismatch_rows'], c['odd_held_order_rows']])
    lines += [table(['股票池','版本','計畫列數','Decimal 不符','float 不符','涉及零股列'],contract_rows), '',
        'Decimal 按序列化後十進位數字嚴格回算；float 則模擬一般浮點向下取整。兩者差異證明邊界編碼不穩定，官方伺服器數值實作尚未公布；不能挑其中一個數字當作已知拒件數。', '',
        '列數包含研究終點後尚未執行的最後計畫；不是主辦方警告次數。舊版凍結檔案未覆寫，修正版本另存，以便核對變動。', '',
        '## 績效比較', '',
        '**本輪主表改用比賽帳面 NAV。** 股息先列應收，到模擬期末才入現金；economic NAV 在除息時就加應收，另列回撤供核對。兩者最終報酬一致，途中曲線與月報酬可能不同。', '']
    for track in TRACKS:
        folder = root / track
        plots(folder, assets, track)
        comparison = pd.read_csv(folder / 'comparison.csv').set_index('model')
        label = '歷史股票池' if track=='historical_pit' else '官方名單事後情境'
        lines += ['### '+label, '', ('使用重建的 2024 年底股票池；部分股票不在 2026 年官方名單，因此此軌不符合正式比賽白名單。' if track=='historical_pit' else
            '**此軌把 2026 年公布的 150 檔名單套回 2025 年，含成分股前視偏誤。** 它可以檢查指定名單的模擬路徑，不能解讀成當年可事前部署的報酬。'), '']
        rows = []
        for model in MODELS:
            r = comparison.loc[model]; eq = pd.read_csv(folder/'final'/model/'equity.csv')
            hard = '不適用¹' if model=='0050' else int(r.measured_hard_breach_days)
            rows.append([NAMES[model],pct(r.total_return),pct(r.max_drawdown),pct(r.economic_max_drawdown),
                f'{int(eq.traded_notional.gt(0).sum())}/417',int(r.trade_count),f'{r.turnover_two_way:.2f}×',hard,int(r.infeasible_executed_days)])
            summary.append(dict(track=track,model=model,**r.to_dict()))
        lines += [table(['版本','總報酬','NAV 最大回撤','含應收回撤','交易日','成交筆數','雙向換手','量測硬超限日','計畫不可行日'],rows), '',
            f'![{label}淨值與回撤]({assets.name}/{track}_nav.png)', '',
            f'![{label}每月比較]({assets.name}/{track}_monthly.png)', '']
        cash_rows=[]
        for model in ('A','A_dplan_guard'):
            r=comparison.loc[model];m=audits[track]['models'][model]
            cash_rows.append([NAMES[model],f'{int(r.holdings_min)}–{int(r.holdings_max)}',pct(r.cash_ratio_min)+'–'+pct(r.cash_ratio_max),
                f'{r.transaction_costs/1e6:.2f}',f"{m['terminal_distribution']/1e6:.2f}"])
        lines += [table(['版本','持股檔數','現金比範圍','累計費稅／百萬元','期末股息入帳／百萬元'],cash_rows), '']
        monthly = pd.read_csv(folder / 'primary_nav_monthly.csv');monthly['track']=track;monthly_all.append(monthly)
        rows=[]
        for month,group in monthly.groupby('month'):
            indexed=group.set_index('model');row=[month+('＊' if month=='2026-09' else '')]
            for model in MODELS:row += [pct(indexed.loc[model,'book_return'])+' / '+pct(indexed.loc[model,'book_max_drawdown'])]
            rows.append(row)
        lines += ['<details markdown="1">', '<summary>展開 21 個月明細：報酬 / 月內最大回撤</summary>', '',
            table(['月份',*[NAMES[m] for m in MODELS]],rows), '', '</details>', '']
    lines += ['「計畫不可行日」是前日規劃器明確以 INFEASIBLE 開頭、在該日未能產生調整的天數。組合可能因續抱仍未超限，但不代表每日運行順暢；此數與正式漏繳／警告不同，也不含所有名稱為 WAIT 的等待狀態。', '',
        '¹ 0050 是單一 ETF 比較基準，不套用競賽 20–30 檔及股票白名單。它使用凍結的一次買入、費用與價格預算緩衝，因此不是 100% 滿倉的 0050 官方總報酬指數。', '',
        '＊2026 年 9 月只到 9/21，且將整段模擬累積股息一次入帳。這會提高最後月份帳面報酬，不代表 9 月突然產生相同幅度的選股優勢。月內 MDD 以「前月末 NAV＋當月每日 NAV」計算。', '',
        'v1 的硬性超限使它不具合規候選資格。本報告沒有模擬違規日交易回退、三次警告淘汰或 Active Share 失格，因此長期 shadow NAV 不能當作這些情況下的正式排名結果。', '',
        '## 能與不能證明的事', '',
        '相容版歷史報酬較原 A 低，官方事後池則較高；這說明零股處理會改變持倉路徑，不能當成一次修正就普遍提升績效。兩軌零量測硬超限只涵蓋各自股票池、檔數、現金及主被動權重限制；尚未包含完整官方 AS 與送件歷史。', '',
        '前日完整訊號決定固定股數，下一交易日才用官方成交均價記帳；獨立截斷回放檢查未讀取未來行情。這不能消除先前用整段歷史挑參數的開發偏誤，也不能消除官方股票池事後選取的偏誤。', '',
        '原 A 並不是最早構想的低換手版本：歷史參數允許零分差替換，4H 只驗覆蓋，現金／權重修正仍可能頻繁加减碼。不要把「保留股不全面重配」寫成「幾乎不交易」。', '',
        '另有公司事件語意未能認證：歷史池相容版在 2025-07-21 計畫出清玉山金 1,146,000 股，翌日先配股 1% 再按固定委託賣出，仍留下 11,460 股。前日公式可以通過，但除權日是否調整待執行委託、SELL_ALL 如何認定，文件未說明。本輪保留事件與餘額，不宣稱所有 D-Plan 語意已符合。', '',
        '官方假設全額均價成交，因此本輪不因成交量容量刪單。超過日量的模擬成交只能另作實盤限制，不影響本次競賽假設；缺均價仍不能憑空補成交。', '',
        '## 正式比賽仍缺什麼', '',
        table(['缺項','目前處理'],[
            ['30 檔 ETF 的歷史／每日前十大權重','UNKNOWN；沒有用現在持股回填過去'],
            ['AS 聯集、正規化與日期細節','等待可驗證的官方口徑；不能僅憑程式算出數字就認證'],
            ['零股與除權日委託細則','本輪採凍結零股名稱、固定待執行股數的明示假設'],
            ['官方帳本與平台回執','尚未接通；禁止將本機產檔標成已送件'],
            ['真正未見期間','未提供；所有本輪歷史已參與研究開發']]), '',
        '## 官方文件與每日操作', '',
        '已讀完 10 份檔案，包括 5 份 PDF、官方 DOCX 模板、D-Plan 指南、schema 與正反 JSON 範例。原文、頁碼、衝突及不適用假設見 [逐檔稽核](../docs/official_docs_rule_audit.md)。ETF 名單只列基金代碼，沒有持股權重。', '',
        '證交所亦說明主動式 ETF 每日揭露持股，但這不等於本機已取得完整的歷史時間版本；[TWSE 說明](https://www.twse.com.tw/zh/ETFortune/hotetf/8a8216d697fc438f0198cfce647b0359) 與 [野村每日 PCF](https://www.nomurafunds.com.tw/ETFWEB/pcf) 僅是後續資料接入的第一手來源。', '',
        '[每日入口](../daily_auto/README.md) · [比賽須知](../daily_auto/competition_guide.md) · [操作清單](../daily_auto/daily_checklist.md) · [自動化範圍](../daily_auto/automation.md)', '',
        '## 重現與證據', '',
        '```bash', 'python3 scripts/run_official_v2.py --output outputs/official_v2_reproduction',
        'python3 scripts/audit_official_v2.py --output outputs/official_v2_reproduction/historical_pit',
        'python3 scripts/audit_official_v2.py --output outputs/official_v2_reproduction/official_ex_post', '```', '',
        '執行器拒絕覆寫已有結果；獨立稽核綁定程式、資料、設定和完整輸出雜湊。原 `v2_A_best.py` 不變，公式相容版由 `src/official_v2_review.py` 的隔離介面執行。', '',
        '[歷史池稽核](../outputs/official_v2_reaudit/historical_pit/audit.json) · [官方事後池稽核](../outputs/official_v2_reaudit/official_ex_post/audit.json) · [事前研究規格](../docs/official_v2_protocol.md)', '',
        f'[比較總表]({assets.name}/comparison.csv) · [全部月表]({assets.name}/monthly.csv)', '']
    pd.DataFrame(summary).to_csv(assets/'comparison.csv',index=False)
    pd.concat(monthly_all,ignore_index=True).to_csv(assets/'monthly.csv',index=False)
    body='\n'.join(lines)
    report.with_suffix('.md').write_text(body)
    rendered=markdown.markdown(body,extensions=['tables','fenced_code','md_in_html'])
    # Tables remain accessible on small screens without shrinking text.
    rendered=rendered.replace('<table>','<div class="table-wrap"><table>').replace('</table>','</table></div>')
    css='''body{margin:0;background:#f4f6fa;color:#142638;font:17px/1.75 system-ui,-apple-system,"Noto Sans TC",sans-serif}main{max-width:1080px;margin:0 auto;padding:42px 24px 70px}h1{font-size:34px;line-height:1.3}h2{margin-top:44px;border-top:2px solid #d9e3ec;padding-top:24px}h3{margin-top:28px}a{color:#086b84}p{max-width:960px}strong{color:#083e50}.table-wrap{overflow-x:auto;background:white;border-radius:12px;border:1px solid #dce4ec;margin:20px 0}table{border-collapse:collapse;min-width:700px;width:100%;font-size:15px}th,td{text-align:left;padding:11px 13px;border-bottom:1px solid #e9edf3}th{background:#e7f0f3;white-space:nowrap}tr:nth-child(even){background:#f8fafc}img{max-width:100%;height:auto;border-radius:10px;margin:12px 0}code{font-size:.88em;background:#e9eef3;padding:2px 4px;border-radius:3px}pre{overflow:auto;padding:18px;background:#e9eef3}summary{cursor:pointer;font-weight:700}details{margin:18px 0;padding:14px;background:#eaf1f5;border-radius:10px}@media(max-width:650px){main{padding:24px 14px}h1{font-size:28px}}'''
    report.with_suffix('.html').write_text('<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>v2 A 官方文件複核與回測</title><style>'+css+'</style><main>'+rendered+'</main></html>')
    hashes['scripts/report_official_v2.py']=sha(__file__)
    outputs=[report.with_suffix('.md'),report.with_suffix('.html'),
        *[p for p in assets.glob('*') if p.name!='provenance.json']]
    (assets/'provenance.json').write_text(json.dumps(dict(status='VERIFIED_INPUTS',inputs=hashes,
        outputs={str(p.relative_to(ROOT)):sha(p) for p in outputs if p.is_file()}),indent=2,ensure_ascii=False)+'\n')
    print(str(report.with_suffix('.html')))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=ROOT/'outputs/official_v2_reaudit')
    parser.add_argument('--report',type=Path,default=ROOT/'reports/official_v2_reaudit')
    args=parser.parse_args();build(args.output.resolve(),args.report.resolve())
