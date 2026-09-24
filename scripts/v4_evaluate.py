#!/usr/bin/env python3
"""Execute the complete predeclared chronological Stage-2 protocol."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.v4_stage2_run import run
if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--study',default='config/v4_stage2_study.json')
    p.add_argument('--output',default='outputs/v4/stage2')
    p.add_argument('--workers',type=int,default=4)
    a=p.parse_args()
    run(a.study,a.output,a.workers)
