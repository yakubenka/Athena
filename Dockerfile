# Athena production image — runs the ingestion or monitor service on Railway.
# Single image, two services pick a different start command via Railway settings.

FROM python:3.12-slim

# uv: fast resolver + lockfile-aware installer
COPY --from=ghcr.io/astral-sh/uv:0.5.5 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Install deps first so Docker layer cache survives source changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

# Tests aren't run in the container; uv install of the project itself.
RUN uv sync --frozen --no-dev

# Default command runs the ingestion watcher in watchlist-only mode.
# Override via Railway's "Start Command" for the monitor service.
CMD ["uv", "run", "python", "-u", "-m", "ingestion.trades", "--watch", "--chunk-size", "100", "--only-watchlist"]
