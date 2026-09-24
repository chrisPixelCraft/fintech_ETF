#!/usr/bin/env python3
"""Run the fixed momentum development budget; no validation/holdout tuning."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.v4_stage2_run import run_development_family
if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--study',default='config/v4_stage2_study.json')
    p.add_argument('--output',default='outputs/v4/momentum_development')
    p.add_argument('--workers',type=int,default=4)
    a=p.parse_args()
    run_development_family('momentum',a.study,a.output,a.workers)
