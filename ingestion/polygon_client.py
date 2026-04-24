"""Minimal JSON-RPC client for Polygon via the configured RPC URL.

Wraps the three RPC methods we actually need:
- ``eth_blockNumber`` — latest block height
- ``eth_getLogs`` — OrderFilled events in a block range
- ``eth_getBlockByNumber`` — block timestamps (batched)

Everything else is handled by :mod:`ingestion.trades`.
"""

from __future__ import annotations

from typing import Any

import httpx

from config.settings import get_settings

REQUEST_TIMEOUT_SECONDS = 30.0
MAX_BATCH_SIZE = 1000  # JSON-RPC batch cap tolerated by Alchemy and most nodes


def _resolve_url(url: str | None, client: httpx.Client | None) -> str:
    """Pick a target URL. If a client with a base_url is supplied, honour that
    and return "" so httpx resolves the relative path against the base."""
    if url is not None:
        return url
    if client is not None and str(client.base_url):
        return ""
    settings = get_settings()
    if settings.polygon_rpc_url is None:
        raise RuntimeError("POLYGON_RPC_URL is not set (see .env.example)")
    return str(settings.polygon_rpc_url)


def rpc_call(
    method: str,
    params: list[Any],
    *,
    client: httpx.Client | None = None,
    url: str | None = None,
) -> Any:
    """Single JSON-RPC call. Returns the ``result`` field, raises on error."""
    body = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    target = _resolve_url(url, client)
    owns = client is None
    active = client if client is not None else httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        resp = active.post(target, json=body)
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            raise RuntimeError(f"RPC error: {data['error']}")
        return data["result"]
    finally:
        if owns:
            active.close()


def get_latest_block(*, client: httpx.Client | None = None, url: str | None = None) -> int:
    result = rpc_call("eth_blockNumber", [], client=client, url=url)
    return int(result, 16)


def get_logs(
    *,
    contract: str,
    topic0: str,
    from_block: int,
    to_block: int,
    client: httpx.Client | None = None,
    url: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch logs from ``contract`` with the given topic0 in [from_block, to_block]."""
    if from_block < 0 or to_block < from_block:
        raise ValueError(f"bad block range [{from_block}, {to_block}]")
    result = rpc_call(
        "eth_getLogs",
        [
            {
                "address": contract,
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block),
                "topics": [topic0],
            }
        ],
        client=client,
        url=url,
    )
    return list(result or [])


def get_block_timestamps(
    block_numbers: list[int],
    *,
    client: httpx.Client | None = None,
    url: str | None = None,
) -> dict[int, int]:
    """Batched ``eth_getBlockByNumber`` — returns ``{block_number: unix_ts}``."""
    unique = sorted(set(block_numbers))
    if not unique:
        return {}

    target = _resolve_url(url, client)
    owns = client is None
    active = client if client is not None else httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    out: dict[int, int] = {}

    try:
        for chunk_start in range(0, len(unique), MAX_BATCH_SIZE):
            chunk = unique[chunk_start : chunk_start + MAX_BATCH_SIZE]
            body = [
                {
                    "jsonrpc": "2.0",
                    "method": "eth_getBlockByNumber",
                    "params": [hex(bn), False],
                    "id": bn,
                }
                for bn in chunk
            ]
            resp = active.post(target, json=body)
            resp.raise_for_status()
            entries = resp.json()
            for entry in entries:
                bn = entry["id"]
                if "error" in entry:
                    raise RuntimeError(f"block {bn} fetch error: {entry['error']}")
                block = entry["result"]
                if block is None:
                    continue
                out[int(bn)] = int(block["timestamp"], 16)
    finally:
        if owns:
            active.close()

    return out
