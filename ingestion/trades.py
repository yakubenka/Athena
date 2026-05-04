"""Backfill Polymarket trades from on-chain ``OrderFilled`` events.

Pipeline::

    eth_getLogs chunks -> decode OrderFilled -> map token_id to (condition_id,
    outcome) -> fetch block timestamps -> batched upsert into trades

Resumable: each run starts at ``MAX(block_number) + 1`` in trades (or at
the configured ``--from-block``), so interrupting with Ctrl-C is safe.

CLI::

    uv run python -m ingestion.trades --from-block 28000000 --chunk-size 2000
    uv run python -m ingestion.trades --max-blocks 50000  # test run
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import psycopg

from ingestion.db import connect
from ingestion.orderfilled import (
    CTF_EXCHANGE_ADDRESS,
    ORDER_FILLED_TOPIC0,
    DecodedTrade,
    decode_order_filled_log,
)
from ingestion.polygon_client import (
    REQUEST_TIMEOUT_SECONDS,
    get_latest_block,
    get_logs,
    rpc_call,
)

# Polymarket CTFExchange was deployed around block 28_000_000 on Polygon.
# Used as the default --from-block when resuming from scratch.
DEFAULT_START_BLOCK = 28_000_000
DEFAULT_CHUNK_SIZE = 2_000
DEFAULT_DB_BATCH_SIZE = 500
DEFAULT_SLEEP_BETWEEN_CHUNKS_SEC = 0.05  # polite pause to stay under RPC rate limits

# Watch-mode tunables. Polygon averages ~2.1s/block, so a 30-second poll
# checks ~14 fresh blocks per cycle. We trail the head by a few
# confirmations to avoid pulling logs from a block that gets reorged.
DEFAULT_WATCH_POLL_SEC = 30.0
DEFAULT_WATCH_CONFIRMATIONS = 3

# Polygon averages ~2.1 seconds per block historically. Using this to
# approximate block timestamps lets us skip per-block eth_getBlockByNumber
# calls entirely — critical for free-tier RPC providers that rate-limit
# hundreds of calls per second. Accuracy is within minutes over multi-year
# spans; plenty for monthly PnL buckets and wash-cycle detection.
POLYGON_AVG_BLOCK_TIME_SEC = 2.1


def make_block_timestamp_estimator(
    http_client: httpx.Client,
    avg_block_time_sec: float = POLYGON_AVG_BLOCK_TIME_SEC,
) -> Callable[[int], datetime]:
    """One RPC call to calibrate, then pure arithmetic for every subsequent block."""
    head = rpc_call("eth_getBlockByNumber", ["latest", False], client=http_client)
    head_number = int(head["number"], 16)
    head_ts = int(head["timestamp"], 16)

    def estimate(block_number: int) -> datetime:
        delta_blocks = head_number - block_number
        unix_ts = head_ts - round(delta_blocks * avg_block_time_sec)
        return datetime.fromtimestamp(unix_ts, tz=UTC)

    return estimate


@dataclass(frozen=True)
class BackfillProgress:
    chunks: int
    logs_seen: int
    trades_written: int
    last_block: int


UPSERT_SQL = """
INSERT INTO trades (
    tx_hash,
    log_index,
    maker_address,
    taker_address,
    condition_id,
    outcome,
    taker_side,
    size,
    price,
    usdc_amount,
    timestamp,
    block_number
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (tx_hash, log_index) DO NOTHING;
"""


def load_token_index(conn: psycopg.Connection) -> dict[str, tuple[str, str]]:
    """Load ``token_id -> (condition_id, outcome)`` from the markets table."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT condition_id, yes_token_id, no_token_id FROM markets "
            "WHERE yes_token_id IS NOT NULL OR no_token_id IS NOT NULL"
        )
        rows = cur.fetchall()

    index: dict[str, tuple[str, str]] = {}
    for condition_id, yes_tid, no_tid in rows:
        if yes_tid:
            index[yes_tid] = (condition_id, "YES")
        if no_tid:
            index[no_tid] = (condition_id, "NO")
    return index


def get_resume_block(conn: psycopg.Connection, default_start: int) -> int:
    """Return the next block to process — MAX(block_number) + 1, or default."""
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(block_number) FROM trades")
        row = cur.fetchone()
    if row and row[0] is not None:
        return int(row[0]) + 1
    return default_start


def _match_row(
    trade: DecodedTrade,
    token_index: dict[str, tuple[str, str]],
    timestamp: datetime,
) -> tuple[Any, ...] | None:
    """Resolve a DecodedTrade to the trades-table row shape, or None to skip."""
    resolved = token_index.get(trade.outcome_token_id)
    if resolved is None:
        return None  # Trade on an unknown market (not ingested yet).
    condition_id, outcome = resolved
    return (
        trade.tx_hash,
        trade.log_index,
        trade.maker_address,
        trade.taker_address,
        condition_id,
        outcome,
        trade.taker_side,
        trade.size,
        trade.price,
        trade.usdc_amount,
        timestamp,
        trade.block_number,
    )


