"""Extend the frozen strategy without selecting parameters from observed returns."""
import argparse
import copy
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from src.backtest import aggregate_four_hour, file_hash, run_backtest, save_result
from src.benchmarks import run_buy_hold

LABELS = {'v1': 'Frozen v1', 'without_4h_direction': 'No 4H direction (matched coverage)',
          'without_turnover_margin': 'Zero replacement margin',
          '0050_buy_hold_85pct': '85% 0050 buy & hold'}
COLORS = ['#006B74', '#81B5BB', '#D99C52', '#384B67']


def risk_metrics(values, initial):
    values = np.r_[initial, np.asarray(values, dtype=float)]
    returns = values[1:] / values[:-1] - 1
    std = float(np.std(returns, ddof=1))
    return dict(total_return=float(values[-1] / initial - 1),
                max_drawdown=float(-(values / np.maximum.accumulate(values) - 1).min()),
                annualized_volatility=std * np.sqrt(252),
                sharpe_zero_rf=float(returns.mean() / std * np.sqrt(252)) if std else None)


def write_analytics(output, results, config):
    initial = config['initial_cash']
    base_date = pd.Timestamp(config['start']) - pd.Timedelta(days=1)
    annual, monthly, summaries = [], [], []
    comparisons = {}
    for name, result in results.items():
        eq = result['equity'].copy()
        eq['date'] = pd.to_datetime(eq.date)
        eq = eq.set_index('date')
        metrics = dict(model=name, **result['metrics'])
        metrics.update({'economic_' + k: v for k, v in risk_metrics(eq.economic_nav, initial).items()})
        elapsed_years = (eq.index[-1] - base_date).days / 365.25
        metrics['calendar_cagr'] = float((eq.nav.iloc[-1] / initial) ** (1 / elapsed_years) - 1)
        summaries.append(metrics)
        for basis in ['nav', 'economic_nav']:
            series = pd.concat([pd.Series([initial], index=[base_date]), eq[basis]])
            comparisons.setdefault(basis, {})[name] = series
        previous_nav = previous_economic = initial
        for year, frame in eq.groupby(eq.index.year):
            row = dict(model=name, year=int(year), start=str(frame.index[0].date()),
                       end=str(frame.index[-1].date()), sessions=len(frame),
                       book_return=float(frame.nav.iloc[-1] / previous_nav - 1),
                       economic_return=float(frame.economic_nav.iloc[-1] / previous_economic - 1),
                       book_max_drawdown=risk_metrics(frame.nav, previous_nav)['max_drawdown'],
                       economic_max_drawdown=risk_metrics(frame.economic_nav, previous_economic)['max_drawdown'])
            annual.append(row)
            previous_nav, previous_economic = frame.nav.iloc[-1], frame.economic_nav.iloc[-1]
        previous_nav = previous_economic = initial
        for period, frame in eq.groupby(eq.index.to_period('M')):
            monthly.append(dict(model=name, month=str(period), through=str(frame.index[-1].date()),
                                book_return=float(frame.nav.iloc[-1] / previous_nav - 1),
                                economic_return=float(frame.economic_nav.iloc[-1] / previous_economic - 1)))
            previous_nav, previous_economic = frame.nav.iloc[-1], frame.economic_nav.iloc[-1]
    pd.DataFrame(summaries).to_csv(output / 'summary.csv', index=False)
    pd.DataFrame(annual).to_csv(output / 'annual_returns.csv', index=False)
    pd.DataFrame(monthly).to_csv(output / 'monthly_returns.csv', index=False)
    for basis, columns in comparisons.items():
        pd.DataFrame(columns).rename_axis('date').to_csv(output / f'{basis}_comparison.csv')
    v1 = results['v1']
    eq, trades, signals = v1['equity'], v1['trades'], v1['signals']
    checks = dict(holding_count_min=int(eq.holdings.min()), holding_count_max=int(eq.holdings.max()),
                  cash_ratio_min=float(eq.cash_ratio.min()), cash_ratio_max=float(eq.cash_ratio.max()),
                  negative_cash_days=int((eq.cash < 0).sum()), raw_violation_days=int(eq.violations.ne('').sum()),
                  violation_types=eq.loc[eq.violations.ne(''), ['date', 'violations']].to_dict('records'),
                  active_trading_days=int(trades.date.nunique()), trade_count=len(trades),
                  above_10pct_volume=int((trades.volume_participation > .1).sum()),
                  above_100pct_volume=int((trades.volume_participation > 1).sum()),
                  unfilled_orders=int(v1['warnings'].issue.eq('UNFILLED_MISSING_PRICE_OR_VOLUME').sum()) if len(v1['warnings']) else 0,
                  infeasible_signal_days=int(signals.loc[signals.plan_reason.str.startswith('INFEASIBLE'), 'date'].nunique()),
                  worst_participation=trades.nlargest(5, 'volume_participation').to_dict('records'),
                  held_2888=bool(v1['holdings'].symbol.eq('2888.TW').any()),
                  receivable_at_penultimate_session=float(eq.economic_nav.iloc[-2] - eq.nav.iloc[-2]) if len(eq) > 1 else 0.)
    (output / 'diagnostics.json').write_text(json.dumps(checks, indent=2, ensure_ascii=False) + '\n')


