"""Decode Polymarket CTFExchange ``OrderFilled`` event logs.

Event signature (topic0 ``0xd0a08e8c...``)::

    OrderFilled(
        bytes32 indexed orderHash,
        address indexed maker,
        address indexed taker,
        uint256 makerAssetId,
        uint256 takerAssetId,
        uint256 makerAmountFilled,
        uint256 takerAmountFilled,
        uint256 fee
    )

Polymarket uses asset id ``0`` for USDC (cash) and a non-zero uint256 for
outcome (ERC-1155) tokens. Exactly one side of every real trade is cash,
so we can tell the taker's direction from which side carries the zero:

- ``takerAssetId == 0`` → taker gave USDC, received outcome tokens → BUY
- ``makerAssetId == 0`` → maker gave USDC, taker gave outcome → SELL

Token amounts are 1e6-scaled on both the USDC and the outcome side,
matching the schema's ``NUMERIC(20, 6)``.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

CTF_EXCHANGE_ADDRESS = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
ORDER_FILLED_TOPIC0 = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"

# Skip events where one side is the exchange itself — Polymarket emits those
# for internal bookkeeping (e.g. neg-risk rebalancing) and they'd confuse
# our maker/taker model.
INTERNAL_ADDRESSES: frozenset[str] = frozenset({CTF_EXCHANGE_ADDRESS.lower()})

_TOKEN_SCALE = Decimal(10**6)  # USDC and Polymarket outcome tokens both use 6 decimals


@dataclass(frozen=True)
class DecodedTrade:
    """One OrderFilled log decoded into our `trades`-table shape."""

    tx_hash: str
    log_index: int
    block_number: int
    maker_address: str
    taker_address: str
    outcome_token_id: str  # uint256 decimal string — lookup key against markets
    taker_side: str  # 'BUY' or 'SELL' from the taker's perspective
    size: Decimal  # outcome-token shares
    price: Decimal  # USDC per share
    usdc_amount: Decimal  # total USDC changed hands
    fee: Decimal


def decode_order_filled_log(log: dict[str, Any]) -> DecodedTrade | None:
    """Decode one log row. Returns None for internal/self-trade events."""
    topics: list[str] = log["topics"]
    if len(topics) < 4:
        raise ValueError(f"OrderFilled expects 4 topics, got {len(topics)}")

    maker = _topic_to_address(topics[2])
    taker = _topic_to_address(topics[3])
    if maker in INTERNAL_ADDRESSES or taker in INTERNAL_ADDRESSES or maker == taker:
        return None

    data_hex = _strip0x(log["data"])
    if len(data_hex) != 5 * 64:
        raise ValueError(f"OrderFilled data must be 5 words, got {len(data_hex) // 64}")

    maker_asset_id = int(data_hex[0:64], 16)
    taker_asset_id = int(data_hex[64:128], 16)
    maker_amount = int(data_hex[128:192], 16)
    taker_amount = int(data_hex[192:256], 16)
    fee_raw = int(data_hex[256:320], 16)

    if taker_asset_id == 0 and maker_asset_id != 0:
        # Taker gave USDC, received outcome tokens.
        outcome_token_id = maker_asset_id
        taker_side = "BUY"
        outcome_amount = maker_amount
        usdc_amount = taker_amount
    elif maker_asset_id == 0 and taker_asset_id != 0:
        # Maker gave USDC, taker gave outcome.
        outcome_token_id = taker_asset_id
        taker_side = "SELL"
        outcome_amount = taker_amount
        usdc_amount = maker_amount
    else:
        # Neither side is cash — token-for-token swap (e.g. neg-risk conversion).
        # Skip; not a countable trade in our model.
        return None

    size = Decimal(outcome_amount) / _TOKEN_SCALE
    if size == 0:
        return None
    usdc = Decimal(usdc_amount) / _TOKEN_SCALE
    price = (usdc / size).quantize(Decimal("0.000001"))

    return DecodedTrade(
        tx_hash=log["transactionHash"],
        log_index=int(log["logIndex"], 16),
        block_number=int(log["blockNumber"], 16),
        maker_address=maker,
        taker_address=taker,
        outcome_token_id=str(outcome_token_id),
        taker_side=taker_side,
        size=size,
        price=price,
        usdc_amount=usdc,
        fee=Decimal(fee_raw) / _TOKEN_SCALE,
    )


def _topic_to_address(topic: str) -> str:
    """Address is the low 20 bytes of a 32-byte topic, lower-cased."""
    return "0x" + _strip0x(topic)[-40:].lower()


def _strip0x(hex_value: str) -> str:
    return hex_value[2:] if hex_value.startswith(("0x", "0X")) else hex_value
