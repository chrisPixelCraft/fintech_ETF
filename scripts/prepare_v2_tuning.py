"""Freeze a bounded matched A/B/C search before executing any new candidate."""
from pathlib import Path
import copy, json
from datetime import datetime, timezone
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
BASE=dict(target_count=25,replacement_margin=.10,max_replacements_per_day=2,
          volatility_spike_ratio=2.,one_day_chase_return=.07,volume_low=.5,volume_high=3.,
          four_hour_mode='strict',returns='base',ema='base',macd='base',score_profile='base',
          sector_top_fraction=.5,sector_short_weight=8/15,sector_fallback_mode='baseline',c_alpha=.08)
SPACES=dict(target_count=[20,25,30],replacement_margin=[0,.05,.10,.20,.30],max_replacements_per_day=[0,1,2,4],
            volatility_spike_ratio=[1.5,2.,2.5,3.],one_day_chase_return=[.04,.07,.095],
            volume_low=[.3,.5,.8],volume_high=[2.,3.,5.],four_hour_mode=['strict','coverage_only'],
            returns=['fast','base','slow'],ema=['fast','base','slow'],macd=['fast','base','slow'],
            score_profile=['fast','base','slow','balanced'],sector_top_fraction=[.25,.5,.75,1.],
            sector_short_weight=[0.,.5,8/15,1.],sector_fallback_mode=['baseline','total_eligible'],
            c_alpha=[0.,.02,.08,.16,.30])
WEIGHTS={'base':[.4,.35,.1,.05,.05,.05],'fast':[.6,.15,.1,.05,.05,.05],
         'slow':[.15,.6,.1,.05,.05,.05],'balanced':[.3,.3,.15,.1,.1,.05]}

def design():
    entries=[dict(candidate_id='p000',design='incumbent',varied='none',params=BASE.copy())]
    probes=dict(replacement_margin=[0,.05,.2,.3],max_replacements_per_day=[0,1,4],target_count=[20,30],
                volatility_spike_ratio=[1.5,3.],one_day_chase_return=[.04,.095],
                volume_range=[(.3,5.),(.8,2.)],four_hour_mode=['coverage_only'],
                returns=['fast','slow'],ema=['fast','slow'],macd=['fast','slow'],score_profile=['fast','slow','balanced'])
    for k,values in probes.items():
        for value in values:
            params=BASE.copy()
            if k=='volume_range':params.update(volume_low=value[0],volume_high=value[1])
            else:params[k]=value
            entries.append(dict(candidate_id=f'p{len(entries):03}',design='one_factor',varied=k,params=params))
    assert len(entries)==26
    # Stratified marginal sampling; no return observations enter the design.
    rng=np.random.default_rng(20260922);n=38
    samples={k:np.floor((rng.permutation(n)+rng.random(n))/n*len(v)).astype(int) for k,v in SPACES.items()}
    for i in range(n):
        params={k:values[samples[k][i]] for k,values in SPACES.items()}
        entries.append(dict(candidate_id=f'p{len(entries):03}',design='joint_stratified',varied='bundle',params=params))
    core=[{k:v for k,v in e['params'].items() if not k.startswith('sector_') and k!='c_alpha'} for e in entries]
    assert len({json.dumps(p,sort_keys=True) for p in core})==64
    return dict(study_id='v2_abc_tuning_20260922',created_at=datetime.now(timezone.utc).isoformat(),seed=20260922,
                strategies=['A','B','C'],candidates_per_strategy=64,trial_budget=192,spaces=SPACES,score_profiles=WEIGHTS,
                presets={'returns':{'fast':[10,30],'base':[20,50],'slow':[30,90]},
                         'ema':{'fast':[10,30],'base':[20,50],'slow':[30,90]},
                         'macd':{'fast':[8,21,5],'base':[12,26,9],'slow':[19,39,9]}},
                cutoffs=['2025-06-30','2025-12-31','2026-03-31','2026-06-30'],
                selection=['valid_accounting','min_hard_breach_days','min_infeasible_executed_days','max_economic_total_return','min_economic_max_drawdown','candidate_id'],
                hard_breach='cash >= 25% OR active weight-cap OR overdue passive weight-cap; union of session dates',
                historical_period_is_development=True,unseen_test=False,strategic_cash_target=0,
                fixed=['frozen_data','150_historical_universe','VWAP','costs','lot_size','price_buffer','local_allocation','company_actions','long_EMA100_200','4H_indicator_periods','overnight_factor_scales_and_exposures'],
                boundary_policy='Report edge winners as unresolved. No claim of global optimum; no adaptive use of evaluation outcomes.',
                schedule_policy='H1 incumbent; at each cutoff rank candidate continuous ledgers using only dates <= cutoff, then switch parameters within one continuing ledger. Never splice candidate NAVs.',
                candidates=entries)

if __name__=='__main__':
    p=ROOT/'config/v2_tuning_study.json'
    if p.exists():raise FileExistsError('Frozen study already exists')
    p.write_text(json.dumps(design(),ensure_ascii=False,indent=2)+'\n')
    print(p)
