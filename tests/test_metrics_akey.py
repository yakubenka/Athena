"""Tests for metrics.akey.compute_akey_features."""

from __future__ import annotations

import pandas as pd
import pytest

from metrics.akey import compute_akey_features
from wash_detector.fixtures import TRADE_COLUMNS, organic_trades


def _small_df() -> pd.DataFrame:
    """Three trades with known values so features can be hand-verified."""
    ts = pd.Timestamp("2025-01-01", tz="UTC")
    rows = [
        # A is maker, B is taker; notional = 100 * 0.50 = 50; not extreme
        {"maker": "A", "taker": "B", "market": "m1", "size": 100.0, "price": 0.50, "timestamp": ts},
        # A maker again; notional = 200 * 0.05 = 10; extreme (low)
        {"maker": "A", "taker": "C", "market": "m2", "size": 200.0, "price": 0.05, "timestamp": ts},
        # A is taker; notional = 100 * 0.95 = 95; extreme (high)
        {"maker": "B", "taker": "A", "market": "m3", "size": 100.0, "price": 0.95, "timestamp": ts},
    ]
    return pd.DataFrame(rows)


def test_empty_dataframe_returns_empty_result() -> None:
    out = compute_akey_features(pd.DataFrame(columns=TRADE_COLUMNS))
    assert out.empty
    assert list(out.columns) == [
        "frac_maker_volume",
        "frac_maker_trades",
        "frac_extreme_price",
        "total_volume",
        "total_trades",
    ]


def test_small_df_matches_hand_computed_values() -> None:
    out = compute_akey_features(_small_df())

    # A: maker in trades 1 & 2 (notional 50 + 10 = 60 maker, 95 taker; vol=155)
    #    trade count: 3 total (2 maker + 1 taker), 2 extreme (trades 2 and 3)
    a = out.loc["A"]
    assert a["frac_maker_volume"] == pytest.approx(60.0 / 155.0)
    assert a["frac_maker_trades"] == pytest.approx(2.0 / 3.0)
    assert a["frac_extreme_price"] == pytest.approx(2.0 / 3.0)
    assert a["total_volume"] == pytest.approx(155.0)
    assert a["total_trades"] == 3

    # B: taker in trade 1, maker in trade 3 (50 taker + 95 maker; vol=145)
    b = out.loc["B"]
    assert b["frac_maker_volume"] == pytest.approx(95.0 / 145.0)
    assert b["frac_maker_trades"] == pytest.approx(1.0 / 2.0)
    assert b["frac_extreme_price"] == pytest.approx(1.0 / 2.0)

    # C: taker in trade 2 only
    c = out.loc["C"]
    assert c["frac_maker_volume"] == pytest.approx(0.0)
    assert c["frac_maker_trades"] == pytest.approx(0.0)
    assert c["frac_extreme_price"] == pytest.approx(1.0)


def test_feature_bounds_on_organic_dataset() -> None:
    df = organic_trades(n_wallets=20, n_trades=200, seed=0)
    out = compute_akey_features(df)

    for col in ("frac_maker_volume", "frac_maker_trades", "frac_extreme_price"):
        assert (out[col] >= 0.0).all(), col
        assert (out[col] <= 1.0).all(), col
    assert (out["total_volume"] > 0).all()
    assert (out["total_trades"] > 0).all()


def test_extreme_threshold_parameters_respected() -> None:
    df = _small_df()
    # Tighten extreme_lo so only price 0.05 (trade 2) is extreme and 0.95
    # (trade 3) becomes non-extreme under extreme_hi=0.99.
    out = compute_akey_features(df, extreme_lo=0.06, extreme_hi=0.99)
    # A participates in trades 1, 2, 3. Only trade 2 is extreme → 1/3.
    assert out.loc["A", "frac_extreme_price"] == pytest.approx(1.0 / 3.0)


def test_rejects_invalid_thresholds() -> None:
    df = _small_df()
    with pytest.raises(ValueError):
        compute_akey_features(df, extreme_lo=0.5, extreme_hi=0.5)
    with pytest.raises(ValueError):
        compute_akey_features(df, extreme_lo=-0.1, extreme_hi=0.9)
    with pytest.raises(ValueError):
        compute_akey_features(df, extreme_lo=0.1, extreme_hi=1.5)


def test_every_participant_gets_row() -> None:
    df = organic_trades(n_wallets=10, n_trades=40, seed=1)
    out = compute_akey_features(df)
    expected = set(df["maker"]) | set(df["taker"])
    assert set(out.index) == expected
