"""End-to-end tests for the Sirolly wash detection pipeline."""

from __future__ import annotations

import pandas as pd

from wash_detector import run_wash_detection
from wash_detector.fixtures import TRADE_COLUMNS, combine, organic_trades, wash_cluster


def test_empty_dataframe_returns_no_scores_or_clusters() -> None:
    scores, clusters = run_wash_detection(pd.DataFrame(columns=TRADE_COLUMNS))
    assert scores == {}
    assert clusters == []


def test_pure_organic_yields_no_clusters() -> None:
    df = organic_trades(n_wallets=40, n_trades=400, seed=0)
    scores, clusters = run_wash_detection(df)

    assert clusters == []
    assert set(scores) == set(df["maker"]) | set(df["taker"])
    # Organic scores should sit meaningfully below the detection threshold.
    assert max(scores.values()) < 0.7


def test_recovers_two_clusters_with_perfect_precision_and_recall() -> None:
    organic = organic_trades(n_wallets=50, n_trades=500, seed=0)
    wash_a = wash_cluster(cluster_size=5, n_round_trips=80, wallet_offset=10_000, seed=1)
    wash_b = wash_cluster(cluster_size=4, n_round_trips=60, wallet_offset=20_000, seed=2)
    df = combine(organic, wash_a, wash_b)

    wash_a_wallets = set(wash_a["maker"]) | set(wash_a["taker"])
    wash_b_wallets = set(wash_b["maker"]) | set(wash_b["taker"])
    all_wash = wash_a_wallets | wash_b_wallets

    _scores, clusters = run_wash_detection(df)

    assert len(clusters) == 2

    flagged = set().union(*clusters)
    # Precision: no organic wallet ended up in any cluster.
    assert flagged.issubset(all_wash), f"false positives: {flagged - all_wash}"
    # Recall: every wash wallet was flagged.
    assert all_wash.issubset(flagged), f"missed wash wallets: {all_wash - flagged}"
    # Clusters match the fixture partitioning, largest first.
    assert clusters[0] == frozenset(wash_a_wallets)
    assert clusters[1] == frozenset(wash_b_wallets)


def test_lowering_threshold_does_not_merge_distinct_clusters() -> None:
    organic = organic_trades(n_wallets=30, n_trades=300, seed=0)
    wash_a = wash_cluster(cluster_size=5, n_round_trips=70, wallet_offset=10_000, seed=1)
    wash_b = wash_cluster(cluster_size=4, n_round_trips=60, wallet_offset=20_000, seed=2)
    df = combine(organic, wash_a, wash_b)

    _s, clusters = run_wash_detection(df, threshold=0.5)
    # Two clusters are structurally disjoint (no shared wallets, no
    # shared markets), so they must remain separate at any threshold.
    assert len(clusters) >= 2
