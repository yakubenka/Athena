"""Reichenbach & Walther (Dec 2025) monthly consistency metrics.

Two signals per wallet derived from monthly PnL time series:

- ``max_consecutive_profitable_months``: longest run of months with
  PnL > 0. Reichenbach's paper identified ~72 wallets with 9+
  consecutive $5k+ months across Polymarket's history — the
  "True Tier S" candidate pool for copy-trading.
- ``max_consecutive_above``: generalization with a configurable
  threshold (defaults to 0); ``threshold=5000`` reproduces the
  headline Reichenbach count.

PnL here is the same proxy used in :mod:`wash_detector.sirolly`:
``maker_notional - taker_notional``. It will be replaced with
realised PnL once resolutions are persisted on the ``trades`` table.
"""

from __future__ import annotations

import pandas as pd

MONTHLY_PNL_COLUMNS = ["wallet", "month", "monthly_pnl", "monthly_volume", "num_trades"]


def monthly_pnl(trades_df: pd.DataFrame) -> pd.DataFrame:
    """Bucket trades by (wallet, calendar month) and aggregate PnL proxy.

    Returns a DataFrame with one row per (wallet, month) combination the
    wallet participated in. Months with zero activity for a wallet are
    omitted.
    """
    if trades_df.empty:
        return pd.DataFrame(columns=MONTHLY_PNL_COLUMNS)

    notional = trades_df["size"] * trades_df["price"]
    # to_period drops tz info, so normalize to UTC and strip tz first,
    # then re-localize after bucketing so the public API stays tz-aware.
    ts_naive = trades_df["timestamp"].dt.tz_convert("UTC").dt.tz_localize(None)
    month = ts_naive.dt.to_period("M").dt.to_timestamp().dt.tz_localize("UTC")

    # Melt into (wallet, signed notional, month) rows: maker is +, taker is -.
    maker_rows = pd.DataFrame(
        {
            "wallet": trades_df["maker"].to_numpy(),
            "month": month.to_numpy(),
            "signed": notional.to_numpy(),
            "volume": notional.to_numpy(),
        }
    )
    taker_rows = pd.DataFrame(
        {
            "wallet": trades_df["taker"].to_numpy(),
            "month": month.to_numpy(),
            "signed": -notional.to_numpy(),
            "volume": notional.to_numpy(),
        }
    )
    long = pd.concat([maker_rows, taker_rows], ignore_index=True)

    grouped = long.groupby(["wallet", "month"])
    out = grouped.agg(
        monthly_pnl=("signed", "sum"),
        monthly_volume=("volume", "sum"),
        num_trades=("signed", "size"),
    ).reset_index()

    out["monthly_pnl"] = out["monthly_pnl"].astype(float)
    out["monthly_volume"] = out["monthly_volume"].astype(float)
    out["num_trades"] = out["num_trades"].astype("int64")
    return out[MONTHLY_PNL_COLUMNS]


def max_consecutive_above(monthly: pd.DataFrame, threshold: float = 0.0) -> pd.Series:
    """Per-wallet longest run of consecutive months with PnL > threshold.

    Expects the DataFrame returned by :func:`monthly_pnl`. Gaps in the
    time series (months with no trades for a wallet) break the run —
    only *consecutive calendar months* count.

    Wallets with no qualifying month get 0.
    """
    if monthly.empty:
        return pd.Series(dtype="int64", name="max_consecutive_above")

    df = monthly.sort_values(["wallet", "month"]).copy()
    month_period = df["month"].dt.to_period("M")

    prev_month = month_period.groupby(df["wallet"]).shift(1)
    gap_to_prev = (month_period - prev_month).apply(
        lambda off: off.n if isinstance(off, pd.offsets.MonthEnd) else None
    )
    # A consecutive month is one exactly 1 month after the previous row
    # for the same wallet; anything else (NaN for first row, >1 for gaps)
    # starts a new run.
    is_consecutive = gap_to_prev == 1
    is_match = df["monthly_pnl"] > threshold

    # Break the run when either the match fails or the month isn't adjacent.
    df["run_break"] = (~is_match | ~is_consecutive).astype("int64")
    df["run_id"] = df.groupby("wallet")["run_break"].cumsum()

    matches = df[is_match]
    if matches.empty:
        result = pd.Series(
            0, index=df["wallet"].unique(), dtype="int64", name="max_consecutive_above"
        )
        result.index.name = "wallet"
        return result

    run_lengths = matches.groupby(["wallet", "run_id"]).size()
    max_per_wallet = run_lengths.groupby(level="wallet").max()

    all_wallets = pd.Index(df["wallet"].unique(), name="wallet")
    out = max_per_wallet.reindex(all_wallets, fill_value=0).astype("int64")
    out.name = "max_consecutive_above"
    return out
