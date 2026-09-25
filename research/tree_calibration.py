"""Stage 1 of the final test: the fixed tree count of LightGBM "D" per learning rate, from pre-2025 data only.

    PYTHONHASHSEED=0 .venv/bin/python -m research.tree_calibration --workers 10

For the first session of every month 2020-01..2024-12 (60 decisions), the
final-test training set is built exactly as in the test (2019 data floor, all
matured history through D-1, raw 10-session return, latest 20% validation) and
each learning rate is early-stopped (model.stopping_schedule). The median
chosen tree count per rate becomes D's fixed tree count. Only tree counts are
recorded: no return is looked at, and every label ends before 2025.
Results: research/results/tree_calibration/{per_date.csv, trees.json}.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from competition.data import load_market
from competition.rules import ROOT
from lgbm_strategy import model
from lgbm_strategy.dataset import build_training_set
from lgbm_strategy.features import COLUMNS

RESULTS = ROOT / 'research/results/tree_calibration'
DATA_START = '2019-01-01'
FIRST, LAST = '2020-01-01', '2024-12-31'
HORIZON, ALL_HISTORY = 10, 100_000
RATES = (.05, .01, .005, .001, .0005)


def decision_dates(calendar: pd.DatetimeIndex) -> list[str]:
    """First session of every month from FIRST to LAST."""
    sessions = calendar[(calendar >= FIRST) & (calendar <= LAST)]
    return [str(d.date()) for d in pd.Series(sessions, index=sessions).groupby(sessions.to_period('M')).first()]


def calibrate_date(decision: str) -> list[dict]:
    market = load_market().since(DATA_START)
    cutoff = market.calendar[market.position(decision) - 1]
    data = build_training_set(market.asof(cutoff), HORIZON, ALL_HISTORY, 'raw_return')
    if pd.Timestamp(data.audit['latest_label_end']) > pd.Timestamp(LAST):
        raise RuntimeError(f'{decision}: a label ends after {LAST}')
    rows = []
    for rate in RATES:
        started = time.perf_counter()
        fitted = model.fit(data.train, data.validation, COLUMNS, learning_rate=rate)
        rows.append(dict(decision=decision, learning_rate=rate, trees=fitted.metadata['best_iteration'],
                         tree_cap=model.stopping_schedule(rate)[0], n_train_rows=data.audit['n_train_rows'],
                         latest_label_end=data.audit['latest_label_end'], seconds=time.perf_counter() - started))
    return rows


def main(argv=None) -> dict:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    dates = decision_dates(load_market().calendar)
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        frame = pd.DataFrame([r for part in pool.map(calibrate_date, dates) for r in part])
    RESULTS.mkdir(parents=True, exist_ok=True)
    frame.round(4).to_csv(RESULTS / 'per_date.csv', index=False)
    stats = frame.groupby('learning_rate').trees.describe(percentiles=[.1, .5, .9])
    trees = {f'{rate:g}': int(round(frame[frame.learning_rate == rate].trees.median())) for rate in RATES}
    (RESULTS / 'trees.json').write_text(json.dumps(dict(
        trees=trees, decisions=len(dates), first=dates[0], last=dates[-1], data_start=DATA_START,
        latest_label_end=frame.latest_label_end.max(),
        spread={f'{r:g}': {k: float(stats.loc[r, k]) for k in ('10%', '50%', '90%', 'min', 'max')} for r in RATES}),
        indent=1))
    print(stats.round(1).to_string())
    print('D trees:', trees)
    return trees


if __name__ == '__main__':
    main()
