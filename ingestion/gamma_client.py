"""Client for Polymarket's Gamma Markets API.

Field mapping against the raw Gamma response is documented in
``research/research-log.md`` (entry dated 2026-04-24). Only the fields
needed by our ``markets`` schema and the ingestion pipeline are modeled;
everything else from the API is ignored via ``extra="ignore"`` so
upstream additions don't break us.

Public API:

- :class:`GammaMarket` — typed Pydantic v2 model for one market row.
- :func:`fetch_markets_page` — one HTTP request, returns list of models.
- :func:`iter_markets` — paginate through all markets as an iterator.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from config.settings import get_settings

GAMMA_MARKETS_PATH = "/markets"
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500
REQUEST_TIMEOUT_SECONDS = 30.0


class GammaMarket(BaseModel):
    """One market as returned by Gamma ``/markets``.

    Only fields we persist (or use to decide what to persist) are typed.
    The raw response carries many more; they are silently dropped.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    condition_id: str = Field(alias="conditionId")
    question: str
    slug: str | None = None
    description: str | None = None

    # Lifecycle dates (all ISO-8601 from the API).
    created_at: datetime | None = Field(default=None, alias="createdAt")
    start_date: datetime | None = Field(default=None, alias="startDate")
    end_date: datetime | None = Field(default=None, alias="endDate")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")

    # Status flags.
    active: bool = False
    closed: bool = False
    archived: bool = False

    # Volume / liquidity — prefer the numeric aliases over the string fields.
    volume_num: float | None = Field(default=None, alias="volumeNum")
    liquidity_num: float | None = Field(default=None, alias="liquidityNum")

    # Outcomes arrive as JSON-encoded strings, e.g. '["Yes", "No"]'.
    # We expose them as already-parsed Python lists.
    outcomes: list[str] = Field(default_factory=list)
    outcome_prices: list[float] = Field(default_factory=list, alias="outcomePrices")

    # Present only on resolved markets. Field names vary by API version;
    # we accept both spellings via aliases and fall back to None.
    resolved_at: datetime | None = Field(default=None, alias="resolvedAt")
    resolved_outcome: str | None = Field(default=None, alias="resolvedOutcome")

    # Multi-option scalar markets use negRisk; regular YES/NO markets don't.
    # We keep the flag so the ingestion layer can filter if desired.
    neg_risk: bool = Field(default=False, alias="negRisk")

    # CLOB ERC-1155 token IDs (one per outcome, position-aligned with
    # ``outcomes``). Needed to map on-chain trade asset IDs back to our
    # (condition_id, outcome) pair.
    clob_token_ids: list[str] = Field(default_factory=list, alias="clobTokenIds")

    @property
    def yes_token_id(self) -> str | None:
        return self._token_id_for("Yes")

    @property
    def no_token_id(self) -> str | None:
        return self._token_id_for("No")

    def _token_id_for(self, outcome_label: str) -> str | None:
        for label, token_id in zip(self.outcomes, self.clob_token_ids, strict=False):
            if label.strip().lower() == outcome_label.strip().lower():
                return token_id
        return None

    @field_validator("outcomes", mode="before")
    @classmethod
    def _parse_outcomes(cls, value: Any) -> Any:
        return _maybe_parse_json_list(value)

    @field_validator("outcome_prices", mode="before")
    @classmethod
    def _parse_outcome_prices(cls, value: Any) -> Any:
        parsed = _maybe_parse_json_list(value)
        if isinstance(parsed, list):
            return [float(x) for x in parsed]
        return parsed

    @field_validator("clob_token_ids", mode="before")
    @classmethod
    def _parse_clob_token_ids(cls, value: Any) -> Any:
        parsed = _maybe_parse_json_list(value)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
        return parsed


def _maybe_parse_json_list(value: Any) -> Any:
    """Gamma encodes list fields as JSON strings. Decode if it looks like one."""
    if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def fetch_markets_page(
    *,
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
    closed: bool | None = None,
    client: httpx.Client | None = None,
    base_url: str | None = None,
) -> list[GammaMarket]:
    """Fetch a single page of markets from Gamma.

    ``closed=None`` returns both open and closed markets; pass an
    explicit bool to filter. Supply ``client`` to reuse a session across
    calls (see :func:`iter_markets`).
    """
    if limit < 1 or limit > MAX_PAGE_SIZE:
        raise ValueError(f"limit must be in [1, {MAX_PAGE_SIZE}]")

    params: dict[str, str | int] = {"limit": limit, "offset": offset}
    if closed is not None:
        params["closed"] = "true" if closed else "false"

    url = (base_url or str(get_settings().polymarket_gamma_api)).rstrip("/") + GAMMA_MARKETS_PATH

    owns_client = client is None
    active_client = client if client is not None else httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        resp = active_client.get(url, params=params)
        resp.raise_for_status()
        payload = resp.json()
    finally:
        if owns_client:
            active_client.close()

    if not isinstance(payload, list):
        raise ValueError(f"expected list payload, got {type(payload).__name__}")

    return [GammaMarket.model_validate(item) for item in payload]


def iter_markets(
    *,
    closed: bool | None = None,
    page_size: int = DEFAULT_PAGE_SIZE,
    max_markets: int | None = None,
    start_offset: int = 0,
    base_url: str | None = None,
    client: httpx.Client | None = None,
) -> Iterator[GammaMarket]:
    """Paginate through Gamma markets, yielding one model per row.

    Stops on the first empty/short page or when ``max_markets`` is reached.
    Reuses a single HTTP connection across pages unless ``client`` is supplied.
    """
    url_base = base_url or str(get_settings().polymarket_gamma_api)
    yielded = 0
    offset = start_offset

    owns_client = client is None
    active_client = client if client is not None else httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        while True:
            batch = fetch_markets_page(
                offset=offset,
                limit=page_size,
                closed=closed,
                client=active_client,
                base_url=url_base,
            )
            if not batch:
                return

            for market in batch:
                yield market
                yielded += 1
                if max_markets is not None and yielded >= max_markets:
                    return

            offset += len(batch)
            if len(batch) < page_size:
                return
    finally:
        if owns_client:
            active_client.close()
