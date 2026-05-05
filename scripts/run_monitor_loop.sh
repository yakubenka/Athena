#!/bin/bash
# Run Athena monitor in a continuous loop. Each iteration polls for fresh
# watchlist trades, writes signals, pushes to Prometheus, alerts via Telegram.
# Sleep is between iterations, not inside them, so a slow tick won't double-up.

set -euo pipefail

cd /Users/yakubenka/Documents/Athena
set -a
# shellcheck disable=SC1091
source .env
set +a

echo "[$(date '+%Y-%m-%d %H:%M:%S')] athena-monitor: starting loop"

while true; do
    uv run python -m monitoring \
        --since auto \
        --push-prometheus \
        --telegram \
        || echo "[$(date '+%Y-%m-%d %H:%M:%S')] monitor tick failed, will retry"
    sleep 60
done
