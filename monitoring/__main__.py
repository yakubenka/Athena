"""CLI entry point — see package docstring."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime, timedelta

from ingestion.db import connect

DEFAULT_LOOKBACK = timedelta(hours=1)

FETCH_NEW_SIGNALS_SQL = """
SELECT
    t.tx_hash,
    t.log_index,
    t.timestamp                                  AS trade_timestamp,
    w.address                                    AS wallet,
    w.tier                                       AS wallet_tier,
    t.condition_id,
    t.outcome,
    t.taker_side,
    CASE WHEN t.taker_address = w.address THEN 'taker' ELSE 'maker' END AS role,
    t.price,
    t.size,
    m.question
FROM trades t
JOIN watchlist w
  ON (w.address = t.maker_address OR w.address = t.taker_address)
LEFT JOIN markets m USING (condition_id)
LEFT JOIN signals s
  ON s.wallet = w.address
 AND s.condition_id = t.condition_id
 AND s.trade_timestamp = t.timestamp
WHERE w.copy_enabled = TRUE
  AND t.timestamp >= %s
  AND s.id IS NULL                               -- not already emitted
ORDER BY t.timestamp ASC
"""

INSERT_SIGNAL_SQL = """
INSERT INTO signals (
    wallet, wallet_tier, condition_id,
    outcome, taker_side, role,
    price, size,
    detected_at, trade_timestamp,
    latency_seconds, signal_strength
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s)
"""


def parse_since(since: str | None) -> datetime:
    """Resolve the --since argument to a UTC datetime.

    Accepts an absolute ISO timestamp, a duration suffix (``1h``, ``30m``,
    ``2d``), or ``"auto"`` (= continue from the last detected_at in
    signals; falls back to DEFAULT_LOOKBACK if signals is empty).
    """
    now = datetime.now(UTC)
    if since is None or since == "auto":
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT MAX(detected_at) FROM signals")
            row = cur.fetchone()
        last = row[0] if row and row[0] is not None else None
        return last if last is not None else now - DEFAULT_LOOKBACK

    m = re.fullmatch(r"(\d+)([hmd])", since)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = {"h": timedelta(hours=n), "m": timedelta(minutes=n), "d": timedelta(days=n)}[unit]
        return now - delta

    return datetime.fromisoformat(since)


def format_signal(row: dict[str, object]) -> str:
    """Pretty single-line signal for stdout / Telegram."""
    side = row["taker_side"] if row["role"] == "taker" else _flip(row["taker_side"])
    short = str(row["wallet"])[:10]
    question = (row["question"] or str(row["condition_id"]))[:60]
    return (
        f"[{row['trade_timestamp']}] tier={row['wallet_tier']} {short}  "
        f"{side} {row['outcome']} @{float(row['price']):.4f} "
        f"size={float(row['size']):.1f}  "
        f"market={question}"
    )


def _flip(side: object) -> str:
    return "SELL" if side == "BUY" else "BUY"


def _signal_strength(price: float) -> str:
    """Crude rule-of-thumb: extreme prices (longshot fade) are strongest."""
    if price <= 0.20 or price >= 0.80:
        return "strong"
    if price <= 0.35 or price >= 0.65:
        return "medium"
    return "weak"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Emit signals from copy-enabled watchlist trades")
    p.add_argument(
        "--since",
        default="auto",
        help="duration (1h, 30m, 2d), ISO timestamp, or 'auto' (default)",
    )
    p.add_argument("--dry-run", action="store_true", help="don't insert into signals table")
    p.add_argument("--limit", type=int, default=None, help="cap output rows")
    args = p.parse_args(argv)

    since = parse_since(args.since)
    print(f"Looking for new watchlist trades since {since.isoformat()}")

    with connect() as conn, conn.cursor() as cur:
        cur.execute(FETCH_NEW_SIGNALS_SQL, (since,))
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

        if args.limit is not None:
            rows = rows[: args.limit]

        if not rows:
            print("  no new signals.")
            return 0

        for row in rows:
            print(format_signal(row))

        if args.dry_run:
            print(f"\n[dry-run] would insert {len(rows)} signals.")
            return 0

        for row in rows:
            side = row["taker_side"] if row["role"] == "taker" else _flip(row["taker_side"])
            latency = int((datetime.now(UTC) - row["trade_timestamp"]).total_seconds())
            cur.execute(
                INSERT_SIGNAL_SQL,
                (
                    row["wallet"],
                    row["wallet_tier"],
                    row["condition_id"],
                    row["outcome"],
                    side,
                    row["role"],
                    row["price"],
                    row["size"],
                    row["trade_timestamp"],
                    latency,
                    _signal_strength(float(row["price"])),
                ),
            )
        conn.commit()

    print(f"\ninserted {len(rows)} signals.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
