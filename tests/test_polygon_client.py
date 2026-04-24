"""Tests for ingestion.polygon_client using httpx MockTransport."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from ingestion.polygon_client import (
    get_block_timestamps,
    get_latest_block,
    get_logs,
    rpc_call,
)

URL = "https://polygon.example/v2/key"


def _mock_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=URL)


def test_rpc_call_unwraps_result() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0xdeadbeef"})

    with _mock_client(handler) as c:
        assert rpc_call("eth_blockNumber", [], client=c, url=URL) == "0xdeadbeef"


def test_rpc_call_raises_on_error_field() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "oops"}}
        )

    with _mock_client(handler) as c, pytest.raises(RuntimeError, match="oops"):
        rpc_call("eth_blockNumber", [], client=c, url=URL)


def test_get_latest_block_decodes_hex() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x51f6124"})

    with _mock_client(handler) as c:
        assert get_latest_block(client=c, url=URL) == 0x51F6124


def test_get_logs_sends_expected_filter() -> None:
    captured: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        captured.append(json.loads(req.content))
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": []})

    with _mock_client(handler) as c:
        logs = get_logs(
            contract="0xCTF",
            topic0="0xtopic",
            from_block=10,
            to_block=20,
            client=c,
            url=URL,
        )
    assert logs == []
    assert captured[0]["method"] == "eth_getLogs"
    params = captured[0]["params"][0]
    assert params["address"] == "0xCTF"
    assert params["fromBlock"] == "0xa"
    assert params["toBlock"] == "0x14"
    assert params["topics"] == ["0xtopic"]


def test_get_logs_rejects_bad_range() -> None:
    with pytest.raises(ValueError):
        get_logs(contract="0x0", topic0="0x0", from_block=10, to_block=5, url=URL)


def test_get_block_timestamps_batches_and_flattens() -> None:
    captured: list[Any] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(req.content)
        captured.append(body)
        # Echo back one result per request.
        payload = [
            {
                "jsonrpc": "2.0",
                "id": entry["id"],
                "result": {"timestamp": hex(entry["id"] * 2)},
            }
            for entry in body
        ]
        return httpx.Response(200, json=payload)

    with _mock_client(handler) as c:
        out = get_block_timestamps([100, 200, 200, 300], client=c, url=URL)
    assert out == {100: 200, 200: 400, 300: 600}
    # Dedup should leave 3 entries in the single batch.
    assert len(captured) == 1
    assert {entry["id"] for entry in captured[0]} == {100, 200, 300}


def test_get_block_timestamps_empty_input_short_circuits() -> None:
    called = False

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=[])

    with _mock_client(handler) as c:
        out = get_block_timestamps([], client=c, url=URL)
    assert out == {}
    assert not called
