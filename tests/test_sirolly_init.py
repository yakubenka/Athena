"""Tests for Sirolly Stage 1: initialize_scores."""

from __future__ import annotations

import pandas as pd

from wash_detector.fixtures import TRADE_COLUMNS, combine, organic_trades, wash_cluster
from wash_detector.sirolly import initialize_scores


def test_empty_dataframe_returns_empty_dict() -> None:
    df = pd.DataFrame(columns=TRADE_COLUMNS)
    assert initialize_scores(df) == {}


def test_score_bounds_are_respected() -> None:
    df = organic_trades(n_wallets=20, n_trades=100, seed=0)
    scores = initialize_scores(df)
    assert scores, "organic dataset should produce some scores"
    for wallet, s in scores.items():
        assert 0.0 <= s <= 1.0, f"{wallet} out of bounds: {s}"


def test_determinism() -> None:
    df = organic_trades(n_wallets=15, n_trades=80, seed=3)
    assert initialize_scores(df) == initialize_scores(df)


def test_wash_cluster_scores_above_organic() -> None:
    organic = organic_trades(n_wallets=30, n_trades=300, seed=0)
    wash = wash_cluster(cluster_size=5, n_round_trips=80, wallet_offset=10_000, seed=1)
    df = combine(organic, wash)

    scores = initialize_scores(df)
    wash_wallets = set(wash["maker"]) | set(wash["taker"])
    organic_wallets = (set(organic["maker"]) | set(organic["taker"])) - wash_wallets

    mean_wash = sum(scores[w] for w in wash_wallets) / len(wash_wallets)
    mean_organic = sum(scores[w] for w in organic_wallets) / len(organic_wallets)

    assert mean_wash > mean_organic + 0.2, (
        f"wash mean {mean_wash:.3f} must clearly exceed organic {mean_organic:.3f}"
    )


def test_wash_wallets_score_high_absolute() -> None:
    wash = wash_cluster(cluster_size=5, n_round_trips=80, seed=2)
    scores = initialize_scores(wash)
    # Every cluster wallet is in rapid-cycle land with ~zero PnL proxy.
    assert all(s >= 0.7 for s in scores.values()), scores


def test_score_covers_every_participant() -> None:
    df = organic_trades(n_wallets=10, n_trades=40, seed=0)
    scores = initialize_scores(df)
    participants = set(df["maker"]) | set(df["taker"])
    assert set(scores) == participants
