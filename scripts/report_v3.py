"""Build the v3 report only from complete independently audited fixed runs."""
from pathlib import Path
import argparse
import hashlib
import html
import json
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import markdown

TRACKS = ('historical_pit', 'official_ex_post')
MODELS = ('A', 'B', 'C', 'D', 'v1_matched', '0050')
NAMES = {'A': 'A 原策略', 'B': 'B 低 Beta／波動', 'C': 'C 殘差動能',
         'D': 'D 等權融合', 'v1_matched': 'v1 同口徑', '0050': '0050 費後買持'}
COLORS = {'A': '#1d4ed8', 'B': '#138a80', 'C': '#d57b17', 'D': '#8050b4',
          'v1_matched': '#7a8790', '0050': '#c63f5c'}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def table(headers, rows):
    return '\n'.join(['| ' + ' | '.join(headers) + ' |',
        '| ' + ' | '.join(['---'] * len(headers)) + ' |',
        *['| ' + ' | '.join(str(x) for x in row) + ' |' for row in rows]])


def pct(x):
    return f'{float(x)*100:.2f}%'


def pp(x):
    return f'{float(x)*100:+.2f}'


def gate(root):
    hashes = {}
    for track in TRACKS:
        folder = root / track
        audit_path = folder / 'audit.json'
        audit = json.loads(audit_path.read_text())
        if audit.get('status') != 'PASS':
            raise ValueError('Report blocked: independent audit not PASS: ' + track)
        for filename, key in [('receipt.json', 'receipt_sha256'), ('manifest.json', 'manifest_sha256')]:
            if audit.get(key) != sha(folder / filename):
                raise ValueError('Audit does not bind the current ' + filename)
        manifest = json.loads((folder / 'manifest.json').read_text())
        if not manifest['outputs_complete']:
            raise ValueError('Report blocked: incomplete run')
        expected = json.loads((folder / 'receipt.json').read_text())
        for name, digest in expected.items():
            path = folder / name
            if sha(path) != digest:
                raise ValueError('Run artifact changed: ' + str(path))
            hashes[str(path.relative_to(ROOT))] = digest
        for name, digest in manifest['hashes'].items():
            if sha(ROOT / name) != digest:
                raise ValueError('Frozen source changed: ' + name)
            hashes[name] = digest
        for path in (audit_path, folder / 'receipt.json', ROOT / 'scripts/audit_v3.py'):
            hashes[str(path.relative_to(ROOT))] = sha(path)
        if audit.get('auditor_sha256') != sha(ROOT / 'scripts/audit_v3.py'):
            raise ValueError('Auditor changed after PASS')
    hashes['scripts/report_v3.py'] = sha(__file__)
    return hashes


def intervention(folder, comparison):
    rows = []
    for model in ('A', 'B', 'C', 'D'):
        signals = pd.read_csv(folder / 'final' / model / 'signals.csv', low_memory=False)
        # Last close creates no in-window execution, so omit it from intervention counts.
        signals = signals[signals.date.lt('2026-09-21')]
        weak = signals[signals.v3_regime.eq('WEAK')]
        applied = signals[signals.v3_rank_applied]
        changed = 0
        for _, g in applied.groupby('date'):
            eligible = g[g.entry_ok]
            old = tuple(eligible.sort_values(['audit_base_score', 'symbol'], ascending=[False, True]).head(20).symbol)
            new = tuple(eligible.sort_values(['score', 'symbol'], ascending=[False, True]).head(20).symbol)
            changed += old != new
        counts = applied.assign(_valid_entry=applied.v3_factor_valid & applied.entry_ok).groupby('date')['_valid_entry'].sum()
        orders = pd.read_csv(folder / 'final' / model / 'orders.csv')
        buys = orders[orders.shares.gt(0)].merge(signals[['date', 'symbol', 'v3_rank_applied', 'v3_factor_valid']],
            left_on=['signal_date', 'symbol'], right_on=['date', 'symbol'], how='inner', validate='many_to_one')
        rows.append(dict(model=model, executed_signal_days=signals.date.nunique(),
            weak_decision_days=weak.date.nunique(), applied_days=applied.date.nunique(),
            unknown_days=signals[signals.v3_regime.eq('UNKNOWN')].date.nunique(),
            changed_eligible_top20_days=changed,
            applied_days_with_fewer_than_20_valid_entries=int(counts.lt(20).sum()),
            weak_invalid_factor_buy_orders=int((buys.v3_rank_applied & ~buys.v3_factor_valid).sum()),
            baseline_fallback_weak_days=weak[~weak.v3_rank_applied].date.nunique() if model != 'A' else 0))
    return pd.DataFrame(rows)


