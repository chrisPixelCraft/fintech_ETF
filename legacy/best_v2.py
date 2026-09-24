"""Fixed x0352 research replay; every formal submission remains blocked."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
STUDY = ROOT / 'outputs/best_v2'
TRACKS = ('official_ex_post', 'historical_pit')
LIVE_BLOCKERS = (
    'OFFICIAL_ACTIVE_SHARE_DEFINITION_UNRESOLVED',
    'OFFICIAL_ACCOUNT_AND_SUBMISSION_ACCEPTANCE_UNVERIFIED',
    'RESEARCH_AUDIT_IS_NOT_OFFICIAL_APPROVAL',
)


def build_config(track='official_ex_post'):
    from scripts.verify_best_v2 import verify_release
    from src.official_deep_tuning import FIXED, validate_params
    if track not in TRACKS:
        raise ValueError('Unknown universe track')
    verify_release()
    config = json.loads((STUDY / track / 'config.json').read_text())
    validate_params(config['full_tuning_params'])
    if config['strategy_id'] != 'full_tuned_v2_double_check_x0352' or config['universe_mode'] != track:
        raise ValueError('Fixed candidate identity changed')
    for key, value in FIXED.items():
        if config.get(key) != value:
            raise ValueError('Fixed setting changed: ' + key)
    return config


def run_backtest(track, output):
    from src import double_check_tuning, tuning_a_deep
    from src.backtest import save_result
    from scripts.audit_double_check import audit_result
    target = Path(output).resolve()
    for protected in (ROOT / 'outputs/best_v2', ROOT / 'outputs/comparisons', ROOT / 'data', ROOT / 'config'):
        if target == protected or protected in target.parents:
            raise ValueError('Replay cannot overwrite retained evidence')
    if target.exists():
        raise FileExistsError('Preserve existing output: ' + str(target))
    config = build_config(track)
    context = tuning_a_deep.context(track)
    result = double_check_tuning.run_model(context, config)
    independent = audit_result(result, context)
    if not double_check_tuning.eligible(dict(status='COMPLETE', **independent)):
        raise ValueError('Fixed replay no longer passes measured research gates')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix='.best-v2-') as temporary:
        staged = Path(temporary) / 'result'
        save_result(result, staged)
        for name in ('plan_audit', 'compliance_daily', 'rejected_trades'):
            result[name].to_csv(staged / (name + '.csv'), index=False)
        (staged / 'independent_audit.json').write_text(json.dumps(independent, indent=2, allow_nan=False) + '\n')
        receipt = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in staged.iterdir() if p.is_file()}
        (staged / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        staged.rename(target)
    print(json.dumps(dict(research_audit='PASS', candidate_id='x0352', track=track,
                          total_return=independent['total_return'], output=str(target),
                          submission_status='BLOCK_SUBMISSION')))


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ('plan', 'research-plan'):
        print(json.dumps(dict(status='BLOCK', submission_status='BLOCK_SUBMISSION',
                              blocking_codes=LIVE_BLOCKERS, detail='No D-Plan exported.')))
        return 2
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--verify-release', action='store_true')
    action.add_argument('--show-config', action='store_true')
    action.add_argument('--output', type=Path)
    parser.add_argument('--track', choices=TRACKS, default=TRACKS[0])
    options = parser.parse_args(args)
    try:
        if options.verify_release:
            from scripts.verify_best_v2 import verify_release
            audit = verify_release()
            print(json.dumps(dict(status=audit['status'], candidate_id='x0352', submission_status='BLOCK_SUBMISSION')))
        elif options.show_config:
            print(json.dumps(build_config(options.track), indent=2, ensure_ascii=False))
        elif options.output is not None:
            run_backtest(options.track, options.output)
        else:
            parser.print_help()
    except (AssertionError, ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps(dict(status='BLOCK', submission_status='BLOCK_SUBMISSION', error=str(exc))))
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
