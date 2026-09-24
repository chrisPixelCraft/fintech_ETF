#!/usr/bin/env python3
"""Read-only analysis of sealed V4 Stage-2 results. Writes CSVs next to this script.

Conventions
- Return = episode_return (24-session terminal return). NaN = incomplete/disqualified
  (forensic only). Summary stats follow scripts/v4_stage2_report.py stats(): NaN excluded
  from return quantiles, but kept in the attempted denominator.
- Disqualification treatment for pairs: 'exclude' (drop pair), 'minus100' (DQ = -100%,
  i.e. competition loss), 'zero' (forensic partial return, which is 0.0 for every V3 DQ
  because V3 never invested before its 3rd warning).
- Paired uncertainty: exact two-sided sign test (ties dropped) and percentile bootstrap
  (20k resamples of episodes) for the median and mean paired delta.
"""
import sys
sys.dont_write_bytecode = True  # never write .pyc into the sealed repo
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sps

REPO = Path('/Users/chrishsieh/Documents/dev/fintech_ETF')
D = REPO / 'outputs/v4/stage2_verified'
OUT = Path(__file__).resolve().parent
CORE = ['development', 'validation', 'historical_holdout']
DIAG = ['seasonal_diagnostic', 'recent_diagnostic', 'rolling_diagnostic']
FIN = ['A0_V3', 'M00', 'A11', 'D13']
FAILS = ['cash_violation_days', 'weight_violation_days', 'holding_count_violation_days',
         'odd_lot_issue_days', 'missing_execution_price_days', 'unfilled_days',
         'no_valid_plan_days', 'hold_without_envelope_days', 'rejected_trade_count']
RNG = np.random.default_rng(20260924)
NB = 20000

df = pd.read_csv(D / 'all_episodes.csv')
df['measured_pass'] = df.measured_pass.astype(str).str.lower().eq('true')
df['ret'] = pd.to_numeric(df.episode_return, errors='coerce')
configs = json.load(open(D / 'candidates.json'))
cfg = {c['id']: c for c in configs}
cfg.update(json.load(open(D / 'freeze.json'))['evaluation_configs'])

# reuse the repo's predeclared neighborhood definition without modifying the repo
spec = importlib.util.spec_from_file_location('rep', REPO / 'scripts/v4_stage2_report.py')
rep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rep)


def summ(g):
    r = g.ret.dropna()
    q = lambda p: r.quantile(p) if len(r) else np.nan
    return pd.Series(dict(
        attempted=len(g), complete=len(r), canonical=int(g.canonical_status.eq('AVAILABLE').sum()),
        measured_pass=int(g.measured_pass.sum()), median=q(.5), mean=r.mean(), p25=q(.25), p10=q(.1),
        worst=r.min() if len(r) else np.nan, positive=int((r > 0).sum()),
        median_dq_as_minus100=g.ret.fillna(-1.0).median(),
        mdd=pd.to_numeric(g.episode_max_drawdown, errors='coerce').max(),
        mean_mdd=pd.to_numeric(g.episode_max_drawdown, errors='coerce').mean(),
        turnover=pd.to_numeric(g.episode_turnover, errors='coerce').mean(),
        cost_total=pd.to_numeric(g.transaction_cost, errors='coerce').sum(),
        day1=g.day_1_invested_ratio.mean(), day3=g.day_3_invested_ratio.mean(),
        day5=g.day_5_invested_ratio.mean(),
        **{f: int(pd.to_numeric(g[f], errors='coerce').fillna(0).sum()) for f in FAILS}))


def sel_key(s):
    # spec order: canonical -> measured -> median -> P25 -> P10 -> worst -> mean -> turnover
    return (-(s.canonical / s.attempted), -(s.measured_pass / s.attempted), -s['median'], -s.p25,
            -s.p10, -s.worst, -s['mean'], s.turnover)


