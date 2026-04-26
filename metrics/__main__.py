"""Run all academic metrics over the trades table.

Loads trades from Postgres, runs the full feature pipeline (Akey,
Becker, HHI, Reichenbach consistency, Sirolly activity proxies),
and prints the top wallets along the most informative dimensions.

CLI::

    uv run python -m metrics --limit 500000   # smoke test
    uv run python -m metrics                  # full dataset
"""

from __future__ import annotations

import argparse
import sys
import time

import pandas as pd

from ingestion.db import connect
from metrics.compute import compute_wallet_metrics

UPSERT_WALLET_AGGREGATES_SQL = """
INSERT INTO wallets (
    address,
    first_seen, last_seen,
    total_volume, total_trades,
    total_maker_volume, total_taker_volume,
    total_maker_trades, total_taker_trades
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (address) DO UPDATE SET
    first_seen         = LEAST(wallets.first_seen, EXCLUDED.first_seen),
    last_seen          = GREATEST(wallets.last_seen, EXCLUDED.last_seen),
    total_volume       = EXCLUDED.total_volume,
    total_trades       = EXCLUDED.total_trades,
    total_maker_volume = EXCLUDED.total_maker_volume,
    total_taker_volume = EXCLUDED.total_taker_volume,
    total_maker_trades = EXCLUDED.total_maker_trades,
    total_taker_trades = EXCLUDED.total_taker_trades
"""

UPSERT_METRICS_SQL = """
INSERT INTO wallet_metrics (
    address,
    frac_maker_volume, frac_maker_trades, frac_extreme_price,
    frac_yes_trades, frac_no_trades, frac_yes_at_longshot, frac_no_at_longshot,
    counterparty_hhi,
    max_consecutive_profitable_months, max_consecutive_5k_plus_months,
    wash_score,
    pnl_to_volume_ratio, rapid_open_close_ratio,
    last_trade_at,
    updated_at
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
ON CONFLICT (address) DO UPDATE SET
    frac_maker_volume                 = EXCLUDED.frac_maker_volume,
    frac_maker_trades                 = EXCLUDED.frac_maker_trades,
    frac_extreme_price                = EXCLUDED.frac_extreme_price,
    frac_yes_trades                   = EXCLUDED.frac_yes_trades,
    frac_no_trades                    = EXCLUDED.frac_no_trades,
    frac_yes_at_longshot              = EXCLUDED.frac_yes_at_longshot,
    frac_no_at_longshot               = EXCLUDED.frac_no_at_longshot,
    counterparty_hhi                  = EXCLUDED.counterparty_hhi,
    max_consecutive_profitable_months = EXCLUDED.max_consecutive_profitable_months,
    max_consecutive_5k_plus_months    = EXCLUDED.max_consecutive_5k_plus_months,
    wash_score                        = EXCLUDED.wash_score,
    pnl_to_volume_ratio               = EXCLUDED.pnl_to_volume_ratio,
    rapid_open_close_ratio            = EXCLUDED.rapid_open_close_ratio,
    last_trade_at                     = EXCLUDED.last_trade_at,
    updated_at                        = NOW()
"""


def load_trades(limit: int | None) -> pd.DataFrame:
    sql = """
        SELECT
            maker_address  AS maker,
            taker_address  AS taker,
            condition_id   AS market,
            outcome,
            taker_side,
            size,
            price,
            timestamp
        FROM trades
        ORDER BY timestamp DESC
    """
    if limit is not None:
        sql += f"\n        LIMIT {int(limit)}"

    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d.name for d in cur.description]
    df = pd.DataFrame(rows, columns=cols)
    df["size"] = df["size"].astype(float)
    df["price"] = df["price"].astype(float)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def save_wallet_aggregates(df: pd.DataFrame) -> None:
    """Upsert per-wallet first/last_seen and maker/taker volume + trade counts.

    These are the wallets-table columns the schema wants populated before
    wallet_metrics rows can FK against them.
    """
    print("\nComputing wallet aggregates...")
    t0 = time.perf_counter()

    notional = (df["size"] * df["price"]).rename("notional")
    df_n = df.assign(notional=notional)

    maker_volume = df_n.groupby("maker")["notional"].sum()
    taker_volume = df_n.groupby("taker")["notional"].sum()
    maker_trades = df_n.groupby("maker").size()
    taker_trades = df_n.groupby("taker").size()

    appearances = pd.concat(
        [
            df[["maker", "timestamp"]].rename(columns={"maker": "wallet"}),
            df[["taker", "timestamp"]].rename(columns={"taker": "wallet"}),
        ],
        ignore_index=True,
    )
    seen = appearances.groupby("wallet")["timestamp"].agg(["min", "max"])

    wallets = sorted(seen.index)
    rows = []
    for w in wallets:
        mv = float(maker_volume.get(w, 0.0))
        tv = float(taker_volume.get(w, 0.0))
        mt = int(maker_trades.get(w, 0))
        tt = int(taker_trades.get(w, 0))
        rows.append(
            (
                w,
                seen.at[w, "min"],
                seen.at[w, "max"],
                mv + tv,
                mt + tt,
                mv,
                tv,
                mt,
                tt,
            )
        )
    t1 = time.perf_counter()
    print(f"  prepared {len(rows):,} wallet rows in {t1 - t0:.1f}s, upserting...")

    with connect() as conn, conn.cursor() as cur:
        cur.executemany(UPSERT_WALLET_AGGREGATES_SQL, rows)
        conn.commit()
    t2 = time.perf_counter()
    print(f"  upserted {len(rows):,} wallets in {t2 - t1:.1f}s")


