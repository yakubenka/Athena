"""Integration tests against the live local Postgres.

Requires DATABASE_URL in .env (set during scaffold step 8) and the
schema to be applied. Skips cleanly if DB is unreachable so that
`uv run pytest` stays green on a fresh clone without Postgres.
"""

from __future__ import annotations

import psycopg
import pytest

from ingestion.db import connect


def _db_reachable() -> bool:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
    except (psycopg.OperationalError, OSError):
        return False
    return True


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="local Postgres not running")


def test_connect_yields_live_connection() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        assert cur.fetchone() == (1,)


def test_schema_tables_are_present() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        tables = {row[0] for row in cur.fetchall()}
    for required in ("wallets", "markets", "trades", "wash_clusters", "signals"):
        assert required in tables, f"missing table: {required}"
