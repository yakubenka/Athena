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


def test_rpc_call_retries_on_transient_status_then_succeeds() -> None:
    # First two calls 502, third succeeds. Should return the result.
    responses = iter([502, 502, 200])

    def handler(req: httpx.Request) -> httpx.Response:
        status = next(responses)
        if status == 200:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": "0x7"})
        return httpx.Response(status, text="bad gateway")

    with _mock_client(handler) as c:
        # max_retries=2 is exactly enough for 2 retries + 1 original attempt.
        out = rpc_call(
            "eth_blockNumber",
            [],
            client=c,
            url=URL,
            max_retries=2,
            retry_base_delay=0.0,  # no actual waiting in tests
            sleep=lambda _s: None,
        )
    assert out == "0x7"


def test_rpc_call_raises_after_max_retries_on_persistent_5xx() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="still down")

    with _mock_client(handler) as c, pytest.raises(RuntimeError, match="503"):
        rpc_call(
            "eth_blockNumber",
            [],
            client=c,
            url=URL,
            max_retries=2,
            retry_base_delay=0.0,
            sleep=lambda _s: None,
        )


def test_rpc_call_does_not_retry_non_retryable_4xx() -> None:
    attempts = [0]

    def handler(req: httpx.Request) -> httpx.Response:
        attempts[0] += 1
        return httpx.Response(401, text="unauthorized")

    with _mock_client(handler) as c, pytest.raises(RuntimeError, match="401"):
        rpc_call(
            "eth_blockNumber",
            [],
            client=c,
            url=URL,
            max_retries=3,
            retry_base_delay=0.0,
            sleep=lambda _s: None,
        )
    assert attempts[0] == 1  # first attempt was final


def test_get_block_timestamps_batches_and_flattens() -> None:
    captured: list[Any] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(req.content)
        captured.append(body)
        # Echo back one result per request.
        if isinstance(body, list):
            payload: Any = [
                {
                    "jsonrpc": "2.0",
                    "id": entry["id"],
                    "result": {"timestamp": hex(entry["id"] * 2)},
                }
                for entry in body
            ]
        else:
            bn = int(body["params"][0], 16)
            payload = {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {"timestamp": hex(bn * 2)},
            }
        return httpx.Response(200, json=payload)

    with _mock_client(handler) as c:
        out = get_block_timestamps([100, 200, 200, 300], client=c, url=URL)
    assert out == {100: 200, 200: 400, 300: 600}
    # Dedup should leave 3 entries in the single batch.
    assert len(captured) == 1
    batch = captured[0]
    assert isinstance(batch, list)
    assert {entry["id"] for entry in batch} == {100, 200, 300}


def test_get_block_timestamps_halves_batch_on_server_error() -> None:
    """Some providers 500 on large batches — code should split and retry."""
    import json

    # Reject any batch of >= 2; accept single requests.
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        if isinstance(body, list) and len(body) >= 2:
            return httpx.Response(500, text="batch too large")
        if isinstance(body, list):  # batch of 1
            entry = body[0]
            bn = int(entry["params"][0], 16)
            return httpx.Response(
                200,
                json=[{"jsonrpc": "2.0", "id": entry["id"], "result": {"timestamp": hex(bn * 3)}}],
            )
        bn = int(body["params"][0], 16)
        return httpx.Response(
            200,
            json={"jsonrpc": "2.0", "id": body["id"], "result": {"timestamp": hex(bn * 3)}},
        )

    with _mock_client(handler) as c:
        out = get_block_timestamps([10, 20, 30], client=c, url=URL)
    assert out == {10: 30, 20: 60, 30: 90}


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
