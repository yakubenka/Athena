"""Tiny Telegram Bot API wrapper for outbound alerts.

Setup (one-time):

1. Talk to ``@BotFather`` on Telegram, ``/newbot`` → get a token
2. Start a chat with the bot, send any message
3. Open ``https://api.telegram.org/bot<TOKEN>/getUpdates`` → copy ``chat.id``
4. Add to ``.env``::

    TELEGRAM_BOT_TOKEN=123456:ABC...
    TELEGRAM_CHAT_ID=123456789
"""

from __future__ import annotations

from typing import Any

import httpx

from config.settings import get_settings

API_BASE = "https://api.telegram.org"
REQUEST_TIMEOUT_SECONDS = 10.0


def send_text(
    message: str,
    *,
    parse_mode: str | None = "Markdown",
    client: httpx.Client | None = None,
) -> None:
    """POST a message to the configured chat. No-op if Telegram isn't configured."""
    settings = get_settings()
    if settings.telegram_bot_token is None or settings.telegram_chat_id is None:
        # Telegram intentionally optional — running without it is a valid mode.
        return

    url = f"{API_BASE}/bot{settings.telegram_bot_token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": settings.telegram_chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode

    owns = client is None
    active = client if client is not None else httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        resp = active.post(url, json=payload)
        resp.raise_for_status()
    finally:
        if owns:
            active.close()


def send_signal(
    *,
    wallet: str,
    wallet_tier: str | None,
    market_question: str,
    outcome: str,
    side: str,
    price: float,
    size: float,
    trade_timestamp: object,
    client: httpx.Client | None = None,
) -> None:
    """Format a single watchlist trade as a Telegram alert and send it."""
    short = wallet[:10]
    tier = wallet_tier or "?"
    notional = size * price
    text = (
        f"*Athena signal* — tier {tier}\n"
        f"`{short}`  {side} *{outcome}* @ {price:.4f}\n"
        f"size = {size:,.1f}  (≈ ${notional:,.0f})\n"
        f"market: _{market_question[:120]}_\n"
        f"trade time: `{trade_timestamp}`"
    )
    send_text(text, client=client)
