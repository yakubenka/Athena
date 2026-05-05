#!/bin/bash
# Run Athena monitor in a continuous loop. Each iteration polls for fresh
# watchlist trades, writes signals, pushes to Prometheus, alerts via Telegram.
# Sleep is between iterations, not inside them, so a slow tick won't double-up.

set -euo pipefail

# Resolve the project root from the script location so the same file works
# locally on macOS (where the repo lives in ~/Documents/Athena) and inside
# the Railway container (where it's at /app).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Local dev sources .env; on Railway the env vars are injected directly.
if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] athena-monitor: starting loop"

while true; do
    uv run python -m monitoring \
        --since auto \
        --push-prometheus \
        --telegram \
        || echo "[$(date '+%Y-%m-%d %H:%M:%S')] monitor tick failed, will retry"
    sleep 60
done
