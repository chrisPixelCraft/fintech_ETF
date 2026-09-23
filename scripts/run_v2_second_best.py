"""Replay all three public second-round entrypoints without parameter selection."""
from pathlib import Path
import argparse,importlib,json,sys,copy
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import pandas as pd
from src import tuning_2nd as t
from src.backtest import save_result
from scripts.run_v2_tuning import dump,sha,monthly_rows


def run(track,output):
    if output.exists() and any(output.iterdir()):raise FileExistsError('Existing outputs preserved')
    output.mkdir(parents=True,exist_ok=True)
    pins=json.loads((ROOT/'outputs/tuning_report_2nd_try/pinned_entrypoints.json').read_text())
    for relative,digest in pins['hashes'].items():
        if sha(ROOT/relative)!=digest:raise ValueError('Pinned entrypoint changed: '+relative)
    ctx=t.context(track);summary=[];source=ROOT/'outputs/tuning_report_2nd_try'/track
    for strategy in ['A','B','C','v1_matched','0050']:
        if strategy in 'ABC':
            module=importlib.import_module(f'v2_{strategy}_best')
            result=module.run_backtest(ctx,track)
            candidate=module.SCENARIO_SELECTIONS[track]
        else:
            cfg=copy.deepcopy(ctx['base'])
            cfg.update(strategy_id=strategy,allocation_mode='full' if strategy=='v1_matched' else 'local',ex_post_fixed_universe=track=='official_ex_post',universe_mode=track)
            result=t.run_model(ctx,strategy,cfg)
            candidate='FIXED_BENCHMARK'
        result['metrics']['candidate_id']=candidate
        for key in ['equity','orders','trades','holdings']:
            expected=pd.read_csv(source/'final'/strategy/f'{key}.csv')
            pd.testing.assert_frame_equal(expected.fillna(''),result[key].fillna(''),check_dtype=False,rtol=1e-12,atol=1e-5)
        expected_config=json.loads((source/'final'/strategy/'config.json').read_text())
        if strategy in 'ABC':
            expected_config['study_policy'].update(parameter_search=False,selected_from_parameter_search=True,selection_scope='EX_POST_DEVELOPMENT',official_live_submission='BLOCK_IF_UNKNOWN')
        if expected_config!=result['config']:raise ValueError('Pinned replay configuration mismatch: '+strategy)
        expected_metrics=json.loads((source/'final'/strategy/'metrics.json').read_text())
        pd.testing.assert_series_equal(pd.Series(expected_metrics).sort_index(),pd.Series(result['metrics']).sort_index(),check_dtype=False,rtol=1e-12,atol=1e-5)
        save_result(result,output/strategy)
        pd.DataFrame(monthly_rows(result['equity'])).to_csv(output/strategy/'monthly.csv',index=False)
        summary.append(dict(strategy=strategy,**result['metrics']))
        print(strategy+' reproducible',flush=True)
    pd.DataFrame(summary).to_csv(output/'summary.csv',index=False)
    dump(output/'replay_audit.json',dict(status='PASS',scope='PUBLIC_ENTRYPOINT_LEDGER_ORDERS_TRADES_HOLDINGS_CONFIG_METRICS_EQUIVALENCE',track=track,
        source_audit_sha256=sha(source/'audit.json'),
        code_hashes={p:sha(ROOT/p) for p in ['v2_A_best.py','v2_B_best.py','v2_C_best.py','src/v2_second_best.py','scripts/run_v2_second_best.py']}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--track',choices=t.TRACKS,default='historical_pit');p.add_argument('--output',required=True)
    a=p.parse_args();run(a.track,ROOT/a.output)
