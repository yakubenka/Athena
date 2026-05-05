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

# Markets — small enough to dump whole.
echo ">>> dumping markets"
pg_dump --data-only --no-owner --no-privileges --table=markets "$DATABASE_URL" >> "$OUT"

# Wash cluster definitions (no FKs into wallets).
echo ">>> dumping wash_clusters"
pg_dump --data-only --no-owner --no-privileges --table=wash_clusters "$DATABASE_URL" >> "$OUT"

# Filtered wallets/metrics/membership/watchlist — we generate INSERT
# statements directly because pg_dump can't filter rows. These tables
# carry FKs into each other, so the insert order matters: wallets must
# arrive before wash_cluster_membership and watchlist.
echo ">>> generating filtered INSERTs (wallets, wallet_metrics, watchlist, wash_cluster_membership)"
psql "$DATABASE_URL" -At >> "$OUT" <<'EOF'
\pset format unaligned
\pset tuples_only on

-- wallets: keep watchlist members + every wallet flagged in any wash cluster.
SELECT format(
    'INSERT INTO wallets VALUES (%L,%L,%L,%s,%s,%s,%s,%s,%s,%L,%L,%s,%s,%L,%L,%L,%L,%s,%L) ON CONFLICT (address) DO NOTHING;',
    address, first_seen, last_seen,
    COALESCE(total_volume::text, 'NULL'),
    COALESCE(total_trades::text, 'NULL'),
    COALESCE(total_maker_volume::text, 'NULL'),
    COALESCE(total_taker_volume::text, 'NULL'),
    COALESCE(total_maker_trades::text, 'NULL'),
    COALESCE(total_taker_trades::text, 'NULL'),
    proxy_wallet, label, '0', '0',
    NULL,
    wash_cluster_id,
    NULL,
    tier,
    COALESCE(consecutive_profitable_months::text, '0'),
    notes
)
FROM wallets
WHERE address IN (SELECT address FROM watchlist)
   OR address IN (SELECT wallet FROM wash_cluster_membership);

-- wallet_metrics: only watchlist members.
SELECT format(
    'INSERT INTO wallet_metrics (address, frac_maker_volume, frac_maker_trades, frac_extreme_price, frac_yes_trades, frac_no_trades, frac_yes_at_longshot, frac_no_at_longshot, counterparty_hhi, max_consecutive_profitable_months, max_consecutive_5k_plus_months, wash_score, pnl_to_volume_ratio, rapid_open_close_ratio, last_trade_at) VALUES (%L,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%L) ON CONFLICT (address) DO NOTHING;',
    address,
    COALESCE(frac_maker_volume::text, 'NULL'),
    COALESCE(frac_maker_trades::text, 'NULL'),
    COALESCE(frac_extreme_price::text, 'NULL'),
    COALESCE(frac_yes_trades::text, 'NULL'),
    COALESCE(frac_no_trades::text, 'NULL'),
    COALESCE(frac_yes_at_longshot::text, 'NULL'),
    COALESCE(frac_no_at_longshot::text, 'NULL'),
    COALESCE(counterparty_hhi::text, 'NULL'),
    COALESCE(max_consecutive_profitable_months::text, 'NULL'),
    COALESCE(max_consecutive_5k_plus_months::text, 'NULL'),
    COALESCE(wash_score::text, 'NULL'),
    COALESCE(pnl_to_volume_ratio::text, 'NULL'),
    COALESCE(rapid_open_close_ratio::text, 'NULL'),
    last_trade_at
)
FROM wallet_metrics
WHERE address IN (SELECT address FROM watchlist);

-- watchlist (after wallets, since FK).
SELECT format(
    'INSERT INTO watchlist (address, added_at, tier, specialty_category, copy_enabled, copy_size_usdc, notes, last_reviewed) VALUES (%L,%L,%L,%L,%L,%s,%L,%L) ON CONFLICT (address) DO NOTHING;',
    address, added_at, tier, specialty_category,
    copy_enabled::text,
    COALESCE(copy_size_usdc::text, 'NULL'),
    notes, last_reviewed
)
FROM watchlist;

-- wash_cluster_membership (after wallets + wash_clusters).
SELECT format(
    'INSERT INTO wash_cluster_membership (cluster_id, wallet, added_at, volume_in_cluster) VALUES (%L,%L,%L,%s) ON CONFLICT (cluster_id, wallet) DO NOTHING;',
    cluster_id, wallet, added_at,
    COALESCE(volume_in_cluster::text, 'NULL')
)
FROM wash_cluster_membership;
EOF

SIZE=$(du -h "$OUT" | cut -f1)
echo
echo "✅ wrote $OUT ($SIZE)"
echo
echo "Import to Railway with:"
echo '    psql "<DATABASE_PUBLIC_URL>" < athena-minimal.sql'
