"""Tests for ingestion.gamma_client using httpx MockTransport."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from ingestion.gamma_client import (
    GammaMarket,
    fetch_markets_page,
    iter_markets,
)

BASE_URL = "https://gamma-api.example/"


def _sample_market(**overrides: Any) -> dict[str, Any]:
    """Minimal market JSON shaped like the real Gamma response."""
    base: dict[str, Any] = {
        "id": "540816",
        "conditionId": "0x9c1a953fe92c8357f1b646ba25d983aa83e90c525992db14fb726fa895cb5763",
        "question": "Russia-Ukraine Ceasefire before GTA VI?",
        "slug": "russia-ukraine-ceasefire-before-gta-vi-554",
        "description": "Rules text...",
        "createdAt": "2025-05-02T15:03:10.397014Z",
        "startDate": "2025-05-02T15:48:00.174Z",
        "endDate": "2026-07-31T12:00:00Z",
        "updatedAt": "2026-04-24T03:51:14.626715Z",
        "active": True,
        "closed": False,
        "archived": False,
        "volumeNum": 1596802.79,
        "liquidityNum": 48442.48,
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.525", "0.475"]',
        "negRisk": False,
        # Extra fields that must be silently ignored.
        "image": "https://example/img.jpg",
        "questionID": "0xaaaa...",
        "clobTokenIds": '["123", "456"]',
    }
    base.update(overrides)
    return base


def _build_client(responses: list[list[dict[str, Any]]]) -> httpx.Client:
    """Build a Client whose transport returns ``responses`` in order, one per call."""
    calls: list[httpx.Request] = []
    iterator = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        try:
            body = next(iterator)
        except StopIteration:
            body = []
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport, base_url=BASE_URL)
    # Stash calls on the client so tests can introspect them.
    client._captured_calls = calls  # type: ignore[attr-defined]
    return client


def test_market_model_parses_realistic_payload() -> None:
    market = GammaMarket.model_validate(_sample_market())

    assert market.condition_id.startswith("0x9c1a9")
    assert market.question.startswith("Russia-Ukraine")
    assert market.active is True
    assert market.closed is False
    assert market.volume_num == pytest.approx(1596802.79)
    assert market.outcomes == ["Yes", "No"]
    assert market.outcome_prices == pytest.approx([0.525, 0.475])
    assert market.created_at == datetime(2025, 5, 2, 15, 3, 10, 397014, tzinfo=UTC)


def test_resolved_fields_are_optional() -> None:
    market = GammaMarket.model_validate(_sample_market())
    assert market.resolved_at is None
    assert market.resolved_outcome is None


def test_resolved_fields_parse_when_present() -> None:
    payload = _sample_market(
        closed=True,
        resolvedAt="2026-06-01T12:00:00Z",
        resolvedOutcome="Yes",
    )
    market = GammaMarket.model_validate(payload)
    assert market.closed is True
    assert market.resolved_outcome == "Yes"
    assert market.resolved_at == datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


def test_outcomes_fall_back_when_not_json() -> None:
    market = GammaMarket.model_validate(_sample_market(outcomes=["Yes", "No"]))
    assert market.outcomes == ["Yes", "No"]


def test_clob_token_ids_parsed_and_mapped_to_outcomes() -> None:
    market = GammaMarket.model_validate(_sample_market())
    assert market.clob_token_ids == ["123", "456"]
    # outcomes is '["Yes", "No"]' => yes maps to first token, no to second.
    assert market.yes_token_id == "123"
    assert market.no_token_id == "456"


def test_clob_token_ids_missing_gives_none() -> None:
    payload = _sample_market()
    payload.pop("clobTokenIds")
    market = GammaMarket.model_validate(payload)
    assert market.clob_token_ids == []
    assert market.yes_token_id is None
    assert market.no_token_id is None


def test_invalid_limit_rejected() -> None:
    with pytest.raises(ValueError):
        fetch_markets_page(offset=0, limit=0, base_url=BASE_URL)
    with pytest.raises(ValueError):
        fetch_markets_page(offset=0, limit=1000, base_url=BASE_URL)


def test_fetch_page_returns_models() -> None:
    client = _build_client([[_sample_market(), _sample_market(conditionId="0xother")]])
    markets = fetch_markets_page(offset=0, limit=10, client=client, base_url=BASE_URL)
    client.close()

    assert len(markets) == 2
    assert markets[1].condition_id == "0xother"


def test_fetch_page_sends_expected_params() -> None:
    client = _build_client([[_sample_market()]])
    fetch_markets_page(offset=200, limit=50, closed=False, client=client, base_url=BASE_URL)
    client.close()

    calls = client._captured_calls  # type: ignore[attr-defined]
    assert len(calls) == 1
    url = calls[0].url
    assert url.params["offset"] == "200"
    assert url.params["limit"] == "50"
    assert url.params["closed"] == "false"


def test_iter_markets_paginates_until_short_page() -> None:
    responses = [
        [_sample_market(conditionId=f"0xpage1-{i}") for i in range(3)],
        [_sample_market(conditionId=f"0xpage2-{i}") for i in range(3)],
        [_sample_market(conditionId="0xlast")],  # short page => stop
    ]
    client = _build_client(responses)
    markets = list(iter_markets(page_size=3, base_url=BASE_URL, client=client))
    client.close()

    assert len(markets) == 7
    assert [m.condition_id for m in markets[:3]] == [f"0xpage1-{i}" for i in range(3)]


def test_iter_markets_respects_max_markets() -> None:
    responses = [[_sample_market(conditionId=f"0xa-{i}") for i in range(5)]] * 4
    client = _build_client(responses)
    markets = list(iter_markets(page_size=5, max_markets=7, base_url=BASE_URL, client=client))
    client.close()
    assert len(markets) == 7


def test_iter_markets_stops_on_empty_page() -> None:
    responses: list[list[dict[str, Any]]] = [
        [_sample_market(conditionId="0xonly")],
        [],
    ]
    client = _build_client(responses)
    markets = list(iter_markets(page_size=100, base_url=BASE_URL, client=client))
    client.close()
    assert len(markets) == 1
