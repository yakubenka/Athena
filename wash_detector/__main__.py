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
import hashlib
import sys
import time

import pandas as pd

from ingestion.db import connect
from wash_detector.sirolly import run_wash_detection

ANALYZE_TOP_DEFAULT = 5
CLUSTER_ID_PREFIX = "WASH-"


def _cluster_id(cluster: frozenset[str]) -> str:
    """Deterministic ID for a cluster — same wallet set always maps to the same id."""
    digest = hashlib.sha1(",".join(sorted(cluster)).encode()).hexdigest()
    return f"{CLUSTER_ID_PREFIX}{digest[:12]}"


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


def save_clusters(
    df: pd.DataFrame,
    clusters: list[frozenset[str]],
    scores: dict[str, float],
) -> None:
    """Persist clusters + memberships + suspect-wallet flags to Postgres.

    Idempotent: re-running produces the same cluster_ids (deterministic hash
    of sorted wallet list) and ON CONFLICT DO UPDATE refreshes the rows.

    For each cluster we store: total notional volume across cluster trades,
    aggregate proxy PnL (maker_flow - taker_flow across all cluster-touching
    trades — captures net cash-out vs the outside world), and confidence_score
    (mean Sirolly score of members). For each wallet we set is_suspected_wash,
    wash_score, wash_cluster_id, and seed first/last_seen from the trades window.
    """
    if not clusters:
        return
    print(f"\nSaving {len(clusters)} clusters to DB...")
    t0 = time.perf_counter()

    notional_all = df["size"] * df["price"]
    df_with_notional = df.assign(_notional=notional_all)

    # Precompute per-wallet aggregates ONCE — eliminates the per-wallet
    # full-DataFrame scan that previously made this O(wallets * trades).
    maker_flow_by_wallet = df_with_notional.groupby("maker")["_notional"].sum()
    taker_flow_by_wallet = df_with_notional.groupby("taker")["_notional"].sum()
    wallet_volume = maker_flow_by_wallet.add(taker_flow_by_wallet, fill_value=0.0)

    all_wash = {w for c in clusters for w in c}
    maker_appear = df.loc[df["maker"].isin(all_wash), ["maker", "timestamp"]].rename(
        columns={"maker": "wallet"}
    )
    taker_appear = df.loc[df["taker"].isin(all_wash), ["taker", "timestamp"]].rename(
        columns={"taker": "wallet"}
    )
    seen = (
        pd.concat([maker_appear, taker_appear], ignore_index=True)
        .groupby("wallet")["timestamp"]
        .agg(["min", "max"])
    )

    insert_cluster_sql = """
        INSERT INTO wash_clusters
            (cluster_id, num_wallets, total_volume, aggregate_pnl,
             first_detected, last_updated, confidence_score)
        VALUES (%s, %s, %s, %s, NOW(), NOW(), %s)
        ON CONFLICT (cluster_id) DO UPDATE SET
            num_wallets      = EXCLUDED.num_wallets,
            total_volume     = EXCLUDED.total_volume,
            aggregate_pnl    = EXCLUDED.aggregate_pnl,
            last_updated     = NOW(),
            confidence_score = EXCLUDED.confidence_score
    """
    upsert_wallet_sql = """
        INSERT INTO wallets
            (address, first_seen, last_seen,
             wash_score, wash_cluster_id, is_suspected_wash)
        VALUES (%s, %s, %s, %s, %s, TRUE)
        ON CONFLICT (address) DO UPDATE SET
            first_seen        = LEAST(wallets.first_seen, EXCLUDED.first_seen),
            last_seen         = GREATEST(wallets.last_seen, EXCLUDED.last_seen),
            wash_score        = EXCLUDED.wash_score,
            wash_cluster_id   = EXCLUDED.wash_cluster_id,
            is_suspected_wash = TRUE
    """
    insert_member_sql = """
        INSERT INTO wash_cluster_membership (cluster_id, wallet, volume_in_cluster)
        VALUES (%s, %s, %s)
        ON CONFLICT (cluster_id, wallet) DO UPDATE SET
            volume_in_cluster = EXCLUDED.volume_in_cluster
    """

    with connect() as conn, conn.cursor() as cur:
        for cluster in clusters:
            cid = _cluster_id(cluster)
            members = sorted(cluster)

            maker_flow = float(maker_flow_by_wallet.reindex(members, fill_value=0.0).sum())
            taker_flow = float(taker_flow_by_wallet.reindex(members, fill_value=0.0).sum())
            # Cluster's gross notional touch — double-counts trades that are
            # internal to the cluster, but for sizing purposes that's fine.
            total_volume = maker_flow + taker_flow
            agg_pnl = maker_flow - taker_flow
            avg_score = sum(scores.get(w, 0.0) for w in members) / len(members)

            cur.execute(
                insert_cluster_sql,
                (cid, len(members), total_volume, agg_pnl, avg_score),
            )

            wallet_rows = [
                (
                    w,
                    seen.at[w, "min"],
                    seen.at[w, "max"],
                    scores.get(w, 0.0),
                    cid,
                )
                for w in members
            ]
            member_rows = [(cid, w, float(wallet_volume.get(w, 0.0))) for w in members]
            cur.executemany(upsert_wallet_sql, wallet_rows)
            cur.executemany(insert_member_sql, member_rows)
        conn.commit()

    t1 = time.perf_counter()
    print(f"  saved {len(clusters)} clusters / {len(all_wash):,} wallets in {t1 - t0:.1f}s")


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
    p.add_argument(
        "--save",
        action="store_true",
        help="persist clusters + memberships + wallet flags to Postgres",
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

        if args.save:
            save_clusters(df, clusters, scores)

    return 0


if __name__ == "__main__":
    sys.exit(main())
