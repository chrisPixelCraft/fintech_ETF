"""Create exportable research figures from the saved, audited study outputs."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='outputs/backtest_2026_v1')
    args = parser.parse_args()
    output = ROOT / args.output
    equity = pd.read_csv(output / 'equity_comparison.csv', index_col=0, parse_dates=True)
    config = json.loads((output / 'v1/config.json').read_text())
    labels = {'v1': 'v1: trend + momentum + 4H', 'without_4h_direction': 'Without 4H direction (matched coverage)',
              'without_turnover_margin': 'Without turnover margin', 'universe_buy_hold_85pct': 'Dec-2025 universe: 85% buy & hold',
              '0050_buy_hold_85pct': '0050: 85% buy & hold'}
    colors = {'v1': '#006B74', 'without_4h_direction': '#81B5BB', 'without_turnover_margin': '#D99C52',
              'universe_buy_hold_85pct': '#8E80B9', '0050_buy_hold_85pct': '#384B67'}
    initial = pd.DataFrame({c: [config['initial_cash']] for c in equity}, index=[pd.Timestamp('2025-12-31')])
    equity = pd.concat([initial, equity])
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False, 'figure.facecolor': '#FAFCFD', 'axes.facecolor': '#FAFCFD'})
    fig, axes = plt.subplots(2, 1, figsize=(12, 8.5), sharex=True, gridspec_kw={'height_ratios': [2, 1]}, constrained_layout=True)
    for name in equity:
        nav = equity[name]
        axes[0].plot(nav.index, (nav / config['initial_cash'] - 1) * 100, label=labels[name], color=colors[name], lw=2.5 if name == 'v1' else 1.6)
        axes[1].plot(nav.index, (nav / nav.cummax() - 1) * 100, color=colors[name], lw=2 if name == 'v1' else 1.2)
    axes[0].set_title('Taiwan equities | Frozen trend + momentum v1 | 2026', loc='left', fontsize=16, weight='bold', pad=16)
    axes[0].set_ylabel('Net return (%)')
    axes[1].set_ylabel('Drawdown (%)')
    axes[0].legend(loc='upper left', fontsize=9)
    for ax in axes:
        ax.grid(axis='y', color='#DFE5E8', linewidth=.7)
        ax.axhline(0, color='#7F8A93', linewidth=.6)
    axes[1].set_xlabel('Next-session open; fees, sell tax and 5 bps slippage; dividends credited at period end.\nResearch universe: Dec-2025 top 100 TWSE + 50 TPEx. Active Share unknown; not contest certified.', fontsize=9)
    fig.savefig(output / 'performance.png', dpi=180)
    plt.close(fig)
    daily = pd.read_csv(output / 'v1/equity.csv', parse_dates=['date'])
    fig, axes = plt.subplots(2, 1, figsize=(12, 5.5), sharex=True, constrained_layout=True)
    axes[0].plot(daily.date, daily.holdings, color=colors['v1'], lw=1.6)
    axes[0].axhspan(20, 30, color='#DCECE7', alpha=.7)
    axes[0].set_ylabel('Stock count')
    axes[0].set_title('v1 | Daily feasibility and cash', loc='left', fontsize=15, weight='bold')
    axes[1].plot(daily.date, daily.cash_ratio * 100, color=colors['v1'], lw=1.6)
    axes[1].axhline(25, color='#B34B4B', ls='--', label='Cash must be below 25%')
    axes[1].axhspan(10, 20, color='#DCECE7', alpha=.7)
    axes[1].set_ylabel('Cash (% NAV)')
    axes[1].legend(fontsize=9)
    for ax in axes:
        ax.grid(axis='y', alpha=.2)
    fig.savefig(output / 'constraints.png', dpi=180)
    plt.close(fig)


if __name__ == '__main__':
    main()
