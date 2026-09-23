"""Offline D-Plan check: python3 -m daily_auto.cli validate --help.

Exit 0: local deterministic checks pass (never formal certification).
Exit 2: blocked/malformed; exit 3: output already exists. No network requests.
Replay permits an explicit historical --checked-at but stays BLOCK_SUBMISSION.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys

from daily_auto.validate import (SCHEMA,OFFICIAL_REFERENCE,TZ,PreflightError,load_json,json_safe,validate_plan)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    check=sub.add_parser('validate',help='Validate a user-produced D-Plan against a real prior ledger')
    check.add_argument('--plan',type=Path,required=True)
    check.add_argument('--state',type=Path,required=True)
    check.add_argument('--output',type=Path,required=True)
    check.add_argument('--mode',choices=('live','replay'),default='live')
    check.add_argument('--checked-at',help='Replay-only hypothetical receipt clock, ISO8601 +08:00')
    args=parser.parse_args(argv)
    if args.output.exists():
        print('Preserve existing report: '+str(args.output),file=sys.stderr);return 3
    if args.checked_at and args.mode!='replay':
        parser.error('--checked-at requires --mode replay; live uses the actual local clock')
    try:
        plan,state=load_json(args.plan),load_json(args.state)
        if args.mode=='replay':
            state=dict(state);state['data_mode']='HISTORICAL_REPLAY'
        now=args.checked_at or datetime.now(TZ)
        report=validate_plan(plan,state,checked_at=now,filename=args.plan.name,evidence_root=args.state.parent)
        report['input_hashes']={str(path.resolve()):hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in (args.plan,args.state,SCHEMA,OFFICIAL_REFERENCE,Path(__file__),Path(__file__).with_name('validate.py'))}
    except (PreflightError,ValueError,TypeError,OSError) as exc:
        report=dict(status='BLOCK',submission_status='BLOCK_SUBMISSION',
                    blocking_codes=[getattr(exc,'code','INPUT_ERROR')],error=str(exc))
    report['mode']=args.mode
    args.output.parent.mkdir(parents=True,exist_ok=True)
    # Exclusive creation also guards races between two validation jobs.
    try:
        with args.output.open('x') as handle:
            json.dump(json_safe(report),handle,ensure_ascii=False,indent=2,allow_nan=False);handle.write('\n')
    except FileExistsError:return 3
    print(json.dumps({k:report.get(k) for k in ('status','submission_status','blocking_codes')},ensure_ascii=False))
    return 0 if report['status']=='LOCAL_PREFLIGHT_PASS' else 2


if __name__=='__main__':
    raise SystemExit(main())