def pair_vec(a, b, split, treat='exclude'):
    sp = [split] if isinstance(split, str) else list(split)
    x = df[(df.candidate == a) & df.split.isin(sp)].set_index('episode').ret
    y = df[(df.candidate == b) & df.split.isin(sp)].set_index('episode').ret
    idx = sorted(set(x.index) & set(y.index))
    x, y = x.reindex(idx), y.reindex(idx)
    n_dq = int((x.isna() | y.isna()).sum())
    if treat == 'minus100':
        x, y = x.fillna(-1.0), y.fillna(-1.0)
    elif treat == 'zero':
        x, y = x.fillna(0.0), y.fillna(0.0)
    d = (x - y).dropna()
    return d, len(idx), n_dq


def boot(d, fn):
    if len(d) < 2:
        return (np.nan, np.nan)
    v = d.values
    s = fn(v[RNG.integers(0, len(v), (NB, len(v)))], axis=1)
    return tuple(np.percentile(s, [2.5, 97.5]))


def pstats(d, n_att, n_dq):
    w, l = int((d > 1e-12).sum()), int((d < -1e-12).sum())
    p = sps.binomtest(w, w + l).pvalue if w + l else np.nan
    mci, meci = boot(d, np.median), boot(d, np.mean)
    return dict(pairs=len(d), attempted=n_att, pairs_with_dq=n_dq, median=d.median(), p25=d.quantile(.25),
                p10=d.quantile(.1), mean=d.mean(), wins=w, losses=l, ties=len(d) - w - l, sign_p=p,
                med_ci_lo=mci[0], med_ci_hi=mci[1], mean_ci_lo=meci[0], mean_ci_hi=meci[1],
                identical=bool(len(d) and np.allclose(d, 0, atol=1e-12)))


def fmt_save(frame, name):
    frame.to_csv(OUT / name, index=False, float_format='%.6g')
    return frame


# ---------------------------------------------------------------- 1. split tables
fam_of = df.groupby('candidate').family.first()
S = df.groupby(['candidate', 'split'])[df.columns.tolist()].apply(summ).reset_index()
S['family'] = S.candidate.map(fam_of)
fmt_save(S, 'all_candidate_split_stats.csv')

dev = S[S.split == 'development'].copy()
dev['k'] = dev.apply(sel_key, axis=1)
search = dev[dev.candidate.isin([c['id'] for c in configs])]
best = []
for fam, g in search.groupby('family'):
    g = g.sort_values('k')
    g['dev_rank'] = range(1, len(g) + 1)
    best.append(g.head(5))
best = pd.concat(best).drop(columns='k')
fmt_save(best, 'dev_top5_by_family_selection_order.csv')
val_cands = sorted(df[df.split == 'validation'].candidate.unique())
alts = sorted(set(best.groupby('family').head(3).candidate) | set(c for c in val_cands if c in cfg and cfg[c].get('stage') != 'post_freeze_ablation' and not c.startswith('adaptive_')))
t1 = S[S.candidate.isin(set(FIN) | set(alts))].copy()
t1['role'] = np.where(t1.candidate.isin(FIN), 'finalist/baseline', 'alternative')
t1 = t1.sort_values(['split', 'role', 'family', 'candidate'])
fmt_save(t1[t1.split.isin(CORE)], 't1_core_splits.csv')
fmt_save(t1[t1.split.isin(DIAG) & t1.candidate.isin(FIN)], 't1_diagnostic_splits.csv')

# ---------------------------------------------------------------- 2. paired comparisons
rows = []
pairs = [(f, 'A0_V3') for f in ['M00', 'A11', 'D13']] + [('D13', 'A11'), ('D13', 'M00'), ('A11', 'M00')]
for a, b in pairs:
    for split in CORE + DIAG + ['core_val+holdout', 'core_all']:
        sp = {'core_val+holdout': ['validation', 'historical_holdout'], 'core_all': CORE}.get(split, split)
        for treat in (['exclude', 'minus100', 'zero'] if b == 'A0_V3' else ['exclude']):
            d, n, ndq = pair_vec(a, b, sp, treat)
            rows.append(dict(treated=a, control=b, split=split, dq_treatment=treat, **pstats(d, n, ndq)))