def _fetch_logs_with_adaptive_split(
    from_block: int,
    to_block: int,
    http_client: httpx.Client,
) -> list[dict[str, Any]]:
    """Call get_logs, halving the range if the RPC rejects it as too large.

    Alchemy and most providers cap getLogs at ~10k results per call; during
    busy Polymarket periods a 2k-block window can exceed that. When the RPC
    returns an error that looks size-related, we recurse on the two halves.
    """
    try:
        return get_logs(
            contract=CTF_EXCHANGE_ADDRESS,
            topic0=ORDER_FILLED_TOPIC0,
            from_block=from_block,
            to_block=to_block,
            client=http_client,
        )
    except RuntimeError as exc:
        message = str(exc).lower()
        too_many = any(
            marker in message
            for marker in (
                "10000",
                "10,000",
                "too many",
                "query returned more",
                "response size",
                "payload size",
                "range is too large",
                "http 400",
                # Truncated/corrupted JSON usually means the response was too
                # big for the provider to serialize in one go — halve and retry.
                "json decode error",
                "unterminated string",
            )
        )
        if not too_many or from_block >= to_block:
            raise
        mid = (from_block + to_block) // 2
        left = _fetch_logs_with_adaptive_split(from_block, mid, http_client)
        right = _fetch_logs_with_adaptive_split(mid + 1, to_block, http_client)
        return left + right


def process_chunk(
    from_block: int,
    to_block: int,
    token_index: dict[str, tuple[str, str]],
    conn: psycopg.Connection,
    http_client: httpx.Client,
    timestamp_fn: Callable[[int], datetime],
) -> tuple[int, int]:
    """Fetch logs in [from_block, to_block], decode, upsert. Returns (seen, written).

    ``timestamp_fn`` maps a block number to its (approximate) wall-clock time.
    """
    logs = _fetch_logs_with_adaptive_split(from_block, to_block, http_client)
    if not logs:
        return (0, 0)

    decoded: list[DecodedTrade] = []
    for log in logs:
        try:
            trade = decode_order_filled_log(log)
        except ValueError:
            # Malformed event (shouldn't happen if topic0 is correct) — skip.
            continue
        if trade is not None:
            decoded.append(trade)

    if not decoded:
        return (len(logs), 0)

    rows: list[tuple[Any, ...]] = []
    for trade in decoded:
        ts = timestamp_fn(trade.block_number)
        row = _match_row(trade, token_index, ts)
        if row is not None:
            rows.append(row)

    if not rows:
        return (len(logs), 0)

    with conn.cursor() as cur:
        for offset in range(0, len(rows), DEFAULT_DB_BATCH_SIZE):
            batch = rows[offset : offset + DEFAULT_DB_BATCH_SIZE]
            cur.executemany(UPSERT_SQL, batch)
    conn.commit()

    return (len(logs), len(rows))


