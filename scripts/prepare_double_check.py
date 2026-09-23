"""Freeze global coverage and an exhaustive, explicitly bounded local grid."""
from pathlib import Path
import copy
import itertools
import json
import math
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scipy.stats import qmc
from scripts.prepare_official_deep import apply_axis, canonical

PAIR_KEYS = {'return_pair': ['return_short', 'return_long'],
             'ema_pair': ['ema_fast', 'ema_slow'],
             'macd_tuple': ['macd_fast', 'macd_slow', 'macd_signal']}


def design():
    old = json.loads((ROOT / 'config/official_deep_study.json').read_text())
    local = json.loads((ROOT / 'outputs/full_tuned_v2/local_candidates.json').read_text())['candidates']
    anchor = json.loads((ROOT / 'outputs/full_tuned_v2/selection.json').read_text())['params']
    spaces = copy.deepcopy(old['spaces'])
    spaces['four_hour_mode'] = ['strict', 'coverage_only']
    trials, seen = [], set()
    def add(params, phase, varied):
        key = canonical(params)
        if key not in seen:
            seen.add(key)
            trials.append(dict(candidate_id=f'd{len(trials):04d}', params=params, phase=phase, varied=varied))
    # Include the incumbent first and replay every old candidate under the new
    # accounting, without reusing historical performance summaries.
    add(anchor, 'incumbent', 'none')
    for trial in old['candidates'] + local:
        add(trial['params'], 'old_design_replayed', trial.get('varied', 'joint'))
    for axis, values in spaces.items():
        for value in values:
            add(apply_axis(anchor, axis, value), 'all_axis_values', axis)
    numeric = {k: v for k, v in spaces.items() if k != 'four_hour_mode'}
    for sample in qmc.Sobol(d=len(numeric), scramble=True, seed=2026092301).random_base2(7):
        p = copy.deepcopy(anchor)
        for (axis, values), x in zip(numeric.items(), sample):
            p = apply_axis(p, axis, values[min(int(x * len(values)), len(values) - 1)])
        for mode in spaces['four_hour_mode']:
            add({**p, 'four_hour_mode': mode}, 'sobol', 'joint_all_axes')
    return dict(study_id='v2_double_check_fintuned', spaces=spaces, candidates=trials,
        raw_cartesian_combinations=math.prod(len(v) for v in spaces.values()),
        exhaustive_global=False, tracks=['official_ex_post', 'historical_pit'],
        selection_track='official_ex_post', primary_metric='BOOK_NAV_NET_TOTAL_RETURN',
        local_policy=dict(axes=['target_count', 'one_day_chase_return', 'return_pair',
                          'ema_pair', 'macd_tuple', 'cash_guard_ratio', 'four_hour_mode'],
                          values_per_axis=2, raw_combinations=128,
                          neighbor='parent plus next discrete value; lower at upper boundary',
                          parent='best eligible official_ex_post development return'),
        claim_scope='DEVELOPMENT_ONLY_NO_UNSEEN_HOLDOUT',
        stop='all frozen global candidates, all128 local combinations (effective dedup), independent audit',
        official_compliance='UNKNOWN_BLOCK_SUBMISSION')


def local_grid(study, parent):
    choices = {}
    for axis in study['local_policy']['axes']:
        values = study['spaces'][axis]
        value = [parent[k] for k in PAIR_KEYS[axis]] if axis in PAIR_KEYS else parent[axis]
        if value in values:
            index = values.index(value)
            neighbor = values[index + 1] if index + 1 < len(values) else values[index - 1]
        else:
            raise ValueError('Local parent outside declared grid: ' + axis)
        choices[axis] = [value, neighbor]
    raw, seen = [], set()
    global_keys = {canonical(t['params']): t['candidate_id'] for t in study['candidates']}
    trials, coverage = [], []
    for combination in itertools.product(*choices.values()):
        p = copy.deepcopy(parent)
        for axis, value in zip(choices, combination):
            p = apply_axis(p, axis, value)
        key = canonical(p)
        if key in global_keys:
            candidate_id = global_keys[key]
        elif key in seen:
            candidate_id = next(t['candidate_id'] for t in trials if canonical(t['params']) == key)
        else:
            seen.add(key)
            candidate_id = f'g{len(trials):04d}'
            trials.append(dict(candidate_id=candidate_id, params=p, phase='exhaustive_local_grid', varied='joint7'))
        coverage.append(dict(candidate_id=candidate_id, values=dict(zip(choices, combination))))
    return dict(choices=choices, raw_combinations=len(coverage), coverage=coverage, candidates=trials)


if __name__ == '__main__':
    path = ROOT / 'config/v2_double_check_study.json'
    if path.exists():
        raise FileExistsError(path)
    value = design()
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in value.items() if k not in ('spaces', 'candidates')}, indent=2))
    print('global candidates', len(value['candidates']))
