"""Walk-forward LightGBM-v2 rankings for panic decision days (docs/hybrid_spec.md section 6).

    PYTHONHASHSEED=0 .venv/bin/python -m hybrid.walkforward --period eval --workers 10
    PYTHONHASHSEED=0 .venv/bin/python -m hybrid.walkforward --period test --workers 10   # only after PASS

Refits sit on the global calendar every REFIT_EVERY sessions from the period
start. The refit at R trains on rows t >= DATA_START whose 10-session label
ends by R-1 (checked, a violation raises), split 80/20 by date with a 10-date
purge, early-stopped on Pearson, then refit on all rows (method C). Decision
day D in [R, next refit) is ranked from features at D-1 with that model. Only
blocks holding a panic decision day are fitted: normal days use Mom20.
Output: data/hybrid/predictions_<variant>_<period>.parquet.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

from competition.data import load_market
from competition.rules import UNIVERSE_PATH
from hybrid.features import HORIZON, VARIANTS, build_panel
from hybrid.gate import panic_table
from hybrid.sources import DATA
from lgbm_strategy import model
from lgbm_strategy.dataset import split_dates

DATA_START = '2014-01-01'
REFIT_EVERY = 5
PERIODS = {'eval': ('2015-01-01', '2024-12-31'), 'test': ('2025-01-01', '2026-09-30')}
PANEL = DATA / 'panel.parquet'

_PANEL: pd.DataFrame | None = None
_CALENDAR: pd.DatetimeIndex | None = None


def symbols_by_ticker() -> dict:
    frame = pd.read_csv(UNIVERSE_PATH, dtype={'ticker': str})
    return dict(zip(frame.ticker, frame.yahoo_symbol))


def market_since_start():
    return load_market().since(DATA_START)


def build_and_save_panel() -> pd.DataFrame:
    market = market_since_start()
    panel = build_panel(market, pd.read_csv(DATA / 'revenue.csv', dtype={'ticker': str}),
                        pd.read_csv(DATA / 'flows.csv', dtype={'ticker': str}), symbols_by_ticker())
    panel.to_parquet(PANEL)
    return panel


def schedule(calendar: pd.DatetimeIndex, benchmark_ret: pd.Series, period: str, all_days: bool = False) -> dict:
    """refit date -> decision days it serves (panic days only, or every day for the daily spec)."""
    lo, hi = map(pd.Timestamp, PERIODS[period])
    gate = panic_table(benchmark_ret).panic.reindex(calendar).fillna(False)
    days = calendar[(calendar >= lo) & (calendar <= hi)]
    refits = days[::REFIT_EVERY]
    blocks = {}
    for i, refit in enumerate(refits):
        nxt = refits[i + 1] if i + 1 < len(refits) else hi + pd.Timedelta(days=1)
        block = [d for d in days[(days >= refit) & (days < nxt)] if all_days or gate.iloc[calendar.get_loc(d) - 1]]
        if block:
            blocks[refit] = block
    return blocks


def _init(panel_path: str):
    global _PANEL, _CALENDAR
    _PANEL = pd.read_parquet(panel_path)
    _CALENDAR = _PANEL.index.get_level_values('date').unique().sort_values()


def training_rows(panel: pd.DataFrame, calendar: pd.DatetimeIndex, refit: pd.Timestamp) -> tuple[pd.DataFrame, dict]:
    """Rows whose label ends by the session before ``refit``; raises if any label ends later."""
    cutoff = calendar.get_loc(refit) - 1
    last_origin = calendar[cutoff - HORIZON]
    dates = panel.index.get_level_values('date')
    rows = panel[(dates >= pd.Timestamp(DATA_START)) & (dates <= last_origin)]
    rows = rows[rows.ready & rows.label.notna()]
    label_end = calendar[calendar.get_loc(rows.index.get_level_values('date').max()) + HORIZON]
    if label_end > calendar[cutoff]:
        raise RuntimeError(f'Label ends {label_end.date()} after the cutoff {calendar[cutoff].date()}')
    return rows, dict(cutoff=str(calendar[cutoff].date()), latest_label_end=str(label_end.date()))


def fit_and_predict(task: tuple) -> list[dict]:
    refit, days, variant = task
    started = time.perf_counter()
    rows, audit = training_rows(_PANEL, _CALENDAR, refit)
    window = rows.index.get_level_values('date').unique().sort_values()
    train_dates, val_dates = split_dates(window, HORIZON)
    dates = rows.index.get_level_values('date')
    cols = VARIANTS[variant]
    train, val = rows[dates.isin(train_dates)].reset_index(), rows[dates.isin(val_dates)].reset_index()
    fitted = model.fit(train, val, cols, refit_on_all=True)
    out = []
    for day in days:
        t = _CALENDAR[_CALENDAR.get_loc(day) - 1]
        today = _PANEL.xs(t, level='date')
        today = today[today.ready]
        score = model.predict(fitted, today.reset_index())
        out += [dict(decision_date=day, symbol=s, score=float(v), refit_date=refit, variant=variant)
                for s, v in zip(today.index, score.to_numpy())]
    seconds = time.perf_counter() - started
    print(f'{variant} {refit.date()} days={len(days)} trees={fitted.metadata["best_iteration"]} '
          f'rows={len(rows)} {seconds:.0f}s', flush=True)
    return out + [dict(decision_date=None, symbol='__audit__', score=float(fitted.metadata['best_iteration']),
                       refit_date=refit, variant=variant, **audit)]


def run(period: str, workers: int, variants=tuple(VARIANTS), all_days: bool = False) -> dict:
    if not PANEL.exists():
        build_and_save_panel()
    market = market_since_start()
    blocks = schedule(market.calendar, market.benchmark_ret, period, all_days)
    suffix = f'{period}_daily' if all_days else period
    tasks = [(refit, days, v) for v in variants for refit, days in blocks.items()]
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(str(PANEL),)) as pool:
        results = [r for part in pool.map(fit_and_predict, tasks) for r in part]
    frame = pd.DataFrame(results)
    report = {}
    for v in variants:
        rows = frame[(frame.variant == v) & (frame.symbol != '__audit__')]
        rows.drop(columns=[c for c in ('cutoff', 'latest_label_end') if c in rows]).to_parquet(
            DATA / f'predictions_{v}_{suffix}.parquet')
        audit = frame[(frame.variant == v) & (frame.symbol == '__audit__')]
        report[v] = dict(refits=len(audit), decision_days=int(rows.decision_date.nunique()),
                         median_trees=float(audit.score.median()))
    (DATA / f'walkforward_{suffix}.json').write_text(json.dumps(report, indent=1))
    print(report, flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--period', choices=sorted(PERIODS), default='eval')
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--rebuild-panel', action='store_true')
    parser.add_argument('--all-days', action='store_true', help='predict every day (docs/lgbm_v2_daily_spec.md)')
    parser.add_argument('--variants', nargs='+', default=list(VARIANTS), choices=list(VARIANTS))
    args = parser.parse_args(argv)
    if args.rebuild_panel or not PANEL.exists():
        build_and_save_panel()
    run(args.period, args.workers, tuple(args.variants), args.all_days)


if __name__ == '__main__':
    main()
