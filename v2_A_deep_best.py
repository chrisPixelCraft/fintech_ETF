"""Fixed, independently audited A-only research replay; never a live order sender.

Default: highest net-return zero-observed-breach development candidate.
--choice risk_controlled restricts MDD to the incumbent; --choice strict_winner
retains bullish 4H confirmation. Official membership is retrospective only.
"""
from pathlib import Path
import argparse, copy, json
import pandas as pd
from src import tuning_a_deep as deep
from src.backtest import save_result
from scripts.run_v2_tuning import sha, dump, monthly_rows

ROOT=Path(__file__).resolve().parent
STUDY=ROOT/'outputs/a_deep_tuning'
CHOICES=('return_winner','risk_controlled','strict_winner','incumbent')
PINNED_SELECTIONS = {'historical_pit': {'return_winner': 'a0036',
                    'risk_controlled': 'a0016',
                    'strict_winner': 'a0139',
                    'incumbent': 'a0000'},
 'official_ex_post': {'return_winner': 'r0050',
                      'risk_controlled': 'r0050',
                      'strict_winner': 'r0050',
                      'incumbent': 'a0001'}}


def build_config(track='historical_pit', choice='return_winner'):
    if track not in deep.second.TRACKS or choice not in CHOICES:
        raise ValueError('Unknown research scenario/selection')
    folder=STUDY/track
    audit=json.loads((folder/'audit.json').read_text())
    if audit.get('status')!='PASS':raise ValueError('BLOCK: study not independently verified')
    if audit.get('track')!=track or audit.get('scope')!='MEASURED_RULES_AND_ACCOUNTING_ONLY_NOT_FORMAL_CONTEST_CERTIFICATION':
        raise ValueError('BLOCK: wrong audit identity or scope')
    if sha(ROOT/'scripts/audit_a_deep.py')!=audit['auditor_sha256']:
        raise ValueError('Independent auditor changed')
    for relative,digest in audit['artifact_hashes'].items():
        if sha(folder/relative)!=digest:raise ValueError('Audited artifact changed: '+relative)
    manifest=json.loads((folder/'manifest.json').read_text())
    if not manifest.get('outputs_complete'):raise ValueError('BLOCK: incomplete study')
    for relative,digest in manifest['hashes'].items():
        if sha(ROOT/relative)!=digest:raise ValueError('Frozen dependency changed: '+relative)
    selection=json.loads((folder/'selection.json').read_text())[choice]
    if selection['status']!='SELECTED_ZERO_MEASURED_HARD' or not selection['candidate_id']:
        raise ValueError('NO_ELIGIBLE_WINNER')
    if PINNED_SELECTIONS.get(track,{}).get(choice)!=selection['candidate_id']:
        raise ValueError('BLOCK: selection not pinned by this entrypoint')
    if audit['selection'][choice]['candidate_id']!=selection['candidate_id']:
        raise ValueError('Independent selection mismatch')
    config=json.loads((folder/'final'/choice/'config.json').read_text())
    if config['tuning_candidate_id']!=selection['candidate_id']:
        raise ValueError('Selection/configuration mismatch')
    if config['universe_mode']!=track or config['ex_post_fixed_universe']!=(track=='official_ex_post'):
        raise ValueError('Scenario interpretation mismatch')
    if choice=='strict_winner' and config['four_hour_mode']!='strict':
        raise ValueError('Strict selection lacks 4H direction confirmation')
    if choice=='risk_controlled':
        m=json.loads((folder/'final'/choice/'metrics.json').read_text())
        incumbent=json.loads((folder/'final/incumbent/metrics.json').read_text())
        if m['economic_max_drawdown']>incumbent['economic_max_drawdown']+1e-12:
            raise ValueError('Risk-controlled selection exceeds incumbent MDD')
    config=copy.deepcopy(config)
    config['study_policy'].update(parameter_search=False,selected_from_parameter_search=True)
    return config


def run_backtest(context=None,track='historical_pit',choice='return_winner'):
    config=build_config(track,choice)
    ctx=deep.context(track) if context is None else context
    result=deep.run_model(ctx,config)
    if result['metrics']['measured_hard_breach_days']!=0:
        raise ValueError('BLOCK: zero measured hard-breach condition no longer reproduces')
    source=STUDY/track/'final'/choice
    for key in ['equity','orders','trades','holdings']:
        expected=pd.read_csv(source/f'{key}.csv').fillna('')
        pd.testing.assert_frame_equal(expected,result[key].fillna(''),check_dtype=False,rtol=1e-12,atol=1e-5)
    expected=json.loads((source/'config.json').read_text())
    expected['study_policy'].update(parameter_search=False,selected_from_parameter_search=True)
    if expected!=result['config']:raise ValueError('Configuration replay mismatch')
    pd.testing.assert_series_equal(pd.Series(json.loads((source/'metrics.json').read_text())).sort_index(),
        pd.Series(result['metrics']).sort_index(),check_dtype=False,rtol=1e-12,atol=1e-5)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--track',choices=deep.second.TRACKS,default='historical_pit')
    p.add_argument('--choice',choices=CHOICES,default='return_winner')
    p.add_argument('--show-config',action='store_true');p.add_argument('--output')
    args=p.parse_args(argv)
    if args.show_config:
        print(json.dumps(build_config(args.track,args.choice),ensure_ascii=False,indent=2));return
    output=ROOT/(args.output or f'outputs/a_deep_entrypoint_replay/{args.track}/{args.choice}')
    if output.exists() and any(output.iterdir()):raise FileExistsError('Preserve existing replay')
    output.mkdir(parents=True,exist_ok=True)
    result=run_backtest(track=args.track,choice=args.choice)
    save_result(result,output)
    pd.DataFrame(monthly_rows(result['equity'])).to_csv(output/'monthly.csv',index=False)
    dump(output/'replay_audit.json',dict(status='PASS',scope='LEDGER_ORDERS_TRADES_HOLDINGS_CONFIG_METRICS',
        track=args.track,choice=args.choice,source_audit_sha256=sha(STUDY/args.track/'audit.json'),
        code_hashes={str(p.relative_to(ROOT)):sha(p) for p in [Path(__file__),ROOT/'src/tuning_a_deep.py']},
        official_submission='BLOCKED_UNKNOWN_ACTIVE_SHARE',selection_scope='EX_POST_DEVELOPMENT'))
    print(json.dumps(result['metrics'],ensure_ascii=False,indent=2))


if __name__=='__main__':main()
