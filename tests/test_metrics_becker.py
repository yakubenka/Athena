"""Tests for metrics.becker.compute_becker_features."""

from __future__ import annotations

import pandas as pd
import pytest

from metrics.becker import BECKER_COLUMNS, compute_becker_features
from wash_detector.fixtures import TRADE_COLUMNS, organic_trades


def _trade(
    *,
    maker: str,
    taker: str,
    outcome: str,
    taker_side: str,
    price: float,
    size: float = 100.0,
) -> dict[str, object]:
    return {
        "maker": maker,
        "taker": taker,
        "market": "m1",
        "outcome": outcome,
        "taker_side": taker_side,
        "size": size,
        "price": price,
        "timestamp": pd.Timestamp("2025-01-01", tz="UTC"),
    }


def test_empty_dataframe_returns_empty() -> None:
    out = compute_becker_features(pd.DataFrame(columns=TRADE_COLUMNS))
    assert out.empty
    assert list(out.columns) == BECKER_COLUMNS


def test_missing_required_columns_raises() -> None:
    df = pd.DataFrame(
        [
            {
                "maker": "A",
                "taker": "B",
                "market": "m1",
                "size": 1.0,
                "price": 0.5,
                "timestamp": pd.Timestamp("2025-01-01", tz="UTC"),
            }
        ]
    )
    with pytest.raises(KeyError):
        compute_becker_features(df)


def test_only_taker_buys_are_counted() -> None:
    # Wallet A is taker BUY twice (one YES, one NO), and taker SELL once.
    # The SELL row must be ignored.
    df = pd.DataFrame(
        [
            _trade(maker="X", taker="A", outcome="YES", taker_side="BUY", price=0.5),
            _trade(maker="X", taker="A", outcome="NO", taker_side="BUY", price=0.5),
            _trade(maker="X", taker="A", outcome="YES", taker_side="SELL", price=0.5),
        ]
    )
    out = compute_becker_features(df)
    assert out.loc["A", "num_taker_buys"] == 2
    assert out.loc["A", "frac_yes_trades"] == pytest.approx(0.5)
    assert out.loc["A", "frac_no_trades"] == pytest.approx(0.5)


def test_yes_no_fractions_sum_to_one_for_buy_only_wallets() -> None:
    df = organic_trades(n_wallets=20, n_trades=200, seed=0)
    out = compute_becker_features(df)
    sums = out["frac_yes_trades"] + out["frac_no_trades"]
    assert (sums - 1.0).abs().max() < 1e-9


def test_longshot_fractions_match_hand_computed() -> None:
    # Wallet A's BUY trades: 4 YES (3 longshot @ 0.10, 1 not @ 0.50),
    # 2 NO (0 longshot @ 0.50, 0.60).
    # frac_yes_at_longshot = 3/4 = 0.75, frac_no_at_longshot = 0/2 = 0.0
    rows = [
        _trade(maker="X", taker="A", outcome="YES", taker_side="BUY", price=0.10),
        _trade(maker="X", taker="A", outcome="YES", taker_side="BUY", price=0.10),
        _trade(maker="X", taker="A", outcome="YES", taker_side="BUY", price=0.10),
        _trade(maker="X", taker="A", outcome="YES", taker_side="BUY", price=0.50),
        _trade(maker="X", taker="A", outcome="NO", taker_side="BUY", price=0.50),
        _trade(maker="X", taker="A", outcome="NO", taker_side="BUY", price=0.60),
    ]
    out = compute_becker_features(pd.DataFrame(rows), longshot_lo=0.20)
    assert out.loc["A", "frac_yes_trades"] == pytest.approx(4 / 6)
    assert out.loc["A", "frac_no_trades"] == pytest.approx(2 / 6)
    assert out.loc["A", "frac_yes_at_longshot"] == pytest.approx(0.75)
    assert out.loc["A", "frac_no_at_longshot"] == pytest.approx(0.0)


def test_longshot_uses_strict_inequality() -> None:
    # price exactly at threshold should NOT count as longshot.
    rows = [
        _trade(maker="X", taker="A", outcome="YES", taker_side="BUY", price=0.20),
    ]
    out = compute_becker_features(pd.DataFrame(rows), longshot_lo=0.20)
    assert out.loc["A", "frac_yes_at_longshot"] == 0.0


def test_zero_yes_buys_gives_zero_yes_longshot_not_nan() -> None:
    # Wallet A only ever buys NO. frac_yes_at_longshot should be 0.0 (not NaN).
    rows = [
        _trade(maker="X", taker="A", outcome="NO", taker_side="BUY", price=0.10),
        _trade(maker="X", taker="A", outcome="NO", taker_side="BUY", price=0.50),
    ]
    out = compute_becker_features(pd.DataFrame(rows))
    assert out.loc["A", "frac_yes_trades"] == 0.0
    assert out.loc["A", "frac_yes_at_longshot"] == 0.0
    assert out.loc["A", "frac_no_at_longshot"] == pytest.approx(0.5)


def test_wallets_without_buys_are_omitted() -> None:
    rows = [
        # Wallet A only ever sells, never buys → should not appear.
        _trade(maker="X", taker="A", outcome="YES", taker_side="SELL", price=0.5),
        _trade(maker="A", taker="B", outcome="YES", taker_side="BUY", price=0.5),
    ]
    out = compute_becker_features(pd.DataFrame(rows))
    assert "A" not in out.index
    assert "B" in out.index


def test_invalid_longshot_threshold_rejected() -> None:
    df = organic_trades(n_wallets=5, n_trades=10, seed=0)
    with pytest.raises(ValueError):
        compute_becker_features(df, longshot_lo=0.0)
    with pytest.raises(ValueError):
        compute_becker_features(df, longshot_lo=1.0)
