# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**Athena** is a Polymarket smart-money intelligence and copy-trading pipeline. Status is early scaffold (v0.0) — pieces work end-to-end but several columns (realised PnL, resolution-dependent metrics) are still placeholder proxies.

The strategy, methodology, and milestones are all in `research/roadmap-v1.3.md` — that file is the **source of truth**. Four peer-reviewed papers drive the design: Akey 2026, Becker 2026, Sirolly 2025, Reichenbach & Walther 2025. Each metric module's docstring cites the paper it implements.

## Common commands

```bash
uv sync                          # install deps (including dev group)
uv sync --no-dev                 # prod-style install (matches Dockerfile)

docker compose up -d             # start local Postgres 16
uv run python -m ingestion.apply_schema   # create / update tables

uv run pytest                    # full test suite
uv run pytest tests/test_metrics_akey.py  # single file
uv run pytest -k akey            # by name pattern
uv run pytest -x                 # stop on first failure

uv run ruff check .              # lint
uv run ruff format .             # format
uv run mypy .                    # strict type-check (configured in pyproject.toml)
uv run pre-commit run --all-files
```

### Pipeline entry points

Each top-level package has a `__main__` so it runs via `python -m`. Stages are designed to be run independently and idempotently — re-running upserts on conflict, never duplicates.

```bash
# 1. Markets metadata (Gamma API). --from-trades refreshes resolution for
#    markets we already have trades for, bypassing Gamma's pagination cap.
uv run python -m ingestion.markets --limit 100
uv run python -m ingestion.markets --from-trades --only-unresolved

# 2. Trades backfill from on-chain OrderFilled events. Resumable: each run
#    starts at MAX(block_number) + 1 in the trades table.
uv run python -m ingestion.trades --max-blocks 50000   # smoke test
uv run python -m ingestion.trades --watch              # tail head of chain
uv run python -m ingestion.trades --watch --only-watchlist  # prod mode

# 3. Wash detection (Sirolly). --save persists clusters + wallet flags.
uv run python -m wash_detector --limit 500000          # smoke test
uv run python -m wash_detector --save                  # full run

# 4. Metrics (Akey + Becker + HHI + Reichenbach). --save upserts wallets +
#    wallet_metrics rows.
uv run python -m metrics --limit 500000
uv run python -m metrics --save

# 5. Monitor: find new watchlist trades, write signals, alert.
uv run python -m monitoring --since auto --telegram --push-prometheus
bash scripts/run_monitor_loop.sh   # cron-friendly while-true wrapper
```

## Architecture

Athena is a five-stage Postgres-centred pipeline. Every stage reads from and writes to the `athena` database; there is no in-memory passing between stages and no orchestrator. The DB schema (`ingestion/schema.sql`) is the contract between them.

```
Gamma API ──► ingestion.markets ──► markets table
Polygon RPC ─► ingestion.trades  ──► trades table  (resumable, watch-mode)
                                       │
                                       ▼
                   wash_detector ──► wash_clusters, wallets.wash_*
                                       │
                                       ▼
                   metrics       ──► wallets (aggregates) + wallet_metrics
                                       │
                                       ▼  (humans curate)
                                   watchlist  (copy_enabled = TRUE)
                                       │
                                       ▼
                   monitoring    ──► signals  ──► alerts (Telegram)
                                                ──► prometheus_bridge (POST /internal/push)
```

### Notable cross-cutting design choices

