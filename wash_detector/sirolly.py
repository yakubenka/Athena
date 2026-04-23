"""Sirolly et al. (Nov 2025) network-based wash detection — Stage 1.

Stage 1 assigns an initial suspicion score in [0, 1] to each wallet by
combining two signals:

- **Turnover:** share of a wallet's trades that are followed by another
  trade on the same market within `max_hold_hours`. Wash farms cycle
  positions rapidly to inflate volume.
- **PnL/volume suspicion:** `1 - min(|pnl|/volume * 100, 1)`. Wash traders
  churn large notional volume with near-zero net cash flow.

Score = 0.5 * turnover + 0.5 * pnl_suspicion.

PnL here is a *proxy*: sum(size*price) as maker minus the same as taker.
For round-trip wash patterns (A -> B then B -> A at near-equal price),
each participant's net is ~0. For organic trading the signed flow is
non-zero on average. Once we have resolved markets we will replace this
proxy with realised PnL.
"""

from __future__ import annotations

import networkx as nx
import numpy as np
import pandas as pd

MAX_HOLD_HOURS_DEFAULT = 24.0


def _wallet_stats(
    trades_df: pd.DataFrame, max_hold_hours: float = MAX_HOLD_HOURS_DEFAULT
) -> pd.DataFrame:
    """Per-wallet aggregates required by the Stage 1 score.

    Returns a DataFrame indexed by wallet with columns:
    num_trades, volume, pnl_proxy, rapid_cycles.
    """
    if trades_df.empty:
        return pd.DataFrame(columns=["num_trades", "volume", "pnl_proxy", "rapid_cycles"])

    notional = trades_df["size"] * trades_df["price"]
    maker_flow = trades_df.assign(amount=notional).groupby("maker")["amount"].sum()
    taker_flow = trades_df.assign(amount=notional).groupby("taker")["amount"].sum()

    all_wallets = maker_flow.index.union(taker_flow.index)
    maker_flow = maker_flow.reindex(all_wallets, fill_value=0.0)
    taker_flow = taker_flow.reindex(all_wallets, fill_value=0.0)

    volume = maker_flow + taker_flow
    pnl_proxy = maker_flow - taker_flow

    # num_trades per wallet (counts in either role)
    maker_count = trades_df.groupby("maker").size().reindex(all_wallets, fill_value=0)
    taker_count = trades_df.groupby("taker").size().reindex(all_wallets, fill_value=0)
    num_trades = maker_count + taker_count

    rapid = _rapid_cycle_counts(trades_df, max_hold_hours).reindex(all_wallets, fill_value=0)

    stats = pd.DataFrame(
        {
            "num_trades": num_trades,
            "volume": volume,
            "pnl_proxy": pnl_proxy,
            "rapid_cycles": rapid,
        }
    )
    stats.index.name = "wallet"
    return stats


def _rapid_cycle_counts(trades_df: pd.DataFrame, max_hold_hours: float) -> pd.Series:
    """Count consecutive same-market trades per wallet within max_hold_hours.

    Each wallet appears in a trade either as maker or taker; both count
    as a "touch" on that market at that timestamp. We count how many of
    those touches are preceded by the same wallet's touch on the same
    market within the window.
    """
    long = pd.concat(
        [
            trades_df[["maker", "market", "timestamp"]].rename(columns={"maker": "wallet"}),
            trades_df[["taker", "market", "timestamp"]].rename(columns={"taker": "wallet"}),
        ],
        ignore_index=True,
    ).sort_values(["wallet", "market", "timestamp"], kind="stable")

    prev_ts = long.groupby(["wallet", "market"])["timestamp"].shift(1)
    delta = long["timestamp"] - prev_ts
    threshold = pd.Timedelta(hours=max_hold_hours)
    rapid_mask = delta.notna() & (delta <= threshold)

    counts = long.loc[rapid_mask].groupby("wallet").size()
    counts.name = "rapid_cycles"
    return counts


def initialize_scores(
    trades_df: pd.DataFrame,
    max_hold_hours: float = MAX_HOLD_HOURS_DEFAULT,
) -> dict[str, float]:
    """Compute Stage 1 suspicion scores for all wallets in trades_df."""
    stats = _wallet_stats(trades_df, max_hold_hours=max_hold_hours)
    if stats.empty:
        return {}

    turnover = (stats["rapid_cycles"] / stats["num_trades"].clip(lower=1)).clip(upper=1.0)
    pnl_vol_ratio = stats["pnl_proxy"].abs() / stats["volume"].clip(lower=1)
    pnl_suspicion = (1.0 - (pnl_vol_ratio * 100).clip(upper=1.0)).clip(lower=0.0)

    score = 0.5 * turnover + 0.5 * pnl_suspicion
    return {wallet: float(s) for wallet, s in score.items()}


