# Research Log

Chronological notes on hypothesis checks, data surprises, and decisions
that shape the codebase. Most recent entry on top.

---

## 2026-04-24 (late) — Trades ingestion routed through on-chain logs

**Discovery:** the Polymarket `data-api.polymarket.com/trades` endpoint
returns **one-sided** records — each row is a single participant's view
of a match, with 100% unique `transactionHash` values across a 100-trade
sample. The counterparty is not exposed there.

For wash detection we need the full `(maker, taker)` pair, so the
project now reads `OrderFilled` events directly from the CTFExchange
contract on Polygon.

**Fixed on-chain facts:**

- Contract: `0x4bfb41d5b3570defd03c39a9a4d8de6bd8b8982e`
- Event topic0: `0xd0a08e8c493f9c94f29311604c9de1b4e8c8d4c06bd0c789af57f2d65bfec0f6`
- ABI:

  ```
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
  ```

- Asset id `0` = USDC; non-zero uint256 = outcome ERC-1155 token id.
- Taker side is derived from which side carries the zero:
  - `takerAssetId == 0` → taker paid USDC → `BUY`
  - `makerAssetId == 0` → taker received USDC → `SELL`
- USDC and Polymarket outcome tokens both have 6 decimals, so
  `size = outcome_amount / 1e6` and `usdc = usdc_amount / 1e6`.

**Events to drop:**

- Self-trades where maker == taker (rare, but present).
- Events where one side is the exchange contract itself (internal
  neg-risk bookkeeping).
- Token-for-token swaps (neither side is cash) — not trades in our model.

**Ingestion design:**

- `ingestion/polygon_client.py` — thin RPC wrapper (eth_blockNumber,
  eth_getLogs, batched eth_getBlockByNumber for timestamps).
- `ingestion/orderfilled.py` — pure decoder from raw log to DecodedTrade.
- `ingestion/trades.py` — orchestrator: resume from
  `MAX(block_number) + 1`, iterate in chunks (default 2k blocks), look
  up `outcome_token_id` against markets, bulk upsert with
  `ON CONFLICT DO NOTHING`.

Schema added `trades.block_number BIGINT` (indexed) as the resume cursor
and `markets.{yes,no}_token_id TEXT` (indexed) to map event asset IDs
back to `(condition_id, outcome)`.

**Status:** code + 21 targeted tests green, ready for first live backfill.

---

## 2026-04-24 — Gamma API shape verified, ingestion unpaused

**Owner ran the probe command from a reachable network** and returned a
full JSON sample for one market. The client is now written and tested
against that exact shape (see `ingestion/gamma_client.py`).

**Confirmed Gamma /markets fields we persist:**

| Gamma field     | Type           | Our `markets` column | Notes                         |
|-----------------|----------------|----------------------|-------------------------------|
| `conditionId`   | str (0x…66)    | `condition_id` (PK)  | direct 1:1                    |
| `question`      | str            | `question`           | direct                        |
| `createdAt`     | ISO datetime   | `created_at`         | timezone-aware                |
| `endDate`       | ISO datetime   | `end_date`           | timezone-aware                |
| `resolvedAt`    | ISO datetime?  | `resolved_at`        | present only on closed markets|
| `resolvedOutcome` | str?         | `resolved_outcome`   | present only on closed markets|
| `volumeNum`     | float          | `total_volume`       | numeric sibling of `volume` string |

**Parsed but not persisted yet:** `slug`, `description`, `active`,
`closed`, `archived`, `outcomes` (JSON-string `'["Yes","No"]'`),
`outcomePrices` (JSON-string of numeric strings), `liquidityNum`,
`negRisk`.

**Gaps / deferred:**

- **No `category` field on the market itself.** Category lives at the
  *event* level (`events[0]`) or via a `/tags` endpoint we haven't
  probed. `markets.category` stays NULL at first backfill. Derive later
  by either (a) joining to `events` endpoint, or (b) keyword matching
  on question text.
- `events[0]` carries a rich nested object (title, slug, image, volume,
  liquidity, context description) that can substitute for category in
  the meantime. Not ingested yet to keep the first backfill simple.
- Multi-option `negRisk` markets use a separate `questionID`; for v1 we
  treat both flavors the same and key on `conditionId`.

**What's in place:**

- `ingestion.gamma_client` — typed Pydantic model, pagination iterator.
- `ingestion.markets` — batched upsert with `ON CONFLICT DO UPDATE`
  that preserves existing resolution data when a later poll omits it.
- CLI: `uv run python -m ingestion.markets --limit N [--closed true|false|all] [--dry-run]`.

Next blocker: trades ingestion (on-chain via Polygon RPC or the CLOB
API). Markets-only backfill is unblocked and can run now.

---

## 2026-04-23 — Gamma API ingestion paused

**Decision:** pause P2.4–P2.7 (Gamma API client + markets ingestion)
until the owner is on a network that can reach
`gamma-api.polymarket.com`.

**Reason:** the current cloud session blocks egress to Polymarket hosts
(`x-deny-reason: host_not_allowed`). Writing the client without any
ability to probe a live response would produce blind code with likely
field mismatches that are only caught on first real run.

**In the meantime:** work shifts to `wash_detector/` (Sirolly network
algorithm) and `metrics/` (PnL engine) — both operate on the `trades`
and `wallets` tables, so they can be developed and tested against
synthetic in-memory data independent of Gamma API.

**TODO on return:**

1. Run a probe: `uv run python -c "import httpx; print(httpx.get('https://gamma-api.polymarket.com/markets', params={'limit': 1, 'closed': 'false'}).json())"`
2. Compare actual fields to the assumptions in `ingestion/gamma_client.py` (when it exists) and fix mapping.
3. Resume P2.4–P2.9.

---

## 2026-04-23 — Phase 1 (Dune validation) deferred

**Decision:** skip Phase 1 Week 1 (Dune Analytics replication of Akey /
Becker / Sirolly / Reichenbach) and move directly to Phase 2 (Gamma API
+ on-chain trades ingestion).

**Rationale:**

- The four underlying papers are peer-reviewed, independent, and
  cross-validate each other (Akey 70.8% loss rate = Reichenbach 70%).
  Treating their findings as a working assumption is acceptable for
  project kickoff.
- Phase 2 infrastructure (markets + trades backfill into local Postgres)
  is required regardless of the Dune replication outcome — Dune is just
  a hosted copy of the same on-chain data.
- Once the backfill is complete, the eight validation queries from the
  roadmap can be run locally via `psql` against our own tables. One
  source of truth, no reliance on Dune's schema.

**Risk accepted:**

- If the hypothesis fails to replicate on fresh data, downstream
  thresholds (Tier S / C / A cutoffs, wash_score threshold) may need
  recalibration. Mitigated by deferring threshold-dependent work
  (Phase 3 classifier) until after on-site replication.

**Gate before Phase 3:** run the eight queries locally and record
results here. Cannot start the classifier until that is done.
