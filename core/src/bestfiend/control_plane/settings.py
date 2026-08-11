"""Env-настройки среза control_plane."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    """Конфигурация bcrypt, TTL и cookie для auth-капабилити."""

    bcrypt_cost: int = Field(12, validation_alias="AUTH_BCRYPT_COST", ge=4, le=14)
    binding_code_ttl_s: int = Field(
        600,
        validation_alias="AUTH_BINDING_CODE_TTL_S",
        gt=0,
    )
    session_ttl_s: int = Field(
        2_592_000,  # 30 дней
        validation_alias="AUTH_SESSION_TTL_S",
        gt=0,
    )
    cookie_name: str = Field("bestfiend_session", validation_alias="AUTH_COOKIE_NAME")
    cookie_secure: bool = Field(False, validation_alias="AUTH_COOKIE_SECURE")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
