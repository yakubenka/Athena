# Athena

Polymarket smart-money intelligence and copy-trading system.

Finds wallets with persistent edge, filters wash-trading clusters, and
auto-copies their trades.

**Status:** early scaffold (v0.0). Not functional yet.

## Source of truth

All strategy, methodology and milestones live in
[`research/roadmap-v1.3.md`](research/roadmap-v1.3.md).
The roadmap is backed by four peer-reviewed papers
(Akey 2026, Becker 2026, Sirolly 2025, Reichenbach & Walther 2025).

## Stack

- Python 3.12
- [uv](https://docs.astral.sh/uv/) for dependency management
- Postgres 16 (local via Docker, prod on Railway)
- `ruff` + `mypy` + `pytest`

## Quickstart

Work in progress. Commands will be filled in as the scaffold is built:

```bash
# 1. Install uv:  https://docs.astral.sh/uv/
# 2. Sync deps:   uv sync
# 3. Start DB:    docker compose up -d
# 4. Apply schema: (TBD)
# 5. Run tests:   uv run pytest
```

## Layout

```
ingestion/     — market & trade ingestion from Polymarket
metrics/       — PnL, Akey/Becker features, Reichenbach consistency
wash_detector/ — Sirolly network-based wash detection
monitoring/    — real-time signal detection
alerts/        — Telegram notifications
copy_trader/   — execution (future Prometheus integration)
dashboard/     — live PnL and signals UI
tests/
research/      — roadmap, logs, notes
```
