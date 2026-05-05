"""Decode Polymarket CTFExchange ``OrderFilled`` event logs.

Polymarket runs **two generations** of the exchange contract concurrently;
both have to be ingested to capture the full trade flow.

**v1** — original CTFExchange / NegRiskCTFExchange (deployed 2022-2024)::

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

topic0 ``0xd0a08e8c...``. Asset id ``0`` = USDC; non-zero = outcome ERC-1155
token id. Taker direction comes from which side carries the zero.

**v2** — CTFExchangeV2 / NegRiskCTFExchangeV2 (live since early 2026,
mostly all current activity)::

    OrderFilled(
        bytes32 indexed orderHash,
        address indexed maker,
        address indexed taker,
        uint8 side,                  // maker's side: 0 = BUY, 1 = SELL
        uint256 tokenId,             // outcome token id (single, not pair)
        uint256 makerAmountFilled,
        uint256 takerAmountFilled,
        uint256 fee,
        bytes32 builder,
        bytes32 metadata
    )

topic0 ``0xd543adfd...``. The ``side`` flag tells us directly which leg is
the maker's; we no longer need to infer it from a zero asset id.

In both versions outcome tokens and USDC use 6 decimals, so the schema's
``NUMERIC(20, 6)`` fits naturally.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

# v1 contracts (mostly historical now)
CTF_EXCHANGE_ADDRESS_V1 = "0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e"
NEG_RISK_CTF_EXCHANGE_ADDRESS_V1 = "0xc5d563a36ae78145c45a50134d48a1215220f80a"
ORDER_FILLED_TOPIC0_V1 = "0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6"

# v2 contracts (current activity)
CTF_EXCHANGE_ADDRESS_V2 = "0xe111180000d2663c0091e4f400237545b87b996b"
NEG_RISK_CTF_EXCHANGE_ADDRESS_V2 = "0xe2222d279d744050d28e00520010520000310f59"
ORDER_FILLED_TOPIC0_V2 = "0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"

# Backwards-compatible aliases — older code paths import the un-suffixed
# names. Default to v1 here so v1 backfill helpers keep working unchanged.
CTF_EXCHANGE_ADDRESS = CTF_EXCHANGE_ADDRESS_V1
ORDER_FILLED_TOPIC0 = ORDER_FILLED_TOPIC0_V1

# Skip events where one side is an exchange contract — Polymarket emits
# those for internal bookkeeping (e.g. neg-risk rebalancing) and they'd
# confuse our maker/taker model.
INTERNAL_ADDRESSES: frozenset[str] = frozenset(
    {
        CTF_EXCHANGE_ADDRESS_V1.lower(),
        NEG_RISK_CTF_EXCHANGE_ADDRESS_V1.lower(),
        CTF_EXCHANGE_ADDRESS_V2.lower(),
        NEG_RISK_CTF_EXCHANGE_ADDRESS_V2.lower(),
    }
)

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
    """Decode any OrderFilled log, dispatching on topic0 to the right version."""
    topics: list[str] = log["topics"]
    if not topics:
        raise ValueError("OrderFilled log has no topics")
    topic0 = topics[0].lower()
    if topic0 == ORDER_FILLED_TOPIC0_V1:
        return _decode_v1(log)
    if topic0 == ORDER_FILLED_TOPIC0_V2:
        return _decode_v2(log)
    raise ValueError(f"unknown OrderFilled topic0: {topic0}")


def _decode_v1(log: dict[str, Any]) -> DecodedTrade | None:
    topics: list[str] = log["topics"]
    if len(topics) < 4:
        raise ValueError(f"v1 OrderFilled expects 4 topics, got {len(topics)}")

    maker = _topic_to_address(topics[2])
    taker = _topic_to_address(topics[3])
    if maker in INTERNAL_ADDRESSES or taker in INTERNAL_ADDRESSES or maker == taker:
        return None

    data_hex = _strip0x(log["data"])
    if len(data_hex) != 5 * 64:
        raise ValueError(f"v1 OrderFilled data must be 5 words, got {len(data_hex) // 64}")

    maker_asset_id = int(data_hex[0:64], 16)
    taker_asset_id = int(data_hex[64:128], 16)
    maker_amount = int(data_hex[128:192], 16)
    taker_amount = int(data_hex[192:256], 16)
    fee_raw = int(data_hex[256:320], 16)

    if taker_asset_id == 0 and maker_asset_id != 0:
        outcome_token_id = maker_asset_id
        taker_side = "BUY"
        outcome_amount = maker_amount
        usdc_amount = taker_amount
    elif maker_asset_id == 0 and taker_asset_id != 0:
        outcome_token_id = taker_asset_id
        taker_side = "SELL"
        outcome_amount = taker_amount
        usdc_amount = maker_amount
    else:
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


def _decode_v2(log: dict[str, Any]) -> DecodedTrade | None:
    """Decode the v2 ``OrderFilled`` event.

    v2 has 7 non-indexed slots: side, tokenId, makerAmountFilled,
    takerAmountFilled, fee, builder, metadata. ``side`` is the maker's
    order side (``0`` = BUY, ``1`` = SELL); the taker is on the opposite
    side. When the maker is buying, the maker's filled amount is USDC and
    the taker's filled amount is outcome tokens; when selling, vice versa.
    """
    topics: list[str] = log["topics"]
    if len(topics) < 4:
        raise ValueError(f"v2 OrderFilled expects 4 topics, got {len(topics)}")

    maker = _topic_to_address(topics[2])
    taker = _topic_to_address(topics[3])
    if maker in INTERNAL_ADDRESSES or taker in INTERNAL_ADDRESSES or maker == taker:
        return None

    data_hex = _strip0x(log["data"])
    if len(data_hex) != 7 * 64:
        raise ValueError(f"v2 OrderFilled data must be 7 words, got {len(data_hex) // 64}")

    maker_side = int(data_hex[0:64], 16)
    token_id = int(data_hex[64:128], 16)
    maker_amount = int(data_hex[128:192], 16)
    taker_amount = int(data_hex[192:256], 16)
    fee_raw = int(data_hex[256:320], 16)
    # slots 5 and 6 are builder + metadata; we don't persist either yet.

    if token_id == 0:
        # No real outcome token in this match — skip (defensive guard).
        return None

    if maker_side == 0:
        # Maker is buying → maker pays USDC, taker delivers tokens.
        usdc_amount = maker_amount
        outcome_amount = taker_amount
        taker_side = "SELL"
    elif maker_side == 1:
        # Maker is selling → maker delivers tokens, taker pays USDC.
        usdc_amount = taker_amount
        outcome_amount = maker_amount
        taker_side = "BUY"
    else:
        raise ValueError(f"v2 OrderFilled side must be 0 or 1, got {maker_side}")

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
        outcome_token_id=str(token_id),
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