def conditioned_periods(monthly):
    """Retrospective month-group diagnostics, never a stitched trading curve."""
    rows = []
    for label, mask in [('weak', monthly.weak_month), ('strong_including_partial', ~monthly.weak_month),
                        ('strong_complete_months', ~monthly.weak_month & ~monthly.partial_month)]:
        selected = monthly[mask]
        base = selected[selected.model.eq('A')].set_index('month').economic_return
        for model, group in selected.groupby('model', sort=False):
            compound = float(np.prod(1 + group.economic_return) - 1)
            base_compound = float(np.prod(1 + base.loc[group.month]) - 1)
            rows.append(dict(model=model, period_class=label, months=len(group),
                mean_return=float(group.economic_return.mean()), compounded_selected_month_return=compound,
                relative_selected_month_wealth_A=(1+compound)/(1+base_compound)-1,
                mean_difference_A=float(group.difference_vs_A.mean()),
                months_below_A=int(group.difference_vs_A.lt(0).sum()),
                mean_within_month_mdd=float(group.economic_max_drawdown.mean()),
                worst_within_month_mdd=float(group.economic_max_drawdown.max())))
    return pd.DataFrame(rows)


def plots(folder, assets, track):
    title = 'Historical reconstructed pool' if track == 'historical_pit' else 'Official 2026 pool: membership lookahead'
    nav = pd.read_csv(folder / 'nav_comparison.csv', index_col='date')
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True, gridspec_kw={'height_ratios': [2, 1]})
    for model in MODELS:
        x, y = pd.to_datetime(nav.index), nav[model].to_numpy(float)
        axes[0].plot(x, y / 1e9, color=COLORS[model], label=model)
        axes[1].plot(x, (y / np.maximum.accumulate(y) - 1) * 100, color=COLORS[model])
    axes[0].set_ylabel('Economic NAV / initial capital');axes[1].set_ylabel('Drawdown (%)')
    axes[0].legend(ncol=3);axes[0].set_title(title + '\nFixed development comparison | 2025-01-02 to 2026-09-21')
    for ax in axes:ax.grid(alpha=.2)
    fig.tight_layout();fig.savefig(assets / 'nav_drawdown.png', dpi=160);plt.close(fig)
    monthly = pd.read_csv(folder / 'monthly_comparison.csv')
    weak = monthly[monthly.weak_month]
    months = sorted(weak.month.unique());x = np.arange(len(months));w = .15
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    for j, model in enumerate(('A', 'B', 'C', 'D', '0050')):
        g = weak[weak.model.eq(model)].set_index('month').loc[months]
        axes[0].bar(x + (j-2)*w, g.economic_return*100, width=w, color=COLORS[model], label=model)
        if model != '0050':
            axes[1].bar(x + (j-1.5)*w, g.excess_vs_0050*100, width=w, color=COLORS[model], label=model)
    axes[0].set_title(title + '\nWeak months are EVALUATION-ONLY: 0050 instrument total return < 0')
    axes[0].set_ylabel('Net monthly return (%)');axes[1].set_ylabel('Excess vs costed 0050 (pp)')
    axes[1].set_xticks(x, months);axes[0].legend(ncol=5)
    for ax in axes:ax.axhline(0, color='#333', lw=.8);ax.grid(axis='y', alpha=.2)
    fig.tight_layout();fig.savefig(assets / 'weak_months.png', dpi=160);plt.close(fig)
    fig, axes = plt.subplots(1, 2, figsize=(13, 11))
    for ax, column, label, cmap in zip(axes, ('economic_return', 'economic_max_drawdown'),
            ('Monthly net return (%)', 'Within-month MDD (%)'), ('RdYlGn', 'YlOrRd')):
        panel = monthly.pivot(index='month', columns='model', values=column)[list(MODELS)] * 100
        bound = max(float(np.nanmax(np.abs(panel.to_numpy()))), 1e-9)
        image = ax.imshow(panel, cmap=cmap, aspect='auto',
                          vmin=-bound if column=='economic_return' else 0, vmax=bound)
        fig.colorbar(image, ax=ax, fraction=.035, pad=.02)
        ax.set_title(label)
        ax.set_xticks(np.arange(len(MODELS)), MODELS, rotation=35, ha='right')
        ax.set_yticks(np.arange(len(panel)), [m + ('*' if m=='2026-09' else '') for m in panel.index])
        for i in range(len(panel)):
            for j in range(len(MODELS)):ax.text(j, i, f'{panel.iloc[i,j]:.1f}', ha='center', va='center', fontsize=8)
    fig.suptitle(title + '\n* September through 21st; MDD includes prior month-end NAV')
    fig.tight_layout();fig.savefig(assets / 'monthly_return_mdd.png', dpi=160);plt.close(fig)


