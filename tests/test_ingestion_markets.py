"""Tests for ingestion.markets — upsert into Postgres."""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest

from ingestion.db import connect
from ingestion.gamma_client import GammaMarket
from ingestion.markets import _chunked, main, market_to_row, upsert_markets


def _db_reachable() -> bool:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
    except (psycopg.OperationalError, OSError):
        return False
    return True


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="local Postgres not running")


def _market(
    condition_id: str = "0xaaaa",
    *,
    question: str = "Will X happen?",
    volume: float = 100.0,
    closed: bool = False,
    resolved_outcome: str | None = None,
    resolved_at: datetime | None = None,
) -> GammaMarket:
    return GammaMarket.model_validate(
        {
            "conditionId": condition_id,
            "question": question,
            "createdAt": "2025-01-01T00:00:00Z",
            "endDate": "2025-12-31T00:00:00Z",
            "active": not closed,
            "closed": closed,
            "archived": False,
            "volumeNum": volume,
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["0.5", "0.5"]',
            "resolvedAt": resolved_at.isoformat() if resolved_at else None,
            "resolvedOutcome": resolved_outcome,
        }
    )


@pytest.fixture
def clean_markets() -> None:
    """Wipe the markets table between tests so inserts are isolated."""
    with connect() as conn, conn.cursor() as cur:
        # signals / wallet_positions reference markets, so truncate their FKs too.
        cur.execute("TRUNCATE markets, wallet_positions, trades, signals RESTART IDENTITY CASCADE")
        conn.commit()


def test_market_to_row_matches_column_order() -> None:
    m = _market("0xrow", volume=42.0)
    row = market_to_row(m)
    assert row[0] == "0xrow"
    assert row[1] == "Will X happen?"
    assert row[6] == 42.0


def test_chunked_yields_full_and_final_partial_batches() -> None:
    items = [_market(f"0x{i:02x}") for i in range(7)]
    batches = list(_chunked(iter(items), size=3))
    assert [len(b) for b in batches] == [3, 3, 1]


def test_upsert_inserts_new_markets(clean_markets: None) -> None:
    n = upsert_markets([_market("0xnew1"), _market("0xnew2", volume=200.0)])
    assert n == 2

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT condition_id, total_volume FROM markets ORDER BY condition_id")
        rows = cur.fetchall()
    assert rows == [("0xnew1", 100.0), ("0xnew2", 200.0)]


def test_upsert_updates_existing_market(clean_markets: None) -> None:
    upsert_markets([_market("0xsame", volume=100.0)])
    upsert_markets([_market("0xsame", volume=250.5, question="Updated?")])

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT question, total_volume FROM markets WHERE condition_id = %s",
            ("0xsame",),
        )
        row = cur.fetchone()
    assert row == ("Updated?", 250.5)


def test_upsert_preserves_resolution_when_later_update_has_none(clean_markets: None) -> None:
    resolved_at = datetime(2025, 6, 1, tzinfo=UTC)
    upsert_markets(
        [
            _market(
                "0xres",
                closed=True,
                resolved_outcome="Yes",
                resolved_at=resolved_at,
            )
        ]
    )
    # Simulate a later fetch from Gamma that lacks resolution fields
    # (shouldn't wipe what we already persisted).
    upsert_markets([_market("0xres", closed=False)])

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT resolved_outcome, resolved_at FROM markets WHERE condition_id = %s",
            ("0xres",),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "Yes"
    assert row[1] is not None


def test_cli_dry_run_does_not_write(clean_markets: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_iter(**_kwargs: object) -> list[GammaMarket]:
        return [_market("0xcli1"), _market("0xcli2")]

    monkeypatch.setattr("ingestion.markets.iter_markets", fake_iter)
    rc = main(["--dry-run", "--limit", "10"])
    assert rc == 0

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM markets")
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 0


def test_cli_writes_to_db(clean_markets: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_iter(**_kwargs: object) -> list[GammaMarket]:
        return [_market("0xcliwrite")]

    monkeypatch.setattr("ingestion.markets.iter_markets", fake_iter)
    rc = main(["--limit", "5"])
    assert rc == 0

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT condition_id FROM markets")
        rows = cur.fetchall()
    assert rows == [("0xcliwrite",)]
