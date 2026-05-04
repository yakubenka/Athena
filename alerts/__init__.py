"""Outbound alert channels (Telegram first, more later).

Public API mirrors the small set of message types the rest of the
codebase needs:

- :func:`send_signal` — formatted watchlist trade alert
- :func:`send_text` — raw passthrough (for debugging / status pings)
"""

from alerts.telegram import send_signal, send_text

__all__ = ["send_signal", "send_text"]
