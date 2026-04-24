"""End-to-end tests for ingestion.trades.

Uses httpx MockTransport to simulate the Polygon RPC and runs against
the live local Postgres. Skips cleanly if the DB isn't reachable.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx
import psycopg
import pytest

from ingestion.db import connect
from ingestion.orderfilled import ORDER_FILLED_TOPIC0
from ingestion.trades import (
    DEFAULT_START_BLOCK,
    get_resume_block,
    load_token_index,
    process_chunk,
)


def _db_reachable() -> bool:
    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
    except (psycopg.OperationalError, OSError):
        return False
    return True


pytestmark = pytest.mark.skipif(not _db_reachable(), reason="local Postgres not running")


# ---------------------------------------------------------------------------
# Helpers to build synthetic RPC responses
# ---------------------------------------------------------------------------


def _address_topic(addr: str) -> str:
    stripped = addr.lower().removeprefix("0x")
    return "0x" + "0" * (64 - len(stripped)) + stripped


def _uint256(value: int) -> str:
    return f"{value:064x}"


def _make_log(
    *,
    maker: str,
    taker: str,
    maker_asset_id: int,
    taker_asset_id: int,
    maker_amount: int,
    taker_amount: int,
    tx_hash: str,
    log_index: int,
    block_number: int,
) -> dict[str, Any]:
    data = "0x" + "".join(
        _uint256(v) for v in (maker_asset_id, taker_asset_id, maker_amount, taker_amount, 0)
    )
    return {
        "topics": [
            ORDER_FILLED_TOPIC0,
            "0x" + "ab" * 32,  # order hash (unused by decoder)
            _address_topic(maker),
            _address_topic(taker),
        ],
        "data": data,
        "transactionHash": tx_hash,
        "logIndex": hex(log_index),
        "blockNumber": hex(block_number),
    }


def _build_rpc_mock(logs: list[dict[str, Any]], block_timestamps: dict[int, int]) -> httpx.Client:
    """Handle eth_getLogs (returns `logs`) and batched eth_getBlockByNumber."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if isinstance(body, list):
            # Batched eth_getBlockByNumber
            out = []
            for entry in body:
                bn = entry["id"]
                ts = block_timestamps.get(int(bn))
                out.append(
                    {
                        "jsonrpc": "2.0",
                        "id": bn,
                        "result": {"timestamp": hex(ts)} if ts is not None else None,
                    }
                )
            return httpx.Response(200, json=out)
        # Single call
        if body["method"] == "eth_getLogs":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": logs})
        if body["method"] == "eth_blockNumber":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x5000000"})
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "error": f"unhandled {body['method']}"}
        )

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://polygon.test")


# ---------------------------------------------------------------------------
# DB-backed fixture: seed markets + reset trades between tests
# ---------------------------------------------------------------------------


MAKER = "0x" + "1" * 40
TAKER = "0x" + "2" * 40
YES_TOKEN = "9001"
NO_TOKEN = "9002"
CONDITION_ID = "0xfeedfacefeedfacefeedfacefeedfacefeedfacefeedfacefeedfacefeedface"


