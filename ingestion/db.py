"""Database connection helper. Pulls DSN from Settings."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg

from config.settings import get_settings


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """Yield a psycopg connection using DATABASE_URL from Settings.

    Commits on successful exit, rolls back on exception
    (standard psycopg `with` semantics).
    """
    dsn = str(get_settings().database_url)
    with psycopg.connect(dsn) as conn:
        yield conn
