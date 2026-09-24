"""Episode registry: 24-session windows on the research calendar.

Adapted from legacy/scripts/v5_data.py (_window, build_registry), with the
AutoTS-first splits fixed by the lead. An episode belongs to a split when its
start date falls in the split range AND all its sessions end before the next
split begins (purge), so no two splits share a session.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

SPLITS = {
    'dev': ('2010-01-01', '2021-12-31'),
    'validation': ('2022-01-01', '2024-12-31'),
    'holdout': ('2025-01-01', '2026-09-30'),
}
OFFSETS = ('month_start', 'mid_month')


@dataclass(frozen=True)
class Episode:
    episode_id: str
    split: str
    prior_session: pd.Timestamp       # D-1 of the first trading day (sizing close / NAV)
    sessions: tuple[pd.Timestamp, ...]

    @property
    def start(self) -> pd.Timestamp:
        return self.sessions[0]

    @property
    def end(self) -> pd.Timestamp:
        return self.sessions[-1]


def build_episodes(calendar: pd.DatetimeIndex, split: str, length: int = 24,
                   offsets: tuple[str, ...] = ('month_start',), data_end=None, start=None) -> list[Episode]:
    """Chronological episodes of one split.

    ``month_start`` anchors on the first session of each month, ``mid_month`` on
    the first session on/after the 15th. ``data_end`` truncates the calendar to
    sessions that have data. ``start`` drops episodes that begin before it.
    """
    if split not in SPLITS:
        raise ValueError(f'Unknown split {split}')
    unknown = set(offsets) - set(OFFSETS)
    if unknown:
        raise ValueError(f'Unknown offsets {sorted(unknown)}')
    lo, hi = map(pd.Timestamp, SPLITS[split])
    if start is not None:
        lo = max(lo, pd.Timestamp(start))
    if data_end is not None:
        calendar = calendar[calendar <= pd.Timestamp(data_end)]
    episodes = []
    for month in pd.period_range(lo, hi, freq='M'):
        for offset in offsets:
            target = month.to_timestamp() + (pd.Timedelta(days=14) if offset == 'mid_month' else pd.Timedelta(0))
            start = int(calendar.searchsorted(target))
            if start < 1 or start + length > len(calendar) or calendar[start].to_period('M') != month:
                continue
            sessions = tuple(calendar[start:start + length])
            if sessions[-1] > hi:
                continue  # purge: would run into the next split
            suffix = '' if offset == 'month_start' else 'm'
            episodes.append(Episode(f'{split}_{month.year}_{month.month:02d}{suffix}', split,
                                    calendar[start - 1], sessions))
    return sorted(episodes, key=lambda e: e.start)


def select(episodes: list[Episode], n: int | None) -> list[Episode]:
    """``n`` evenly spaced episodes (deterministic), or all when ``n`` is None."""
    if n is None or n >= len(episodes):
        return list(episodes)
    index = np.unique(np.linspace(0, len(episodes) - 1, n).round().astype(int))
    return [episodes[i] for i in index]
