"""Predeclared sparse Stage-2 candidates; never consult episode outcomes."""
from copy import deepcopy
import numpy as np
from src.v4_direct_optimizer import DEFAULT_FEATURES


def _base(family):
    return dict(family=family, score_family='full', short=5, medium=20, long=60,
                target_count=22, max_replacements=1, replacement_margin=.05,
                cash_target=.05, risk_penalty=1., turnover_penalty=0., optimizer='equal',
                confidence=False, regime=False, horizon=5, weighting='inverse_error',
                seed=0, remaining_horizon=False)


def candidates():
    """36 per family, fixed before development; sparse coverage, not a grid.

    Stage labels document variable roles. All configurations are predeclared;
    no outcome-driven expansion or local search after validation is permitted.
    """
    result = []
    m = []
    for score in ('pure', 'trend', 'volume', 'volatility', 'full'):
        m.append(dict(stage='architecture', score_family=score))
    m.append(dict(stage='architecture', score_family='full', optimizer='score'))
    for key, values in (('short', (3,10)), ('medium', (5,10,15)), ('long', (20,30))):
        m.extend(dict(stage='horizon', **{key:v}) for v in values)
    m.extend(dict(stage='horizon', short=s, medium=t, long=l) for s,t,l in
             ((3,5,20),(3,10,30),(10,15,30),(10,10,60),(5,10,20)))
    m.extend(dict(stage='rotation_portfolio', target_count=n) for n in (20,25,28,30))
    m.extend(dict(stage='rotation_portfolio', max_replacements=r) for r in (0,2,3))
    m.extend(dict(stage='rotation_portfolio', replacement_margin=v) for v in (0.,.10,.15,.20))
    m.extend(dict(stage='portfolio', optimizer='score', target_count=n) for n in (20,25,28))
    m.extend(dict(stage='local_neighborhood', score_family=score, cash_target=.12)
             for score in ('pure','trend','volume','volatility'))
    a = []
    a.extend(dict(stage='architecture', optimizer=o, confidence=True, regime=True)
             for o in ('equal','score','continuous','de'))
    a.extend((dict(stage='architecture', confidence=False,regime=True),
              dict(stage='architecture', confidence=True,regime=False,optimizer='score')))
    a.extend(dict(stage='horizon', horizon=h, confidence=c)
             for h in (1,5,10,20) for c in (False,True))
    a.extend(dict(stage='ensemble',weighting=w,horizon=h,confidence=True)
             for w in ('rank_ic','equal') for h in (5,20))
    a.extend(dict(stage='rotation_portfolio',target_count=n,confidence=True)
             for n in (20,25,28,30))
    a.extend(dict(stage='rotation_portfolio',max_replacements=r,confidence=True)
             for r in (0,2,3))
    a.extend(dict(stage='rotation_portfolio',replacement_margin=v,confidence=True)
             for v in (0.,.10,.15,.20))
    a.extend(dict(stage='portfolio',optimizer=o,confidence=True,risk_penalty=2.)
             for o in ('score','continuous','de'))
    a.extend(dict(stage='local_neighborhood',cash_target=c,confidence=True)
             for c in (.12,.15))
    a.extend(dict(stage='local_neighborhood',horizon=5,confidence=True,regime=True,risk_penalty=r)
             for r in (.5,2.))
    assert len(m) == len(a) == 36
    for family, prefix, variants in (('momentum','M',m),('adaptive','A',a)):
        for i,variant in enumerate(variants):
            cfg = _base(family); cfg.update(variant); cfg['id'] = f'{prefix}{i:02d}'
            result.append(cfg)
    rng = np.random.default_rng(20260924)
    coeffs = [np.ones(len(DEFAULT_FEATURES))/len(DEFAULT_FEATURES)]
    for j in range(len(DEFAULT_FEATURES)):
        for sign in (1.,-1.):
            c = np.zeros(len(DEFAULT_FEATURES)); c[j] = sign; coeffs.append(c)
    while len(coeffs) < 36:
        # Fixed local neighborhoods around simple sparse allocation rules.
        anchor = coeffs[(len(coeffs)-13) % 13]
        c = anchor + rng.uniform(-.2,.2,len(anchor))
        coeffs.append(c / max(np.abs(c).sum(),1e-12))
    for i,c in enumerate(coeffs):
        cfg = _base('direct')
        cfg.update(id=f'D{i:02d}', stage='direct_development_portfolio_utility',
                   feature_names=list(DEFAULT_FEATURES),coefficients=c.tolist(),
                   regularization=.01,optimizer=('equal','score','continuous')[(i if i<13 else (i-13)%13)%3],
                   regime=((i if i<13 else (i-13)%13)%2==1))
        result.append(cfg)
    return result


