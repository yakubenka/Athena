"""Tests for metrics.hhi — counterparty and category concentration."""

from __future__ import annotations

import pandas as pd
import pytest

from metrics.hhi import category_hhi, counterparty_hhi
from wash_detector.fixtures import TRADE_COLUMNS, combine, organic_trades, wash_cluster


def _make_trade(
    maker: str,
    taker: str,
    size: float,
    price: float,
    market: str = "m1",
    category: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "maker": maker,
        "taker": taker,
        "market": market,
        "size": size,
        "price": price,
        "timestamp": pd.Timestamp("2025-01-01", tz="UTC"),
    }
    if category is not None:
        row["category"] = category
    return row


def test_counterparty_hhi_empty_returns_empty() -> None:
    out = counterparty_hhi(pd.DataFrame(columns=TRADE_COLUMNS))
    assert out.empty
    assert out.name == "counterparty_hhi"


def test_counterparty_hhi_single_counterparty_equals_one() -> None:
    df = pd.DataFrame([_make_trade("A", "B", 100, 0.5), _make_trade("A", "B", 50, 0.5)])
    hhi = counterparty_hhi(df)
    assert hhi.loc["A"] == pytest.approx(1.0)
    assert hhi.loc["B"] == pytest.approx(1.0)


def test_counterparty_hhi_two_equal_counterparties_equals_half() -> None:
    # Wallet A trades $50 with B and $50 with C (same notional).
    df = pd.DataFrame(
        [
            _make_trade("A", "B", 100, 0.5),  # notional 50
            _make_trade("A", "C", 100, 0.5),  # notional 50
        ]
    )
    hhi = counterparty_hhi(df)
    assert hhi.loc["A"] == pytest.approx(0.5)


def test_counterparty_hhi_three_equal_counterparties() -> None:
    df = pd.DataFrame(
        [
            _make_trade("A", "B", 100, 0.5),
            _make_trade("A", "C", 100, 0.5),
            _make_trade("A", "D", 100, 0.5),
        ]
    )
    hhi = counterparty_hhi(df)
    # 3 * (1/3)^2 = 1/3
    assert hhi.loc["A"] == pytest.approx(1.0 / 3.0)


def test_counterparty_hhi_self_loops_ignored() -> None:
    df = pd.DataFrame(
        [
            _make_trade("A", "A", 100, 0.5),  # self-loop, ignored
            _make_trade("A", "B", 100, 0.5),
        ]
    )
    hhi = counterparty_hhi(df)
    assert hhi.loc["A"] == pytest.approx(1.0)
    assert "A" not in set(hhi.index) - {"A", "B"}


def test_counterparty_hhi_wash_exceeds_organic() -> None:
    organic = organic_trades(n_wallets=40, n_trades=400, seed=0)
    wash = wash_cluster(cluster_size=5, n_round_trips=80, wallet_offset=10_000, seed=1)
    df = combine(organic, wash)

    hhi = counterparty_hhi(df)
    wash_wallets = set(wash["maker"]) | set(wash["taker"])
    organic_wallets = (set(organic["maker"]) | set(organic["taker"])) - wash_wallets

    wash_mean = hhi.loc[list(wash_wallets)].mean()
    organic_mean = hhi.loc[list(organic_wallets)].mean()
    assert wash_mean > organic_mean * 2, (
        f"wash {wash_mean:.3f} should clearly exceed organic {organic_mean:.3f}"
    )


def test_counterparty_hhi_bounds() -> None:
    df = organic_trades(n_wallets=20, n_trades=200, seed=1)
    hhi = counterparty_hhi(df)
    assert (hhi >= 0.0).all()
    assert (hhi <= 1.0 + 1e-9).all()


def test_category_hhi_requires_category_column() -> None:
    df = organic_trades(n_wallets=5, n_trades=10, seed=0)
    with pytest.raises(KeyError):
        category_hhi(df)


def test_category_hhi_hand_computed() -> None:
    # A: two trades in Politics (notional 60+40=100), one in Sports (50).
    # Total = 150. Shares: Politics 100/150, Sports 50/150.
    # HHI = (2/3)^2 + (1/3)^2 = 4/9 + 1/9 = 5/9.
    df = pd.DataFrame(
        [
            _make_trade("A", "X", 120, 0.5, category="Politics"),  # 60
            _make_trade("A", "Y", 80, 0.5, category="Politics"),  # 40
            _make_trade("A", "Z", 100, 0.5, category="Sports"),  # 50
        ]
    )
    hhi = category_hhi(df)
    assert hhi.loc["A"] == pytest.approx(5.0 / 9.0)


def test_category_hhi_single_category_equals_one() -> None:
    df = pd.DataFrame(
        [
            _make_trade("A", "B", 100, 0.5, category="Politics"),
            _make_trade("A", "C", 50, 0.5, category="Politics"),
        ]
    )
    hhi = category_hhi(df)
    assert hhi.loc["A"] == pytest.approx(1.0)
