"""Sirolly network-based wash detection (Sirolly et al., Nov 2025).

Public API:

- :func:`run_wash_detection` — full 3-stage pipeline
- :func:`initialize_scores` — Stage 1: per-wallet suspicion from turnover + PnL/volume
- :func:`build_trade_graph` — volume-weighted counterparty graph
- :func:`iterate_scores` — Stage 2: 3 iterations of neighbor redistribution
- :func:`detect_wash_clusters` — Stage 3: connected components of suspects
"""

from wash_detector.sirolly import (
    build_trade_graph,
    detect_wash_clusters,
    initialize_scores,
    iterate_scores,
    run_wash_detection,
)

__all__ = [
    "build_trade_graph",
    "detect_wash_clusters",
    "initialize_scores",
    "iterate_scores",
    "run_wash_detection",
]
