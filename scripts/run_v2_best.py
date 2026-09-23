"""Compatibility entrypoint for the current audited second-round five-way replay.

The first-round runner remains in outputs/v2_best_final_20260922/input_snapshot.
"""
from pathlib import Path
import argparse
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_v2_second_best import run
from src.tuning_2nd import TRACKS

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--track', choices=TRACKS, default='historical_pit')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    run(args.track, ROOT / args.output)
