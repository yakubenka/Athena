"""Settings loading tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.settings import Settings, get_settings


def test_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:5432/db")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert str(s.database_url).startswith("postgresql://")
    assert s.log_level == "DEBUG"
    assert str(s.polymarket_gamma_api).startswith("https://gamma-api.polymarket.com")
    assert s.dune_api_key is None


def test_settings_requires_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()