def ablations(selected):
    """Post-freeze, fixed diagnostic interventions; never select using these."""
    def build(label, source, **changes):
        out = deepcopy(source); out.update(changes); out['id'] = label
        out['stage'] = 'post_freeze_ablation'; return out
    m,a,d = (selected[k] for k in ('momentum','adaptive','direct'))
    return [
        build('A1',m,max_replacements=0,confidence=False,regime=False,optimizer='equal'),
        build('A2',m,confidence=False,regime=False,optimizer='equal'),
        build('A3',m,confidence=True,regime=False,optimizer='equal'),
        build('A4',m,confidence=True,regime=True,optimizer='equal'),
        build('A5',m,confidence=True,regime=True,optimizer='continuous'),
        build('A6',a), build('B1',d,regime=False), build('B2',d,regime=True),
        build('adaptive_no_confidence',a,confidence=False),
        build('adaptive_no_regime',a,regime=False),
        build('adaptive_equal',a,optimizer='equal'),
        build('adaptive_remaining',a,horizon='remaining',remaining_horizon=True),
        *[build(f'adaptive_de_seed_{seed}',a,optimizer='de',seed=seed) for seed in (0,1,2)]
    ]


def search_protocol():
    return dict(schema_version=1, candidates=candidates(),
                development=dict(years=[2010,2018],episodes=18,trials_per_family=36,
                                 episode_budget_per_family=648),
                validation=dict(years=[2019,2022],top_development_candidates_per_family=3),
                holdout=dict(years=[2023,2024],frozen_candidates_per_family=1,
                             retuning_allowed=False),
                diagnostics=dict(recent_start=2025,seasonal_only=True,
                                 overlapping_windows_independent=False,de_seeds=[0,1,2]),
                gate=dict(canonical_availability=1.,complete_episode_rate=1.,
                          measured_compliance_pass_rate=1.,noninferiority_tolerance=0.,
                          lower_tail_noninferiority_tolerance=0.,
                          no_valid_plan_days=0,missing_execution_price_days=0,
                          sector_robustness=False,
                          sector_robustness_reason='UNVERIFIED historical sector coverage: existing taxonomy CANDIDATE_PENDING_INDEPENDENT_REVIEW; no authenticated full development/validation/holdout coverage.',
                          de_seed_stability='If selected optimizer is de, seeds0/1/2 must all have complete canonical/measured core episodes and paired development/validation/holdout median/P25/P10 noninferior to V3 at zero tolerance.'),
                coverage='Sparse fixed staged candidates, not an exhaustive Cartesian grid.',
                local_search='Neighborhoods predeclared before development; no validation expansion.',
                neighborhood_policy='Exactly one changed research axis; nearest observed numeric value or categorical single change; Direct coefficient vectors use nearest Euclidean distance <=0.5 with all other settings fixed; all other settings fixed. At least one neighbor, every neighbor 100% canonical/measured/complete on development and paired median/P25/P10 noninferior to V3 at zero tolerance.',
                direct_objective='Hard feasibility gates first, then development median net return minus risk_penalty*MDD minus turnover_penalty*turnover minus 0.01*squared coefficient norm. Realized returns already include costs; no stock-return regression.',
                rank_order=['canonical_availability','measured_feasibility','median_return','p25','p10',
                            'worst_return','mdd','mean_return','turnover','cost','simplicity'])