P = fmt_save(pd.DataFrame(rows), 't2_paired.csv')

# per-episode detail for the core splits (finalists and V3)
wide = df[df.candidate.isin(FIN)].pivot_table(index=['split', 'episode'], columns='candidate', values='ret', aggfunc='first')
mp = df[df.candidate.isin(FIN)].pivot_table(index=['split', 'episode'], columns='candidate', values='measured_pass', aggfunc='first')
wide = wide.reindex(columns=FIN).join(mp.reindex(columns=FIN).add_suffix('_pass')).reset_index()
fmt_save(wide, 't2_episode_returns_finalists.csv')

# ---------------------------------------------------------------- 3. ablations
abl = [
    ('determinism: A2 == M00', 'A2', 'M00'), ('determinism: A6 == A11', 'A6', 'A11'),
    ('determinism: B1 == D13', 'B1', 'D13'), ('no-op: adaptive_equal == A11', 'adaptive_equal', 'A11'),
    ('no-op: adaptive_no_regime == A11', 'adaptive_no_regime', 'A11'),
    ('rotation 1 vs 0 (momentum pure)', 'A2', 'A1'),
    ('confidence (momentum): A3 - A2', 'A3', 'A2'),
    ('confidence (adaptive): A11 - no_conf', 'A11', 'adaptive_no_confidence'),
    ('regime (momentum+conf): A4 - A3', 'A4', 'A3'),
    ('regime (direct): B2 - B1', 'B2', 'B1'),
    ('optimizer continuous vs equal: A5 - A4', 'A5', 'A4'),
    ('forecast vs momentum score (both +conf): A6 - A3', 'A6', 'A3'),
    ('adaptive family net: A11 - M00', 'A11', 'M00'),
    ('direct family net: D13 - M00', 'D13', 'M00'),
    ('remaining horizon: remaining - A11', 'adaptive_remaining', 'A11'),
    ('DE seed0 - equal(A11)', 'adaptive_de_seed_0', 'A11'),
    ('DE seed1 - equal(A11)', 'adaptive_de_seed_1', 'A11'),
    ('DE seed2 - equal(A11)', 'adaptive_de_seed_2', 'A11'),
    ('DE seed1 - seed0', 'adaptive_de_seed_1', 'adaptive_de_seed_0'),
    ('DE seed2 - seed0', 'adaptive_de_seed_2', 'adaptive_de_seed_0'),
]
rows = []
for label, a, b in abl:
    for split in ['development', 'validation', 'historical_holdout', 'core_all', 'val+holdout']:
        sp = {'core_all': CORE, 'val+holdout': ['validation', 'historical_holdout']}.get(split, split)
        for treat in ['exclude', 'minus100']:
            d, n, ndq = pair_vec(a, b, sp, treat)
            rows.append(dict(effect=label, treated=a, control=b, split=split, dq_treatment=treat, **pstats(d, n, ndq)))
A = fmt_save(pd.DataFrame(rows), 't3_ablation_paired.csv')

# development-only optimizer comparisons from the searched grid (same other settings)
opt_rows = []
for lab, a, b in [('score vs equal (A01-A00)', 'A01', 'A00'), ('continuous vs equal (A02-A00)', 'A02', 'A00'),
                  ('DE vs equal (A03-A00)', 'A03', 'A00'), ('score vs equal momentum (M05-M04)', 'M05', 'M04'),
                  ('continuous vs score rp2 (A30-A29)', 'A30', 'A29'), ('DE vs score rp2 (A31-A29)', 'A31', 'A29'),
                  ('forecast conf, adaptive (A09-A08)', 'A09', 'A08'), ('regime adaptive (A00-A09)', 'A00', 'A09'),
                  ('confidence h10 (A11-A10)', 'A11', 'A10'), ('confidence h1 (A07-A06)', 'A07', 'A06'),
                  ('confidence h20 (A13-A12)', 'A13', 'A12')]:
    for treat in ['exclude', 'minus100']:
        d, n, ndq = pair_vec(a, b, 'development', treat)
        opt_rows.append(dict(effect=lab, treated=a, control=b, split='development', dq_treatment=treat, **pstats(d, n, ndq)))