def backfill(
    *,
    from_block: int | None = None,
    to_block: int | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_blocks: int | None = None,
    sleep_between_chunks: float = DEFAULT_SLEEP_BETWEEN_CHUNKS_SEC,
    progress_every: int = 10,
    watch: bool = False,
    poll_interval_sec: float = DEFAULT_WATCH_POLL_SEC,
    confirmations: int = DEFAULT_WATCH_CONFIRMATIONS,
) -> BackfillProgress:
    """Run the backfill loop. Returns a progress summary.

    With ``watch=True`` the function keeps running after the initial
    catch-up: every ``poll_interval_sec`` it asks the RPC for the new head,
    waits for ``confirmations`` blocks to bury the tip (cheap reorg
    protection), and ingests anything new. Stops on Ctrl+C.
    """
    with connect() as conn:
        token_index = load_token_index(conn)
        if not token_index:
            raise RuntimeError("No token IDs in markets — run `python -m ingestion.markets` first.")
        resume_from = (
            from_block if from_block is not None else get_resume_block(conn, DEFAULT_START_BLOCK)
        )

        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as http_client:
            end_block = to_block if to_block is not None else get_latest_block(client=http_client)
            if max_blocks is not None:
                end_block = min(end_block, resume_from + max_blocks - 1)

            timestamp_fn = make_block_timestamp_estimator(http_client)

            chunks = logs_seen = trades_written = 0
            cursor = resume_from

            print(
                f"Backfill: blocks {resume_from:,} -> {end_block:,} "
                f"({end_block - resume_from + 1:,} blocks, "
                f"chunk={chunk_size}, known tokens={len(token_index):,}, "
                f"ts-mode=approximate)"
            )

            cursor, run_chunks, run_seen, run_written = _process_range(
                cursor=cursor,
                end_block=end_block,
                start_block=resume_from,
                chunk_size=chunk_size,
                token_index=token_index,
                conn=conn,
                http_client=http_client,
                timestamp_fn=timestamp_fn,
                sleep_between_chunks=sleep_between_chunks,
                progress_every=progress_every,
            )
            chunks += run_chunks
            logs_seen += run_seen
            trades_written += run_written

            if watch:
                print(
                    f"Catch-up done at block {cursor - 1:,}. "
                    f"Entering watch mode (poll={poll_interval_sec:.0f}s, "
                    f"confirmations={confirmations}). Ctrl+C to stop."
                )
                try:
                    while True:
                        time.sleep(poll_interval_sec)
                        try:
                            head = get_latest_block(client=http_client)
                        except RuntimeError as exc:
                            print(f"  ! head fetch failed: {exc}; will retry next tick")
                            continue
                        target = head - confirmations
                        if target < cursor:
                            continue  # nothing safely buried yet
                        cursor, run_chunks, run_seen, run_written = _process_range(
                            cursor=cursor,
                            end_block=target,
                            start_block=cursor,
                            chunk_size=chunk_size,
                            token_index=token_index,
                            conn=conn,
                            http_client=http_client,
                            timestamp_fn=timestamp_fn,
                            sleep_between_chunks=sleep_between_chunks,
                            progress_every=progress_every,
                        )
                        chunks += run_chunks
                        logs_seen += run_seen
                        trades_written += run_written
                        if run_written > 0:
                            print(
                                f"  watch tick: head={head:,} cursor={cursor - 1:,} "
                                f"+{run_written} trades"
                            )
                except KeyboardInterrupt:
                    print("\nstopping watch loop on Ctrl+C")

            last_block = cursor - 1

        print(
            f"Done. chunks={chunks} logs_seen={logs_seen:,} "
            f"trades_written={trades_written:,} last_block={last_block:,}"
        )
        return BackfillProgress(chunks, logs_seen, trades_written, last_block)


def _process_range(
    *,
    cursor: int,
    end_block: int,
    start_block: int,
    chunk_size: int,
    token_index: dict[str, tuple[str, str]],
    conn: psycopg.Connection,
    http_client: httpx.Client,
    timestamp_fn: Callable[[int], datetime],
    sleep_between_chunks: float,
    progress_every: int,
) -> tuple[int, int, int, int]:
    """Walk ``[cursor, end_block]`` in chunks. Returns the new cursor + counters."""
    chunks = logs_seen = trades_written = 0
    while cursor <= end_block:
        chunk_end = min(cursor + chunk_size - 1, end_block)
        seen, written = process_chunk(
            cursor, chunk_end, token_index, conn, http_client, timestamp_fn
        )
        logs_seen += seen
        trades_written += written
        chunks += 1

        if progress_every and chunks % progress_every == 0:
            denom = max(end_block - start_block, 1)
            pct = (chunk_end - start_block) / denom * 100
            print(
                f"  block {chunk_end:,} [{pct:5.1f}%] "
                f"chunks={chunks} seen={logs_seen:,} written={trades_written:,}"
            )

        cursor = chunk_end + 1
        if sleep_between_chunks > 0:
            time.sleep(sleep_between_chunks)
    return cursor, chunks, logs_seen, trades_written


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Backfill Polymarket trades from Polygon logs")
    p.add_argument(
        "--from-block",
        type=int,
        default=None,
        help="override start block (default: resume from last ingested block)",
    )
    p.add_argument(
        "--to-block",
        type=int,
        default=None,
        help="override end block (default: current head)",
    )
    p.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"blocks per eth_getLogs call (default {DEFAULT_CHUNK_SIZE})",
    )
    p.add_argument(
        "--max-blocks",
        type=int,
        default=None,
        help="stop after this many blocks (useful for test runs)",
    )
    p.add_argument(
        "--sleep",
        type=float,
        default=DEFAULT_SLEEP_BETWEEN_CHUNKS_SEC,
        help="seconds to sleep between chunks (rate-limit politeness)",
    )
    p.add_argument(
        "--watch",
        action="store_true",
        help="after catch-up, keep polling for new blocks (Ctrl+C to stop)",
    )
    p.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_WATCH_POLL_SEC,
        help=f"seconds between watch-mode polls (default {int(DEFAULT_WATCH_POLL_SEC)})",
    )
    p.add_argument(
        "--confirmations",
        type=int,
        default=DEFAULT_WATCH_CONFIRMATIONS,
        help=f"blocks to trail head in watch mode (default {DEFAULT_WATCH_CONFIRMATIONS})",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_argparser().parse_args(argv)
    backfill(
        from_block=args.from_block,
        to_block=args.to_block,
        chunk_size=args.chunk_size,
        max_blocks=args.max_blocks,
        sleep_between_chunks=args.sleep,
        watch=args.watch,
        poll_interval_sec=args.poll_interval,
        confirmations=args.confirmations,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
