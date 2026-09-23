"""Write requested fixed research entrypoints only from audited eligible selections."""
from pathlib import Path
import json,sys,pprint
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.run_v2_tuning import sha,dump


def run():
    base=ROOT/'outputs/tuning_report_2nd_try';selections={};parameters={}
    for track in ['historical_pit','official_ex_post']:
        folder=base/track
        if not (folder/'audit.json').exists():continue
        audit=json.loads((folder/'audit.json').read_text())
        if audit.get('status')!='PASS':raise ValueError('Audit not PASS: '+track)
        for relative,digest in audit['artifact_hashes'].items():
            if sha(folder/relative)!=digest:raise ValueError('Verified result changed: '+relative)
        manifest=json.loads((folder/'manifest.json').read_text())
        if not manifest.get('outputs_complete'):raise ValueError('Incomplete study: '+track)
        for relative,digest in manifest['hashes'].items():
            if sha(ROOT/relative)!=digest:raise ValueError('Frozen dependency changed: '+relative)
        selections[track]=json.loads((folder/'selection.json').read_text())
        parameters[track]={}
        for strategy in 'ABC':
            cid=selections[track][strategy]['candidate_id']
            parameters[track][strategy]=json.loads((folder/'final'/strategy/'config.json').read_text())['tuning_params'] if cid else None
    if not selections:raise ValueError('No audited study to pin')
    staged=[]
    for strategy in 'ABC':
        ids={track:selections[track][strategy]['candidate_id'] for track in selections}
        params={track:parameters[track][strategy] for track in parameters}
        source='''"""v2 STRATEGY: second-round fixed research candidates, formal submission blocked.

Default: historical PIT pool. --track official_ex_post: retrospective official
150-name pool with explicit membership lookahead. Neither is an unseen test.
"""
from src.v2_second_best import make_config,run_fixed,single_cli

STRATEGY = STRATEGY_LITERAL
SCENARIO_SELECTIONS = IDS_LITERAL
PARAMETERS_BY_TRACK = PARAMS_LITERAL
CANDIDATE_ID = SCENARIO_SELECTIONS.get('historical_pit')
PARAMETERS = PARAMETERS_BY_TRACK.get('historical_pit')


def build_config(track='historical_pit'):
    return make_config(STRATEGY,SCENARIO_SELECTIONS,PARAMETERS_BY_TRACK,track)


def run_backtest(context=None,track='historical_pit'):
    return run_fixed(STRATEGY,build_config(track),context)


def main(argv=None):
    return single_cli(STRATEGY,SCENARIO_SELECTIONS,PARAMETERS_BY_TRACK,argv)


if __name__=='__main__':main()
'''
        source=source.replace('v2 STRATEGY:',f'v2 {strategy}:').replace('STRATEGY_LITERAL',repr(strategy)).replace('IDS_LITERAL',repr(ids)).replace('PARAMS_LITERAL',pprint.pformat(params,width=90,sort_dicts=False))
        target=ROOT/f'v2_{strategy}_best.py'
        temporary=target.with_suffix('.py.tmp')
        temporary.write_text(source);staged.append((temporary,target))
    for temporary,target in staged:temporary.replace(target)
    dump(base/'pinned_entrypoints.json',dict(selections=selections,hashes={p:sha(ROOT/p) for p in ['v2_A_best.py','v2_B_best.py','v2_C_best.py','src/v2_second_best.py']}))

if __name__=='__main__':run()