fmt_save(pd.DataFrame(opt_rows), 't3_dev_grid_component_paired.csv')

# DE seed agreement: per-episode spread
de = df[df.candidate.str.startswith('adaptive_de_seed') & df.split.isin(CORE)].pivot_table(
    index=['split', 'episode'], columns='candidate', values='ret', aggfunc='first')
de['range'] = de.max(axis=1) - de.min(axis=1)
de['n_nan'] = de.iloc[:, :3].isna().sum(axis=1)
fmt_save(de.reset_index(), 't3_de_seed_episode_spread.csv')

# ---------------------------------------------------------------- 4. rotation
rot_rows = []
grids = {'momentum(full,h5)': ('M04', {'max_replacements': ['M22', 'M04', 'M23', 'M24'],
                                       'replacement_margin': ['M25', 'M04', 'M26', 'M27', 'M28'],
                                       'target_count': ['M18', 'M04', 'M19', 'M20', 'M21']}),
         'adaptive(conf,h5)': ('A09', {'max_replacements': ['A22', 'A09', 'A23', 'A24'],
                                       'replacement_margin': ['A25', 'A09', 'A26', 'A27', 'A28'],
                                       'target_count': ['A18', 'A09', 'A19', 'A20', 'A21']})}
for fam, (base, axes) in grids.items():
    for axis, ids in axes.items():
        for cid in ids:
            s = S[(S.candidate == cid) & (S.split == 'development')].iloc[0]
            d, n, ndq = pair_vec(cid, base, 'development', 'exclude')
            ps = pstats(d, n, ndq)
            rot_rows.append(dict(grid=fam, axis=axis, value=cfg[cid][axis], candidate=cid,
                                 **{k: s[k] for k in ['complete', 'measured_pass', 'median', 'mean', 'p25', 'p10', 'worst',
                                                      'mdd', 'turnover', 'cost_total']},
                                 vs_base_median=ps['median'], vs_base_mean=ps['mean'], vs_base_wins=ps['wins'],
                                 vs_base_losses=ps['losses'], vs_base_sign_p=ps['sign_p']))
fmt_save(pd.DataFrame(rot_rows), 't4_rotation_dev.csv')
# A1 (0 replacements) vs A2 (1) across every split is in t3 ("rotation 1 vs 0")

