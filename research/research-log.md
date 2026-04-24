# Research Log

Chronological notes on hypothesis checks, data surprises, and decisions
that shape the codebase. Most recent entry on top.

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
