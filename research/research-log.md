# Research Log

Chronological notes on hypothesis checks, data surprises, and decisions
that shape the codebase. Most recent entry on top.

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
