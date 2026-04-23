# Research Log

Chronological notes on hypothesis checks, data surprises, and decisions
that shape the codebase. Most recent entry on top.

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