# ---------------------------------------------------------------- 5. neighborhood
nrows = []
for fin in ['M00', 'A11', 'D13']:
    predeclared = rep.neighborhood(cfg[fin], configs)
    base = {k: v for k, v in cfg[fin].items() if k not in ('id', 'stage')}
    extended = []
    for o in configs:
        if o['id'] == fin or o['family'] != cfg[fin]['family']:
            continue
        ch = [k for k in (set(base) | set(o)) - {'id', 'stage'} if base.get(k) != o.get(k)]
        if len(ch) == 1:
            extended.append((o['id'], ch[0]))
    for cid, axis in extended + [(c, 'coefficients') for c in predeclared if c not in [e[0] for e in extended]]:
        s = S[(S.candidate == cid) & (S.split == 'development')].iloc[0]
        d, n, ndq = pair_vec(cid, fin, 'development', 'exclude')
        dv, nv, _ = pair_vec(cid, 'A0_V3', 'development', 'exclude')
        dist = (float(np.linalg.norm(np.array(cfg[cid]['coefficients']) - np.array(cfg[fin]['coefficients'])))
                if axis == 'coefficients' else np.nan)
        nrows.append(dict(finalist=fin, neighbor=cid, axis=axis, neighbor_value=str(cfg[cid].get(axis))[:60],
                          predeclared=cid in predeclared, coef_distance=dist,
                          complete=s.complete, measured_pass=s.measured_pass, median=s['median'], p25=s.p25,
                          p10=s.p10, worst=s.worst, vs_finalist_median=d.median(), vs_finalist_mean=d.mean(),
                          vs_finalist_wins=int((d > 0).sum()), vs_finalist_losses=int((d < 0).sum()),
                          vs_v3_median=dv.median(), vs_v3_p25=dv.quantile(.25), vs_v3_p10=dv.quantile(.1)))
    # also nearest coefficient vectors (any optimizer) for direct
    if fin == 'D13':
        for o in configs:
            if o['family'] == 'direct' and o['id'] != fin:
                dist = float(np.linalg.norm(np.array(o['coefficients']) - np.array(cfg[fin]['coefficients'])))
                if dist <= 0.5 and o['id'] not in [r['neighbor'] for r in nrows if r['finalist'] == fin]:
                    s = S[(S.candidate == o['id']) & (S.split == 'development')].iloc[0]
                    d, _, _ = pair_vec(o['id'], fin, 'development')
                    nrows.append(dict(finalist=fin, neighbor=o['id'], axis='coefficients+other',
                                      neighbor_value=f"opt={o['optimizer']},regime={o['regime']}",
                                      predeclared=False, coef_distance=dist, complete=s.complete,
                                      measured_pass=s.measured_pass, median=s['median'], p25=s.p25, p10=s.p10,
                                      worst=s.worst, vs_finalist_median=d.median(), vs_finalist_mean=d.mean(),
                                      vs_finalist_wins=int((d > 0).sum()), vs_finalist_losses=int((d < 0).sum())))
fmt_save(pd.DataFrame(nrows), 't5_neighborhood_dev.csv')
# rank of each finalist in its family on development under the selection order
dev_rank = pd.concat([g.sort_values('k').assign(rank=range(1, len(g) + 1), of=len(g))
                      for _, g in search.groupby('family')])
fmt_save(dev_rank[['family', 'candidate', 'rank', 'of', 'complete', 'measured_pass', 'median', 'p25', 'p10',
                   'mean', 'worst']], 't5_dev_rank_all.csv')

# ---------------------------------------------------------------- 6. compliance
fin_rows = df[df.candidate.isin(FIN) & ~df.measured_pass]
fmt_save(fin_rows[['candidate', 'split', 'episode', 'ret', 'episode_status', 'failure_reasons', 'canonical_status'] + FAILS
                  + ['observed_sessions', 'disqualified']].sort_values(['candidate', 'split', 'episode']),
         't6_finalist_failed_episodes.csv')
tok = df.assign(tok=df.failure_reasons.fillna('').str.split(';')).explode('tok')
tok = tok[tok.tok != '']
core_tok = tok[tok.split.isin(CORE)]
fmt_save(core_tok.groupby(['tok']).agg(episodes=('episode', 'size'), candidates=('candidate', 'nunique')).reset_index()
         .sort_values('episodes', ascending=False), 't6_failure_tokens_core_all_candidates.csv')
fmt_save(core_tok.groupby(['family', 'tok']).size().unstack(fill_value=0).reset_index(), 't6_failure_tokens_by_family.csv')
fmt_save(core_tok.groupby(['episode', 'tok']).size().unstack(fill_value=0).reset_index()
         .assign(total=lambda x: x.drop(columns='episode').sum(axis=1)).sort_values('total', ascending=False),
         't6_failure_tokens_by_episode.csv')

