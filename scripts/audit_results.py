"""Verify real-run accounting and prefix invariance independently of saved metrics."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal
from src.backtest import aggregate_four_hour, file_hash, run_backtest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='outputs/backtest_2026_v1')
    args = parser.parse_args()
    output = ROOT / args.output
    provenance = json.loads((output / 'provenance.json').read_text())
    for relative, expected in provenance['hashes'].items():
        assert file_hash(ROOT / relative) == expected, f'Input/code changed: {relative}'
    config = json.loads((output / 'v1/config.json').read_text())
    equity = pd.read_csv(output / 'v1/equity.csv', dtype={'date': str})
    holdings = pd.read_csv(output / 'v1/holdings.csv', dtype={'date': str, 'symbol': str})
    trades = pd.read_csv(output / 'v1/trades.csv', dtype={'date': str, 'symbol': str})
    daily_path = next(ROOT / p for p in provenance['hashes'] if p.endswith('daily_canonical.csv'))
    hourly_path = next(ROOT / p for p in provenance['hashes'] if p.endswith('hourly_canonical.csv'))
    daily = pd.read_csv(daily_path, dtype={'symbol': str, 'date': str})
    hourly = pd.read_csv(hourly_path, dtype={'symbol': str})
    universe = pd.read_csv(ROOT / 'data/processed/universe_20251231.csv', dtype={'symbol': str})
    universe['known_at'] = universe['known_at_assumption']
    reconstructed_cash = config['initial_cash']
    previous_holdings = {}
    dividends = 0.
    for idx, row in equity.iterrows():
        day = row['date']
        prices = daily[daily.date == day].set_index('symbol')
        for s, q in previous_holdings.items():
            if s in prices.index:
                dividends += q * prices.at[s, 'dividend']
        fills = trades[trades.date == day]
        reconstructed_cash -= float((fills.shares * fills.price + fills.fee + fills.tax).sum())
        if idx == len(equity) - 1:
            reconstructed_cash += dividends
        assert np.isclose(reconstructed_cash, row['cash'], atol=.001, rtol=1e-12), f'Cash ledger: {day}'
        positions = holdings[holdings.date == day]
        marked_value = float((positions.shares * positions.close).sum())
        assert np.isclose(marked_value + row['cash'], row['nav'], atol=.001, rtol=1e-12), f'NAV: {day}'
        assert np.isclose(row['nav'] + row.dividend_receivable, row.economic_nav, atol=.001, rtol=1e-12), f'Receivable: {day}'
        previous_holdings = dict(zip(positions.symbol, positions.shares))
    assert (pd.to_datetime(trades.signal_date) < pd.to_datetime(trades.date)).all()
    assert np.allclose(trades.shares % config['lot_size'], 0.), 'Every actual trade must be a whole lot'
    metrics = json.loads((output / 'v1/metrics.json').read_text())
    assert np.isclose(equity.nav.iloc[-1] / config['initial_cash'] - 1, metrics['total_return'])
    assert np.isclose(float((trades.fee + trades.tax).sum()), metrics['transaction_costs'])
    cutoff = '2026-06-30'
    shortened_config = {**config, 'end': cutoff}
    short_daily = daily[daily.date <= cutoff]
    short_hourly = hourly[hourly.date <= cutoff]
    prefix = run_backtest(short_daily, universe, shortened_config, aggregate_four_hour(short_hourly))
    for name, datecol in [('orders', 'signal_date'), ('signals', 'date')]:
        full = pd.read_csv(output / f'v1/{name}.csv')
        full = full[full[datecol] <= cutoff].reset_index(drop=True)
        observed = prefix[name].reset_index(drop=True)
        assert_frame_equal(full, observed, check_dtype=False, check_exact=False, rtol=1e-12, atol=1e-10)
    report = dict(status='PASS', verified_at=datetime.now(timezone.utc).isoformat(), sessions=len(equity), trades=len(trades),
                  checks=['All recorded input/code hashes unchanged', 'Every daily cash balance independently reconciled',
                          'Every daily NAV reconciled to positions plus cash', 'Deferred dividends transfer once',
                          'All trades occur after signal date', 'Final return and costs independently reconciled',
                          'Every executed quantity is an integer multiple of 1000 shares',
                          'Actual-data prefix through 2026-06-30 preserves every signal and order'],
                  engine_hash=file_hash(ROOT / 'src/backtest.py'),
                  scope='Computational reproducibility and causality, not a certification of source completeness or contest eligibility')
    (output / 'audit.json').write_text(json.dumps(report, indent=2) + '\n')
    provenance['trust'] = 'COMPUTATION_VERIFIED_WITH_DISCLOSED_DATA_AND_EXECUTION_LIMITATIONS'
    provenance['audit'] = 'audit.json'
    (output / 'provenance.json').write_text(json.dumps(provenance, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
