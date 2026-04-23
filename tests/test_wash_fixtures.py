"""Sanity tests for the synthetic trade generators in wash_detector.fixtures.

We trust the fixtures downstream, so check they exhibit the structural
properties the Sirolly algorithm is supposed to discriminate between.
"""

from __future__ import annotations

import pandas as pd

from wash_detector.fixtures import (
    TRADE_COLUMNS,
    combine,
    organic_trades,
    wash_cluster,
)


def test_organic_trades_schema_and_determinism() -> None:
    df1 = organic_trades(n_wallets=20, n_trades=100, seed=0)
    df2 = organic_trades(n_wallets=20, n_trades=100, seed=0)

    assert list(df1.columns) == TRADE_COLUMNS
    assert len(df1) == 100
    assert (df1["maker"] != df1["taker"]).all(), "maker and taker must differ"
    pd.testing.assert_frame_equal(df1, df2)  # deterministic


def test_organic_uses_many_counterparties() -> None:
    df = organic_trades(n_wallets=30, n_trades=300, seed=1)
    pair_counts = df.groupby(["maker", "taker"]).size().sort_values(ascending=False)
    # No single pair should dominate — heuristic for "organic"
    assert pair_counts.iloc[0] / len(df) < 0.1


def test_wash_cluster_is_tight_and_high_turnover() -> None:
    df = wash_cluster(cluster_size=5, n_round_trips=50, seed=42)

    # Round trip = 2 trades, so we expect exactly 100 rows.
    assert len(df) == 100

    # All participants are from the cluster — only 5 distinct wallets total.
    participants = set(df["maker"]) | set(df["taker"])
    assert len(participants) == 5

    # At least 90% of the market volume is concentrated in <=3 markets.
    top_markets = df["market"].value_counts().head(3).sum()
    assert top_markets / len(df) >= 0.9


def test_wash_cluster_round_trip_prices_are_nearly_flat() -> None:
    df = wash_cluster(cluster_size=4, n_round_trips=30, seed=7)
    # Each round trip = two consecutive rows (open, close). Check that
    # within each pair the price barely moves — that's the wash signature.
    opens = df.iloc[0::2].reset_index(drop=True)
    closes = df.iloc[1::2].reset_index(drop=True)
    delta = (closes["price"] - opens["price"]).abs()
    assert delta.max() <= 0.01


def test_combine_preserves_rows_and_sorts_by_time() -> None:
    a = organic_trades(n_wallets=10, n_trades=20, seed=0)
    b = wash_cluster(cluster_size=3, n_round_trips=10, seed=0)
    merged = combine(a, b)

    assert len(merged) == len(a) + len(b)
    assert merged["timestamp"].is_monotonic_increasing


def test_wallet_offsets_prevent_collision() -> None:
    organic = organic_trades(n_wallets=10, n_trades=30, wallet_offset=0, seed=0)
    wash = wash_cluster(cluster_size=4, n_round_trips=10, wallet_offset=1000, seed=0)

    organic_wallets = set(organic["maker"]) | set(organic["taker"])
    wash_wallets = set(wash["maker"]) | set(wash["taker"])
    assert organic_wallets.isdisjoint(wash_wallets)
