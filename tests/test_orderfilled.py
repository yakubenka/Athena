"""Tests for ingestion.orderfilled — OrderFilled event decoder."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from ingestion.orderfilled import (
    CTF_EXCHANGE_ADDRESS,
    ORDER_FILLED_TOPIC0,
    DecodedTrade,
    decode_order_filled_log,
)


def _address_topic(addr: str) -> str:
    stripped = addr.lower().removeprefix("0x")
    return "0x" + "0" * (64 - len(stripped)) + stripped


def _uint256(value: int) -> str:
    return f"{value:064x}"


def _log(
    *,
    order_hash: str = "0x" + "ab" * 32,
    maker: str = "0x1111111111111111111111111111111111111111",
    taker: str = "0x2222222222222222222222222222222222222222",
    maker_asset_id: int = 0,
    taker_asset_id: int = 12345,
    maker_amount: int = 2_500_000,  # 2.5 USDC
    taker_amount: int = 5_000_000,  # 5 outcome tokens
    fee: int = 0,
    tx_hash: str = "0x" + "cd" * 32,
    log_index: int = 4,
    block_number: int = 50_000_000,
) -> dict[str, Any]:
    data = "0x" + "".join(
        _uint256(v)
        for v in (
            maker_asset_id,
            taker_asset_id,
            maker_amount,
            taker_amount,
            fee,
        )
    )
    return {
        "topics": [
            ORDER_FILLED_TOPIC0,
            order_hash,
            _address_topic(maker),
            _address_topic(taker),
        ],
        "data": data,
        "transactionHash": tx_hash,
        "logIndex": hex(log_index),
        "blockNumber": hex(block_number),
    }


def test_decode_taker_sell_sets_outcome_token_and_price() -> None:
    # maker bought YES for USDC → takerAssetId != 0 (maker received YES tokens)
    trade = decode_order_filled_log(
        _log(maker_asset_id=0, taker_asset_id=999, maker_amount=2_500_000, taker_amount=5_000_000)
    )
    assert isinstance(trade, DecodedTrade)
    assert trade.taker_side == "SELL"
    assert trade.outcome_token_id == "999"
    assert trade.size == Decimal("5.000000")
    assert trade.usdc_amount == Decimal("2.500000")
    assert trade.price == Decimal("0.500000")


def test_decode_taker_buy_flips_semantics() -> None:
    # maker sold YES → makerAssetId != 0 (maker gave YES tokens for USDC)
    trade = decode_order_filled_log(
        _log(maker_asset_id=777, taker_asset_id=0, maker_amount=5_000_000, taker_amount=2_500_000)
    )
    assert isinstance(trade, DecodedTrade)
    assert trade.taker_side == "BUY"
    assert trade.outcome_token_id == "777"
    assert trade.size == Decimal("5.000000")
    assert trade.usdc_amount == Decimal("2.500000")
    assert trade.price == Decimal("0.500000")


def test_decode_skips_internal_counterparty() -> None:
    trade = decode_order_filled_log(
        _log(
            maker=CTF_EXCHANGE_ADDRESS,
            taker="0x2222222222222222222222222222222222222222",
        )
    )
    assert trade is None

    trade = decode_order_filled_log(
        _log(
            maker="0x1111111111111111111111111111111111111111",
            taker=CTF_EXCHANGE_ADDRESS,
        )
    )
    assert trade is None


def test_decode_skips_self_trade() -> None:
    addr = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    trade = decode_order_filled_log(_log(maker=addr, taker=addr))
    assert trade is None


def test_decode_skips_token_for_token_swap() -> None:
    # Both asset IDs non-zero — neg-risk conversion or similar, not a
    # real cash-for-outcome trade in our model.
    trade = decode_order_filled_log(_log(maker_asset_id=111, taker_asset_id=222))
    assert trade is None


def test_decode_rejects_bad_topic_count() -> None:
    bad = _log()
    bad["topics"] = bad["topics"][:3]  # drop the taker topic
    with pytest.raises(ValueError):
        decode_order_filled_log(bad)


def test_decode_rejects_bad_data_length() -> None:
    bad = _log()
    bad["data"] = "0x" + "00" * 100  # too short
    with pytest.raises(ValueError):
        decode_order_filled_log(bad)


def test_decoded_addresses_are_lowercased() -> None:
    trade = decode_order_filled_log(
        _log(
            maker="0xABCDef0000000000000000000000000000000001",
            taker="0x0000000000000000000000000000000000000002",
        )
    )
    assert trade is not None
    assert trade.maker_address == "0xabcdef0000000000000000000000000000000001"
    assert trade.taker_address == "0x0000000000000000000000000000000000000002"
