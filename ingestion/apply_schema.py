"""Apply ingestion/schema.sql to the database pointed to by DATABASE_URL."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def main() -> int:
    load_dotenv()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL not set (see .env.example)", file=sys.stderr)
        return 1

    sql = SCHEMA_PATH.read_text()
    print(f"Applying {SCHEMA_PATH.name} ({len(sql):,} bytes) to {_mask(dsn)}")

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql)
        conn.commit()
        cur.execute(
            "SELECT tablename FROM pg_tables "
            "WHERE schemaname = 'public' ORDER BY tablename"
        )
        tables = [row[0] for row in cur.fetchall()]

    print(f"OK. {len(tables)} tables in public schema:")
    for t in tables:
        print(f"  - {t}")
    return 0


def _mask(dsn: str) -> str:
    """Hide password in DSN for log output."""
    if "@" not in dsn or "//" not in dsn:
        return dsn
    prefix, rest = dsn.split("//", 1)
    creds, tail = rest.split("@", 1)
    user = creds.split(":", 1)[0] if ":" in creds else creds
    return f"{prefix}//{user}:***@{tail}"


if __name__ == "__main__":
    sys.exit(main())
