"""Diagnostic only: does the LightGBM learning rate (or the early-stopping metric) change out-of-sample ranking?

    PYTHONHASHSEED=0 .venv/bin/python -m research.lr_diagnostic --workers 10

For evenly spaced dev decision dates, the JPX2 raw-return training set is fit
with each learning rate (tree cap and early-stopping patience scale with 1/lr so
small rates are not starved), plus two runs that early-stop on the mean daily
cross-sectional rank IC instead of pooled Pearson. Each fit is scored on its
validation window and, out of sample, on the next OOS_SESSIONS decision days
(daily Spearman rank IC and top-25 excess forward return over the universe).
Future labels are used for scoring only. No strategy, config or holdout is touched.
Results: research/results/lr_diagnostic/.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor

import lightgbm
import numpy as np
import pandas as pd

from competition.data import load_market
from competition.episodes import SPLITS
from competition.rules import ROOT
from lgbm_strategy import model
from lgbm_strategy.dataset import build_training_set
from lgbm_strategy.features import COLUMNS, build_features
from lgbm_strategy.targets import raw_forward_return

RESULTS = ROOT / 'research/results/lr_diagnostic'
HORIZON, LOOKBACK, TOP = 10, 750, 25
OOS_SESSIONS = 20
N_DATES = 20
RATES = (.1, .05, .01, .005, .001, .0005)
VARIANTS = [dict(lr=lr, stop='pearson') for lr in RATES] + [dict(lr=lr, stop='rank_ic') for lr in (.05, .005)]


def daily_rank_ic(dates: np.ndarray, y: np.ndarray, p: np.ndarray) -> float:
    """Mean over dates of the Spearman correlation between prediction and label within the date."""
    frame = pd.DataFrame(dict(d=dates, y=y, p=p))
    ics = [g.y.rank().corr(g.p.rank()) for _, g in frame.groupby('d') if len(g) > 2 and g.p.nunique() > 1]
    return float(np.mean(ics)) if ics else 0.


def top_excess(dates: np.ndarray, y: np.ndarray, p: np.ndarray) -> float:
    """Mean over dates of (mean label of the top-25 predictions) - (mean label of all names)."""
    frame = pd.DataFrame(dict(d=dates, y=y, p=p))
    return float(np.mean([g.nlargest(TOP, 'p').y.mean() - g.y.mean() for _, g in frame.groupby('d')]))


def fit(train: pd.DataFrame, validation: pd.DataFrame, lr: float, stop: str) -> lightgbm.LGBMRegressor:
    params = dict(model.PARAMS, learning_rate=lr, n_estimators=max(3000, int(15 / lr)))
    patience = max(model.EARLY_STOPPING_ROUNDS, int(1.5 / lr))
    dates = validation.date.to_numpy()
    metric = model.pearson_metric if stop == 'pearson' else \
        (lambda y, p: ('rank_ic', daily_rank_ic(dates, y, p), True))
    regressor = lightgbm.LGBMRegressor(**params)
    regressor.fit(train[list(COLUMNS)], train.label, eval_X=(validation[list(COLUMNS)],), eval_y=(validation.label,),
                  eval_metric=metric, callbacks=[lightgbm.early_stopping(patience, verbose=False)])
    return regressor


def oos_rows(market, decision: pd.Timestamp) -> pd.DataFrame:
    """Feature-ready rows on the OOS_SESSIONS days from the decision's cutoff on, with realised raw labels."""
    cal = market.calendar
    i = cal.get_loc(decision) - 1                                   # cutoff D-1 is the first OOS origin
    dates = cal[i:i + OOS_SESSIONS]
    view = market.asof(cal[i + OOS_SESSIONS - 1 + HORIZON])         # features only read rows <= their date
    table = build_features(view, dates)
    label = raw_forward_return(view.ret, HORIZON).reindex(index=dates, columns=sorted(view.ret.columns))
    table['label'] = label.stack(future_stack=True).to_numpy()
    return table[table.feature_ready & np.isfinite(table.label)]


def evaluate_date(decision: str) -> list[dict]:
    market = load_market()
    decision = pd.Timestamp(decision)
    data = build_training_set(market.asof(market.calendar[market.calendar.get_loc(decision) - 1]), HORIZON, LOOKBACK,
                              'raw_return')
    oos = oos_rows(market, decision)
    rows = []
    for v in VARIANTS:
        started = time.perf_counter()
        regressor = fit(data.train, data.validation, **v)
        pv = regressor.predict(data.validation[list(COLUMNS)], num_iteration=regressor.best_iteration_ or None)
        po = regressor.predict(oos[list(COLUMNS)], num_iteration=regressor.best_iteration_ or None)
        rows.append(dict(decision=str(decision.date()), **v, best_iteration=int(regressor.best_iteration_ or 0),
                         shrinkage=v['lr'] * int(regressor.best_iteration_ or 0),
                         val_pearson=model.pearson(data.validation.label.to_numpy(), pv),
                         val_rank_ic=daily_rank_ic(data.validation.date.to_numpy(), data.validation.label.to_numpy(),
                                                   pv),
                         oos_rank_ic=daily_rank_ic(oos.date.to_numpy(), oos.label.to_numpy(), po),
                         oos_top25_excess=top_excess(oos.date.to_numpy(), oos.label.to_numpy(), po),
                         seconds=time.perf_counter() - started))
    return rows


def decision_dates(market) -> list[str]:
    """N_DATES evenly spaced dev sessions from 2019 whose OOS labels end inside dev."""
    cal = market.calendar
    end = cal[cal <= pd.Timestamp(SPLITS['dev'][1])][-(OOS_SESSIONS + HORIZON)]
    pool = cal[(cal >= pd.Timestamp('2019-01-01')) & (cal <= end)]
    return [str(pool[i].date()) for i in np.linspace(0, len(pool) - 1, N_DATES).round().astype(int)]


def summarise(frame: pd.DataFrame) -> pd.DataFrame:
    g = frame.groupby(['stop', 'lr'], sort=False)
    out = g[['best_iteration', 'shrinkage', 'val_pearson', 'val_rank_ic', 'oos_rank_ic', 'oos_top25_excess',
             'seconds']].mean()
    out['oos_rank_ic_se'] = g.oos_rank_ic.std() / np.sqrt(g.size())
    out['oos_top25_excess_se'] = g.oos_top25_excess.std() / np.sqrt(g.size())
    base = frame[(frame.stop == 'pearson') & (frame.lr == .005)].set_index('decision')
    for col in ('oos_rank_ic', 'oos_top25_excess'):
        diff = frame.set_index('decision').groupby(['stop', 'lr'], sort=False)[col].apply(
            lambda s: (s - base[col].reindex(s.index)).mean())
        out[f'{col}_vs_baseline'] = diff
    return out.reset_index()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args(argv)
    dates = decision_dates(load_market())
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        rows = [r for part in pool.map(evaluate_date, dates) for r in part]
    frame = pd.DataFrame(rows)
    RESULTS.mkdir(parents=True, exist_ok=True)
    frame.round(6).to_csv(RESULTS / 'per_date.csv', index=False)
    summary = summarise(frame)
    summary.round(6).to_csv(RESULTS / 'summary.csv', index=False)
    (RESULTS / 'meta.json').write_text(json.dumps(dict(dates=dates, variants=VARIANTS, horizon=HORIZON,
                                                       lookback=LOOKBACK, oos_sessions=OOS_SESSIONS), indent=1))
    print(summary.round(4).to_string(index=False))


if __name__ == '__main__':
    main()
