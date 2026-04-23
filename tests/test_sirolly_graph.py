"""Tests for Sirolly helper: build_trade_graph."""

from __future__ import annotations

import networkx as nx
import pandas as pd
import pytest

from wash_detector.fixtures import TRADE_COLUMNS, combine, organic_trades, wash_cluster
from wash_detector.sirolly import build_trade_graph


def test_empty_trades_returns_empty_graph() -> None:
    g = build_trade_graph(pd.DataFrame(columns=TRADE_COLUMNS))
    assert g.number_of_nodes() == 0
    assert g.number_of_edges() == 0


def test_all_participants_appear_as_nodes() -> None:
    df = organic_trades(n_wallets=10, n_trades=40, seed=0)
    g = build_trade_graph(df)
    participants = set(df["maker"]) | set(df["taker"])
    assert set(g.nodes) == participants


def test_graph_is_undirected() -> None:
    df = organic_trades(n_wallets=5, n_trades=10, seed=0)
    assert not build_trade_graph(df).is_directed()


def test_edge_weight_equals_sum_of_notional() -> None:
    df = organic_trades(n_wallets=6, n_trades=30, seed=1)
    g = build_trade_graph(df)

    for a, b in g.edges:
        pair_mask = ((df["maker"] == a) & (df["taker"] == b)) | (
            (df["maker"] == b) & (df["taker"] == a)
        )
        expected = (df.loc[pair_mask, "size"] * df.loc[pair_mask, "price"]).sum()
        assert g[a][b]["volume"] == pytest.approx(expected)


def test_aggregates_both_directions_into_one_edge() -> None:
    df = pd.DataFrame(
        [
            {
                "maker": "0xA",
                "taker": "0xB",
                "market": "m1",
                "size": 10.0,
                "price": 0.5,
                "timestamp": pd.Timestamp("2025-01-01", tz="UTC"),
            },
            {
                "maker": "0xB",
                "taker": "0xA",
                "market": "m1",
                "size": 10.0,
                "price": 0.5,
                "timestamp": pd.Timestamp("2025-01-02", tz="UTC"),
            },
        ]
    )
    g = build_trade_graph(df)
    assert g.number_of_edges() == 1
    assert g["0xA"]["0xB"]["volume"] == pytest.approx(10.0)


def test_restrict_to_keeps_only_inner_edges() -> None:
    organic = organic_trades(n_wallets=15, n_trades=60, seed=0)
    wash = wash_cluster(cluster_size=5, n_round_trips=30, wallet_offset=10_000, seed=1)
    df = combine(organic, wash)
    wash_wallets = set(wash["maker"]) | set(wash["taker"])

    g = build_trade_graph(df, restrict_to=wash_wallets)

    assert set(g.nodes).issubset(wash_wallets)
    # 30 round trips across a 5-wallet set are virtually guaranteed to
    # connect them into a single component.
    components = list(nx.connected_components(g))
    assert len(components) == 1
    assert len(components[0]) == 5


def test_self_loops_are_dropped() -> None:
    df = pd.DataFrame(
        [
            {
                "maker": "0xA",
                "taker": "0xA",
                "market": "m1",
                "size": 5.0,
                "price": 0.5,
                "timestamp": pd.Timestamp("2025-01-01", tz="UTC"),
            },
            {
                "maker": "0xA",
                "taker": "0xB",
                "market": "m1",
                "size": 5.0,
                "price": 0.5,
                "timestamp": pd.Timestamp("2025-01-02", tz="UTC"),
            },
        ]
    )
    g = build_trade_graph(df)
    assert not any(a == b for a, b in g.edges)
    assert g.number_of_edges() == 1


def test_determinism() -> None:
    df = organic_trades(n_wallets=8, n_trades=25, seed=2)
    e1 = sorted((a, b, d["volume"]) for a, b, d in build_trade_graph(df).edges(data=True))
    e2 = sorted((a, b, d["volume"]) for a, b, d in build_trade_graph(df).edges(data=True))
    assert e1 == e2
