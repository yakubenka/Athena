"""Orchestrator: assemble a wallet_metrics-ready DataFrame from trades.

Runs every metric module we have against a trades DataFrame and joins
the outputs on wallet address. The result shape mirrors the subset of
``wallet_metrics`` columns we can fill without market resolution — ready
to be upserted into Postgres once ingestion lands.

Columns produced, in schema order:

- total_volume, total_trades
- frac_maker_volume, frac_maker_trades, frac_extreme_price (Akey)
- frac_yes_trades, frac_no_trades, frac_yes_at_longshot, frac_no_at_longshot (Becker)
- counterparty_hhi, category_hhi (HHI; category_hhi only if 'category' column present)
- max_consecutive_profitable_months, max_consecutive_5k_plus_months (Reichenbach)
- wash_score, wash_cluster_id, is_suspected_wash (Sirolly)
- pnl_to_volume_ratio, rapid_open_close_ratio (Sirolly activity)

Columns still pending real resolution data: ``realised_pnl``,
``unrealised_pnl``, ``total_pnl``, ``win_rate``, ``excess_hit_rate``,
``sharpe_like``, ``alpha_per_trade``. Left out of the output to avoid
silent zeros; callers will join them in later.
"""

from __future__ import annotations

import pandas as pd

from metrics.akey import compute_akey_features
from metrics.becker import compute_becker_features
from metrics.consistency import max_consecutive_above, monthly_pnl
from metrics.hhi import category_hhi, counterparty_hhi
from wash_detector.sirolly import MAX_HOLD_HOURS_DEFAULT, _wallet_stats, run_wash_detection

WALLET_METRICS_COLUMNS = [
    "total_volume",
    "total_trades",
    "frac_maker_volume",
    "frac_maker_trades",
    "frac_extreme_price",
    "frac_yes_trades",
    "frac_no_trades",
    "frac_yes_at_longshot",
    "frac_no_at_longshot",
    "counterparty_hhi",
    "category_hhi",
    "max_consecutive_profitable_months",
    "max_consecutive_5k_plus_months",
    "wash_score",
    "wash_cluster_id",
    "is_suspected_wash",
    "pnl_to_volume_ratio",
    "rapid_open_close_ratio",
]


def compute_wallet_metrics(
    trades_df: pd.DataFrame,
    *,
    longshot_lo: float = 0.20,
    extreme_lo: float = 0.10,
    extreme_hi: float = 0.90,
    wash_threshold: float = 0.7,
    wash_min_cluster_size: int = 3,
    max_hold_hours: float = MAX_HOLD_HOURS_DEFAULT,
) -> pd.DataFrame:
    """Run every metric module and return a joined per-wallet DataFrame."""
    if trades_df.empty:
        return pd.DataFrame(columns=WALLET_METRICS_COLUMNS).rename_axis("wallet")

    akey = compute_akey_features(trades_df, extreme_lo=extreme_lo, extreme_hi=extreme_hi)
    becker = compute_becker_features(trades_df, longshot_lo=longshot_lo)
    cp_hhi = counterparty_hhi(trades_df)
    cat_hhi = (
        category_hhi(trades_df)
        if "category" in trades_df.columns
        else pd.Series(dtype=float, name="category_hhi")
    )

    monthly = monthly_pnl(trades_df)
    max_profit_months = max_consecutive_above(monthly, threshold=0.0).rename(
        "max_consecutive_profitable_months"
    )
    max_5k_months = max_consecutive_above(monthly, threshold=5_000.0).rename(
        "max_consecutive_5k_plus_months"
    )

    scores, clusters = run_wash_detection(
        trades_df,
        threshold=wash_threshold,
        min_cluster_size=wash_min_cluster_size,
        max_hold_hours=max_hold_hours,
    )
    wash_score = pd.Series(scores, name="wash_score", dtype=float)
    cluster_map: dict[str, str] = {}
    for idx, cluster in enumerate(clusters):
        cluster_id = f"cluster_{idx:03d}"
        for wallet in cluster:
            cluster_map[wallet] = cluster_id
    wash_cluster_id = pd.Series(cluster_map, name="wash_cluster_id", dtype="object")

    activity = _wallet_stats(trades_df, max_hold_hours=max_hold_hours)
    pnl_to_vol = (activity["pnl_proxy"].abs() / activity["volume"].clip(lower=1)).rename(
        "pnl_to_volume_ratio"
    )
    rapid_ratio = (activity["rapid_cycles"] / activity["num_trades"].clip(lower=1)).rename(
        "rapid_open_close_ratio"
    )

    # Start from Akey's universe (one row per wallet that ever traded).
    out = akey.copy()
    out = out.join(becker, how="left")
    out["counterparty_hhi"] = cp_hhi
    out["category_hhi"] = cat_hhi
    out = out.join(max_profit_months, how="left")
    out = out.join(max_5k_months, how="left")
    out["wash_score"] = wash_score
    out["wash_cluster_id"] = wash_cluster_id
    out["pnl_to_volume_ratio"] = pnl_to_vol
    out["rapid_open_close_ratio"] = rapid_ratio

    out["max_consecutive_profitable_months"] = (
        out["max_consecutive_profitable_months"].fillna(0).astype("int64")
    )
    out["max_consecutive_5k_plus_months"] = (
        out["max_consecutive_5k_plus_months"].fillna(0).astype("int64")
    )
    out["is_suspected_wash"] = out["wash_cluster_id"].notna()
    out["wash_score"] = out["wash_score"].fillna(0.0)

    return out.reindex(columns=WALLET_METRICS_COLUMNS)
