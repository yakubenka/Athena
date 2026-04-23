"""Smoke tests: scaffold sanity checks. Run with `uv run pytest`."""

from __future__ import annotations

from pathlib import Path

from ingestion.apply_schema import SCHEMA_PATH, _mask


def test_runtime_imports() -> None:
    import httpx
    import networkx
    import numpy
    import pandas
    import psycopg
    import pydantic
    import structlog

    for mod in (httpx, networkx, numpy, pandas, psycopg, pydantic, structlog):
        assert mod.__name__


def test_schema_file_is_readable_and_nonempty() -> None:
    assert SCHEMA_PATH.is_file(), f"missing: {SCHEMA_PATH}"
    body = SCHEMA_PATH.read_text()
    assert len(body) > 1000, "schema.sql looks truncated"
    for table in ("wallets", "markets", "trades", "wash_clusters", "signals"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in body, f"missing table: {table}"


def test_mask_hides_password() -> None:
    dsn = "postgresql://athena:supersecret@localhost:5432/athena"
    masked = _mask(dsn)
    assert "supersecret" not in masked
    assert "athena" in masked
    assert "localhost:5432/athena" in masked


def test_mask_handles_dsn_without_password() -> None:
    assert _mask("postgresql://localhost/athena") == "postgresql://localhost/athena"


def test_roadmap_exists() -> None:
    roadmap = Path(__file__).parent.parent / "research" / "roadmap-v1.3.md"
    assert roadmap.is_file()
    assert "Polymarket Intelligence" in roadmap.read_text()
