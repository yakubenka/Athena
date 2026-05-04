"""Watchlist monitor — emit signals on new trades by copy-enabled wallets.

Reads from the local trades table (kept fresh by ``ingestion.trades``) and
writes one row to ``signals`` per new trade made by a wallet flagged
``copy_enabled = TRUE``. Designed to run on a cron — picks up where the
previous run left off via ``signals.detected_at``.

CLI::

    uv run python -m monitoring                      # incremental from last run
    uv run python -m monitoring --since 1h           # last hour of trades
    uv run python -m monitoring --dry-run            # don't write signals rows
"""