@pytest.fixture
def seeded_market() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("TRUNCATE markets, wallet_positions, trades, signals RESTART IDENTITY CASCADE")
        cur.execute(
            "INSERT INTO markets (condition_id, question, yes_token_id, no_token_id) "
            "VALUES (%s, %s, %s, %s)",
            (CONDITION_ID, "Will X happen?", YES_TOKEN, NO_TOKEN),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_load_token_index_reads_both_outcomes(seeded_market: None) -> None:
    with connect() as conn:
        index = load_token_index(conn)
    assert index[YES_TOKEN] == (CONDITION_ID, "YES")
    assert index[NO_TOKEN] == (CONDITION_ID, "NO")


def test_get_resume_block_returns_default_when_empty(seeded_market: None) -> None:
    with connect() as conn:
        assert get_resume_block(conn, DEFAULT_START_BLOCK) == DEFAULT_START_BLOCK


def test_process_chunk_decodes_and_writes_trades(seeded_market: None) -> None:
    # Two trades on the YES token in the same block.
    logs = [
        _make_log(
            maker=MAKER,
            taker=TAKER,
            maker_asset_id=0,
            taker_asset_id=int(YES_TOKEN),
            maker_amount=2_500_000,
            taker_amount=5_000_000,
            tx_hash="0x" + "aa" * 32,
            log_index=4,
            block_number=1_000_000,
        ),
        _make_log(
            maker=MAKER,
            taker=TAKER,
            maker_asset_id=int(YES_TOKEN),
            taker_asset_id=0,
            maker_amount=10_000_000,
            taker_amount=7_500_000,
            tx_hash="0x" + "bb" * 32,
            log_index=2,
            block_number=1_000_005,
        ),
    ]
    timestamps = {1_000_000: 1_700_000_000, 1_000_005: 1_700_000_100}

    with connect() as conn, _build_rpc_mock(logs, timestamps) as client:
        index = load_token_index(conn)
        seen, written = process_chunk(1_000_000, 1_000_010, index, conn, client)

    assert seen == 2
    assert written == 2

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT tx_hash, condition_id, outcome, taker_side, size, price, block_number "
            "FROM trades ORDER BY block_number, log_index"
        )
        rows = cur.fetchall()

    assert len(rows) == 2
    first, second = rows
    assert first[1] == CONDITION_ID
    assert first[2] == "YES"
    assert first[3] == "SELL"
    assert first[4] == Decimal("5.000000")
    assert first[5] == Decimal("0.500000")
    assert first[6] == 1_000_000

    assert second[3] == "BUY"
    assert second[5] == Decimal("0.750000")


def test_process_chunk_is_idempotent(seeded_market: None) -> None:
    log = _make_log(
        maker=MAKER,
        taker=TAKER,
        maker_asset_id=0,
        taker_asset_id=int(YES_TOKEN),
        maker_amount=1_000_000,
        taker_amount=2_000_000,
        tx_hash="0x" + "cc" * 32,
        log_index=1,
        block_number=2_000_000,
    )
    timestamps = {2_000_000: 1_700_000_500}

    with connect() as conn, _build_rpc_mock([log], timestamps) as client:
        index = load_token_index(conn)
        process_chunk(2_000_000, 2_000_000, index, conn, client)
        # Second run should not insert duplicates thanks to ON CONFLICT DO NOTHING.
        process_chunk(2_000_000, 2_000_000, index, conn, client)

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM trades")
        row = cur.fetchone()
    assert row is not None
    assert row[0] == 1


def test_process_chunk_skips_unknown_token(seeded_market: None) -> None:
    # Token id not in our markets table — trade should be dropped.
    log = _make_log(
        maker=MAKER,
        taker=TAKER,
        maker_asset_id=0,
        taker_asset_id=999_999_999,  # unknown
        maker_amount=1_000_000,
        taker_amount=2_000_000,
        tx_hash="0x" + "dd" * 32,
        log_index=0,
        block_number=3_000_000,
    )
    with connect() as conn, _build_rpc_mock([log], {3_000_000: 1}) as client:
        index = load_token_index(conn)
        seen, written = process_chunk(3_000_000, 3_000_000, index, conn, client)

    assert seen == 1
    assert written == 0


def test_resume_block_advances_after_write(seeded_market: None) -> None:
    log = _make_log(
        maker=MAKER,
        taker=TAKER,
        maker_asset_id=0,
        taker_asset_id=int(NO_TOKEN),
        maker_amount=1_000_000,
        taker_amount=1_000_000,
        tx_hash="0x" + "ee" * 32,
        log_index=0,
        block_number=4_000_000,
    )
    with connect() as conn, _build_rpc_mock([log], {4_000_000: 1}) as client:
        index = load_token_index(conn)
        process_chunk(4_000_000, 4_000_000, index, conn, client)

    with connect() as conn:
        assert get_resume_block(conn, DEFAULT_START_BLOCK) == 4_000_001
