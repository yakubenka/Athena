"""Becker (Jan 2026) optimism-tax features for prediction-market traders.

Becker showed two robust microstructure facts on Kalshi that Reichenbach
later replicated on Polymarket:

1. **YES overtrading.** Most takers prefer to BUY YES (positive framing)
   even when the EV favors NO. Wallets with abnormally high YES-BUY
   share are more likely to be losing money.
2. **Optimism tax at long shots.** Buying YES at price ~0.01 has -41% EV
   (gross overpayment for unlikely upside). Buying NO at price ~0.01 has
   +23% EV (mild underpricing). The bigger the long-shot exposure on the
   YES side, the worse the wallet's expected return.

For each wallet we look only at trades where the wallet was the taker
(i.e. the active party that crossed the spread) AND ``taker_side ==
"BUY"`` — that's where the optimism choice is being expressed. Among
those entries:

- ``frac_yes_trades`` — share of YES BUYs.
- ``frac_no_trades``  — share of NO  BUYs.
- ``frac_yes_at_longshot`` — share of YES BUYs whose price falls below
  ``longshot_lo``. High = aggressive optimism-tax payer.
- ``frac_no_at_longshot``  — share of NO  BUYs whose price falls below
  ``longshot_lo``. High = NO long-shot specialist (Becker's positive-EV
  side).

Wallets that never acted as a buying taker are omitted.
"""

from __future__ import annotations

import pandas as pd

LONGSHOT_LO_DEFAULT = 0.20

BECKER_COLUMNS = [
    "frac_yes_trades",
    "frac_no_trades",
    "frac_yes_at_longshot",
    "frac_no_at_longshot",
    "num_taker_buys",
]


def compute_becker_features(
    trades_df: pd.DataFrame,
    *,
    longshot_lo: float = LONGSHOT_LO_DEFAULT,
) -> pd.DataFrame:
    """Per-wallet YES/NO BUY-side optimism features."""
    if not 0.0 < longshot_lo < 1.0:
        raise ValueError("longshot_lo must be in (0, 1)")

    if trades_df.empty:
        return pd.DataFrame(columns=BECKER_COLUMNS).rename_axis("wallet")

    for col in ("outcome", "taker_side"):
        if col not in trades_df.columns:
            raise KeyError(f"compute_becker_features requires a '{col}' column")

    buys = trades_df[trades_df["taker_side"] == "BUY"]
    if buys.empty:
        return pd.DataFrame(columns=BECKER_COLUMNS).rename_axis("wallet")

    is_yes = buys["outcome"] == "YES"
    is_no = buys["outcome"] == "NO"
    is_longshot = buys["price"] < longshot_lo

    by_wallet = buys.groupby("taker", sort=False)
    total = by_wallet.size()
    yes_count = is_yes.groupby(buys["taker"], sort=False).sum()
    no_count = is_no.groupby(buys["taker"], sort=False).sum()
    yes_long = (is_yes & is_longshot).groupby(buys["taker"], sort=False).sum()
    no_long = (is_no & is_longshot).groupby(buys["taker"], sort=False).sum()

    wallets = total.index
    yes_count = yes_count.reindex(wallets, fill_value=0)
    no_count = no_count.reindex(wallets, fill_value=0)
    yes_long = yes_long.reindex(wallets, fill_value=0)
    no_long = no_long.reindex(wallets, fill_value=0)

    yes_denom = yes_count.where(yes_count > 0, 1)
    no_denom = no_count.where(no_count > 0, 1)

    out = pd.DataFrame(
        {
            "frac_yes_trades": (yes_count / total).astype(float),
            "frac_no_trades": (no_count / total).astype(float),
            "frac_yes_at_longshot": (yes_long / yes_denom).astype(float).where(yes_count > 0, 0.0),
            "frac_no_at_longshot": (no_long / no_denom).astype(float).where(no_count > 0, 0.0),
            "num_taker_buys": total.astype("int64"),
        },
        index=wallets,
    )
    out.index.name = "wallet"
    return out[BECKER_COLUMNS]
