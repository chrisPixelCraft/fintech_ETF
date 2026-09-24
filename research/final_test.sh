#!/usr/bin/env bash
# Final test (docs/final_test_plan.md): unit tests -> stage 1 tree calibration -> stage 2 final test.
#
#   bash research/final_test.sh                 # 10 workers
#   WORKERS=8 bash research/final_test.sh
#
# Rerun the same command to resume: finished calibration and finished runs are reused.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PY:-.venv/bin/python}
WORKERS=${WORKERS:-10}
export PYTHONHASHSEED=0 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 MKL_NUM_THREADS=1
KEEP_AWAKE=(); command -v caffeinate >/dev/null && KEEP_AWAKE=(caffeinate -i)

echo "== unit tests"
"$PY" -m unittest discover -s tests -q

if [ ! -f research/results/tree_calibration/trees.json ]; then
  echo "== stage 1: tree calibration (2020-2024 decisions, no returns looked at)"
  "${KEEP_AWAKE[@]}" "$PY" -m research.tree_calibration --workers "$WORKERS"
fi

echo "== stage 2: final test 2025-01..2026-09"
"${KEEP_AWAKE[@]}" "$PY" -m research.final_test --workers "$WORKERS"
