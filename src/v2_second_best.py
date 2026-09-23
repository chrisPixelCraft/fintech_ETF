"""Pinned second-round research entrypoints; formal submission always blocked."""
from pathlib import Path
import argparse,copy,json
from src import tuning_2nd as tuning
from scripts.run_v2_tuning import dump,sha,monthly_rows
from src.backtest import save_result
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
STUDY_ROOT=ROOT/'outputs/tuning_report_2nd_try'


def make_config(strategy,selections,parameters,track='historical_pit'):
    if track not in tuning.TRACKS:raise ValueError('Unknown universe track')
    folder=STUDY_ROOT/track
    if not (folder/'audit.json').exists():raise ValueError('BLOCK: no independently verified study for '+track)
    audit=json.loads((folder/'audit.json').read_text())
    if audit.get('status')!='PASS':raise ValueError('BLOCK: independent audit not PASS')
    for relative,expected in audit['artifact_hashes'].items():
        if sha(folder/relative)!=expected:raise ValueError('Verified artifact changed: '+relative)
    manifest=json.loads((folder/'manifest.json').read_text())
    if not manifest.get('outputs_complete'):raise ValueError('BLOCK: incomplete candidate study')
    for relative,expected in manifest['hashes'].items():
        if sha(ROOT/relative)!=expected:raise ValueError('Frozen study dependency changed: '+relative)
    selected=json.loads((folder/'selection.json').read_text())[strategy]
    identifier=selections.get(track)
    if not identifier or selected['candidate_id']!=identifier or selected['status']!='SELECTED_ZERO_MEASURED_HARD':
        raise ValueError('NO_ELIGIBLE_WINNER: no promotable research configuration for '+track)
    cfg=json.loads((folder/'final'/strategy/'config.json').read_text())
    candidate=next(t for t in manifest['study']['candidates'] if t['candidate_id']==identifier)
    if cfg['tuning_params']!=candidate['params'] or cfg['tuning_params']!=parameters[track]:
        raise ValueError('Pinned parameters differ from independently verified candidate')
    if cfg['cash_guard_ratio']!=manifest['study']['cash_guard_ratio']:
        raise ValueError('Fixed compliance guard changed')
    cfg=copy.deepcopy(cfg)
    cfg['study_policy'].update(parameter_search=False,selected_from_parameter_search=True,
        selection_scope='EX_POST_DEVELOPMENT',official_live_submission='BLOCK_IF_UNKNOWN')
    cfg['research_shadow']=True
    return cfg


def run_fixed(strategy,config,context=None):
    if not config.get('research_shadow'):raise ValueError('BLOCK_SUBMISSION')
    ctx=tuning.context(config['universe_mode']) if context is None else context
    if ctx['track']!=config['universe_mode']:raise ValueError('Context universe mismatch')
    result=tuning.run_model(ctx,strategy,config)
    if result['metrics']['measured_hard_breach_days']!=0:
        raise ValueError('Selected candidate no longer reproduces zero measured hard breaches')
    return result


def single_cli(strategy,selections,parameters,argv=None):
    parser=argparse.ArgumentParser(description=f'v2 {strategy}: pinned second-round research replay, formal submission blocked')
    parser.add_argument('--track',choices=tuning.TRACKS,default='historical_pit')
    parser.add_argument('--show-config',action='store_true')
    parser.add_argument('--output',default=f'outputs/v2_{strategy}_second_best_replay')
    args=parser.parse_args(argv)
    cfg=make_config(strategy,selections,parameters,args.track)
    if args.show_config:
        print(json.dumps(cfg,indent=2,ensure_ascii=False));return
    out=Path(args.output).resolve()
    if out.exists() and any(out.iterdir()):raise FileExistsError('Preserve existing results; choose an empty output')
    out.mkdir(parents=True,exist_ok=True)
    source=STUDY_ROOT/args.track
    provenance=dict(source_study=str(source),source_manifest_sha256=sha(source/'manifest.json'),
        candidate_id=selections[args.track],strategy=strategy,universe_mode=args.track,
        selection_scope='EX_POST_DEVELOPMENT',official_compliance='UNKNOWN_BLOCK_SUBMISSION',outputs_complete=False)
    dump(out/'provenance.json',provenance)
    result=run_fixed(strategy,cfg)
    save_result(result,out/strategy)
    pd.DataFrame(monthly_rows(result['equity'])).to_csv(out/'monthly.csv',index=False)
    provenance['outputs_complete']=True;dump(out/'provenance.json',provenance)
    print(json.dumps(result['metrics'],indent=2,ensure_ascii=False))
