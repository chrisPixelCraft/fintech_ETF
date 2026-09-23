"""Predeclare bounded A-only OFAT, paired Sobol, and local development search."""
from pathlib import Path
import copy, json, sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scipy.stats import qmc
from src.tuning_a_deep import from_old, validate_params

SPACES = {
    'target_count': [20, 22, 25, 28, 30],
    'replacement_margin': [0., .025, .05, .1, .2, .3, .4],
    'max_replacements_per_day': [0, 1, 2, 4, 6, 8],
    'volatility_spike_ratio': [1.5, 2., 2.5, 3., 4., 5.],
    'one_day_chase_return': [.03, .04, .055, .07, .085, .095],
    'volume_low': [.2, .3, .5, .8, 1.],
    'volume_high': [2., 3., 5., 8.],
    'return_pair': [[5, 20], [10, 30], [15, 40], [20, 50], [30, 90], [40, 120]],
    'ema_pair': [[5, 20], [10, 30], [15, 40], [20, 50], [30, 90], [50, 150]],
    'macd_tuple': [[6, 13, 4], [8, 21, 5], [12, 26, 9], [16, 35, 9], [19, 39, 9], [24, 52, 12]],
    'momentum_weight': [.55, .65, .75, .85, .95],
    'long_return_fraction': [.15, .2, .35, 7/15, .5, .7, .85, .9],
}
OFAT = {
    'target_count': [20, 25, 30], 'replacement_margin': [0., .05, .1, .2, .4],
    'max_replacements_per_day': [0, 1, 2, 4, 8], 'volatility_spike_ratio': [1.5, 2., 3., 5.],
    'one_day_chase_return': [.03, .055, .07, .095], 'volume_low': [.2, .5, .8, 1.],
    'volume_high': [2., 3., 5.], 'four_hour_mode': ['strict', 'coverage_only'],
    'return_pair': [[5,20], [10,30], [20,50], [40,120]],
    'ema_pair': [[5,20], [10,30], [30,90], [50,150]],
    'macd_tuple': [[6,13,4], [8,21,5], [12,26,9], [24,52,12]],
    'momentum_weight': [.55, .75, .95], 'long_return_fraction': [.15, 7/15, .7, .9],
}


def apply_axis(params, axis, value):
    out = copy.deepcopy(params)
    if axis in ('return_pair', 'ema_pair', 'macd_tuple'):
        keys = {'return_pair': ('return_short', 'return_long'), 'ema_pair': ('ema_fast', 'ema_slow'),
                'macd_tuple': ('macd_fast', 'macd_slow', 'macd_signal')}[axis]
        out.update(zip(keys, value))
    else:
        out[axis] = value
    validate_params(out)
    return out


def canonical(params):
    effective = copy.deepcopy(params)
    # A score-difference gate is inactive when ordinary replacements are disabled.
    if effective['max_replacements_per_day'] == 0:
        effective['replacement_margin'] = 0.
    momentum = effective.pop('momentum_weight')
    long_fraction = effective.pop('long_return_fraction')
    effective['score_weights'] = [round(w, 12) for w in
        [momentum*(1-long_fraction), momentum*long_fraction,
         (1-momentum)*.4, (1-momentum)*.2, (1-momentum)*.2, (1-momentum)*.2]]
    return json.dumps(effective, sort_keys=True, separators=(',', ':'))


def design():
    anchors = {}
    for track, name in [('historical_pit', 'anchor_p052'), ('official_ex_post', 'anchor_p049')]:
        cfg = json.loads((ROOT/f'outputs/tuning_report_2nd_try/{track}/final/A/config.json').read_text())
        anchors[name] = from_old(cfg)
    trials, seen = [], set()
    def add(params, phase, varied, anchor=None, seed=None, pair=None):
        key = canonical(params)
        if key in seen:
            return
        seen.add(key)
        trials.append(dict(candidate_id=f'a{len(trials):04d}', params=params, phase=phase,
                           varied=varied, anchor=anchor, search_seed=seed, pair_id=pair))
    for name, params in anchors.items():
        add(params, 'incumbent', 'none', anchor=name)
    for name, params in anchors.items():
        for axis, values in OFAT.items():
            for value in values:
                add(apply_axis(params, axis, value), 'ofat', axis, anchor=name)
    # Same nuisance configuration gets both 4H conditions, with equal budgets.
    for seed in [20260922, 20260923]:
        samples = qmc.Sobol(d=len(SPACES), scramble=True, seed=seed).random_base2(5)
        for i, sample in enumerate(samples):
            params = copy.deepcopy(anchors['anchor_p052'])
            for (axis, choices), value in zip(SPACES.items(), sample):
                params = apply_axis(params, axis, choices[min(int(value*len(choices)), len(choices)-1)])
            for mode in ['strict', 'coverage_only']:
                params = {**params, 'four_hour_mode': mode}
                add(params, 'sobol', 'joint_bundle', seed=seed, pair=f'{seed}_{i:02d}')
    return dict(study_id='a_deep_tuning_20260922', status='FROZEN', start='2025-01-01', end='2026-09-21',
        tracks=['historical_pit', 'official_ex_post'], phases=['incumbent_and_ofat', 'paired_sobol', 'local_refinement'],
        exploration_trials=len(trials), local_trials_per_track=64, candidates=trials,
        spaces=SPACES, ofat=OFAT, search_seeds=[20260922, 20260923],
        fixed_execution='SECOND_ROUND_UNCHANGED', primary='ZERO_MEASURED_HARD_THEN_NET_RETURN_THEN_MDD',
        secondary='MAX_RETURN_WITH_MDD_NOT_ABOVE_SAME_TRACK_INCUMBENT',
        refinement=dict(parent_rule='top return and risk-controlled per 4H mode, deduplicated',
                        trials=64, seed=20260924, mutations=[1,2,3],
                        boundary_extensions={'max_replacements_per_day': [12], 'volatility_spike_ratio': [8.],
                            'return_pair': [[50,160]], 'ema_pair': [[60,180]],
                            'macd_tuple': [[30,65,15]], 'volume_high': [10.]},
                        note='Boundary extensions are available in local stage; no bounds beyond competition rules'),
        interpretation='EX_POST_DEVELOPMENT_NOT_UNSEEN_TEST', formal_compliance='UNKNOWN_BLOCK_SUBMISSION')


if __name__ == '__main__':
    path=ROOT/'config/a_deep_study.json'
    if path.exists(): raise FileExistsError('Preserve existing declared study')
    result=design();path.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    print(f"{result['exploration_trials']} exploration + 64 refinement per track")
