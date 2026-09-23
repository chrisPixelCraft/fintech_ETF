"""Metrics for fixed monthly-objective candidates, not hindsight switching."""
from __future__ import annotations

import numpy as np
import pandas as pd


def monthly_returns(equity, initial=1e9):
    dates = pd.to_datetime(equity['date'])
    values = equity['economic_nav'].to_numpy(dtype=float)
    if (not len(values) or not dates.is_monotonic_increasing or dates.duplicated().any()
            or not np.isfinite(values).all() or (values <= 0).any() or initial <= 0):
        raise ValueError('Invalid ordered economic NAV series')
    rows, previous = [], float(initial)
    for month, group in equity.groupby(dates.dt.to_period('M'), sort=True):
        wealth = np.r_[previous, group.economic_nav.to_numpy(dtype=float)]
        end = float(wealth[-1])
        rows.append(dict(month=str(month), sessions=len(group),
                         return_net=end / previous - 1, start_nav=previous, end_nav=end,
                         max_drawdown=float(1 - np.min(wealth / np.maximum.accumulate(wealth)))))
        previous = end
    return rows


def selection_row(candidate_id, equity):
    months = [r for r in monthly_returns(equity) if r['month'].startswith('2025-')]
    if [r['month'] for r in months] != [f'2025-{m:02}' for m in range(1, 13)]:
        raise ValueError('Selection requires all twelve 2025 calendar months')
    returns = [r['return_net'] for r in months]
    turnover = equity.loc[equity.date.astype(str).str.startswith('2025-'), 'turnover'].sum()
    if not np.isfinite(turnover):
        raise ValueError('Invalid selection turnover')
    return dict(candidate_id=candidate_id, median_2025=float(np.median(returns)),
                worst_2025=float(min(returns)), turnover_2025=float(turnover))


def select_candidate(rows):
    if not rows or len({r['candidate_id'] for r in rows}) != len(rows):
        raise ValueError('Empty or duplicated selection candidates')
    if not all(np.isfinite([r['median_2025'], r['worst_2025'], r['turnover_2025']]).all()
               for r in rows):
        raise ValueError('Nonfinite selection scores')
    return min(rows, key=lambda r: (-r['median_2025'], -r['worst_2025'],
                                   r['turnover_2025'], r['candidate_id']))['candidate_id']


def episode_windows(calendar):
    dates = sorted(set(str(d)[:10] for d in calendar))
    live = [d for d in dates if '2025-01-01' <= d <= '2026-09-21']
    if len(live) != 417 or live[-1] != '2026-09-21':
        raise ValueError('Unexpected frozen market calendar')
    windows = [dict(episode='full', kind='continuous', start=live[0], end=live[-1])]
    for month in [f'2025-{m:02}' for m in range(1, 13)] + [f'2026-{m:02}' for m in range(1, 9)]:
        first = next(d for d in live if d.startswith(month))
        position = live.index(first)
        period = live[position:position+25]
        if len(period) != 25:
            raise ValueError('Insufficient complete reset window: ' + month)
        windows.append(dict(episode='m25_' + month, kind='reset_25_sessions',
                            start=first, end=period[-1]))
    windows.append(dict(episode='contest_2025', kind='reset_calendar_analogue',
                        start='2025-10-26', end='2025-11-27'))
    return windows
