#!/usr/bin/env bash
# Daily production entry point (docs/production_spec.md section 11).
#
#   ./run_daily.sh                                   # trade date = today (Taipei)
#   ./run_daily.sh 2026-10-27 --holdings ~/Downloads/holdings.json
#   ./run_daily.sh 2026-10-27 --offline              # dry run on the local snapshot, no network
#
# Run between 05:00 and 08:55 Taipei on trade date T. Output: production_runs/<T>/
#   D-Plan_<team>_<T>.json  -> upload this file (submission is manual, P-U2)
#   audit.md / audit.json   -> what was decided and why
#   status.txt              -> READY_TO_SUBMIT or EMERGENCY_REVIEW_REQUIRED
# Exit code 0 = ready to submit, 2 = a person must look at audit.md.
set -euo pipefail
cd "$(dirname "$0")"

PY=${PY:-.venv/bin/python}
export PYTHONHASHSEED=0 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 MKL_NUM_THREADS=1

TRADE_DATE=${1:-$(TZ=Asia/Taipei date +%F)}
[ $# -gt 0 ] && shift

"$PY" -m production.run_daily --trade-date "$TRADE_DATE" "$@"
STATUS=$(cat "production_runs/$TRADE_DATE/status.txt" 2>/dev/null || echo EMERGENCY_REVIEW_REQUIRED)
cat "production_runs/$TRADE_DATE/audit.md" 2>/dev/null || true
[ "$STATUS" = "READY_TO_SUBMIT" ] && exit 0
echo "!! $STATUS: read production_runs/$TRADE_DATE/audit.md before 08:55" >&2
exit 2
