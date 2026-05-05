"""HTTP client for pushing Athena signals into Prometheus.

Prometheus exposes ``POST /internal/push`` (auth: ``X-Bot-Key`` header)
with a ``Push`` body that has an optional ``smart_money`` dict slot.

We send a payload shaped like::

    {
        "smart_money": {
            "updated_at": "2026-05-05T12:34:56+00:00",
            "traders": [
                {
                    "address": "0x...",
                    "tier": "S",
                    "realized_pnl_usd": 92068.0,
                    "win_rate": 0.87,
                    "active_signals": [
                        {
                            "signal_id": 12345,
                            "condition_id": "0x...",
                            "market_question": "Will X happen?",
                            "outcome": "YES",
                            "side": "BUY",                # what *we* should do
                            "price": 0.69,
                            "size": 100.0,
                            "trade_timestamp": "2026-05-05T12:00:00+00:00",
                            "signal_strength": "strong",
                        }
                    ],
                },
                ...
            ],
        }
    }

Prometheus's ``SmartMoneyMonitor.scan()`` can pick this up via
``store_get("smart_money")`` and merge with its internal scan output
before the risk + execute pipeline runs.
"""

from __future__ import annotations

from typing import Any

import httpx

from config.settings import get_settings

REQUEST_TIMEOUT_SECONDS = 10.0
PUSH_PATH = "/internal/push"


def push_smart_money(
    payload: dict[str, Any],
    *,
    client: httpx.Client | None = None,
) -> bool:
    """POST a smart_money payload to Prometheus. Returns True on success.

    No-op (returns False) if Prometheus credentials are not configured —
    callers can stay path-independent of whether the bridge is wired up.
    """
    settings = get_settings()
    if settings.prometheus_api_url is None or settings.prometheus_bot_key is None:
        return False

    url = str(settings.prometheus_api_url).rstrip("/") + PUSH_PATH
    headers = {"X-Bot-Key": settings.prometheus_bot_key}
    body = {"smart_money": payload}

    owns = client is None
    active = client if client is not None else httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        resp = active.post(url, json=body, headers=headers)
        resp.raise_for_status()
    finally:
        if owns:
            active.close()
    return True