def save_metrics(metrics: pd.DataFrame, df: pd.DataFrame) -> None:
    """Upsert per-wallet metric rows into wallet_metrics."""
    print("\nSaving metrics...")
    t0 = time.perf_counter()

    last_trade_maker = df.groupby("maker")["timestamp"].max()
    last_trade_taker = df.groupby("taker")["timestamp"].max()
    last_trade = pd.concat([last_trade_maker, last_trade_taker]).groupby(level=0).max()

    rows = []
    for wallet, m in metrics.iterrows():
        rows.append(
            (
                wallet,
                _opt_float(m.get("frac_maker_volume")),
                _opt_float(m.get("frac_maker_trades")),
                _opt_float(m.get("frac_extreme_price")),
                _opt_float(m.get("frac_yes_trades")),
                _opt_float(m.get("frac_no_trades")),
                _opt_float(m.get("frac_yes_at_longshot")),
                _opt_float(m.get("frac_no_at_longshot")),
                _opt_float(m.get("counterparty_hhi")),
                _opt_int(m.get("max_consecutive_profitable_months")),
                _opt_int(m.get("max_consecutive_5k_plus_months")),
                _opt_float(m.get("wash_score")),
                _opt_float(m.get("pnl_to_volume_ratio")),
                _opt_float(m.get("rapid_open_close_ratio")),
                last_trade.get(wallet),
            )
        )
    t1 = time.perf_counter()
    print(f"  prepared {len(rows):,} metric rows in {t1 - t0:.1f}s, upserting...")

    with connect() as conn, conn.cursor() as cur:
        cur.executemany(UPSERT_METRICS_SQL, rows)
        conn.commit()
    t2 = time.perf_counter()
    print(f"  upserted {len(rows):,} metric rows in {t2 - t1:.1f}s")


def _opt_float(v: object) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


def _opt_int(v: object) -> int | None:
    f = _opt_float(v)
    return None if f is None else int(f)


def print_top(metrics: pd.DataFrame, column: str, n: int, ascending: bool = False) -> None:
    if column not in metrics.columns:
        return
    sub = metrics[metrics[column].notna()].sort_values(column, ascending=ascending).head(n)
    if sub.empty:
        return
    print(f"\nTop {n} by {column} ({'asc' if ascending else 'desc'}):")
    for wallet, row in sub.iterrows():
        flag = " 🚨wash" if bool(row.get("is_suspected_wash", False)) else ""
        print(f"  {wallet}  {column}={row[column]}{flag}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Compute Athena wallet metrics from trades")
    p.add_argument(
        "--limit", type=int, default=None, help="cap to most recent N trades (smoke test)"
    )
    p.add_argument("--top", type=int, default=10, help="rows to print per leaderboard")
    p.add_argument(
        "--save",
        action="store_true",
        help="upsert wallet aggregates and wallet_metrics rows into Postgres",
    )
    args = p.parse_args(argv)

    print(f"Loading trades (limit={args.limit})...")
    t0 = time.perf_counter()
    df = load_trades(args.limit)
    t1 = time.perf_counter()
    mem_mb = df.memory_usage(deep=True).sum() / 1e6
    print(
        f"  loaded {len(df):,} trades in {t1 - t0:.1f}s, ~{mem_mb:.0f} MB, "
        f"window {df['timestamp'].min()} -> {df['timestamp'].max()}"
    )

    print("Computing metrics...")
    t2 = time.perf_counter()
    metrics = compute_wallet_metrics(df)
    t3 = time.perf_counter()
    print(f"  done in {t3 - t2:.1f}s, {len(metrics):,} wallets scored")

    clean = metrics[~metrics["is_suspected_wash"].fillna(False)]
    print(
        f"\nNon-wash wallets: {len(clean):,} (filtered out {len(metrics) - len(clean):,} suspect)"
    )

    print_top(clean, "max_consecutive_5k_plus_months", args.top)
    print_top(clean, "max_consecutive_profitable_months", args.top)
    print_top(clean, "frac_maker_volume", args.top, ascending=True)
    print_top(clean, "counterparty_hhi", args.top, ascending=True)

    if args.save:
        save_wallet_aggregates(df)
        save_metrics(metrics, df)

    return 0


if __name__ == "__main__":
    sys.exit(main())
