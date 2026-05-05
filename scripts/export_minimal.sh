#!/bin/bash
# Export the production-only slice of Athena's local Postgres for the
# Railway target. Skips the bulky historical ``trades`` (7M rows) and
# the local ``signals`` log; everything else (markets, wallets,
# wallet_metrics, watchlist, wash data) goes verbatim through
# pg_dump's COPY blocks, which import cleanly.
#
# Output: athena-minimal.sql (~150 MB), pipeable into psql.
#
# Usage: bash scripts/export_minimal.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

OUT="athena-minimal.sql"

echo ">>> dumping schema + everything except trades and signals"
pg_dump \
    --no-owner --no-privileges \
    --exclude-table-data=trades \
    --exclude-table-data=signals \
    "$DATABASE_URL" > "$OUT"

SIZE=$(du -h "$OUT" | cut -f1)
echo
echo "✅ wrote $OUT ($SIZE)"
echo
echo "Import to Railway with:"
echo '    psql "<DATABASE_PUBLIC_URL>" < athena-minimal.sql'
