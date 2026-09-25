import unittest

import pandas as pd

from competition import episodes as ep
from competition.data import load_calendar


class EpisodeSplitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calendar = load_calendar()
        cls.splits = {s: ep.build_episodes(cls.calendar, s, offsets=ep.OFFSETS, data_end='2026-09-23')
                      for s in ep.SPLITS}

    def test_windows_are_24_consecutive_sessions(self):
        pos = {d: i for i, d in enumerate(self.calendar)}
        for episodes in self.splits.values():
            for e in episodes:
                self.assertEqual(len(e.sessions), 24)
                idx = [pos[d] for d in e.sessions]
                self.assertEqual(idx, list(range(idx[0], idx[0] + 24)))
                self.assertEqual(pos[e.prior_session], idx[0] - 1)

    def test_splits_chronological_and_disjoint(self):
        order = ['dev', 'validation', 'holdout']
        for a, b in zip(order, order[1:]):
            last_a = max(e.end for e in self.splits[a])
            first_b = min(e.start for e in self.splits[b])
            self.assertLess(last_a, first_b)                 # no shared session (purged boundary)
        ids = [e.episode_id for s in order for e in self.splits[s]]
        self.assertEqual(len(ids), len(set(ids)))
        for s, (lo, hi) in ep.SPLITS.items():
            starts = [e.start for e in self.splits[s]]
            self.assertEqual(starts, sorted(starts))
            self.assertTrue(all(pd.Timestamp(lo) <= e.start and e.end <= pd.Timestamp(hi) for e in self.splits[s]))

    def test_anchors(self):
        month = ep.build_episodes(self.calendar, 'dev')
        self.assertEqual(month[0].episode_id, 'dev_2010_01')
        self.assertEqual(month[0].start, self.calendar[self.calendar >= '2010-01-01'][0])
        mid = [e for e in self.splits['dev'] if e.episode_id.endswith('m')]
        self.assertTrue(all(e.start.day >= 15 for e in mid))
        self.assertEqual(len(month), 143)

    def test_start_drops_earlier_episodes_only(self):
        recent = ep.build_episodes(self.calendar, 'dev', offsets=ep.OFFSETS, start='2019-01-01')
        self.assertEqual((recent[0].episode_id, recent[1].episode_id), ('dev_2019_01', 'dev_2019_01m'))
        self.assertEqual(recent, [e for e in self.splits['dev'] if e.start >= pd.Timestamp('2019-01-01')])

    def test_select_is_even_and_deterministic(self):
        month = ep.build_episodes(self.calendar, 'dev')
        pick = ep.select(month, 6)
        self.assertEqual(pick, ep.select(month, 6))
        self.assertEqual((pick[0], pick[-1], len(pick)), (month[0], month[-1], 6))
        self.assertEqual(ep.select(month, None), month)


if __name__ == '__main__':
    unittest.main()
