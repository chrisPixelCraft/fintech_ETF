"""Fixed full-tuned v2 release. Spelling 'offcial' preserves the requested name.

No parameter search happens during daily planning. Unknown official evidence
blocks submission; this module never posts orders or claims platform acceptance.
"""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parent
STUDY=ROOT/'outputs/full_tuned_v2'
REFERENCE_SHA256='0009ee5d68b74ee24da9556f5f3ebd32b23adff85eb5bd7d871250ab3d8a8b13'


def _sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verified_reference():
    # Canonical extraction independently audited against all ten official docs.
    # Pin the accepted content, rather than trusting an arbitrary current hash.
    path=ROOT/'daily_auto/reference/official_reference.json'
    if _sha(path)!=REFERENCE_SHA256:raise ValueError('Audited official reference changed')
    return json.loads(path.read_text())


def verify_release():
    verified_reference()
    audit=json.loads((STUDY/'audit.json').read_text())
    if audit['status']!='PASS':raise ValueError('Release has no independent PASS')
    if audit['auditor_sha256']!=_sha(ROOT/'scripts/audit_official_deep.py'):
        raise ValueError('Independent auditor changed')
    for rel,digest in audit['input_hashes'].items():
        if _sha(ROOT/rel)!=digest:raise ValueError('Frozen source/input changed: '+rel)
    # Daily inference needs this exact audited selection and configuration.
    paths=['selection.json']+[f'{track}/final/full_tuned_v2/config.json' for track in ['official_ex_post','historical_pit']]
    for rel in paths:
        if _sha(STUDY/rel)!=audit['artifact_hashes'].get(rel):
            raise ValueError('Fixed release selection/config changed: '+rel)
    return audit


def get_params():
    verify_release();return copy.deepcopy(json.loads((STUDY/'selection.json').read_text())['params'])


def build_config(track='official_ex_post'):
    if track not in ['official_ex_post','historical_pit']:raise ValueError('Unknown universe track')
    verify_release()
    cfg=json.loads((STUDY/track/'final/full_tuned_v2/config.json').read_text())
    from src.official_deep_tuning import FIXED, validate_params
    validate_params(cfg['full_tuning_params'])
    for key,value in FIXED.items():
        if cfg.get(key)!=value:raise ValueError('Fixed rule changed: '+key)
    return copy.deepcopy(cfg)


