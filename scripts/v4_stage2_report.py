#!/usr/bin/env python3
"""Generate evidence-backed Stage-2 reports; fail closed on incomplete evidence."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
import numpy as np
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CORE = ('development', 'validation', 'historical_holdout')
AXES = ('short','medium','long','target_count','max_replacements','replacement_margin',
        'cash_target','risk_penalty','horizon','optimizer','confidence','regime','weighting','coefficients')
FAILURES = ('cash_violation_days','weight_violation_days','holding_count_violation_days',
            'odd_lot_issue_days','missing_execution_price_days','unfilled_days','no_valid_plan_days')


def pct(value):
    return 'N/A' if pd.isna(value) else f'{value:.3%}'


def truth(series):
    return series.astype(str).str.lower().eq('true')


def stats(frame):
    r = pd.to_numeric(frame.episode_return, errors='coerce')
    finite = np.isfinite(r)
    return dict(attempted=len(frame), complete=int(finite.sum()),
                canonical=int(frame.canonical_status.eq('AVAILABLE').sum()),
                measured=int(truth(frame.measured_pass).sum()),
                median=r[finite].median(), p25=r[finite].quantile(.25),
                p10=r[finite].quantile(.1), mean=r[finite].mean(),
                worst=r[finite].min(), positive=int((r[finite]>0).sum()),
                mdd=pd.to_numeric(frame.episode_max_drawdown,errors='coerce').max(),
                turnover=pd.to_numeric(frame.episode_turnover,errors='coerce').mean(),
                cost=pd.to_numeric(frame.transaction_cost,errors='coerce').sum())


def table(frame, group='candidate'):
    lines = [f'| {group} | Complete / attempted | Canonical | Measured PASS | Median | P25 | P10 | Worst | MDD |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for label, part in frame.groupby(group,sort=True):
        s=stats(part)
        lines.append(f'| {label} | {s["complete"]}/{s["attempted"]} | {s["canonical"]}/{s["attempted"]} | '
                     f'{s["measured"]}/{s["attempted"]} | '+ ' | '.join(pct(s[k]) for k in ('median','p25','p10','worst','mdd'))+' |')
    return '\n'.join(lines)


def capital_table(frame, group='candidate'):
    lines=[f'| {group} | Mean return | Positive / attempted | Mean turnover | Total cost (NT$) | Day 1 invested | Day 3 | Day 5 |',
           '|---|---:|---:|---:|---:|---:|---:|---:|']
    for cid,part in frame.groupby(group):
        s=stats(part)
        lines.append(f'| {cid} | {pct(s["mean"])} | {s["positive"]}/{s["attempted"]} | {s["turnover"]:.3f} | {s["cost"]:,.0f} | '+
                     ' | '.join(pct(pd.to_numeric(part.get(f'day_{d}_invested_ratio',pd.Series(dtype=float)),errors='coerce').mean()) for d in (1,3,5))+' |')
    return '\n'.join(lines)


def paired(frame, candidate, split, expected):
    a=frame.loc[frame.candidate.eq(candidate)&frame.split.eq(split)].set_index('episode')
    b=frame.loc[frame.candidate.eq('A0_V3')&frame.split.eq(split)].set_index('episode')
    if a.index.has_duplicates or b.index.has_duplicates:
        raise ValueError('Duplicate paired episodes')
    common=sorted(set(a.index)&set(b.index))
    ar=pd.to_numeric(a.reindex(common).episode_return,errors='coerce')
    br=pd.to_numeric(b.reindex(common).episode_return,errors='coerce')
    good=np.isfinite(ar)&np.isfinite(br)
    complete=(set(common)==set(expected) and bool(good.all()) and len(expected)>0)
    # Never silently discard missing paired outcomes to pass a winner gate.
    delta=ar-br
    out=dict(paired=int(good.sum()),expected=len(expected),complete=bool(complete))
    for k,q in [('median',.5),('p25',.25),('p10',.1)]:
        out[k]=float(delta.quantile(q)) if complete else None
    out['pass']=bool(complete and all(out[k]>=0 for k in ('median','p25','p10')))
    return out


def neighborhood(selected, configs):
    """Predeclared one-axis nearest neighbor; Direct coefficients form one vector axis."""
    ignore={'id','stage'}
    base={k:v for k,v in selected.items() if k not in ignore}
    found=[]
    for other in configs:
        if other['id']==selected['id'] or other['family']!=selected['family']:
            continue
        keys=(set(base)|set(other))-ignore
        changed=[k for k in keys if base.get(k)!=other.get(k)]
        if len(changed)==1 and changed[0] in AXES:
            found.append((other,changed[0]))
    nearest=[]
    for other,axis in found:
        value=base[axis]
        if axis=='coefficients':
            distance=float(np.linalg.norm(np.asarray(other[axis])-np.asarray(value)))
            if distance>.5 or any(ax==axis and np.linalg.norm(np.asarray(c[axis])-np.asarray(value)) < distance-1e-12 for c,ax in found):
                continue
        elif isinstance(value,(float,int)) and not isinstance(value,bool):
            distance=abs(other[axis]-value)
            if any(ax==axis and 0<abs(c[axis]-value)<distance for c,ax in found):
                continue
        nearest.append(other['id'])
    return sorted(nearest)


def component_effect(frame, treated, control):
    a=frame.loc[frame.candidate.eq(treated)&frame.split.isin(CORE)].set_index('episode')
    b=frame.loc[frame.candidate.eq(control)&frame.split.isin(CORE)].set_index('episode')
    common=sorted(set(a.index)&set(b.index))
    if not common:
        return 'No paired component evidence.'
    x=pd.to_numeric(a.reindex(common).episode_return,errors='coerce')
    y=pd.to_numeric(b.reindex(common).episode_return,errors='coerce')
    valid=np.isfinite(x)&np.isfinite(y)
    if not valid.all():
        return f'{int(valid.sum())}/{len(common)} complete pairs; component benefit is not established.'
    delta=x-y
    feasible=(truth(a.reindex(common).measured_pass)&truth(b.reindex(common).measured_pass)).sum()
    return (f'{treated} minus {control}: paired median {pct(delta.median())}, '
            f'P10 {pct(delta.quantile(.1))}; {int(feasible)}/{len(common)} pairs pass measured constraints. '
            'These are descriptive diagnostics, not additional selection trials.')


def stress_table(frame, selected, directory, manifest):
    rows=[]
    for _,record in frame.loc[frame.candidate.isin([c['id'] for c in selected.values()])].iterrows():
        path=directory/'runs'/record.candidate/record.episode/'predictions.csv'
        row=record.to_dict(); row.update(prior_regime='unknown', prior_volatility=np.nan)
        if path.exists() and path.stat().st_size>2:
            key=str(path.relative_to(directory))
            if manifest['outputs'].get(key)!=hashlib.sha256(path.read_bytes()).hexdigest():
                raise ValueError('Unsealed stress prediction file: '+key)
            try:
                first=pd.read_csv(path,nrows=1)
                if len(first):
                    row['prior_regime']=first.iloc[0].get('prior_regime','unknown')
                    row['prior_volatility']=first.iloc[0].get('market_volatility',np.nan)
            except pd.errors.EmptyDataError:
                pass
        rows.append(row)
    stress=pd.DataFrame(rows)
    dev=stress.loc[stress.split.eq('development')].drop_duplicates('episode')
    dv=pd.to_numeric(dev.prior_volatility,errors='coerce').dropna()
    threshold=dv.median() if len(dv) else np.nan
    stress['volatility_group']=np.where(stress.prior_volatility.isna(),'unknown',
                               np.where(stress.prior_volatility>threshold,'high','low'))
    out=('Regime labels use the first available causal prediction. risk_on/risk_off/neutral are '
         'breadth/momentum proxies for bull/bear/sideways conditions, not independent market labels. '
         'Missing initial predictions are unknown. High/low volatility uses the development-only median '
         f'of initial episode market volatility ({threshold:.6f}); no diagnostic outcome sets the threshold.\n\n')
    out+='### Regime and volatility\n\n'
    for group in ('prior_regime','volatility_group'):
        out+=table(stress,group)+'\n\n'
    out+='### Start dates and sector coverage\n\n'
    out+=table(stress,'split')+'\n\n'
    out+=('Fixed January/July starts, late-October seasonal starts, and shifted recent rolling starts '
          'test date sensitivity without selecting dates by outcome. Sector-leadership stress is **unverified**: '
          'this study has no authenticated historical sector-classification/leadership series. '
          'No cross-sector robustness claim is made.\n')
    return out


def generate(directory, reports, final_config):
    directory=Path(directory); reports=Path(reports); final_config=Path(final_config)
    required=['all_episodes.csv','development_summary.csv','validation_summary.csv','freeze.json',
              'manifest.json','verification.json','candidates.json','episodes.json']
    for name in required:
        if not (directory/name).exists():
            raise FileNotFoundError(f'Report requires completed evidence: {directory/name}')
    frame=pd.read_csv(directory/'all_episodes.csv')
    freeze=json.loads((directory/'freeze.json').read_text())
    selected=freeze['selected']
    registry=json.loads((directory/'episodes.json').read_text())
    configs=json.loads((directory/'candidates.json').read_text())
    verification=json.loads((directory/'verification.json').read_text())
    manifest=json.loads((directory/'manifest.json').read_text())
    # Reports must never promote edited summary files after independent auditing.
    for name in ('all_episodes.csv','development_summary.csv','validation_summary.csv','freeze.json','candidates.json','episodes.json'):
        expected=manifest.get('outputs',{}).get(name)
        if expected != hashlib.sha256((directory/name).read_bytes()).hexdigest():
            raise ValueError('Unsealed/changed report input: '+name)
    if frame.duplicated(['candidate','episode']).any():
        raise ValueError('Duplicate study observations')
    manifest_hash=hashlib.sha256((directory/'manifest.json').read_bytes()).hexdigest()
    if verification.get('manifest_sha256') != manifest_hash:
        raise ValueError('Verification does not authenticate the current manifest')
    audit_ok=verification.get('status')=='PASS_INDEPENDENT_STAGE2_AUDIT'
    expected={s:[e['episode_id'] for e in registry if e['split']==s] for s in CORE}
    gates={}
    for family,cfg in selected.items():
        part=frame.loc[frame.candidate.eq(cfg['id'])&frame.split.isin(CORE)]
        fullset=set(part.episode)==set(sum(expected.values(),[]))
        st=stats(part)
        feasibility=bool(fullset and st['attempted']>0 and all(st[k]==st['attempted'] for k in ('complete','canonical','measured')))
        neighbors=neighborhood(cfg,configs)
        neighbor_results=[]
        for cid in neighbors:
            partn=frame.loc[frame.candidate.eq(cid)&frame.split.eq('development')]
            ns=stats(partn)
            nfeasible=all(ns[k]==len(expected['development']) for k in ('attempted','complete','canonical','measured'))
            comparison=paired(frame,cid,'development',expected['development'])
            neighbor_results.append(dict(candidate=cid,feasible=nfeasible,comparison=comparison))
        stable=bool(neighbor_results and all(n['feasible'] and n['comparison']['pass'] for n in neighbor_results))
        comparisons={s:paired(frame,cfg['id'],s,expected[s]) for s in ('validation','historical_holdout')}
        gates[family]=dict(candidate=cfg['id'],core_feasibility=feasibility,audit=audit_ok,
                           neighborhood_stability=stable,neighbors=neighbor_results,paired=comparisons)
        seed_results=[]
        if cfg.get('optimizer')=='de':
            for seed in (0,1,2):
                cid=f'adaptive_de_seed_{seed}'
                seedframe=frame.loc[frame.candidate.eq(cid)&frame.split.isin(CORE)]
                ss=stats(seedframe)
                seed_feasible=(set(seedframe.episode)==set(sum(expected.values(),[])) and
                               all(ss[k]==sum(len(e) for e in expected.values()) for k in ('attempted','complete','canonical','measured')))
                seed_pairs={split:paired(frame,cid,split,expected[split]) for split in CORE}
                seed_results.append(dict(seed=seed,feasible=bool(seed_feasible),paired=seed_pairs))
        seed_stable=bool(cfg.get('optimizer')!='de' or (len(seed_results)==3 and all(r['feasible'] and all(p['pass'] for p in r['paired'].values()) for r in seed_results)))
        gates[family].update(sector_robustness=False,
                             sector_robustness_reason='UNVERIFIED historical sector coverage; taxonomy CANDIDATE_PENDING_INDEPENDENT_REVIEW; no authenticated full development/validation/holdout sector series.',
                             de_seed_stability=seed_stable,de_seeds=seed_results)
        gates[family]['pass']=bool(feasibility and audit_ok and stable and seed_stable and gates[family]['sector_robustness'] and all(p['pass'] for p in comparisons.values()))
    # Choose family using validation ranking only, then confirm; never switch
    # to another family in response to holdout or recent results.
    val=pd.read_csv(directory/'validation_summary.csv')
    chosen_ids={c['id'] for c in selected.values()}
    ranking=val.loc[val.candidate.isin(chosen_ids)]
    if len(ranking)!=3:
        raise ValueError('Validation summary must include the three frozen candidates')
    preferred=ranking.iloc[0].family
    if freeze.get('preferred_family') != preferred:
        raise ValueError('Preferred family differs from pre-holdout freeze')
    winner=preferred if gates[preferred]['pass'] else None
    decision='V4_WINNER' if winner else 'NO_V4_WINNER'
    if winner:
        payload=dict(selected[winner],decision=decision,submission_status='BLOCK_SUBMISSION',
                     evidence_manifest_sha256=hashlib.sha256((directory/'manifest.json').read_bytes()).hexdigest())
        final_config.parent.mkdir(parents=True,exist_ok=True)
        if final_config.exists() and json.loads(final_config.read_text())!=payload:
            raise ValueError('Refusing to replace an existing frozen V4 config')
        final_config.write_text(json.dumps(payload,indent=2)+'\n')
    elif final_config.exists():
        raise ValueError('NO_V4_WINNER conflicts with existing final config; preserve and resolve explicitly')
    reports.mkdir(parents=True,exist_ok=True)
    evidence=f'[{directory}/all_episodes.csv](../{directory}/all_episodes.csv)'
    caveat=('All attempted episodes are retained. Pooled development/validation/holdout tables are descriptive, not untouched-test estimates. Return statistics describe complete episodes, including measured compliance failures; '
            'incomplete outcomes are counted, never treated as zero returns or removed from gates. '
            'The historical universe is the fixed 2026 competition roster, not point-in-time membership. '
            'The historical holdout was previously viewed and is retrospective confirmation. '
            'Recent, seasonal and overlapping rolling windows are diagnostics only; overlap does not create independent samples.')
    for family,cfg in selected.items():
        part=frame.loc[frame.candidate.eq(cfg['id'])]
        dev=frame.loc[frame.family.eq(family)&frame.split.eq('development')]
        text=f'# V4-{family.title()}\n\nFrozen research candidate: `{cfg["id"]}`. Winner gate: **{gates[family]["pass"]}**. Alpha status: `DESCRIPTIVE_ONLY`; submission status: `BLOCK_SUBMISSION`.\n\n'
        text+='## Chronological results\n\n'+table(part,'split')+'\n\n'+caveat+'\n\n'
        text+='## Deployment and cost\n\n'+capital_table(part,'split')+'\n\n'
        text+='## Development search\n\n36 configurations received the same 18-episode budget. '+('Direct scores were selected after hard feasibility gates using development median net return minus risk-penalty × MDD, turnover penalty, and 0.01 × squared coefficient norm; no stock-return regression was fit. ' if family=='direct' else '')
        text+='The fixed sparse search is not exhaustive.\n\n'+table(dev)+'\n\n'
        text+='## Frozen configuration and evidence\n\n```json\n'+json.dumps(cfg,indent=2)+'\n```\n\nSource: '+evidence+'.\n'
        (reports/f'v4_{family}.md').write_text(text)
    abl=frame.loc[frame.candidate.isin(['A1','A2','A3','A4','A5','A6','B1','B2','adaptive_no_confidence','adaptive_no_regime','adaptive_equal','adaptive_remaining','adaptive_de_seed_0','adaptive_de_seed_1','adaptive_de_seed_2'])]
    abltext='# V4 ablations\n\nThese frozen interventions are diagnostic; their outcomes do not change candidate selection.\n\n'
    for split,group in abl.groupby('split'):
        abltext+=f'## {split}\n\n'+table(group)+'\n\n'
    abltext+=('A1/A2 contrast rotation within Momentum. A3/A4 contrast regime with equal weights. '
              'Full Adaptive versus `adaptive_no_confidence`, `adaptive_no_regime`, and `adaptive_equal` provides matched component removal. '
              'A2/A3 isolates confidence within Momentum; A5/A6 changes architecture and must be interpreted separately. '
              'If a frozen component was already disabled, its removal is a no-op, not evidence that the component is useless. '
              'B1/B2 contrast Direct regime. Three DE seeds measure optimizer sensitivity, not independent market samples.\n\n'+caveat+'\n\nSource: '+evidence+'.\n')
    (reports/'v4_ablation.md').write_text(abltext)
    vtext='# V4 validation and freeze\n\nDecision: **'+decision+'**. Family preference was determined from validation ranking, before confirmation gates.\n\n'
    vtext+='## Paired confirmation\n\n| Family | Split | Complete pairs | Median difference | P25 difference | P10 difference | Pass |\n|---|---|---:|---:|---:|---:|---|\n'
    for family,g in gates.items():
        for split,p in g['paired'].items():
            vtext+=f'| {family} | {split} | {p["paired"]}/{p["expected"]} | '+ ' | '.join(pct(p[k]) for k in ('median','p25','p10'))+f' | {p["pass"]} |\n'
    vtext+='\nDifferences are quantiles of matched episode return differences against V3, with zero noninferiority tolerance. Every registered pair must be complete.\n\n## Stability gate\n\n'
    vtext+=('Neighbors differ on exactly one declared research axis; numeric axes use the nearest observed value, categorical axes use a one-setting change. '
            'Direct coefficients form one vector axis, using the nearest Euclidean neighbor within distance 0.5; '
            'all other settings remain fixed. At least one neighbor must exist, and every neighbor must have complete canonical/measured development results '
            'and nonnegative paired median/P25/P10 against V3. DE finalists additionally require all three fixed seeds '
            'to pass complete core canonical/measured and paired confirmation gates. Historical sector robustness '
            'is currently UNVERIFIED and blocks a winner independently of numeric alpha results. Sparse candidates without such evidence cannot be declared stable.\n\n')
    vtext+='```json\n'+json.dumps(gates,indent=2,allow_nan=False)+'\n```\n\n'+caveat+'\n\n'
    vtext+='## Regime and start-date diagnostics\n\n'+stress_table(frame,selected,directory,manifest)
    (reports/'v4_validation.md').write_text(vtext)
    core=frame.loc[frame.candidate.isin(['A0_V3',*chosen_ids])&frame.split.isin(CORE)]
    final=f'# V4 final evidence\n\n**{decision}**. Submission remains **BLOCK_SUBMISSION**. '
    final+=('A final config was frozen after all gates passed.' if winner else 'No `configs/v4_final.json` is frozen. The evidence does not establish a stable executable V4 winner.')+'\n\n'
    final+='## Comparison and scope\n\n'+table(core)+'\n\n'+caveat+'\n\n'
    final+='## What the study establishes\n\n'
    answers=[
      ('1. Open to official execution','Stage 1 measures execution sensitivity; see [execution comparison](v4_execution_comparison.md). Stage 2 uses official-average execution and official D-1 sizing consistently.'),
      ('2. Momentum baseline',f'Frozen candidate `{selected["momentum"]["id"]}` has the split results in [Momentum](v4_momentum.md); its complete/canonical/measured counts are reported separately.'),
      ('3. Adaptive improvement','The paired validation/confirmation table in [validation](v4_validation.md) determines whether an improvement survives complete paired evaluation. A positive mean alone is insufficient.'),
      ('4. Direct improvement','Direct coefficients optimize development portfolio utility. [Direct](v4_direct.md) reports validation and confirmation; a portfolio-score result is not evidence of individual-return forecast accuracy.'),
      ('5. Confidence value','Compare frozen full Adaptive with `adaptive_no_confidence` in [ablations](v4_ablation.md). Interpret complete matched windows only; compliance failures prevent an executable superiority claim.'),
      ('6. Regime value','Compare full Adaptive with `adaptive_no_regime`, and B1 with B2. Regime is a predefined causal state rule; diagnostics never retune its thresholds.'),
      ('7. Rotation region','The bounded development search covers 0–3 replacements and margins 0–0.20. It does not establish a universal optimum; see candidate counts and neighborhood gates.'),
      ('8. Optimized versus equal weights','The Adaptive equal-weight removal and seeded DE sensitivity are in [ablations](v4_ablation.md). Fractional optimizer utility does not override lot-rounded ledger feasibility.'),
      ('9. Components without value','No component is declared useless solely from one aggregate score. Matched ablations, completeness and the actual enabled treatment determine the supported conclusion.'),
      ('10. Recent-only effects','Recent/seasonal/rolling tables are diagnostic and excluded from selection. Strong recent performance cannot rescue failed development/validation/confirmation gates.'),
      ('11. Lower tail','P25, P10, worst return and MDD appear above and per split. Paired median/P25/P10 must not deteriorate, with zero tolerance.'),
      ('12. Compliance failures','The failure counts below include all attempts, including incomplete and disqualified runs. Alpha description and measured/submission validity remain separate.'),
      ('13. Stable candidate',f'The decision is `{decision}`. Audit, all-core feasibility, paired confirmation and predefined neighborhood stability are conjunctive gates.'),
      ('14. Official D-Plan','Research order plans are not authorized competition submissions. Retrospective roster, Active Share and unresolved submission contracts preserve BLOCK_SUBMISSION.')]
    effects={'5. Confidence value':('A6','adaptive_no_confidence'), '6. Regime value':('A6','adaptive_no_regime'), '8. Optimized versus equal weights':('A6','adaptive_equal')}
    for title,answer in answers:
        if title in effects:
            answer=component_effect(frame,*effects[title])+' '+answer
        final+=f'**{title}.** {answer}\n\n'
    final+='## Failure and deployment accounting\n\n| Candidate | Mean return | Positive / attempted | Mean turnover | Total cost (NT$) | Day 1 invested | Day 3 | Day 5 |\n|---|---:|---:|---:|---:|---:|---:|---:|\n'
    for cid,part in core.groupby('candidate'):
        s=stats(part)
        final+=f'| {cid} | {pct(s["mean"])} | {s["positive"]}/{s["attempted"]} | {s["turnover"]:.3f} | {s["cost"]:,.0f} | '+ ' | '.join(pct(pd.to_numeric(part.get(f'day_{d}_invested_ratio',pd.Series(dtype=float)),errors='coerce').mean()) for d in (1,3,5))+' |\n'
    final+='\n| Failure metric | All attempted runs |\n|---|---:|\n'
    for metric in FAILURES:
        value=pd.to_numeric(frame[metric],errors='coerce').sum() if metric in frame else 'Not recorded under this name'
        final+=f'| {metric} | {value} |\n'
    final+='\n## Reproduction and evidence\n\n'
    final+=f'Independent audit: `{verification.get("status","UNKNOWN")}`. '+f'{len(frame)} attempted candidate/episode records are retained. '
    final+='The manifest seals input/config/code and output hashes; the freeze log records ordering before confirmation.\n\n'
    final+='```bash\n.venv/bin/python scripts/v4_stage2_report.py --output '+str(directory)+'\n```\n\n'
    final+='Sources: '+evidence+', [freeze](../'+str(directory)+'/freeze.json), [verification](../'+str(directory)+'/verification.json), [manifest](../'+str(directory)+'/manifest.json).\n'
    (reports/'v4_final.md').write_text(final)
    return dict(decision=decision,preferred_family=preferred,gates=gates)


def generate_blocked(data_directory, reports, final_config, data_audit):
    """Report an acquisition stop without inventing trials or a research freeze."""
    data_directory=Path(data_directory); reports=Path(reports); final_config=Path(final_config)
    manifest_path=data_directory/'execution_data.manifest.json'
    manifest=json.loads(manifest_path.read_text())
    status=json.loads((data_directory/'download_status.json').read_text())
    audit_path=Path(data_audit)
    audit=json.loads(audit_path.read_text())
    if not str(status.get('status','')).startswith('BLOCK') or not str(manifest.get('status','')).startswith('BLOCK'):
        raise ValueError('Blocked mode requires explicitly blocked acquisition and canonical data status')
    if audit.get('status')=='PASS_INDEPENDENT_STAGE2_AUDIT':
        raise ValueError('A full-study audit cannot substitute for blocked data verification')
    digest=hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    if audit.get('manifest_sha256') != digest:
        raise ValueError('Blocked data audit must bind the exact execution-data manifest')
    csv_path=data_directory/'execution_data.csv'
    if hashlib.sha256(csv_path.read_bytes()).hexdigest()!=manifest['normalized_sha256']:
        raise ValueError('Normalized data differs from audited manifest')
    if final_config.exists():
        raise ValueError('Preserve existing frozen config; blocked-mode report cannot replace it')
    attempts=manifest.get('attempts',[])
    success=sum(a.get('status')=='OK' for a in attempts)
    errors={}
    for a in attempts:
        if a.get('status')!='OK':
            error=str(a.get('error',a.get('status','UNKNOWN')))
            errors[error]=errors.get(error,0)+1
    episodes=json.loads((data_directory/'episodes.json').read_text())
    split_counts={}
    for e in episodes:
        split_counts[e['split']]=split_counts.get(e['split'],0)+1
    reports.mkdir(parents=True,exist_ok=True)
    common=("**Implementation complete; empirical Stage 2 is blocked.** Research outcome: `NO_V4_WINNER`. "
            "Strategy comparisons, validation, holdout, and ablations are **NOT_RUN**. "
            "No research candidate is frozen and no `configs/v4_final.json` exists. "
            "This is an execution-data stop, not evidence that any strategy failed scientifically.\n\n")
    coverage=('## Official-data evidence\n\n'
              '| Measure | Audited snapshot |\n|---|---:|\n'
              f'| Requested exchange/date records | {len(attempts)} |\n'
              f'| Successful official records | {success} |\n'
              f'| Failed official records | {len(attempts)-success} |\n'
              f'| Normalized symbol/date rows | {manifest["rows"]} |\n'
              f'| Usable official value/volume rows | {manifest["available_rows"]} |\n\n'
              f'Acquisition status: `{status["status"]}`. {status.get("reason", "")} '
              'Counts are from the current normalized manifest, not cumulative retry attempts. '
              'A successful response does not guarantee historical roster coverage.\n\n'
              f'Data verification: `{audit.get("status","UNKNOWN")}`. This verifies the blocked data snapshot only; '
              'it is not a completed strategy-study audit.\n\n'
              f'Sources: [data manifest](../{manifest_path}), [acquisition status](../{data_directory}/download_status.json), '
              f'[data audit](../{audit_path}).\n\n')
    protocol=('## Implemented protocol\n\n'
              'The registry predeclares 36 sparse candidates per family and 18 development episodes per candidate '
              '(648 development candidate/episode evaluations per family). Three development finalists per family '
              'would enter validation. One candidate per family and the preferred family would freeze before confirmation. '
              'No outcome-dependent date filtering, proxy substitution, or recent-period tuning is allowed.\n\n'
              '| Split | Registered episodes | Execution status |\n|---|---:|---|\n'+
              ''.join(f'| {k} | {v} | NOT_RUN |\n' for k,v in split_counts.items())+'\n'
              'Winner gates require 100% core canonical availability, completeness and measured feasibility, '
              'nonnegative paired validation/confirmation median/P25/P10 against V3 at zero tolerance, '
              'stable predeclared development neighborhoods, verified historical sector robustness, any required DE seed stability, and an independent study audit. '
              'These gates are **NOT_EVALUATED**, not passed.\n\n'
              'Recent, seasonal and overlapping rolling windows are diagnostics only. The 2026 fixed universe '
              'is retrospective, the historical holdout was previously viewed, and historical sector-leadership '
              'coverage is unverified. These limitations prevent broader generalization.\n\n')
    resume=('## Resume and verification\n\n'
            'Resume the same full registry once official responses are accessible. Retain successful raw caches, '
            'refresh the normalized table, independently audit provenance, and run the original full study. '
            'The runner rejects changed sealed inputs and retains incomplete run evidence for explicit quarantine. '
            'Do not select an available-date subset or substitute proxy execution.\n\n'
            '```bash\n'
            '.venv/bin/python scripts/v4_stage2_data.py --workers 2\n'
            '.venv/bin/python scripts/v4_stage2_run.py --workers 4\n'
            '.venv/bin/python scripts/v4_stage2_verify.py --output outputs/v4/stage2 > outputs/v4/stage2/verification.json\n'
            '.venv/bin/python scripts/v4_stage2_report.py --output outputs/v4/stage2\n'
            '```\n\n'
            'Implementation evidence: [study runner](../scripts/v4_stage2_run.py), '
            '[search registry](../config/v4_stage2_search.json), [strategy adapter](../src/v4_strategy.py), '
            '[portfolio optimizer](../src/v4_portfolio_optimizer.py), and '
            '[independent verifier](../scripts/v4_stage2_verify.py).\n\n'
            'Tests cover causality, label maturity, lot sizing, fees, settlement cash, caps, deterministic optimization, '
            'direct development-only fitting, search budgets and report gates. '
            'Unit/smoke tests validate engineering behavior; they are not the required full empirical comparison.\n')
    implementations={
       'momentum':'Causal short/medium/long returns, trend/volume/volatility variants, bounded rotation, and equal versus score allocation are implemented.',
       'adaptive':'Causal walk-forward expert/model weighting, mature historical labels, confidence, regime and common constrained allocation are implemented.',
       'direct':'Frozen linear feature-score coefficient candidates are selected by development portfolio utility, after hard feasibility gates, with L2 regularization; no stock-return regression is required.'}
    for family,description in implementations.items():
        (reports/f'v4_{family}.md').write_text(f'# V4-{family.title()} — blocked study\n\n'+common+
             description+' Performance, lower-tail behavior, parameter stability and superiority: **NOT_RUN**.\n\n'+coverage+protocol+resume)
    (reports/'v4_validation.md').write_text('# V4 validation — NOT_RUN\n\n'+common+coverage+protocol+resume)
    abl=('## Predeclared ablations\n\n'
         '| Ablation | Controlled comparison | Status |\n|---|---|---|\n'
         '| A0 | Frozen V3 baseline | NOT_RUN |\n'
         '| A1 / A2 | Momentum without / with rotation | NOT_RUN |\n'
         '| A3 | Momentum plus confidence | NOT_RUN |\n'
         '| A4 | Momentum plus confidence and regime | NOT_RUN |\n'
         '| A5 | Add continuous allocation | NOT_RUN |\n'
         '| A6 | Full Adaptive architecture | NOT_RUN |\n'
         '| B1 / B2 | Direct without / with regime | NOT_RUN |\n'
         '| Adaptive removals | Confidence, regime, equal allocation | NOT_RUN |\n'
         '| Remaining horizon | Fixed versus remaining-session labels | NOT_RUN |\n'
         '| DE seeds | 0 / 1 / 2 optimizer sensitivity | NOT_RUN |\n\n'
         'A5/A6 changes architecture and is not a confidence-only contrast. A disabled component makes its '
         'removal a no-op; such a comparison cannot establish component uselessness.\n\n')
    (reports/'v4_ablation.md').write_text('# V4 ablations — NOT_RUN\n\n'+common+abl+protocol+resume)
    final='# V4 final — NO_V4_WINNER\n\n'+common+coverage
    final+='## Required research answers\n\n'
    questions=[('Open to official execution','Stage 1 remains the verified execution comparison; see [Stage-1 report](v4_execution_comparison.md). No new Stage-2 comparison was run.'),
       ('Momentum performance','NOT_RUN; implementation alone supplies no performance evidence.'),
       ('Adaptive improvement','NOT_RUN; no improvement claim is supported.'),
       ('Direct improvement','NOT_RUN; no improvement claim is supported.'),
       ('Confidence value','NOT_RUN; matched ablation is pending.'),
       ('Regime value','NOT_RUN; matched ablation is pending.'),
       ('Rotation region','NOT_RUN; 0–3 replacements and margins 0–0.20 are predeclared, not selected.'),
       ('Optimizer versus equal weights','NOT_RUN; no allocation winner is established.'),
       ('Unhelpful components','Unknown; no component may be rejected from missing experiments.'),
       ('Recent-only effects','NOT_RUN; recent diagnostics cannot select or rescue candidates.'),
       ('Lower tail','NOT_RUN; P25/P10/worst/MDD cannot be inferred from unit or smoke tests.'),
       ('Largest compliance obstacle','Canonical execution acquisition is incomplete. Strategy-level failure rates are NOT_RUN.'),
       ('Stable candidate','NO_V4_WINNER because evidence is unavailable, not because a completed experiment rejected every family.'),
       ('Official D-Plan','BLOCK_SUBMISSION. No final architecture is frozen; unresolved submission and historical-universe limits remain.')]
    for i,(question,answer) in enumerate(questions,1):
        final+=f'**{i}. {question}.** {answer}\n\n'
    final+=protocol+abl+resume
    (reports/'v4_final.md').write_text(final)
    return dict(decision='NO_V4_WINNER',implementation_status='IMPLEMENTED',
                empirical_status='BLOCKED_NOT_RUN',data_status=status['status'],
                official_records_ok=success,official_records_attempted=len(attempts),
                manifest_sha256=digest)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='outputs/v4/stage2')
    parser.add_argument('--reports',default='reports')
    parser.add_argument('--final-config',default='configs/v4_final.json')
    parser.add_argument('--data-blocked',action='store_true')
    parser.add_argument('--data-directory',default='outputs/v4/stage2_data')
    parser.add_argument('--data-audit',default='outputs/v4/stage2_data/verification.json')
    args=parser.parse_args()
    result=(generate_blocked(args.data_directory,args.reports,args.final_config,args.data_audit)
            if args.data_blocked else generate(args.output,args.reports,args.final_config))
    print(json.dumps(result,indent=2,allow_nan=False))
