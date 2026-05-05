#!/bin/bash
# Export the "production-only" slice of Athena's local Postgres for the
# Railway target. Skips the bulky historical trades + 415k wallet rows we
# don't need on the watcher; keeps watchlist + wash + minimal markets +
# wallet rows that are FK'd from the kept tables.
#
# Output: athena-minimal.sql (a few MB), pipeable into psql.
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
: > "$OUT"

# Schema first — same DDL that initialises a fresh DB.
echo ">>> dumping schema"
pg_dump --schema-only --no-owner --no-privileges "$DATABASE_URL" >> "$OUT"

# Markets: needed for token_id → (condition_id, outcome) lookup at decode
# time AND for the question text in Telegram alerts. ~50k rows, small.
echo ">>> dumping markets"
pg_dump --data-only --no-owner --no-privileges --table=markets "$DATABASE_URL" >> "$OUT"

# Watchlist + wash data first because wallets has FKs we'll filter against.
echo ">>> dumping watchlist + wash_clusters + wash_cluster_membership"
pg_dump --data-only --no-owner --no-privileges \
    --table=watchlist \
    --table=wash_clusters \
    --table=wash_cluster_membership \
    "$DATABASE_URL" >> "$OUT"

# Wallets: only those referenced by anything we kept above.
echo ">>> dumping relevant wallets"
psql "$DATABASE_URL" -At -c "
COPY (
    SELECT * FROM wallets
    WHERE address IN (SELECT address FROM watchlist)
       OR address IN (SELECT wallet FROM wash_cluster_membership)
) TO STDOUT WITH (FORMAT text)
" > /tmp/athena-wallets.tsv

WALLET_COUNT=$(wc -l < /tmp/athena-wallets.tsv | tr -d ' ')
echo ">>> $WALLET_COUNT wallet rows"

cat >> "$OUT" <<EOF

COPY wallets FROM stdin;
$(cat /tmp/athena-wallets.tsv)
\.
EOF
rm -f /tmp/athena-wallets.tsv

# Wallet metrics: only watchlist members (need full profile for push payload).
echo ">>> dumping watchlist wallet_metrics"
psql "$DATABASE_URL" -At -c "
COPY (
    SELECT * FROM wallet_metrics
    WHERE address IN (SELECT address FROM watchlist)
) TO STDOUT WITH (FORMAT text)
" > /tmp/athena-metrics.tsv

cat >> "$OUT" <<EOF

COPY wallet_metrics FROM stdin;
$(cat /tmp/athena-metrics.tsv)
\.
EOF
rm -f /tmp/athena-metrics.tsv

SIZE=$(du -h "$OUT" | cut -f1)
echo
echo "✅ wrote $OUT ($SIZE)"
echo "Import to Railway with:"
echo "    psql \"\$RAILWAY_DATABASE_URL\" < $OUT"
