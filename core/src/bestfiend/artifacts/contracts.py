"""Контракты границы artifacts: create-запрос, тело meta.json, error-payload, порт стораджа."""

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field


class CreateArtifactRequest(BaseModel):
    """Контракт trusted-create артефакта."""

    user_id: UUID
    art_source: str = Field(min_length=1)
    type: str = Field(min_length=1)
    description: str = Field(default="", max_length=500)
    filename: str = Field(min_length=1, max_length=255)
    art_meta: dict[str, Any] = Field(default_factory=dict)
    payload_bytes: bytes


class ArtifactStoredMetadata(BaseModel):
    """Тело meta.json — source of truth метаданных артефакта в сторадже."""

    filename: str = Field(min_length=1)
    type: str = Field(min_length=1)
    description: str = Field(default="", max_length=500)
    art_source: str = Field(min_length=1)
    user_id: UUID
    created_at: datetime
    art_meta: dict[str, Any] = Field(default_factory=dict)


class ArtifactErrorResponse(BaseModel):
    """Стабильный error payload internal artifacts boundary."""

    error_code: str = Field(min_length=1)
    detail: str = Field(min_length=1)


class ArtifactStorageBackend(Protocol):
    """Контракт объектного storage backend (S3-семантика, ключ → байты)."""

    def put_object(
        self, key: str, body: bytes, content_type: str | None = None
    ) -> None:
        """Кладёт объект по ключу (overwrite-семантика)."""
        ...

    def get_object(self, key: str) -> bytes:
        """Читает байты объекта по ключу."""
        ...

    def ensure_bucket(self) -> None:
        """Идемпотентно создаёт bucket."""
        ...
