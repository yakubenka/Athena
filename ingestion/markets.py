"""Ingest Polymarket markets metadata into the local ``markets`` table.

Pulls pages from Gamma via :mod:`ingestion.gamma_client` and upserts rows
with an ``INSERT ... ON CONFLICT (condition_id) DO UPDATE`` so re-runs
are idempotent. Fields not exposed by Gamma yet (``category``,
``category_tier``, ``est_wash_fraction``) are left untouched.

CLI::

    uv run python -m ingestion.markets --limit 100
    uv run python -m ingestion.markets --closed false --page-size 200
    uv run python -m ingestion.markets --dry-run --limit 10
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Iterator
from typing import Any

import psycopg

from ingestion.db import connect
from ingestion.gamma_client import GammaMarket, iter_markets

BATCH_SIZE_DEFAULT = 500

UPSERT_SQL = """
INSERT INTO markets (
    condition_id,
    question,
    created_at,
    end_date,
    resolved_at,
    resolved_outcome,
    total_volume
)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (condition_id) DO UPDATE SET
    question = EXCLUDED.question,
    created_at = COALESCE(EXCLUDED.created_at, markets.created_at),
    end_date = COALESCE(EXCLUDED.end_date, markets.end_date),
    resolved_at = COALESCE(EXCLUDED.resolved_at, markets.resolved_at),
    resolved_outcome = COALESCE(EXCLUDED.resolved_outcome, markets.resolved_outcome),
    total_volume = COALESCE(EXCLUDED.total_volume, markets.total_volume);
"""


def market_to_row(market: GammaMarket) -> tuple[Any, ...]:
    """Project a GammaMarket onto the markets-table column order."""
    return (
        market.condition_id,
        market.question,
        market.created_at,
        market.end_date,
        market.resolved_at,
        market.resolved_outcome,
        market.volume_num,
    )


def upsert_markets(
    markets: Iterable[GammaMarket],
    *,
    conn: psycopg.Connection | None = None,
    batch_size: int = BATCH_SIZE_DEFAULT,
) -> int:
    """Upsert an iterable of markets. Returns the number of rows sent.

    If ``conn`` is None the function opens and commits its own connection
    via :func:`ingestion.db.connect`.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    total = 0

    def _run(active_conn: psycopg.Connection) -> None:
        nonlocal total
        with active_conn.cursor() as cur:
            for batch in _chunked(markets, batch_size):
                rows = [market_to_row(m) for m in batch]
                cur.executemany(UPSERT_SQL, rows)
                total += len(rows)

    if conn is None:
        with connect() as own:
            _run(own)
            own.commit()
    else:
        _run(conn)

    return total


def _chunked(items: Iterable[GammaMarket], size: int) -> Iterator[list[GammaMarket]]:
    batch: list[GammaMarket] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Ingest Polymarket markets into Postgres")
    p.add_argument(
        "--closed",
        choices=("true", "false", "all"),
        default="all",
        help="filter by market closed status (default: all)",
    )
    p.add_argument("--limit", type=int, default=None, help="stop after this many markets")
    p.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="Gamma API page size (default 100, max 500)",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=BATCH_SIZE_DEFAULT,
        help="DB upsert batch size",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="fetch and parse but do not write to Postgres",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)

    closed_arg: bool | None = None
    if args.closed != "all":
        closed_arg = args.closed == "true"

    stream = iter_markets(
        closed=closed_arg,
        page_size=args.page_size,
        max_markets=args.limit,
    )

    if args.dry_run:
        n = sum(1 for _ in stream)
        print(f"[dry-run] fetched {n} markets, nothing written")
        return 0

    n = upsert_markets(stream, batch_size=args.batch_size)
    print(f"Upserted {n} markets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
