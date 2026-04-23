"""Akey et al. (Mar 2026) wallet features that don't require market resolution.

Three signals that the Akey paper showed strongly predict skill on
Polymarket and can be computed straight from the trades table:

- ``frac_maker_volume`` — share of a wallet's total notional executed
  as a maker. Strongest single predictor in Akey: -35.9 p.p. impact
  on loss probability.
- ``frac_maker_trades`` — share of a wallet's trade count executed
  as a maker (companion to the volume-weighted version).
- ``frac_extreme_price`` — share of a wallet's trades at long-shot
  prices (below ``extreme_lo`` or above ``extreme_hi``). High values
  usually mean degenerate lottery-ticket betting.

All features are computed per wallet across its full role set (maker
and taker). The output DataFrame is indexed by wallet address.
"""

from __future__ import annotations

import pandas as pd

EXTREME_LO_DEFAULT = 0.10
EXTREME_HI_DEFAULT = 0.90


def compute_akey_features(
    trades_df: pd.DataFrame,
    *,
    extreme_lo: float = EXTREME_LO_DEFAULT,
    extreme_hi: float = EXTREME_HI_DEFAULT,
) -> pd.DataFrame:
    """Compute per-wallet Akey features.

    Returns a DataFrame indexed by wallet with columns
    ``frac_maker_volume``, ``frac_maker_trades``, ``frac_extreme_price``,
    ``total_volume``, ``total_trades``. Wallets appear if they acted
    as maker or taker at least once.
    """
    if not 0.0 <= extreme_lo < extreme_hi <= 1.0:
        raise ValueError("require 0 <= extreme_lo < extreme_hi <= 1")

    columns = [
        "frac_maker_volume",
        "frac_maker_trades",
        "frac_extreme_price",
        "total_volume",
        "total_trades",
    ]
    if trades_df.empty:
        return pd.DataFrame(columns=columns).rename_axis("wallet")

    notional = trades_df["size"] * trades_df["price"]
    is_extreme = (trades_df["price"] < extreme_lo) | (trades_df["price"] > extreme_hi)

    maker_vol = trades_df.assign(n=notional).groupby("maker")["n"].sum()
    taker_vol = trades_df.assign(n=notional).groupby("taker")["n"].sum()
    maker_cnt = trades_df.groupby("maker").size()
    taker_cnt = trades_df.groupby("taker").size()

    maker_ext = trades_df.assign(e=is_extreme.astype(int)).groupby("maker")["e"].sum()
    taker_ext = trades_df.assign(e=is_extreme.astype(int)).groupby("taker")["e"].sum()

    wallets = maker_vol.index.union(taker_vol.index)

    mv = maker_vol.reindex(wallets, fill_value=0.0)
    tv = taker_vol.reindex(wallets, fill_value=0.0)
    mc = maker_cnt.reindex(wallets, fill_value=0).astype("int64")
    tc = taker_cnt.reindex(wallets, fill_value=0).astype("int64")
    me = maker_ext.reindex(wallets, fill_value=0).astype("int64")
    te = taker_ext.reindex(wallets, fill_value=0).astype("int64")

    total_vol = mv + tv
    total_cnt = mc + tc
    ext_cnt = me + te

    out = pd.DataFrame(
        {
            "frac_maker_volume": (mv / total_vol.where(total_vol > 0, 1.0)).astype(float),
            "frac_maker_trades": (mc / total_cnt.where(total_cnt > 0, 1)).astype(float),
            "frac_extreme_price": (ext_cnt / total_cnt.where(total_cnt > 0, 1)).astype(float),
            "total_volume": total_vol.astype(float),
            "total_trades": total_cnt.astype("int64"),
        },
        index=wallets,
    )
    out.index.name = "wallet"
    return out[columns]
