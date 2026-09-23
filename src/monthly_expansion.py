"""Bounded entry-feasibility search for fixed 25-session portfolios."""
from __future__ import annotations
import copy
import itertools
import math
import statistics

from src.double_check_tuning import eligible
from src.official_deep_tuning import validate_params

ANCHORS = ('x0352', 'x0454')
AXES = dict(ema_slow=[75, 100, 150, 200], volume_low=[0.4, 0.6, 0.8],
            cash_guard_ratio=[0.10, 0.12, 0.14])
TRAIN = tuple(f'm25_2025-{month:02}' for month in range(1, 12))
SCREEN = 'm25_2025-04'


def candidates(anchor_params):
    rows = []
    for anchor in ANCHORS:
        for values in itertools.product(*AXES.values()):
            params = copy.deepcopy(anchor_params[anchor])
            params.update(zip(AXES, values))
            validate_params(params)
            rows.append(dict(candidate_id=f'mx{len(rows):04}', anchor=anchor, params=params))
    return rows


def strict(row):
    return eligible(dict(row, status='COMPLETE'))


def ranking(records, trials):
    """Incomplete or noncompliant training evidence never enters ranking."""
    identities = [(r['track'], r['candidate_id'], r['episode']) for r in records]
    if len(set(identities)) != len(identities):
        raise ValueError('Duplicated evaluation identity')
    rows = []
    for trial in trials:
        train = [r for r in records if r['track'] == 'official_ex_post'
                 and r['candidate_id'] == trial['candidate_id'] and r['episode'] in TRAIN]
        passed = sum(strict(r) for r in train)
        valid = len(train) == len(TRAIN) and passed == len(TRAIN)
        values = [r['economic_total_return'] for r in train]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('Nonfinite return in accounting evidence')
        rows.append(dict(candidate_id=trial['candidate_id'], anchor=trial['anchor'],
                         evaluated_train_windows=len(train), eligible_train_windows=passed,
                         training_eligible=valid,
                         median_return=statistics.median(values) if valid else None,
                         worst_return=min(values) if valid else None,
                         mean_turnover=statistics.mean(r['turnover_two_way'] for r in train) if valid else None))
    valid = [r for r in rows if r['training_eligible']]
    selected = min(valid, key=lambda r: (-r['median_return'], -r['worst_return'],
                                        r['mean_turnover'], r['candidate_id'])) if valid else None
    return rows, selected['candidate_id'] if selected else None
