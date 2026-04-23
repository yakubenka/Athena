"""Synthetic trade datasets for developing and testing wash detection.

Two primitive generators:

- `organic_trades`: broad counterparty mix, varied sizes and prices,
  no structural bias — the "clean" baseline.
- `wash_cluster`: a tight group of wallets trading mostly with each
  other via rapid open/close pairs at near-flat prices — the wash
  signature the Sirolly algorithm must catch.

Both return a DataFrame with the same schema so they can be concatenated
to form richer scenarios. Deterministic via seed.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pandas as pd

TRADE_COLUMNS = ["maker", "taker", "market", "size", "price", "timestamp"]


def wallet_id(n: int) -> str:
    """Generate a deterministic 0x-prefixed 40-char hex address."""
    return f"0x{n:040x}"


def organic_trades(
    n_wallets: int = 50,
    n_trades: int = 500,
    *,
    wallet_offset: int = 0,
    n_markets: int = 20,
    seed: int = 0,
    start: datetime | None = None,
) -> pd.DataFrame:
    """Random trades among distinct wallets with broad counterparty mix."""
    rng = random.Random(seed)
    t0 = start or datetime(2025, 1, 1, tzinfo=UTC)
    wallets = [wallet_id(wallet_offset + i) for i in range(n_wallets)]
    markets = [f"market_{i}" for i in range(n_markets)]

    rows = []
    for k in range(n_trades):
        maker, taker = rng.sample(wallets, 2)
        rows.append(
            {
                "maker": maker,
                "taker": taker,
                "market": rng.choice(markets),
                "size": round(rng.uniform(10, 5000), 2),
                "price": round(rng.uniform(0.05, 0.95), 4),
                "timestamp": t0 + timedelta(minutes=k * 3),
            }
        )
    return pd.DataFrame(rows, columns=TRADE_COLUMNS)


def wash_cluster(
    cluster_size: int = 5,
    n_round_trips: int = 100,
    *,
    wallet_offset: int = 1000,
    n_markets: int = 3,
    seed: int = 42,
    start: datetime | None = None,
    max_hold_minutes: int = 30,
) -> pd.DataFrame:
    """Tight cluster: wallets trade mostly with each other in rapid cycles.

    Each "round trip" is two trades A->B then B->A within `max_hold_minutes`
    on the same market at nearly the same price — the classic wash pattern
    that inflates volume but leaves PnL near zero.
    """
    if cluster_size < 2:
        raise ValueError("cluster_size must be >= 2")

    rng = random.Random(seed)
    t0 = start or datetime(2025, 1, 1, tzinfo=UTC)
    wallets = [wallet_id(wallet_offset + i) for i in range(cluster_size)]
    markets = [f"wash_market_{i}" for i in range(n_markets)]

    rows = []
    t = t0
    for _ in range(n_round_trips):
        a, b = rng.sample(wallets, 2)
        market = rng.choice(markets)
        size = round(rng.uniform(500, 2000), 2)
        price = round(rng.uniform(0.2, 0.8), 4)

        rows.append(
            {
                "maker": a,
                "taker": b,
                "market": market,
                "size": size,
                "price": price,
                "timestamp": t,
            }
        )
        t2 = t + timedelta(minutes=rng.randint(1, max_hold_minutes))
        rows.append(
            {
                "maker": b,
                "taker": a,
                "market": market,
                "size": size,
                "price": round(price + rng.uniform(-0.005, 0.005), 4),
                "timestamp": t2,
            }
        )
        t = t2 + timedelta(minutes=rng.randint(10, 120))
    return pd.DataFrame(rows, columns=TRADE_COLUMNS)


def combine(*frames: pd.DataFrame) -> pd.DataFrame:
    """Concatenate trade frames and sort chronologically."""
    return (
        pd.concat(frames, ignore_index=True)
        .sort_values("timestamp", kind="stable")
        .reset_index(drop=True)
    )