def build_trade_graph(
    trades_df: pd.DataFrame,
    restrict_to: set[str] | None = None,
) -> nx.Graph:
    """Build an undirected weighted counterparty graph.

    Nodes are wallet addresses. Each edge carries a ``volume`` attribute
    equal to the sum of notional (``size * price``) across all trades
    between the two wallets (order-insensitive).

    When ``restrict_to`` is supplied, only trades whose both participants
    lie in that set are used, producing the Stage 3 suspected-wallet
    subgraph. Wallets with no qualifying edges become isolated and are
    omitted (consistent with ``nx.connected_components`` semantics).
    """
    g: nx.Graph = nx.Graph()
    if trades_df.empty:
        return g

    working = trades_df.assign(notional=trades_df["size"] * trades_df["price"])
    if restrict_to is not None:
        working = working[working["maker"].isin(restrict_to) & working["taker"].isin(restrict_to)]
    # Drop accidental self-loops — no information in wallet trading with itself.
    working = working[working["maker"] != working["taker"]]
    if working.empty:
        return g

    # Canonicalize each pair so (a, b) and (b, a) aggregate together.
    pair = working[["maker", "taker"]]
    lo = pair.min(axis=1)
    hi = pair.max(axis=1)
    edges = (
        working.assign(w_lo=lo, w_hi=hi).groupby(["w_lo", "w_hi"])["notional"].sum().reset_index()
    )

    for row in edges.itertuples(index=False):
        g.add_edge(row.w_lo, row.w_hi, volume=float(row.notional))
    return g


def iterate_scores(
    scores: dict[str, float],
    graph: nx.Graph,
    n_iterations: int = 3,
) -> dict[str, float]:
    """Sirolly Stage 2: redistribute scores along volume-weighted edges.

    Each iteration replaces every wallet's score with the volume-weighted
    average of its neighbors' scores:

        new[w] = Σ_cp volume(w, cp) * old[cp] / Σ_cp volume(w, cp)

    Wallets with no edges (isolates, or those present in ``scores`` but
    not in ``graph``) keep their score unchanged. Wallets that appear
    in ``graph`` but not in ``scores`` start at 0.0.

    Implemented with bincount-based edge aggregation — O((E + N) * k)
    time, O(E + N) memory — so the full ~14 % wash population of
    ~1.26 M Polymarket wallets is tractable without scipy.sparse.
    """
    if n_iterations < 0:
        raise ValueError("n_iterations must be >= 0")

    result = dict(scores)
    if n_iterations == 0 or graph.number_of_edges() == 0:
        return result

    nodes: list[str] = list(graph.nodes())
    idx = {w: i for i, w in enumerate(nodes)}
    n = len(nodes)

    # Flatten undirected edges into two directed rows each so bincount
    # aggregates both directions.
    us: list[int] = []
    vs: list[int] = []
    ws: list[float] = []
    for u, v, data in graph.edges(data=True):
        volume = float(data.get("volume", 0.0))
        iu, iv = idx[u], idx[v]
        us.append(iu)
        vs.append(iv)
        ws.append(volume)
        us.append(iv)
        vs.append(iu)
        ws.append(volume)

    u_arr = np.asarray(us, dtype=np.int64)
    v_arr = np.asarray(vs, dtype=np.int64)
    w_arr = np.asarray(ws, dtype=np.float64)

    row_sums = np.bincount(u_arr, weights=w_arr, minlength=n)
    score_vec = np.asarray([result.get(node, 0.0) for node in nodes], dtype=np.float64)

    for _ in range(n_iterations):
        weighted = w_arr * score_vec[v_arr]
        neighbor_sum = np.bincount(u_arr, weights=weighted, minlength=n)
        # Isolates inside `graph` would have row_sums == 0; preserve their score.
        denom = np.where(row_sums > 0, row_sums, 1.0)
        updated = neighbor_sum / denom
        score_vec = np.where(row_sums > 0, updated, score_vec)

    for i, node in enumerate(nodes):
        result[node] = float(score_vec[i])
    return result
