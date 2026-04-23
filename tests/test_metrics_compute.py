"""Tests for the metrics.compute orchestrator."""

from __future__ import annotations

import pandas as pd

from metrics.compute import WALLET_METRICS_COLUMNS, compute_wallet_metrics
from wash_detector.fixtures import TRADE_COLUMNS, combine, organic_trades, wash_cluster


def test_empty_input_returns_empty_schema() -> None:
    out = compute_wallet_metrics(pd.DataFrame(columns=TRADE_COLUMNS))
    assert out.empty
    assert list(out.columns) == WALLET_METRICS_COLUMNS
    assert out.index.name == "wallet"


def test_output_has_schema_columns_and_participant_coverage() -> None:
    df = organic_trades(n_wallets=20, n_trades=200, seed=0)
    out = compute_wallet_metrics(df)

    assert list(out.columns) == WALLET_METRICS_COLUMNS
    expected = set(df["maker"]) | set(df["taker"])
    assert set(out.index) == expected


def test_wash_cluster_wallets_flagged_with_cluster_id_and_high_score() -> None:
    organic = organic_trades(n_wallets=40, n_trades=400, seed=0)
    wash = wash_cluster(cluster_size=5, n_round_trips=80, wallet_offset=10_000, seed=1)
    df = combine(organic, wash)

    out = compute_wallet_metrics(df)
    wash_wallets = set(wash["maker"]) | set(wash["taker"])

    wash_rows = out.loc[list(wash_wallets)]
    assert wash_rows["is_suspected_wash"].all()
    assert wash_rows["wash_score"].min() >= 0.7
    assert wash_rows["wash_cluster_id"].notna().all()
    # Every wash wallet shares the same cluster id in this fixture.
    assert wash_rows["wash_cluster_id"].nunique() == 1


def test_organic_wallets_not_flagged_as_wash() -> None:
    df = organic_trades(n_wallets=30, n_trades=300, seed=0)
    out = compute_wallet_metrics(df)

    assert not out["is_suspected_wash"].any()
    assert out["wash_cluster_id"].isna().all()
    assert out["wash_score"].max() < 0.7


def test_consistency_columns_are_non_negative_integers() -> None:
    df = organic_trades(n_wallets=10, n_trades=50, seed=0)
    out = compute_wallet_metrics(df)
    for col in ("max_consecutive_profitable_months", "max_consecutive_5k_plus_months"):
        assert out[col].dtype.kind == "i"
        assert (out[col] >= 0).all()


def test_category_hhi_only_present_when_column_given() -> None:
    df = organic_trades(n_wallets=10, n_trades=40, seed=0)
    out_no_cat = compute_wallet_metrics(df)
    assert out_no_cat["category_hhi"].isna().all()

    with_cat = df.copy()
    with_cat["category"] = "Politics"
    out_with = compute_wallet_metrics(with_cat)
    # Single category → HHI = 1 for every active wallet.
    assert (out_with["category_hhi"] == 1.0).all()


def test_ratios_are_within_expected_ranges() -> None:
    df = organic_trades(n_wallets=15, n_trades=150, seed=0)
    out = compute_wallet_metrics(df)

    for col in ("frac_maker_volume", "frac_maker_trades", "frac_extreme_price"):
        assert (out[col].between(0.0, 1.0)).all()
    assert (out["counterparty_hhi"].between(0.0, 1.0 + 1e-9)).all()
    assert (out["wash_score"].between(0.0, 1.0)).all()
    assert (out["rapid_open_close_ratio"].between(0.0, 1.0)).all()


def test_total_volume_matches_sum_of_notional_per_wallet() -> None:
    df = organic_trades(n_wallets=5, n_trades=20, seed=1)
    out = compute_wallet_metrics(df)

    notional = df["size"] * df["price"]
    maker = df.assign(n=notional).groupby("maker")["n"].sum()
    taker = df.assign(n=notional).groupby("taker")["n"].sum()
    wallets = maker.index.union(taker.index)
    expected = maker.reindex(wallets, fill_value=0.0) + taker.reindex(wallets, fill_value=0.0)

    assert (out["total_volume"].loc[expected.index] - expected).abs().max() < 1e-6
