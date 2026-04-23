"""Tests for metrics.consistency — monthly PnL and consecutive run counts."""

from __future__ import annotations

import pandas as pd
import pytest

from metrics.consistency import max_consecutive_above, monthly_pnl
from wash_detector.fixtures import TRADE_COLUMNS


def _trade(
    maker: str,
    taker: str,
    size: float,
    price: float,
    timestamp: pd.Timestamp,
) -> dict[str, object]:
    return {
        "maker": maker,
        "taker": taker,
        "market": "m1",
        "size": size,
        "price": price,
        "timestamp": timestamp,
    }


def _ts(year: int, month: int, day: int = 15) -> pd.Timestamp:
    return pd.Timestamp(f"{year}-{month:02d}-{day:02d}", tz="UTC")


def test_monthly_pnl_empty_returns_empty() -> None:
    out = monthly_pnl(pd.DataFrame(columns=TRADE_COLUMNS))
    assert list(out.columns) == [
        "wallet",
        "month",
        "monthly_pnl",
        "monthly_volume",
        "num_trades",
    ]
    assert out.empty


def test_monthly_pnl_bucketing_and_signs() -> None:
    # Month Jan 2025: A is maker +50, then taker -30.  Net +20, volume 80.
    # Month Feb 2025: A is maker +100. Net +100, volume 100.
    df = pd.DataFrame(
        [
            _trade("A", "B", 100, 0.5, _ts(2025, 1, 10)),  # notional 50 maker
            _trade("C", "A", 60, 0.5, _ts(2025, 1, 20)),  # notional 30 taker
            _trade("A", "B", 200, 0.5, _ts(2025, 2, 5)),  # notional 100 maker
        ]
    )
    out = monthly_pnl(df).set_index(["wallet", "month"])

    jan = out.loc[("A", pd.Timestamp("2025-01-01", tz="UTC"))]
    feb = out.loc[("A", pd.Timestamp("2025-02-01", tz="UTC"))]

    assert jan["monthly_pnl"] == pytest.approx(50 - 30)
    assert jan["monthly_volume"] == pytest.approx(80)
    assert jan["num_trades"] == 2

    assert feb["monthly_pnl"] == pytest.approx(100.0)
    assert feb["monthly_volume"] == pytest.approx(100.0)
    assert feb["num_trades"] == 1


def test_max_consecutive_above_empty_input() -> None:
    out = max_consecutive_above(pd.DataFrame(columns=["wallet", "month", "monthly_pnl"]))
    assert out.empty
    assert out.name == "max_consecutive_above"


def _monthly_for(wallet: str, pnl_per_month: list[tuple[int, int, float]]) -> pd.DataFrame:
    rows = []
    for year, month, pnl in pnl_per_month:
        rows.append(
            {
                "wallet": wallet,
                "month": pd.Timestamp(f"{year}-{month:02d}-01"),
                "monthly_pnl": pnl,
                "monthly_volume": abs(pnl) * 10,
                "num_trades": 1,
            }
        )
    return pd.DataFrame(rows)


def test_max_consecutive_all_positive() -> None:
    df = _monthly_for(
        "A",
        [
            (2025, 1, 100.0),
            (2025, 2, 200.0),
            (2025, 3, 50.0),
        ],
    )
    assert max_consecutive_above(df).loc["A"] == 3


def test_max_consecutive_all_non_positive() -> None:
    df = _monthly_for("A", [(2025, 1, -10.0), (2025, 2, 0.0), (2025, 3, -5.0)])
    assert max_consecutive_above(df).loc["A"] == 0


def test_max_consecutive_picks_longest_streak() -> None:
    # Streaks of positives: [1], [3]. Longest is 3.
    df = _monthly_for(
        "A",
        [
            (2025, 1, 100.0),
            (2025, 2, -50.0),
            (2025, 3, 200.0),
            (2025, 4, 150.0),
            (2025, 5, 50.0),
            (2025, 6, -10.0),
            (2025, 7, 10.0),
        ],
    )
    assert max_consecutive_above(df).loc["A"] == 3


def test_max_consecutive_gap_breaks_run() -> None:
    # Jan, Feb, then skip March entirely (no activity), April.
    # Jan and Feb are adjacent (streak 2). April isn't adjacent to Feb, so
    # it starts a fresh streak of 1. Expected max = 2.
    df = _monthly_for(
        "A",
        [
            (2025, 1, 100.0),
            (2025, 2, 200.0),
            (2025, 4, 300.0),
        ],
    )
    assert max_consecutive_above(df).loc["A"] == 2


def test_max_consecutive_with_threshold() -> None:
    df = _monthly_for(
        "A",
        [
            (2025, 1, 5_000.0),
            (2025, 2, 6_000.0),
            (2025, 3, 3_000.0),
            (2025, 4, 7_000.0),
            (2025, 5, 8_000.0),
        ],
    )
    assert max_consecutive_above(df, threshold=5_000.0).loc["A"] == 2


def test_max_consecutive_wallets_independent() -> None:
    rows = []
    rows += _monthly_for(
        "A",
        [
            (2025, 1, 100.0),
            (2025, 2, 200.0),
            (2025, 3, 300.0),
        ],
    ).to_dict("records")
    rows += _monthly_for(
        "B",
        [
            (2025, 1, -50.0),
            (2025, 2, 100.0),
        ],
    ).to_dict("records")
    df = pd.DataFrame(rows)

    out = max_consecutive_above(df)
    assert out.loc["A"] == 3
    assert out.loc["B"] == 1