def run(output):
    paths = {key: ROOT / value for key, value in dict(
        daily='data/extended/processed/daily_canonical.csv',
        hourly='data/extended/processed/hourly_canonical.csv',
        universe='data/extended/processed/universe_20241231.csv',
        config='config/strategy_v1_2025_to_now.json').items()}
    config = json.loads(paths['config'].read_text())
    old = json.loads((ROOT / 'config/strategy_v1.json').read_text())
    changes = {k: [old.get(k), v] for k, v in config.items() if old.get(k) != v}
    if set(changes) - {'start', 'end', 'strategy_id'}:
        raise ValueError(f'Frozen strategy parameters changed: {changes}')
    if (output / 'provenance.json').exists():
        raise FileExistsError('Do not overwrite an existing run; archive an invalid candidate explicitly.')
    daily = pd.read_csv(paths['daily'], dtype={'symbol': str})
    hourly = pd.read_csv(paths['hourly'], dtype={'symbol': str})
    universe = pd.read_csv(paths['universe'], dtype={'symbol': str})
    if 'known_at' not in universe:
        universe['known_at'] = universe['known_at_assumption']
    bars = aggregate_four_hour(hourly)
    output.mkdir(parents=True, exist_ok=True)
    provenance = dict(run_started_at=datetime.now(timezone.utc).isoformat(), command=sys.argv,
                      python=platform.python_version(), pandas=pd.__version__, numpy=np.__version__,
                      inputs={k: str(p.relative_to(ROOT)) for k, p in paths.items()},
                      hashes={str(p.relative_to(ROOT)): file_hash(p) for p in [*paths.values(), ROOT / 'config/strategy_v1.json', ROOT / 'src/backtest.py', ROOT / 'src/benchmarks.py', ROOT / 'scripts/fetch_extended_data.py', Path(__file__)]},
                      config_changes_from_prior_study=changes, trust='CANDIDATE_PENDING_INDEPENDENT_AUDIT',
                      omitted_comparisons={'universe_buy_hold_85pct': '2888 merger delivers 0.672 common + 0.175 preferred shares; multi-asset entitlement ledger not available. Do not omit the stock or fabricate a cash settlement.'},
                      limitations=['Fixed reconstructed Dec-2024 research universe, not the contest universe.',
                                   'Data histories retrieved today, not archived release vintages.',
                                   'Conditional full fills; capacity and contest Active Share not certified.',
                                   'Period-end dividend cash convention; annual economic NAV also reported.'])
    (output / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    bars.groupby('symbol').agg(first=('date', 'min'), last=('date', 'max'), complete_4h_bars=('date', 'size')).to_csv(output / 'four_hour_coverage.csv')
    results = {}
    for name, changes in [('v1', {}), ('without_4h_direction', {'use_4h': False, 'match_4h_coverage': True}),
                          ('without_turnover_margin', {'replacement_margin': 0.})]:
        print(f'Running {name}', flush=True)
        result = run_backtest(daily, universe, {**copy.deepcopy(config), **changes}, bars)
        unsupported = result['holdings'].query("symbol == '2888.TW' and date >= '2025-07-24'")
        if len(unsupported):
            raise ValueError(f'{name}: held 2888 through unsupported two-security merger. No result may be published.')
        save_result(result, output / name)
        results[name] = result
    print('Running 0050 buy & hold', flush=True)
    result = run_buy_hold(daily, ['0050.TW'], copy.deepcopy(config))
    save_result(result, output / '0050_buy_hold_85pct')
    results['0050_buy_hold_85pct'] = result
    calendars = {tuple(r['equity'].date) for r in results.values()}
    if len(calendars) != 1:
        raise ValueError('All comparisons must use the same observed market sessions')
    write_analytics(output, results, config)
    provenance.update(run_completed_at=datetime.now(timezone.utc).isoformat(), outputs_complete=True)
    (output / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(pd.read_csv(output / 'summary.csv')[['model', 'total_return', 'max_drawdown']].to_string(index=False))


def plot(output):
    audit = json.loads((output / 'audit_extension.json').read_text())
    if audit['status'] != 'PASS':
        raise ValueError('Only independently audited output may feed figures')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    config = json.loads((output / 'v1/config.json').read_text())
    equity = pd.read_csv(output / 'economic_nav_comparison.csv', index_col=0, parse_dates=True)
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False,
                         'figure.facecolor': '#FAFCFD', 'axes.facecolor': '#FAFCFD'})
    fig, axes = plt.subplots(2, 1, figsize=(8.5, 6.0), sharex=True,
                             gridspec_kw={'height_ratios': [2, 1]}, constrained_layout=True)
    for (name, nav), color in zip(equity.items(), COLORS):
        axes[0].plot(nav.index, (nav / config['initial_cash'] - 1) * 100, label=LABELS[name], color=color, lw=2.4 if name == 'v1' else 1.4)
        axes[1].plot(nav.index, (nav / nav.cummax() - 1) * 100, color=color, lw=2 if name == 'v1' else 1.2)
    axes[0].set_title('Taiwan trend + momentum | 2025 to Sep 21, 2026', loc='left', fontsize=13, weight='bold')
    axes[0].set_ylabel('Economic net return (%)')
    axes[1].set_ylabel('Drawdown (%)')
    axes[0].legend(loc='upper left', fontsize=9)
    axes[1].set_xlabel('Receivables included; dividends not reinvested.\nNext-open full fills, fees, tax and 5 bps slippage; capacity not established.', fontsize=9)
    for ax in axes:
        ax.grid(axis='y', color='#DFE5E8', linewidth=.7)
        ax.axhline(0, color='#7F8A93', linewidth=.6)
    fig.savefig(output / 'performance.png', dpi=180)
    plt.close(fig)
    eq = pd.read_csv(output / 'v1/equity.csv', parse_dates=['date'])
    fig, axes = plt.subplots(2, 1, figsize=(8.5, 3.8), sharex=True, constrained_layout=True)
    axes[0].plot(eq.date, eq.holdings, color=COLORS[0], lw=1.5)
    axes[0].axhspan(20, 30, color='#DCECE7', alpha=.7)
    axes[0].set_title('v1 | Holdings and available cash', loc='left', fontsize=14, weight='bold')
    axes[0].set_ylabel('Stock count')
    axes[1].plot(eq.date, eq.cash_ratio * 100, color=COLORS[0], lw=1.5)
    axes[1].axhline(25, color='#B34B4B', ls='--', label='Cash must be below 25%')
    axes[1].axhspan(10, 20, color='#DCECE7', alpha=.7)
    axes[1].set_ylabel('Cash (% book NAV)')
    axes[1].legend(fontsize=9)
    for ax in axes:
        ax.grid(axis='y', alpha=.2)
    fig.savefig(output / 'constraints.png', dpi=180)
    plt.close(fig)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['run', 'plot'], default='run')
    parser.add_argument('--output', default='outputs/backtest_2025_to_now_v1')
    args = parser.parse_args()
    (run if args.mode == 'run' else plot)(ROOT / args.output)
