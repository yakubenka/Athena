"""Minimal JSON-RPC client for Polygon via the configured RPC URL.

Wraps the three RPC methods we actually need:
- ``eth_blockNumber`` — latest block height
- ``eth_getLogs`` — OrderFilled events in a block range
- ``eth_getBlockByNumber`` — block timestamps (batched)

Everything else is handled by :mod:`ingestion.trades`.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from config.settings import get_settings

REQUEST_TIMEOUT_SECONDS = 30.0
MAX_BATCH_SIZE = 1000  # JSON-RPC batch cap tolerated by Alchemy and most nodes

# Transient server/rate-limit errors we retry with exponential backoff.
# 408 (timeout), 410 (e.g. drpc "GRPC Context cancellation") and the
# 5xx family are all transient — Alchemy / drpc push through these
# under load. Size-related 400s are handled separately by the
# adaptive-split code in ingestion.trades because they need a smaller
# range, not a retry.
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({408, 410, 429, 500, 502, 503, 504})
DEFAULT_RETRIES = 5
RETRY_BASE_DELAY_SEC = 1.0  # doubles each attempt: 1s, 2s, 4s, 8s, 16s


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
    max_retries: int = DEFAULT_RETRIES,
    retry_base_delay: float = RETRY_BASE_DELAY_SEC,
    sleep: Any = time.sleep,
) -> Any:
    """Single JSON-RPC call with retry on transient errors.

    Retries HTTP 429/500/502/503/504 and network errors with exponential
    backoff (base * 2**attempt). Size-related 400s are left for the
    caller to handle via range splitting — retrying those is pointless.
    """
    body = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
    target = _resolve_url(url, client)
    owns = client is None
    active = client if client is not None else httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        for attempt in range(max_retries + 1):
            try:
                resp = active.post(target, json=body)
            except httpx.TransportError as exc:
                # Network hiccup (connection reset, timeout, DNS blip).
                if attempt >= max_retries:
                    raise RuntimeError(f"RPC transport error on {method}: {exc}") from exc
                sleep(retry_base_delay * (2**attempt))
                continue

            if resp.status_code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                sleep(retry_base_delay * (2**attempt))
                continue

            if resp.status_code >= 400:
                # Surface body so Alchemy/drpc-specific quirks show up in logs.
                raise RuntimeError(f"RPC HTTP {resp.status_code} on {method}: {resp.text[:500]}")
            try:
                data = resp.json()
            except json.JSONDecodeError as exc:
                # Truncated / corrupted payload (e.g. drpc cutting a huge
                # response mid-stream). Treat as transient and retry.
                if attempt < max_retries:
                    sleep(retry_base_delay * (2**attempt))
                    continue
                raise RuntimeError(
                    f"RPC JSON decode error on {method} after {max_retries} retries: {exc}"
                ) from exc
            if "error" in data:
                raise RuntimeError(f"RPC error on {method}: {data['error']}")
            return data["result"]

        # Exhausted retries after only retryable statuses.
        raise RuntimeError(
            f"RPC HTTP {resp.status_code} on {method} after {max_retries} retries: "
            f"{resp.text[:500]}"
        )
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
    """Batched ``eth_getBlockByNumber`` — returns ``{block_number: unix_ts}``.

    RPC providers vary wildly in how they handle JSON-RPC batching. Some
    reject large batches with 500, others don't support batching at all.
    We start with MAX_BATCH_SIZE and halve on any error, eventually
    falling back to one-request-per-block as a last resort.
    """
    unique = sorted(set(block_numbers))
    if not unique:
        return {}

    target = _resolve_url(url, client)
    owns = client is None
    active = client if client is not None else httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    out: dict[int, int] = {}

    try:
        _fetch_timestamps_adaptive(unique, out, active, target, MAX_BATCH_SIZE)
    finally:
        if owns:
            active.close()

    return out


def _fetch_timestamps_adaptive(
    blocks: list[int],
    out: dict[int, int],
    client: httpx.Client,
    target: str,
    batch_size: int,
) -> None:
    """Fill `out` with timestamps for `blocks`, adapting batch size on error."""
    if not blocks:
        return
    if batch_size < 1:
        batch_size = 1

    for chunk_start in range(0, len(blocks), batch_size):
        chunk = blocks[chunk_start : chunk_start + batch_size]
        try:
            _fetch_one_batch(chunk, out, client, target)
        except _BatchRejectedError:
            if batch_size == 1:
                raise
            # Halve and recurse just on this failing chunk.
            _fetch_timestamps_adaptive(chunk, out, client, target, max(batch_size // 2, 1))


class _BatchRejectedError(RuntimeError):
    """Raised when the provider rejects a batch so the caller can retry smaller."""


def _fetch_one_batch(
    chunk: list[int],
    out: dict[int, int],
    client: httpx.Client,
    target: str,
) -> None:
    if len(chunk) == 1:
        bn = chunk[0]
        body: Any = {
            "jsonrpc": "2.0",
            "method": "eth_getBlockByNumber",
            "params": [hex(bn), False],
            "id": bn,
        }
    else:
        body = [
            {
                "jsonrpc": "2.0",
                "method": "eth_getBlockByNumber",
                "params": [hex(bn), False],
                "id": bn,
            }
            for bn in chunk
        ]

    # Retry transient errors a few times before giving up.
    resp = None
    last_exc: Exception | None = None
    for attempt in range(DEFAULT_RETRIES + 1):
        try:
            resp = client.post(target, json=body)
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt >= DEFAULT_RETRIES:
                raise RuntimeError(f"eth_getBlockByNumber transport error: {exc}") from exc
            time.sleep(RETRY_BASE_DELAY_SEC * (2**attempt))
            continue
        if resp.status_code in RETRYABLE_STATUS_CODES and attempt < DEFAULT_RETRIES:
            time.sleep(RETRY_BASE_DELAY_SEC * (2**attempt))
            continue
        break
    if resp is None:
        raise RuntimeError(f"eth_getBlockByNumber failed without response: {last_exc}")
    if resp.status_code >= 400:
        # Surface body for visibility; tag retryable 4xx/5xx as batch errors.
        snippet = resp.text[:300]
        if resp.status_code in (400, 413, *RETRYABLE_STATUS_CODES) and len(chunk) > 1:
            raise _BatchRejectedError(f"HTTP {resp.status_code} on batch: {snippet}")
        raise RuntimeError(f"eth_getBlockByNumber HTTP {resp.status_code}: {snippet}")

    entries = resp.json()
    if isinstance(entries, dict):
        entries = [entries]
    for entry in entries:
        bn = entry["id"]
        if "error" in entry:
            raise RuntimeError(f"block {bn} fetch error: {entry['error']}")
        block = entry["result"]
        if block is None:
            continue
        out[int(bn)] = int(block["timestamp"], 16)
