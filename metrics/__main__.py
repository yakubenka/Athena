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

    return 0


if __name__ == "__main__":
    sys.exit(main())
