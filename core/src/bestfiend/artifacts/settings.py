"""Настройки сервиса artifacts."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ArtifactSettings(BaseSettings):
    """Конфигурация S3-стораджа (SeaweedFS) для артефактов."""

    artifact_s3_endpoint_url: str = Field(
        "http://localhost:8333",
        validation_alias="ARTIFACT_S3_ENDPOINT_URL",
    )
    artifact_s3_access_key: str = Field(
        "bestfiend",
        validation_alias="ARTIFACT_S3_ACCESS_KEY",
    )
    artifact_s3_secret_key: str = Field(
        "changeme",
        validation_alias="ARTIFACT_S3_SECRET_KEY",
    )
    artifact_s3_bucket: str = Field(
        "artifacts",
        validation_alias="ARTIFACT_S3_BUCKET",
    )
    artifact_s3_region: str = Field(
        "us-east-1",
        validation_alias="ARTIFACT_S3_REGION",
    )
    artifact_max_payload_size_mb: int = Field(
        25,
        validation_alias="ARTIFACT_MAX_PAYLOAD_SIZE_MB",
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