def run(source, report):
    hashes = gate(source)
    asset_root = report.parent / 'v3_weak_market_assets'
    if report.exists() or report.with_suffix('.html').exists() or asset_root.exists():
        raise FileExistsError('Report artifacts already exist; preserve verified output')
    asset_root.mkdir(parents=True)
    pieces = ['# v3｜弱市選股實驗結果',
        '本輪完成 A／B／C／D 四組固定策略，兩個股票池分開回放，沒有事後挑每月冠軍或自動切換策略。期間為 2025-01-02 至 2026-09-21，共 417 個完整交易日、本金 10 億元。全部金額績效扣除凍結費稅，主表採含應收股息的 economic NAV。',
        'A 凍結目前公開 `v2_A_best.py` 的 p052／p049，並逐筆重現。上一輪 deep A 的 a0036／a0139 並非本輪 A 對照。B 在事前弱市改用低 Beta／低波動排名，C 用前期回歸模型計算的殘差動能，D 等權融合其百分位；正常市場回到原 A 分數。',
        '「少跌」與「逆勢正報酬」分開判斷。0050 下跌月份是月末才能知道的評估標籤，不能用於決策；交易端只使用前日 0050 校正價格低於 EMA60 且 20 個市場交易日報酬負值的訊號。所有設定在正式回放前凍結，本輪不調參。',
        '缺少可驗證的 Active Share 歷史 ETF 持倉，正式提交仍 UNKNOWN／BLOCK_SUBMISSION。歷史池是重建時點假設，正式名單事後池另含成分股前視；兩者都是已見開發歷史，不是未見樣本外驗證。',
        '[原始規格](../docs/AI_CUP_Trading_Agent_v3.md) · [凍結實驗規格](../docs/v3_protocol.md) · [完整設定](../config/v3_weak_market.json) · [方法與文獻檢查](../docs/v3_methodology_review.md)']
    historical = pd.read_csv(source / 'historical_pit/weak_month_summary.csv').set_index('model')
    positive_base = int(historical.loc['A', 'weak_positive_months'])
    stronger = [m for m in ('B','C','D') if historical.loc[m,'weak_positive_months'] > positive_base]
    conclusion = ('歷史池有版本增加逆勢正報酬月數：' + '、'.join(stronger) + '；仍需評估回撤與強月代價。') if stronger else (
        f'核心結果：歷史池 B／C／D 都沒有增加逆勢正報酬月份，仍與 A 一樣只有 {positive_base}／{int(historical.loc["A","weak_months"])} 個弱月賺錢。部分版本較少跌，不能因此宣稱已解決「弱市找到逆勢上漲公司」的問題。')
    pieces.insert(1, conclusion)
    all_months, all_summaries, all_interventions, all_periods = [], [], [], []
    for track in TRACKS:
        folder = source / track;assets = asset_root / track;assets.mkdir()
        comp = pd.read_csv(folder / 'comparison.csv').set_index('model')
        summary = pd.read_csv(folder / 'weak_month_summary.csv').set_index('model')
        monthly = pd.read_csv(folder / 'monthly_comparison.csv')
        states = pd.read_csv(folder / 'market_state.csv')
        states['execution_date'] = states.date.shift(-1)
        states = states[states.execution_date.between('2025-01-01', '2026-09-21')].copy()
        states['execution_month'] = states.execution_date.str[:7]
        regime_months = states.groupby('execution_month').agg(
            sessions=('date', 'size'), weak_signal_trade_days=('regime', lambda x: int(x.eq('WEAK').sum())),
            unknown_signal_trade_days=('regime', lambda x: int(x.eq('UNKNOWN').sum())))
        regime_months.to_csv(assets / 'regime_by_execution_month.csv')
        periods = conditioned_periods(monthly)
        all_periods.append(periods.assign(track=track))
        diag = intervention(folder, comp)
        diag.to_csv(assets / 'intervention_detail.csv', index=False)
        all_interventions.append(diag.assign(track=track));all_months.append(monthly.assign(track=track))
        all_summaries.append(summary.reset_index().assign(track=track))
        plots(folder, assets, track)
        title = '歷史股票池' if track == 'historical_pit' else '正式名單事後情境'
        pieces += ['## ' + title]
        if track == 'official_ex_post':
            pieces += ['此名單於 2026 年才公布，套回 2025 年包含成分股前視，不能視為當時可部署策略。此軌 A 普通換股上限為 0，排名干預只能透過補位與規則修正等渠道影響實際持倉。']
        met = [m for m in ('B', 'C', 'D') if bool(summary.loc[m, 'development_objective_met'])]
        pieces += [('符合預先定義開發目標：' + '、'.join(met) + '。仍維持 HOLD，沒有自動升級為正式交易。') if met
                   else 'B／C／D 均未同時滿足預先定義的開發目標，因此本輪不能宣稱已解決弱市問題；實作及帳務有效與策略改善成立是兩個不同判斷。']
        pieces += ['這個開發門檻要求弱月平均改善、勝月與正報酬月數不減少、全期回撤不增加及零已量測硬違規；它屬「少跌」篩查，不要求正報酬月數增加。是否真正增加逆勢賺錢月份，必須另讀下表。']
        pieces += [table(['版本', '費後報酬', '最大回撤', '硬違規日', '費稅百萬', '雙向換手'],
            [[NAMES[m], pct(comp.loc[m,'economic_total_return']), pct(comp.loc[m,'economic_max_drawdown']),
              '不適用' if m=='0050' else int(comp.loc[m,'measured_hard_breach_days']),
              f"{comp.loc[m,'transaction_costs']/1e6:.2f}", f"{comp.loc[m,'turnover_two_way']:.2f}×"] for m in MODELS])]
        capacity_model = comp.loc[['A','B','C','D'], 'max_daily_volume_participation'].idxmax()
        pieces += [f'本輪依比賽情境採有均價時全額撮合，市場容量不作資格門檻。此軌 {capacity_model} 最大單筆股數為當日成交量的 {pct(comp.loc[capacity_model,"max_daily_volume_participation"])}；這是實盤容量限制，不能把此模擬解讀為 10 億元可按相同价格實際成交。']
        count = int(summary.loc['A','weak_months'])
        pieces += [f'本軌共有 {count} 個弱月。下表每個平均值均以這些月份等權計算；勝月是相對同口徑費後 0050 買持帳本，正報酬月要求策略自身報酬大於 0。兩者不能互換。',
            table(['版本','弱月均報酬','弱月超額0050','弱月相對A','勝0050月數','正報酬月數','強月相對A'],
            [[NAMES[m],pct(summary.loc[m,'weak_mean_return']),pp(summary.loc[m,'weak_mean_excess_0050']),
              pp(summary.loc[m,'weak_mean_difference_A']),f"{int(summary.loc[m,'weak_outperform_0050_months'])}/{count}",
              f"{int(summary.loc[m,'weak_positive_months'])}/{count}",pp(summary.loc[m,'strong_mean_difference_A'])] for m in MODELS]),
            '差值單位為百分點。完整 CSV 另列相對 0050 工具總報酬的超額，避免現金預算使基準曝險不足而混淆比較。強月是工具月報酬非負；9 月只計至 21 日。',
            f'![弱月報酬與超額](v3_weak_market_assets/{track}/weak_months.png)',
            f'![淨值與回撤](v3_weak_market_assets/{track}/nav_drawdown.png)',
            f'![全部月份報酬與月內回撤](v3_weak_market_assets/{track}/monthly_return_mdd.png)']
        weak = monthly[monthly.weak_month].pivot(index='month', columns='model', values='economic_return')
        pieces += [table(['弱月', 'A', 'B', 'C', 'D', '0050'],
            [[month,*[pct(row[m]) for m in ('A','B','C','D','0050')]] for month,row in weak.iterrows()])]
        pieces += [table(['弱月', '事前弱市交易日', '當月交易日'],
            [[month, int(regime_months.loc[month,'weak_signal_trade_days']), int(regime_months.loc[month,'sessions'])]
             for month in weak.index]),
            '上表按訊號的下一個交易日歸屬月份，沒有把月末訊號算成當月已執行。2026-03 雖是事後弱月，當月沒有任何交易日由弱市排名驅動；2026-07 只有 4 日。這顯示本輪的事前狀態與事後弱月並不重合，不能把弱月失利全歸因於個股排名。']
        weak_period = periods[periods.period_class.eq('weak')].set_index('model')
        strong_full = periods[periods.period_class.eq('strong_complete_months')].set_index('model')
        pieces += [table(['版本','弱月連乘','弱月平均月內MDD','完整強月相對A'],
            [[NAMES[m],pct(weak_period.loc[m,'compounded_selected_month_return']),
              pct(weak_period.loc[m,'mean_within_month_mdd']),pp(strong_full.loc[m,'mean_difference_A'])] for m in MODELS]),
            '完整強月欄排除 2026-09 部分月份，差值仍為每月平均百分點。弱月連乘是事後挑出不連續月份的條件統計，不能當作可交易淨值曲線；所有交易始終使用各版本完整連續帳本。']
        d = diag.set_index('model')
        pieces += [table(['版本','弱市決策日','排名介入日','候選順序改變','有效可進場不足20日','缺因子買單'],
            [[m,*[int(d.loc[m,k]) for k in ('weak_decision_days','applied_days','changed_eligible_top20_days',
              'applied_days_with_fewer_than_20_valid_entries','weak_invalid_factor_buy_orders')]] for m in ('A','B','C','D')]),
            '介入日僅計能在回測期間內執行的訊號。有效可進場不足 20 並不等於違規：策略可能保留既有持股，或因共同最少持股／現金限制使用因子無效股票。這些缺因子買單已明列，不冒稱全部由完整弱市訊號產生。',
            '原 A 的趨勢進場與退出條件完全保留。低 Beta 與正殘差只是不同排名，不能保證絕對上漲；候選仍可能被趨勢門檻擋住。正常日各組分數一致，但先前弱市持倉與交易成本會延續，因此正常市場的 NAV 不必相同。',
            f'[總比較](../outputs/v3_weak_market/{track}/comparison.csv) · [月表](../outputs/v3_weak_market/{track}/monthly_comparison.csv) · [稽核](../outputs/v3_weak_market/{track}/audit.json) · [市場狀態](../outputs/v3_weak_market/{track}/market_state.csv)']
    pd.concat(all_months, ignore_index=True).to_csv(asset_root / 'monthly_comparison.csv', index=False)
    pd.concat(all_summaries, ignore_index=True).to_csv(asset_root / 'weak_month_summary.csv', index=False)
    pd.concat(all_interventions, ignore_index=True).to_csv(asset_root / 'intervention_detail.csv', index=False)
    pd.concat(all_periods, ignore_index=True).to_csv(asset_root / 'conditioned_periods.csv', index=False)
    pieces += ['## 設定與驗證邊界',
        'Beta 與波動率使用 60 筆有效配對日報酬；含截距 OLS 使用當日之前 120 筆有效配對資料，逐日一步預測殘差取最近 20 筆的標準化總和。窗口跨缺漏時可長於相同數量的市場交易日；起訖與觀測數保存在 features.csv。0050 停牌日不補零報酬，未知市場狀態整日回 A。',
        'B／C／D 共用因子完整交集，百分位同分取平均。無效股分數為 0，保留原進出場資格；完整交集少於 20 檔則整日回 A。A 的持股／現金／權重、均價成交、費用及規則修正全部共用，沒有為弱市績效放寬。缺設定拒絕執行，缺認證資料則保持正式阻擋。',
        '獨立稽核核對來源、A 精確重播、原條件保留、弱市排名、帳務與月表。截斷資料重播只證明固定設定的時間一致性，不證明這些設計在 2025 年初已被選定。完整 scope 與截斷日期以各軌 audit.json 為準。',
        '弱月樣本少，且本段歷史已多次用於研究。本輪沒有充分調整 B／C／D 的配套參數，因此只能評價這組預先固定的實作，不能判定所有低風險或殘差動能方法有效或無效。若目標未達成，保留負面證據，不事後修改成功標準。',
        '下一個可採用判斷需要固定版本的新期間資料與完整合規證據。正式提交策略必須在該期間之前指定；本輪不啟用自動選策略。',
        '[完整月表](v3_weak_market_assets/monthly_comparison.csv) · [弱月比較](v3_weak_market_assets/weak_month_summary.csv) · [分組統計](v3_weak_market_assets/conditioned_periods.csv) · [介入明細](v3_weak_market_assets/intervention_detail.csv) · [執行程式](../scripts/run_v3_study.py)',
        '重現命令：`python3 scripts/run_v3_study.py --output outputs/v3_weak_market_reproduction`。程式拒絕覆寫非空結果；稽核後才可生成報告。']
    text = '\n\n'.join(pieces) + '\n'
    report.write_text(text)
    body = markdown.markdown(text, extensions=['tables', 'fenced_code'])
    css = 'body{margin:auto;max-width:1200px;padding:32px;font:16px/1.7 -apple-system,BlinkMacSystemFont,"Noto Sans TC",sans-serif;color:#182333;background:#f7f9fc}h1,h2{line-height:1.3;color:#142d4e}h2{margin-top:48px}table{border-collapse:collapse;display:block;overflow-x:auto;background:white;margin:24px 0}td,th{padding:9px 13px;border-bottom:1px solid #dce3ec;white-space:nowrap;text-align:right}td:first-child,th:first-child{text-align:left}th{background:#e9eff6}img{width:100%;height:auto;background:white}a{color:#155b9b}code{background:#e8edf4;padding:2px 5px}'
    report.with_suffix('.html').write_text('<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>v3 弱市選股實驗</title><style>' + css + '</style><body>' + body + '</body></html>')
    outputs = [report, report.with_suffix('.html'), *[p for p in asset_root.rglob('*') if p.is_file()]]
    provenance = dict(status='GENERATED_FROM_TWO_PASS_AUDITS', created_at=datetime.now(timezone.utc).isoformat(),
        input_hashes=hashes, output_hashes={str(p.relative_to(ROOT)):sha(p) for p in outputs},
        formal_submission='BLOCKED_UNKNOWN_ACTIVE_SHARE', interpretation='FIXED_DEVELOPMENT_COMPARISON')
    (asset_root / 'provenance.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'report':str(report), 'status':provenance['status']}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT / 'outputs/v3_weak_market')
    parser.add_argument('--report', type=Path, default=ROOT / 'reports/v3_weak_market_report.md')
    args = parser.parse_args();run(args.source.resolve(), args.report.resolve())