def prepare_signals(*,daily_path, hourly_path, state, source_manifest, input_paths=()):
    """Compute all ranks from time-bounded raw snapshots, never hand-enter scores.

    Expected manifest: as_of, known_at, sources (one daily and one hourly role).
    Each source carries authority/source_url/content_as_of/known_at/sha256.
    Sources describe actual acquisition; historical replay cannot fabricate an
    earlier capture timestamp. Official quote evidence is separately checked.
    """
    import pandas as pd
    import numpy as np
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from src.backtest import aggregate_four_hour, score_candidates, validate_daily
    from src.tuning_features import FeatureCache
    from src.tuning_a_deep import NumericFeatureCache
    from daily_auto.validate import _timestamp
    cfg=build_config('official_ex_post')
    asof=str(state['as_of']);end=pd.Timestamp(asof);now=datetime.now(ZoneInfo('Asia/Taipei'))
    if source_manifest.get('as_of')!=asof:raise ValueError('Signal source date differs from official ledger')
    known=_timestamp(source_manifest['known_at'],'signal known_at')
    if known>now:raise ValueError('Future source acquisition')
    sources=copy.deepcopy(source_manifest.get('sources',[]))
    paths={'daily':Path(daily_path),'hourly':Path(hourly_path)}
    content_times={}
    for role,path in paths.items():
        matches=[s for s in sources if s.get('role')==role]
        if len(matches)!=1:raise ValueError('Exactly one source record per input role required: '+role)
        s=matches[0]
        for key in ['authority','source_url','content_as_of','known_at','sha256']:
            if key not in s:raise ValueError('Missing source evidence: '+key)
        if _sha(path)!=s['sha256']:raise ValueError('Signal source hash mismatch: '+role)
        content=_timestamp(s['content_as_of'],'source content_as_of');captured=_timestamp(s['known_at'],'source known_at')
        if content.date().isoformat()!=asof or content>captured or captured>known:
            raise ValueError('Source availability/date mismatch: '+role)
        content_times[role]=pd.Timestamp(content).tz_convert('Asia/Taipei')
    market_close=pd.Timestamp(asof+' 13:30:00',tz='Asia/Taipei')
    if content_times['daily']<market_close:
        raise ValueError('Daily close unavailable before 13:30 market close')
    reference=verified_reference()
    allowed={r['ticker'] for r in reference['stocks']}
    # An audited entity alias, not a new security invented from its price series.
    aliases={'5371':'3718'}
    def names(frame):
        frame=frame.copy();symbols=frame.symbol.astype(str).str.split('.').str[0].replace(aliases)
        frame['symbol']=symbols;return frame[frame.symbol.isin(allowed)].copy()
    daily=names(pd.read_csv(daily_path));daily['date']=pd.to_datetime(daily.date)
    daily=daily[daily.date.le(end)].copy()
    hourly=names(pd.read_csv(hourly_path))
    if 'timestamp' not in hourly:raise ValueError('Timezone-aware hourly timestamp required')
    # Reject ambiguous local timestamps before pandas can silently assume UTC.
    if not hourly.timestamp.astype(str).str.contains(r'(?:Z|[+-]\d\d:\d\d)$',regex=True).all():
        raise ValueError('Hourly timestamps must include timezone offsets')
    ts=pd.to_datetime(hourly.timestamp,utc=True).dt.tz_convert('Asia/Taipei')
    included=ts.dt.tz_localize(None).dt.normalize().le(end)
    hourly=hourly[included].copy();ts=ts[included]
    # Match the frozen aggregate_four_hour contract exactly: only 09,10,11,12
    # bar starts contribute. Session tails/13:30 observations are archived but
    # never reinterpreted as full hourly bars or included in any signal.
    used=ts.dt.hour.between(9,12)&ts.dt.minute.eq(0)&ts.dt.second.eq(0)
    source_hourly=next(s for s in sources if s['role']=='hourly')
    source_hourly['consumed_window']='09:00–13:00: four complete hourly bars only'
    source_hourly['excluded_outside_four_hour_window_rows']=int((~used).sum())
    hourly=hourly[used].copy();ts=ts[used]
    completion=ts+pd.Timedelta(hours=1)
    if completion.gt(content_times['hourly']).any():
        raise ValueError('Hourly bar not complete at source content_as_of')
    normalized_keys=pd.DataFrame({'symbol':hourly.symbol,'instant':ts})
    if normalized_keys.duplicated(['symbol','instant']).any():raise ValueError('Duplicate hourly rows at same instant')
    daily=validate_daily(daily)
    if set(daily.loc[daily.date.eq(end),'symbol'])!=allowed:
        raise ValueError('All 150 current signal-day quotes required; no fabricated forward fill')
    bars=aggregate_four_hour(hourly)
    cache=NumericFeatureCache(FeatureCache(daily,bars));frame=cache.frame(cfg,daily,bars)
    ranked=score_candidates(frame[frame.date.eq(end)].copy(),cfg).reset_index(drop=True)
    # Not-yet-mature indicators may be missing. They can never qualify as new
    # entrants, and receive a fixed bottom score rather than future backfill.
    invalid=~np.isfinite(ranked.score.to_numpy(float))
    ranked.loc[invalid,'entry_ok']=False;ranked.loc[invalid,'score']=-1.
    ranked['entry_ok']=ranked.entry_ok.astype(bool);ranked['exit']=ranked.exit.astype(bool)
    ranked['return_short']=cfg['full_tuning_params']['return_short']
    ranked['return_long']=cfg['full_tuning_params']['return_long']
    return dict(frame=ranked,as_of=asof,known_at=source_manifest['known_at'],
        sources=tuple(sources),input_paths=tuple(dict.fromkeys([*paths.values(),*map(Path,input_paths)])))


def run_backtest(track,output):
    from src import tuning_a_deep, official_deep_tuning
    from src.backtest import save_result
    target=Path(output)
    if target.exists():raise FileExistsError('Preserve prior output: '+str(target))
    result=official_deep_tuning.run_model(tuning_a_deep.context(track),build_config(track))
    save_result(result,target);result['plan_audit'].to_csv(target/'plan_audit.csv',index=False)
    print(json.dumps(result['metrics'],ensure_ascii=False,indent=2))


def main():
    if len(sys.argv)>1 and sys.argv[1]=='plan':
        from daily_auto.full_tuned import main as daily_main
        sys.argv.pop(1);return daily_main()
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--show-config',action='store_true')
    p.add_argument('--track',choices=['official_ex_post','historical_pit'],default='official_ex_post')
    p.add_argument('--output',type=Path)
    a=p.parse_args()
    if a.show_config:print(json.dumps(build_config(a.track),ensure_ascii=False,indent=2))
    elif a.output:run_backtest(a.track,a.output)
    else:p.print_help()


if __name__=='__main__':raise SystemExit(main())
