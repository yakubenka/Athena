"""Tests for Sirolly Stage 3: detect_wash_clusters."""

from __future__ import annotations

import pandas as pd
import pytest

from wash_detector.fixtures import TRADE_COLUMNS, combine, organic_trades, wash_cluster
from wash_detector.sirolly import (
    build_trade_graph,
    detect_wash_clusters,
    initialize_scores,
    iterate_scores,
)


def _run_full_pipeline(df: pd.DataFrame, n_iterations: int = 3) -> dict[str, float]:
    initial = initialize_scores(df)
    graph = build_trade_graph(df)
    return iterate_scores(initial, graph, n_iterations=n_iterations)


def test_empty_scores_returns_empty_list() -> None:
    assert detect_wash_clusters({}, pd.DataFrame(columns=TRADE_COLUMNS)) == []


def test_nobody_above_threshold_returns_empty_list() -> None:
    df = organic_trades(n_wallets=20, n_trades=80, seed=0)
    scores = dict.fromkeys(set(df["maker"]) | set(df["taker"]), 0.1)
    assert detect_wash_clusters(scores, df, threshold=0.7) == []


def test_finds_single_wash_cluster() -> None:
    organic = organic_trades(n_wallets=30, n_trades=300, seed=0)
    wash = wash_cluster(cluster_size=5, n_round_trips=80, wallet_offset=10_000, seed=1)
    df = combine(organic, wash)
    wash_wallets = set(wash["maker"]) | set(wash["taker"])

    scores = _run_full_pipeline(df)
    clusters = detect_wash_clusters(scores, df, threshold=0.7)

    assert len(clusters) == 1
    assert clusters[0] == frozenset(wash_wallets)


def test_finds_two_separate_wash_clusters() -> None:
    organic = organic_trades(n_wallets=30, n_trades=300, seed=0)
    wash_a = wash_cluster(cluster_size=5, n_round_trips=60, wallet_offset=10_000, seed=1)
    wash_b = wash_cluster(cluster_size=4, n_round_trips=50, wallet_offset=20_000, seed=2)
    df = combine(organic, wash_a, wash_b)

    scores = _run_full_pipeline(df)
    clusters = detect_wash_clusters(scores, df, threshold=0.7)

    wash_a_wallets = set(wash_a["maker"]) | set(wash_a["taker"])
    wash_b_wallets = set(wash_b["maker"]) | set(wash_b["taker"])

    assert len(clusters) == 2
    # Larger cluster first (sorted by descending size).
    assert clusters[0] == frozenset(wash_a_wallets)
    assert clusters[1] == frozenset(wash_b_wallets)


def test_pair_of_suspects_ignored_by_default_min_size() -> None:
    # Manually crafted: only 2 wallets score above threshold, with an
    # edge between them.
    df = pd.DataFrame(
        [
            {
                "maker": "0xA",
                "taker": "0xB",
                "market": "m1",
                "size": 100.0,
                "price": 0.5,
                "timestamp": pd.Timestamp("2025-01-01", tz="UTC"),
            }
        ]
    )
    scores = {"0xA": 0.95, "0xB": 0.95, "0xC": 0.2}

    assert detect_wash_clusters(scores, df, threshold=0.7, min_cluster_size=3) == []
    # With min_cluster_size=2 the pair becomes a cluster.
    clusters = detect_wash_clusters(scores, df, threshold=0.7, min_cluster_size=2)
    assert clusters == [frozenset({"0xA", "0xB"})]


def test_isolated_high_scorer_is_not_a_cluster() -> None:
    organic = organic_trades(n_wallets=20, n_trades=100, seed=0)
    scores = _run_full_pipeline(organic)
    # Force one organic wallet to an artificially high score; it has no
    # connections to other suspects, so no cluster should be reported.
    victim = next(iter(set(organic["maker"])))
    scores[victim] = 0.99

    assert detect_wash_clusters(scores, organic, threshold=0.7) == []


def test_invalid_parameters_rejected() -> None:
    df = pd.DataFrame(columns=TRADE_COLUMNS)
    with pytest.raises(ValueError):
        detect_wash_clusters({}, df, threshold=1.5)
    with pytest.raises(ValueError):
        detect_wash_clusters({}, df, min_cluster_size=1)
