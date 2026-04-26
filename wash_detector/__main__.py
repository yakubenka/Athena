"""Run Sirolly wash detection over the trades table.

Loads trades from Postgres, runs the 3-stage Sirolly pipeline, and
prints a summary plus the largest clusters. Use ``--limit`` for a quick
smoke test on the most recent N trades before committing to the full run.

CLI::

    uv run python -m wash_detector --limit 500000   # smoke test
    uv run python -m wash_detector                  # full dataset
"""

from __future__ import annotations

import argparse
import sys
import time

import pandas as pd

from ingestion.db import connect
from wash_detector.sirolly import run_wash_detection

ANALYZE_TOP_DEFAULT = 5


def load_trades(limit: int | None) -> pd.DataFrame:
    """Read trades into the column shape Sirolly expects."""
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


def analyze_clusters(clusters: list[frozenset[str]], top_n: int) -> None:
    """For the top-N clusters, query the DB for behaviour stats.

    For each cluster prints: # wallets, # unique markets traded,
    total volume in USD, # internal trades (both sides in cluster) and
    the share of all cluster-touching trades that are internal. A high
    internal-trade share is the strongest behavioural signal of wash:
    organic trading touches ten thousand counterparties; a wash farm
    keeps the volume in the family.
    """
    if not clusters:
        return
    print(f"\nAnalyzing top {min(top_n, len(clusters))} clusters...")
    sql = """
        SELECT
            COUNT(*) AS total_trades,
            COUNT(DISTINCT condition_id) AS markets,
            SUM(CASE WHEN maker_address = ANY(%s) AND taker_address = ANY(%s)
                     THEN 1 ELSE 0 END) AS internal_trades,
            COALESCE(SUM(usdc_amount), 0) AS volume_usd
        FROM trades
        WHERE maker_address = ANY(%s) OR taker_address = ANY(%s)
    """
    ranked = sorted(clusters, key=len, reverse=True)[:top_n]
    with connect() as conn, conn.cursor() as cur:
        for i, cluster in enumerate(ranked, start=1):
            wallets = list(cluster)
            cur.execute(sql, (wallets, wallets, wallets, wallets))
            total, markets, internal, volume = cur.fetchone()
            internal_pct = 100.0 * (internal or 0) / max(total or 1, 1)
            print(
                f"  #{i}: {len(cluster):4} wallets | "
                f"{total:>8,} trades | "
                f"{markets:>5} markets | "
                f"${float(volume):>14,.0f} | "
                f"internal {internal_pct:5.1f}%"
            )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run Sirolly wash detection on the trades table")
    p.add_argument(
        "--limit", type=int, default=None, help="cap to most recent N trades (smoke test)"
    )
    p.add_argument(
        "--threshold", type=float, default=0.7, help="suspect score cutoff (default 0.7)"
    )
    p.add_argument(
        "--min-cluster-size", type=int, default=3, help="ignore clusters smaller than this"
    )
    p.add_argument(
        "--analyze-top",
        type=int,
        default=ANALYZE_TOP_DEFAULT,
        help=f"after detection, query DB stats for top N clusters (default {ANALYZE_TOP_DEFAULT})",
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

    print(f"Running Sirolly (threshold={args.threshold}, min_cluster={args.min_cluster_size})...")
    t2 = time.perf_counter()
    scores, clusters = run_wash_detection(
        df,
        threshold=args.threshold,
        min_cluster_size=args.min_cluster_size,
    )
    t3 = time.perf_counter()
    print(f"  done in {t3 - t2:.1f}s")

    suspect_count = sum(1 for s in scores.values() if s >= args.threshold)
    print()
    print(f"Wallets scored:    {len(scores):,}")
    print(
        f"Above threshold:   {suspect_count:,} ({100 * suspect_count / max(len(scores), 1):.1f}%)"
    )
    print(f"Clusters detected: {len(clusters)}")

    if clusters:
        clusters_sorted = sorted(clusters, key=len, reverse=True)
        print("\nTop 10 clusters by size:")
        for i, cluster in enumerate(clusters_sorted[:10], start=1):
            sample = ", ".join(sorted(cluster)[:3])
            more = f", +{len(cluster) - 3} more" if len(cluster) > 3 else ""
            print(f"  #{i}: {len(cluster):4} wallets — {sample}{more}")

        if args.analyze_top > 0:
            analyze_clusters(clusters, args.analyze_top)

    return 0


if __name__ == "__main__":
    sys.exit(main())
