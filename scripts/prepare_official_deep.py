"""Freeze a finite, reproducible A-core search before reading its returns."""
from pathlib import Path
import copy, json, sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scipy.stats import qmc
from src import official_deep_tuning as full, tuning_a_deep as deep
from scripts.prepare_a_deep import SPACES as OLD_SPACES, OFAT as OLD_OFAT, canonical as old_canonical

SPACES = {**copy.deepcopy(OLD_SPACES), 'cash_guard_ratio': [.08, .10, .12, .14, .16, .18]}
SPACES['max_replacements_per_day'] += [12]
SPACES['return_pair'] += [[50,160]]
SPACES['ema_pair'] += [[60,180]]
SPACES['macd_tuple'] += [[30,65,15]]
SPACES['volume_high'] += [10.]


def apply_axis(params, axis, value):
    p = copy.deepcopy(params)
    keys = {'return_pair': ('return_short','return_long'), 'ema_pair': ('ema_fast','ema_slow'),
            'macd_tuple': ('macd_fast','macd_slow','macd_signal')}
    if axis in keys: p.update(zip(keys[axis], value))
    else: p[axis] = value
    full.validate_params(p)
    return p


def canonical(p):
    return old_canonical({k:p[k] for k in deep.FIELDS}) + '|' + str(p['cash_guard_ratio'])


def design():
    anchors=[]
    for track in ['official_ex_post','historical_pit']:
        cfg=json.loads((ROOT/f'outputs/tuning_report_2nd_try/{track}/final/A/config.json').read_text())
        anchors.append(dict(name=track+'_pinned', params={**deep.from_old(cfg),'cash_guard_ratio':.12}))
    for track in ['official_ex_post','historical_pit']:
        for name in ['return_winner','risk_controlled','strict_winner']:
            path=ROOT/f'outputs/a_deep_tuning/{track}/final/{name}/config.json'
            if path.exists():
                cfg=json.loads(path.read_text())
                anchors.append(dict(name=track+'_'+name,params={**cfg['deep_feature_params'],'cash_guard_ratio':.12}))
    trials=[];seen=set()
    def add(p,phase,varied,anchor=None,seed=None,pair=None):
        key=canonical(p)
        if key in seen:return
        seen.add(key)
        trials.append(dict(candidate_id=f'f{len(trials):04d}',params=p,phase=phase,varied=varied,
            anchor=anchor,search_seed=seed,pair_id=pair))
    for a in anchors: add(a['params'],'anchor','none',a['name'])
    axes={**OLD_OFAT,'cash_guard_ratio':[.08,.10,.12,.14,.16,.18]}
    for a in anchors[:2]:
        for axis,values in axes.items():
            for v in values:add(apply_axis(a['params'],axis,v),'ofat',axis,a['name'])
    for seed in [226091,226092]:
        samples=qmc.Sobol(d=len(SPACES),scramble=True,seed=seed).random_base2(6)
        for i,sample in enumerate(samples):
            p=copy.deepcopy(anchors[0]['params'])
            for (axis,values),x in zip(SPACES.items(),sample):p=apply_axis(p,axis,values[min(int(x*len(values)),len(values)-1)])
            for mode in ['strict','coverage_only']:
                add({**p,'four_hour_mode':mode},'sobol','joint_bundle',seed=seed,pair=f'{seed}_{i}')
    return dict(study_id='full_tuned_v2_official_20260922',status='FROZEN',
        start='2025-01-01',end='2026-09-21',sessions=417,
        scope='V2_A_CORE_ONLY; B/C/v3 NOT REINTRODUCED',
        fields=list(full.FIELDS),spaces=SPACES,ofat=axes,anchors=anchors,candidates=trials,
        exploration_candidates=len(trials),local_candidates=64,tracks=['official_ex_post','historical_pit'],
        primary_metric='BOOK_NAV_NET_TOTAL_RETURN',
        eligibility='ZERO_MEASURED_HARD; ZERO_NO_VALID_PLAN; ZERO_UNFILLED; official track',
        selection='OFFICIAL_RETURN_DESC_MDD_ASC_TURNOVER_ASC_ID_ASC',
        cross_track='SAME_SELECTED_PARAMETERS_REPLAYED_IN_PIT; not separately selected best',
        local_policy=dict(parents='top2 eligible per4H mode plus lowestMDD among top10 each mode',
            seed=226093,mutations=[1,2,3],trials=64,boundaries='use declared expanded SPACES only'),
        frozen_rules=full.FIXED,known_limits=['NO_UNSEEN_HOLDOUT','OFFICIAL_POOL_IS_EX_POST_IN_HISTORY',
            'ACTIVE_SHARE_HISTORY_AND_METHOD_UNKNOWN','FIXED_SHARE_CORPORATE_ORDER_ASSUMPTION',
            'NO_OFFICIAL_WARNING_ROLLBACK_SIMULATED'],
        stop='All declared exploration and 64 local candidates on both tracks, then audit; no endless resweep')


if __name__=='__main__':
    path=ROOT/'config/official_deep_study.json'
    if path.exists():raise FileExistsError(path)
    study=design();path.write_text(json.dumps(study,ensure_ascii=False,indent=2)+'\n')
    print(study['exploration_candidates'], '+',study['local_candidates'],'per track')
