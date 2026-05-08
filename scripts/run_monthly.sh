#!/usr/bin/env bash
# Cron-friendly wrapper. Add to crontab:
#   0 9 1 * *  /path/to/stock-predictor/scripts/run_monthly.sh
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
python -m src.main "$@"
