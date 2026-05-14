"""CLI entry point — see package docstring."""

from __future__ import annotations

import argparse
import re
import sys
from datetime import UTC, datetime, timedelta

from alerts import send_signal
from ingestion.db import connect
from prometheus_bridge import push_smart_money

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
    latency_seconds, signal_strength,
    signal_type, source
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, 'athena')
"""

# Map a watchlist trade direction to our copy semantics. When a watched
# wallet BUYs the outcome they're entering — Prometheus should open a
# matching position. When they SELL, that's an exit signal — Prometheus
# should close any open copy on the same condition_id from this source.
SIGNAL_TYPE_BY_SIDE = {"BUY": "entry", "SELL": "exit"}


def _signal_type(side: str) -> str:
    return SIGNAL_TYPE_BY_SIDE.get(side, "entry")


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


WATCHLIST_PROFILES_SQL = """
SELECT
    w.address,
    w.tier,
    wal.total_volume,
    wal.total_trades,
    wm.frac_maker_volume,
    wm.counterparty_hhi,
    wm.max_consecutive_5k_plus_months
FROM watchlist w
JOIN wallets wal ON wal.address = w.address
LEFT JOIN wallet_metrics wm ON wm.address = w.address
WHERE w.copy_enabled = TRUE
ORDER BY w.address
"""


def build_prometheus_payload(
    new_signals: list[dict[str, object]],
    profiles: list[dict[str, object]],
) -> dict[str, object]:
    """Shape the payload Prometheus's /internal/push expects.

    ``traders`` is the canonical key (Prometheus's /api/smart_money default
    response uses it). Each trader carries the lifetime profile plus any
    fresh signal we just emitted for them in this monitor tick — so
    Prometheus's strategy can act on the same delta we're acting on.
    """
    by_wallet: dict[str, list[dict[str, object]]] = {}
    for s in new_signals:
        side = s["taker_side"] if s["role"] == "taker" else _flip(s["taker_side"])
        ts = s["trade_timestamp"]
        by_wallet.setdefault(str(s["wallet"]), []).append(
            {
                "condition_id": s["condition_id"],
                "market_question": s.get("question") or s["condition_id"],
                "outcome": s["outcome"],
                "side": side,
                "signal_type": _signal_type(side),  # entry on BUY, exit on SELL
                "price": float(s["price"]),
                "size": float(s["size"]),
                "trade_timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "signal_strength": _signal_strength(float(s["price"])),
            }
        )

    traders = []
    for prof in profiles:
        addr = str(prof["address"])
        traders.append(
            {
                "address": addr,
                "tier": prof.get("tier"),
                "total_volume_usd": float(prof["total_volume"] or 0),
                "total_trades": int(prof["total_trades"] or 0),
                "frac_maker_volume": (
                    float(prof["frac_maker_volume"])
                    if prof.get("frac_maker_volume") is not None
                    else None
                ),
                "counterparty_hhi": (
                    float(prof["counterparty_hhi"])
                    if prof.get("counterparty_hhi") is not None
                    else None
                ),
                "max_consecutive_5k_plus_months": int(
                    prof.get("max_consecutive_5k_plus_months") or 0
                ),
                "active_signals": by_wallet.get(addr, []),
            }
        )

    return {
        "updated_at": datetime.now(UTC).isoformat(),
        "source": "athena",  # Prometheus filters / dashboards by this tag
        "traders": traders,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Emit signals from copy-enabled watchlist trades")
    p.add_argument(
        "--since",
        default="auto",
        help="duration (1h, 30m, 2d), ISO timestamp, or 'auto' (default)",
    )
    p.add_argument("--dry-run", action="store_true", help="don't insert into signals table")
    p.add_argument("--limit", type=int, default=None, help="cap output rows")
    p.add_argument(
        "--telegram",
        action="store_true",
        help="also push each emitted signal to Telegram (requires TELEGRAM_* in .env)",
    )
    p.add_argument(
        "--push-prometheus",
        action="store_true",
        help="also POST the watchlist + new signals to Prometheus /internal/push",
    )
    p.add_argument(
        "--min-usd",
        type=float,
        default=20.0,
        help=(
            "drop signals where notional (size * price) is below this many USDC. "
            "Default 20 — tiny dust trades from watchlist wallets are noise and "
            "Prometheus rejects them at the order-book level anyway."
        ),
    )
    args = p.parse_args(argv)

    since = parse_since(args.since)
    print(f"Looking for new watchlist trades since {since.isoformat()}")

    with connect() as conn, conn.cursor() as cur:
        cur.execute(FETCH_NEW_SIGNALS_SQL, (since,))
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]

        if args.min_usd > 0:
            before = len(rows)
            rows = [r for r in rows if float(r["size"]) * float(r["price"]) >= args.min_usd]
            if before > len(rows):
                print(f"  filtered out {before - len(rows)} dust signals (< ${args.min_usd:.0f})")

        if args.limit is not None:
            rows = rows[: args.limit]

        if not rows:
            print("  no new signals.")
            if args.push_prometheus:
                # Even with no fresh deltas, refresh Prometheus's view of the
                # watchlist profile so it always has the latest tier-S roster
                # to consume on its next scan tick.
                cur.execute(WATCHLIST_PROFILES_SQL)
                pcols = [d.name for d in cur.description]
                profiles = [dict(zip(pcols, r, strict=True)) for r in cur.fetchall()]
                payload = build_prometheus_payload([], profiles)
                ok = push_smart_money(payload)
                if ok:
                    print(
                        f"  pushed watchlist snapshot ({len(profiles)} traders) "
                        "with no new signals."
                    )
                else:
                    print("  Prometheus credentials missing — skipped push.")
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
                    _signal_type(side),
                ),
            )
            if args.telegram:
                send_signal(
                    wallet=str(row["wallet"]),
                    wallet_tier=str(row["wallet_tier"]) if row["wallet_tier"] else None,
                    market_question=str(row["question"] or row["condition_id"]),
                    outcome=str(row["outcome"]),
                    side=str(side),
                    price=float(row["price"]),
                    size=float(row["size"]),
                    trade_timestamp=row["trade_timestamp"],
                )
        conn.commit()

        if args.push_prometheus:
            cur.execute(WATCHLIST_PROFILES_SQL)
            pcols = [d.name for d in cur.description]
            profiles = [dict(zip(pcols, r, strict=True)) for r in cur.fetchall()]
            payload = build_prometheus_payload(rows, profiles)
            ok = push_smart_money(payload)
            if ok:
                print(f"  pushed {len(rows)} signals to Prometheus.")
            else:
                print("  Prometheus credentials missing — skipped push.")

    print(f"\ninserted {len(rows)} signals.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
