"""Herfindahl-Hirschman concentration indices for wallet activity.

HHI is the sum of squared shares in [0, 1]:

- 0 (asymptotic) — perfectly diversified across many buckets
- 1 — the wallet's entire volume is with a single counterparty or in
  a single category

Two signals Akey found useful:

- **Counterparty HHI:** -5.2 p.p. impact on loss probability. Lower
  concentration (diverse counterparties) correlates with skill.
  Elevated counterparty HHI is also a wash-cluster tell.
- **Category HHI:** +13.6 p.p. impact on loss probability. Specialists
  who concentrate in one category outperform generalists in Akey.

``category_hhi`` requires the trades DataFrame to carry a ``category``
column (joined in from ``markets``). Ingestion isn't wired up yet, so
in practice this is the feature to call once market ingestion lands.
"""

from __future__ import annotations

import pandas as pd


def counterparty_hhi(trades_df: pd.DataFrame) -> pd.Series:
    """Per-wallet volume-weighted HHI across distinct counterparties.

    Each trade contributes its notional to both participants as a
    touch against the other wallet; self-trades (maker == taker) are
    ignored.
    """
    if trades_df.empty:
        return pd.Series(dtype=float, name="counterparty_hhi")

    df = trades_df[trades_df["maker"] != trades_df["taker"]]
    if df.empty:
        return pd.Series(dtype=float, name="counterparty_hhi")

    notional = df["size"] * df["price"]
    long = pd.concat(
        [
            pd.DataFrame({"wallet": df["maker"], "cp": df["taker"], "n": notional}),
            pd.DataFrame({"wallet": df["taker"], "cp": df["maker"], "n": notional}),
        ],
        ignore_index=True,
    )
    pair = long.groupby(["wallet", "cp"], as_index=False)["n"].sum()
    pair["total"] = pair.groupby("wallet")["n"].transform("sum")
    pair["share_sq"] = (pair["n"] / pair["total"]) ** 2

    hhi = pair.groupby("wallet")["share_sq"].sum()
    hhi.name = "counterparty_hhi"
    return hhi.astype(float)


def category_hhi(trades_df: pd.DataFrame) -> pd.Series:
    """Per-wallet volume-weighted HHI across market categories.

    Requires a ``category`` column in ``trades_df`` (typically joined
    from the ``markets`` table). Each trade contributes its notional
    to the category bucket for both maker and taker.
    """
    if "category" not in trades_df.columns:
        raise KeyError("category_hhi requires a 'category' column in trades_df")
    if trades_df.empty:
        return pd.Series(dtype=float, name="category_hhi")

    notional = trades_df["size"] * trades_df["price"]
    long = pd.concat(
        [
            pd.DataFrame(
                {
                    "wallet": trades_df["maker"],
                    "category": trades_df["category"],
                    "n": notional,
                }
            ),
            pd.DataFrame(
                {
                    "wallet": trades_df["taker"],
                    "category": trades_df["category"],
                    "n": notional,
                }
            ),
        ],
        ignore_index=True,
    )
    bucket = long.groupby(["wallet", "category"], as_index=False)["n"].sum()
    bucket["total"] = bucket.groupby("wallet")["n"].transform("sum")
    bucket["share_sq"] = (bucket["n"] / bucket["total"]) ** 2

    hhi = bucket.groupby("wallet")["share_sq"].sum()
    hhi.name = "category_hhi"
    return hhi.astype(float)
