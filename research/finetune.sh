#!/usr/bin/env bash
# One-shot AutoTS tuning (research/tune.py). Parameters are chosen on
# 2010-2024 (dev + validation); 2025-2026/09 (holdout) is scored once at the
# end as the test and does not change the choice.
#
#   bash research/finetune.sh                          # crazy profile, 10 workers
#   PROFILE=normal WORKERS=8 bash research/finetune.sh
#   PROFILE=quick bash research/finetune.sh            # short smoke test
#
# Rerun the same command to resume after an interruption. TAG=<name> starts a
# separate search. Results to commit: research/results/tune_<tag>/
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PY:-.venv/bin/python}
PROFILE=${PROFILE:-crazy}
WORKERS=${WORKERS:-10}
TAG=${TAG:-$PROFILE}

if [ ! -x "$PY" ]; then
  echo "Missing $PY. Set up the venv first (README section 4.1)."
  exit 1
fi
if ! "$PY" -c "import autots" 2>/dev/null; then
  echo "AutoTS is not importable. Add the .pth file (README section 4.1)."
  exit 1
fi

# Fixed hashing; one BLAS thread per worker process so workers do not fight for cores.
export PYTHONHASHSEED=0 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 MKL_NUM_THREADS=1

echo "profile=$PROFILE workers=$WORKERS tag=$TAG"
echo "== unit tests"
"$PY" -m unittest discover -s tests -q

TUNE=("$PY" -m research.tune --profile "$PROFILE" --workers "$WORKERS" --tag "$TAG")
if command -v caffeinate >/dev/null; then
  caffeinate -i "${TUNE[@]}"   # macOS: no sleep while tuning
else
  "${TUNE[@]}"
fi

echo
echo "Done. Summary: research/results/tune_${TAG}/summary.md"
echo "Commit it:    git add research/results/tune_${TAG} && git commit -m \"Add tuning results ${TAG}\""
