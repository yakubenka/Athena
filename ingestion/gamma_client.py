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
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from config.settings import get_settings

GAMMA_MARKETS_PATH = "/markets"
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500
REQUEST_TIMEOUT_SECONDS = 30.0

# A winning outcome's price in a resolved binary market is approximately 1.0;
# the loser's is approximately 0.0. Floating-point noise from the API
# justifies a small tolerance.
RESOLVED_PRICE_THRESHOLD = 0.99


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

    # Polymarket's Gamma API does NOT actually expose ``resolvedAt`` /
    # ``resolvedOutcome`` keys. The resolution signal lives in:
    # - ``closedTime`` (timestamp the market was closed/resolved)
    # - ``umaResolutionStatus`` ("resolved" once UMA has finalised it)
    # - ``outcomePrices`` (winner has price ~1.0, loser ~0.0)
    # We keep the typed ``resolved_at`` / ``resolved_outcome`` fields as
    # the public surface and derive them in ``_derive_resolution`` below.
    # The aliases stay so a future API revision that adds the explicit
    # keys would Just Work.
    resolved_at: datetime | None = Field(default=None, alias="resolvedAt")
    resolved_outcome: str | None = Field(default=None, alias="resolvedOutcome")

    closed_time: datetime | None = Field(default=None, alias="closedTime")
    uma_resolution_status: str | None = Field(default=None, alias="umaResolutionStatus")

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

    @field_validator("closed_time", mode="before")
    @classmethod
    def _parse_closed_time(cls, value: Any) -> Any:
        """Gamma sends ``closedTime`` as ``"2020-11-02 16:31:01+00"`` —
        space separator, short tz offset — which pydantic refuses. Python's
        ``fromisoformat`` accepts both that and the standard ISO shape
        once we normalise the offset.
        """
        return _parse_loose_datetime(value)

    @model_validator(mode="after")
    def _derive_resolution(self) -> GammaMarket:
        """Backfill ``resolved_at`` / ``resolved_outcome`` from raw Gamma fields.

        Only applied when the market is actually resolved. Voided / refunded
        markets (where every outcome price is ~0) intentionally stay null —
        we don't want to count them as YES or NO wins for PnL purposes.
        """
        if not self.closed:
            return self
        if (
            self.uma_resolution_status is not None
            and self.uma_resolution_status.lower() != "resolved"
        ):
            return self

        if self.resolved_at is None and self.closed_time is not None:
            self.resolved_at = self.closed_time

        if self.resolved_outcome is None and self.outcomes and self.outcome_prices:
            for label, price in zip(self.outcomes, self.outcome_prices, strict=False):
                if price >= RESOLVED_PRICE_THRESHOLD:
                    upper = label.strip().upper()
                    if upper in ("YES", "NO"):
                        self.resolved_outcome = upper
                    break
        return self


def _maybe_parse_json_list(value: Any) -> Any:
    """Gamma encodes list fields as JSON strings. Decode if it looks like one."""
    if isinstance(value, str) and value.startswith("[") and value.endswith("]"):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _parse_loose_datetime(value: Any) -> Any:
    """Best-effort parse for Gamma's loose datetime strings.

    Handles ``"2020-11-02 16:31:01+00"`` (space sep, short tz) by
    expanding the offset to ``"+00:00"`` so ``datetime.fromisoformat``
    can take it. Anything we can't normalise is returned unchanged so
    pydantic's own validator can have its turn (and produce the proper
    error message).
    """
    if not isinstance(value, str):
        return value
    s = value.strip()
    if len(s) >= 3 and s[-3] in ("+", "-") and s[-2:].isdigit():
        s = s + ":00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return value


RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
DEFAULT_RETRIES = 5
RETRY_BASE_DELAY_SEC = 1.0


def fetch_market_by_condition_id(
    condition_id: str,
    *,
    client: httpx.Client | None = None,
    base_url: str | None = None,
    max_retries: int = DEFAULT_RETRIES,
    retry_base_delay: float = RETRY_BASE_DELAY_SEC,
) -> GammaMarket | None:
    """Look up a single market by condition_id.

    Polymarket's batch ``condition_ids=A,B,C`` syntax silently returns 0
    matches, and the array form ``condition_ids[]=A&condition_ids[]=B``
    is ignored entirely. Only the singular form actually filters, so
    we issue one request per ID and let callers parallelise.

    Retries 429/5xx with exponential backoff (1, 2, 4, 8, 16s) so a
    burst of concurrent requests doesn't crash the whole refresh run.
    """
    import time as _time

    url = (base_url or str(get_settings().polymarket_gamma_api)).rstrip("/") + GAMMA_MARKETS_PATH
    owns_client = client is None
    active_client = client if client is not None else httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        resp: httpx.Response | None = None
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                resp = active_client.get(url, params={"condition_ids": condition_id, "limit": 1})
            except httpx.TransportError as exc:
                # Network blip (read timeout, connection reset, DNS) — retry.
                last_exc = exc
                if attempt >= max_retries:
                    raise
                _time.sleep(retry_base_delay * (2**attempt))
                continue
            if resp.status_code == 422:
                return None
            if resp.status_code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                _time.sleep(retry_base_delay * (2**attempt))
                continue
            break
        if resp is None:  # all attempts hit transport errors
            raise RuntimeError(f"gamma transport failure on {condition_id}: {last_exc}")
        resp.raise_for_status()
        payload = resp.json()
    finally:
        if owns_client:
            active_client.close()

    if not isinstance(payload, list) or not payload:
        return None
    return GammaMarket.model_validate(payload[0])


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
        # Polymarket caps offset around ~250k; once you walk past it the
        # API replies 422 instead of an empty page. Treat that the same
        # as "no more rows" so callers can rely on the empty-page sentinel.
        if resp.status_code == 422:
            return []
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
