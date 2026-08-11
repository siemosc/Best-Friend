"""Настройки инфраструктуры core: PostgreSQL + HTTP-сервер + Langfuse."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class CoreDatabaseSettings(BaseSettings):
    """Настройки PostgreSQL для core (schema-owner общей схемы `core`)."""

    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_db: str = "bestfiend"
    postgres_user: str = "bestfiend"
    postgres_password: str = "changeme"
    postgres_pool_min_size: int = 5
    postgres_pool_max_size: int = 20
    # Core — schema-owner: `True` запускает migration runner из app/db.py
    # (применяет core/scripts/migrations/*.sql, legacy auto-seed).
    postgres_auto_migrate: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class TracingSettings(BaseSettings):
    """Настройки Langfuse трейсинга."""

    langfuse_enabled: bool = True
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_base_url: str = "https://cloud.langfuse.com"
    langfuse_flush_interval: int = 2

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class PublicUrlSettings(BaseSettings):
    """Публичный базовый URL приложения (OAuth redirect_uri, фронт-редиректы).

    dev: vite на 5173 проксирует `/api` в core. redirect_uri OAuth-тракта и
    браузерные редиректы callback'а собираются от этого URL.
    """

    public_base_url: str = "http://localhost:5173"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
