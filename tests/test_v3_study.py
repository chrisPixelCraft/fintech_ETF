"""Independent examples for the study boundary and evaluation labels."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from src import v3_study as v3


class StudyTests(unittest.TestCase):
    def test_missing_or_changed_contract_is_blocked(self):
        study = v3.load_study()
        for mutate in (lambda s: s.pop('signal_parameters'),
                       lambda s: s.update(status='UNFROZEN'),
                       lambda s: s.update(baseline_candidates={'historical_pit': 'a0036'}),
                       lambda s: s.update(formal_submission='LIVE')):
            bad = copy.deepcopy(study)
            mutate(bad)
            with tempfile.TemporaryDirectory() as folder:
                p = Path(folder) / 'bad.json'
                p.write_text(json.dumps(bad))
                with self.assertRaises(ValueError):
                    v3.load_study(p)

    def test_instrument_split_dividend_and_missing_mark(self):
        dates = ['2024-12-31', '2025-01-02', '2025-01-03', '2025-01-06']
        rows = []
        for i, day in enumerate(dates):
            rows.append(dict(date=day, symbol='X.TW', open=10, high=10, low=10, close=10, volume=1))
            if i != 2:
                close = [100, 49, None, 51][i]
                rows.append(dict(date=day, symbol='0050.TW', open=close, high=close,
                    low=close, close=close, volume=1, split=2 if i == 1 else 1,
                    dividend=2 if i == 1 else 0))
        frame = pd.DataFrame(rows).fillna({'split': 1, 'dividend': 0})
        values = v3.instrument_series(frame)
        for actual, expected in zip(values, [100, 100, 100, 100 * 51 / 49]):
            self.assertAlmostEqual(actual, expected, places=12)

    def test_frozen_versions_only_change_metadata(self):
        study = v3.load_study()
        base = {'strategy_id': 'original', 'replacement_margin': .2,
                'max_replacements_per_day': 0, 'score_weights': {'x': .3}}
        ctx = {'track': 'official_ex_post', 'v3_base': base}
        for name in v3.VERSIONS:
            config = v3.config_for(ctx, name, study)
            config.pop('strategy_id');config.pop('v3')
            self.assertEqual(config, {k: v for k, v in base.items() if k != 'strategy_id'})
        self.assertEqual(base['strategy_id'], 'original')


if __name__ == '__main__':
    unittest.main()
