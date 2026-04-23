"""Typed application settings loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import HttpUrl, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: PostgresDsn

    polymarket_gamma_api: HttpUrl = HttpUrl("https://gamma-api.polymarket.com")
    polygon_rpc_url: HttpUrl | None = None

    dune_api_key: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    log_level: str = "INFO"

    @field_validator(
        "polygon_rpc_url",
        "dune_api_key",
        "telegram_bot_token",
        "telegram_chat_id",
        mode="before",
    )
    @classmethod
    def _empty_string_is_none(cls, v: Any) -> Any:
        """Treat VAR= in .env (empty string) as unset."""
        if isinstance(v, str) and v.strip() == "":
            return None
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