- **PnL is a proxy until resolutions land.** `wash_detector.sirolly` and `metrics.consistency` both compute `pnl ≈ maker_notional − taker_notional`. This will be replaced with realised PnL once resolution data is wired into the `trades` table. Don't add silent zeros for resolution-dependent columns — leave them NULL.
- **Two CTFExchange generations must both be ingested.** Polymarket runs v1 (`OrderFilled` with maker/takerAssetId; one side is `0` = USDC) and v2 (single `tokenId` + `side` flag) concurrently. `ingestion/orderfilled.py` handles both topic0 hashes and four contract addresses. Don't drop v1 — there's still residual activity.
- **Block timestamps are estimated, not fetched.** `make_block_timestamp_estimator` calibrates from a single `eth_getBlockByNumber("latest")` call, then uses a 2.1 s/block average for everything else. Accurate within minutes — fine for monthly buckets. Don't add per-block RPC calls.
- **Adaptive log fetching.** `_fetch_logs_with_adaptive_split` recursively halves the block range when the RPC rejects "too many results" (Alchemy caps `getLogs` at ~10k results). Recurses on errors that look size-related; size-related errors and rate-limit/transient 5xx are handled differently — see `polygon_client.RETRYABLE_STATUS_CODES`.
- **`--only-watchlist` flips ingestion into prod mode.** Cloud deployments only persist trades touching a watchlist address. RPC traffic is unchanged (we read every log), but DB writes drop by ~99%. Locally, omit the flag so the trades table accumulates the full history for backtesting/metrics.
- **Wash cluster IDs are deterministic.** `_cluster_id` in `wash_detector/__main__.py` is `sha1(sorted_members)[:12]`, so the same wallet set always produces the same ID across runs — safe to re-run `--save` without orphaning prior rows.
- **Signals are deduped via a JOIN, not a marker column.** `monitoring` re-runs are safe because `FETCH_NEW_SIGNALS_SQL` left-joins `signals` and filters `s.id IS NULL`. Don't add a "processed" flag.
- **`signal_type` follows the BUY/SELL of the watched wallet.** BUY → `'entry'` (open copy); SELL → `'exit'` (close copy). `source` is always `'athena'` so Prometheus dashboards can filter by origin.
- **Prometheus + Telegram are both optional.** If credentials aren't set, `push_smart_money` and `send_text` are no-ops. The codebase must keep working in standalone mode — don't make them mandatory dependencies.
- **Empty env vars are treated as unset.** `config/settings.py` uses `env_ignore_empty=True` plus a `_empty_string_is_none` validator. `FOO=` in `.env` doesn't shadow a default; placeholder lines from `.env.example` are safe.

### Database schema notes

- `schema.sql` is idempotent — `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` for newer columns. Always add new columns as `IF NOT EXISTS` ALTERs at the bottom of their section rather than rewriting the original CREATE.
- `trades.block_number` (added after initial backfill) is the resume cursor — `MAX(block_number) + 1`. Don't rely on `id` or `timestamp` for cursoring.
- `markets.yes_token_id` / `no_token_id` are the bridge from on-chain asset IDs to (condition_id, outcome). They're CTF uint256 values, stored as TEXT to preserve precision. `load_token_index` builds the lookup map at start of every backfill.

### Deployment

One Docker image, two Railway services that differ only by start command:

- **ingestion service** — `uv run python -m ingestion.trades --watch --chunk-size 100 --only-watchlist` (the Dockerfile default).
- **monitor service** — `bash scripts/run_monitor_loop.sh` (the `Procfile` defines both).

Local seeding for a fresh Railway DB: `bash scripts/export_minimal.sh` dumps everything **except** the bulky `trades` and `signals` tables to `athena-minimal.sql`. Import with `psql "<DATABASE_PUBLIC_URL>" < athena-minimal.sql`, then let the cloud ingestion service backfill the watchlist-only trade slice from scratch.

## Conventions

- **Python 3.12, strict mypy.** All modules use `from __future__ import annotations`. Public functions get full type hints; `Any` is fine for `psycopg` row tuples and JSON-RPC blobs but avoid elsewhere.
- **Ruff selects E/F/W/I/N/UP/B/SIM/RUF.** Line length 100.
- **CLI argparse pattern.** Each `__main__.py` builds an argparser, exposes `--limit` for smoke runs, and prints a progress + final-summary line. Match this when adding new entry points.
- **DB access pattern.** `with connect() as conn, conn.cursor() as cur: ...` — psycopg's context manager commits on success / rolls back on exception. Per-batch commits inside long loops are intentional so a flake on the last page doesn't lose hours of work.
- **No new files unless necessary.** Prefer extending the existing module in the right package (`ingestion/`, `metrics/`, `wash_detector/`, `monitoring/`, `alerts/`, `prometheus_bridge/`).
- **Tests live in `tests/` mirroring the package name** (`test_metrics_akey.py` ↔ `metrics/akey.py`). Use `wash_detector.fixtures` (`organic_trades`, `wash_cluster`) for synthetic trade data — don't roll your own.
- **Commit messages follow conventional-commits prefixes** in use across history: `feat(<scope>):`, `fix(<scope>):`, `chore(<scope>):`. Scope is the package name.