# ---------------------------------------------------------------- 8. selection robustness
v = wide[wide.split == 'validation'].set_index('episode')[FIN]
rk = []
for name, fn in [('median', np.median), ('mean', np.mean), ('p25', lambda x: np.quantile(x, .25)),
                 ('p10', lambda x: np.quantile(x, .1)), ('worst', np.min)]:
    vals = {c: fn(v[c].dropna().values) for c in FIN}
    order = sorted(['M00', 'A11', 'D13'], key=lambda c: -vals[c])
    rk.append(dict(scheme=f'full:{name}', order_3finalists=' > '.join(order), **vals))
for ep in v.index:
    vv = v.drop(ep)
    vals = {c: np.median(vv[c].dropna().values) for c in FIN}
    order = sorted(['M00', 'A11', 'D13'], key=lambda c: -vals[c])
    rk.append(dict(scheme=f'LOO_median:-{ep}', order_3finalists=' > '.join(order), **vals))
# bootstrap probability each finalist ranks first by median on validation
bi = RNG.integers(0, len(v), (NB, len(v)))
meds = {c: np.median(v[c].values[bi], axis=1) for c in ['M00', 'A11', 'D13']}
first = pd.Series(np.argmax(np.vstack([meds[c] for c in ['M00', 'A11', 'D13']]), axis=0)).map({0: 'M00', 1: 'A11', 2: 'D13'})
for c, p in first.value_counts(normalize=True).items():
    rk.append(dict(scheme=f'bootstrap_P(first by median)={c}', order_3finalists=f'{p:.3f}'))
# same on development and holdout for comparison
for split in ['development', 'historical_holdout']:
    w = wide[wide.split == split].set_index('episode')[FIN]
    for name, fn in [('median', np.median), ('mean', np.mean), ('p25', lambda x: np.quantile(x, .25))]:
        vals = {c: fn(w[c].dropna().values) for c in FIN}
        rk.append(dict(scheme=f'{split}:{name}', order_3finalists=' > '.join(sorted(['M00', 'A11', 'D13'], key=lambda c: -vals[c])), **vals))
fmt_save(pd.DataFrame(rk), 't8_selection_robustness.csv')

# correlation of finalists' per-episode returns (how different are they really?)
corr = wide[wide.split.isin(CORE)][FIN].corr()
corr.to_csv(OUT / 't8_core_return_correlation.csv', float_format='%.3f')
print('done')

# ---------------------------------------------------------------- 6b. sensitivity: jointly measured-PASS pairs only
# FAIL_ROUND_LOT for every finalist is a stock-dividend odd-lot residual (July ex-rights season): the planner
# returns INFEASIBLE_V4 "Odd-lot legacy holdings ..." and the book is frozen for the rest of the episode.
jp = []
passed = df.set_index(['candidate', 'episode']).measured_pass
for a, b in pairs:
    for split in ['development', 'validation', 'historical_holdout', 'core_all']:
        sp = CORE if split == 'core_all' else [split]
        x = df[(df.candidate == a) & df.split.isin(sp)].set_index('episode')
        y = df[(df.candidate == b) & df.split.isin(sp)].set_index('episode')
        ok = [e for e in x.index if e in y.index and x.measured_pass[e] and y.measured_pass[e]]
        d = (x.ret.reindex(ok) - y.ret.reindex(ok)).dropna()
        jp.append(dict(treated=a, control=b, split=split, subset='both_measured_pass', **pstats(d, len(x), 0)))
        for half in ['_01', '_07']:
            ep = [e for e in x.index if e.endswith(half) and e in y.index]
            d = (x.ret.reindex(ep) - y.ret.reindex(ep)).dropna()
            jp.append(dict(treated=a, control=b, split=split, subset=f'windows{half}', **pstats(d, len(ep), 0)))
fmt_save(pd.DataFrame(jp), 't6b_paired_sensitivity_pass_only_and_jan_jul.csv')
print('done2')
