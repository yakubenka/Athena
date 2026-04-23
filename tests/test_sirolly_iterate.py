"""Tests for Sirolly Stage 2: iterate_scores."""

from __future__ import annotations

import networkx as nx
import pytest

from wash_detector.fixtures import combine, organic_trades, wash_cluster
from wash_detector.sirolly import build_trade_graph, initialize_scores, iterate_scores


def _triangle() -> tuple[dict[str, float], nx.Graph]:
    g = nx.Graph()
    g.add_edge("A", "B", volume=10.0)
    g.add_edge("B", "C", volume=20.0)
    g.add_edge("A", "C", volume=30.0)
    scores = {"A": 0.8, "B": 0.2, "C": 0.5}
    return scores, g


def test_zero_iterations_returns_copy() -> None:
    scores, g = _triangle()
    out = iterate_scores(scores, g, n_iterations=0)
    assert out == scores
    assert out is not scores


def test_matches_hand_computed_triangle() -> None:
    scores, g = _triangle()
    out = iterate_scores(scores, g, n_iterations=1)
    # A = (0.2*10 + 0.5*30) / 40 = 17/40 = 0.425
    # B = (0.8*10 + 0.5*20) / 30 = 18/30 = 0.600
    # C = (0.8*30 + 0.2*20) / 50 = 28/50 = 0.560
    assert out["A"] == pytest.approx(0.425)
    assert out["B"] == pytest.approx(0.600)
    assert out["C"] == pytest.approx(0.560)


def test_isolated_node_unchanged() -> None:
    g = nx.Graph()
    g.add_edge("a", "b", volume=5.0)
    g.add_node("loner")  # added as isolated node
    scores = {"a": 0.1, "b": 0.9, "loner": 0.42}

    out = iterate_scores(scores, g, n_iterations=5)
    assert out["loner"] == 0.42


def test_wallet_in_scores_but_not_in_graph_is_preserved() -> None:
    g = nx.Graph()
    g.add_edge("a", "b", volume=5.0)
    scores = {"a": 0.3, "b": 0.7, "orphan": 0.55}

    out = iterate_scores(scores, g, n_iterations=3)
    assert out["orphan"] == 0.55


def test_homogeneous_cluster_is_a_fixed_point() -> None:
    g = nx.Graph()
    for u, v, vol in (("a", "b", 10), ("b", "c", 20), ("a", "c", 15)):
        g.add_edge(u, v, volume=vol)
    scores = dict.fromkeys(["a", "b", "c"], 0.4)

    out = iterate_scores(scores, g, n_iterations=10)
    for node in ("a", "b", "c"):
        assert out[node] == pytest.approx(0.4)


def test_determinism() -> None:
    scores, g = _triangle()
    assert iterate_scores(scores, g, n_iterations=3) == iterate_scores(scores, g, n_iterations=3)


def test_wash_cluster_stays_high_after_iteration() -> None:
    organic = organic_trades(n_wallets=30, n_trades=300, seed=0)
    wash = wash_cluster(cluster_size=5, n_round_trips=80, wallet_offset=10_000, seed=1)
    df = combine(organic, wash)

    initial = initialize_scores(df)
    graph = build_trade_graph(df)
    iterated = iterate_scores(initial, graph, n_iterations=3)

    wash_wallets = set(wash["maker"]) | set(wash["taker"])
    organic_wallets = (set(organic["maker"]) | set(organic["taker"])) - wash_wallets

    wash_mean = sum(iterated[w] for w in wash_wallets) / len(wash_wallets)
    organic_mean = sum(iterated[w] for w in organic_wallets) / len(organic_wallets)

    assert wash_mean > 0.7, f"wash mean collapsed: {wash_mean:.3f}"
    # Separation should remain comfortably positive after redistribution.
    assert wash_mean - organic_mean > 0.2, f"wash {wash_mean:.3f} vs organic {organic_mean:.3f}"


def test_negative_iterations_rejected() -> None:
    scores, g = _triangle()
    with pytest.raises(ValueError):
        iterate_scores(scores, g, n_iterations=-1)
