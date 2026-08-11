"""Сервисный слой artifacts service."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import PurePosixPath
import re
from typing import TypeVar
from uuid import UUID

import orjson
from uuid6 import uuid7

from bestfiend.artifacts.contracts import (
    ArtifactStorageBackend,
    ArtifactStoredMetadata,
    CreateArtifactRequest,
)
from bestfiend.artifacts.errors import (
    ArtifactInvalidRequestError,
    ArtifactTooLargeError,
    ArtifactUnsupportedTypeError,
)
from bestfiend.artifacts.settings import ArtifactSettings
from bestfiend.contracts.artifacts import ArtifactRef


class ArtifactService:
    """Сервис артефактов: создание (create / create_from_raw) и чтение поверх S3-стораджа."""

    __slots__ = ("_settings", "_storage")

    def __init__(
        self,
        *,
        settings: ArtifactSettings,
        storage: ArtifactStorageBackend,
    ) -> None:
        self._settings = settings
        self._storage = storage

    async def create(self, request: CreateArtifactRequest) -> ArtifactRef:
        """Создаёт артефакт (объекты `data` + `meta.json`) и возвращает ссылку.

        Атомарность — через порядок PUT: сначала байты, затем `meta.json` как
        commit-маркер «артефакт готов».
        """
        art_source = _normalize_art_source(request.art_source)
        artifact_type = _normalize_type(request.type)
        description = (request.description or "").strip()
        filename = _require_non_empty_str(request.filename, field_name="filename")
        payload_bytes = _ensure_payload_within_limit(request, self._settings)

        artifact_id = str(uuid7())
        prefix = f"{request.user_id}/{artifact_id}/"

        metadata = ArtifactStoredMetadata(
            filename=filename,
            type=artifact_type,
            description=description,
            art_source=art_source,
            user_id=request.user_id,
            created_at=datetime.now(UTC),
            art_meta=request.art_meta,
        )
        meta_body = orjson.dumps(metadata.model_dump(mode="json"))

        await _run_storage_io(self._storage.put_object, f"{prefix}data", payload_bytes)
        await _run_storage_io(
            self._storage.put_object,
            f"{prefix}meta.json",
            meta_body,
            "application/json",
        )

        return ArtifactRef(
            artifact_id=artifact_id,
            type=artifact_type,
            artifact_user_name=filename,
            description=description,
            storage_key=f"{prefix}data",
            art_meta=request.art_meta,
        )

    async def create_from_raw(
        self,
        *,
        user_id: UUID,
        art_source: str,
        filename: str,
        payload: bytes,
    ) -> ArtifactRef:
        """Создаёт артефакт из сырых байтов файла: без LLM, type по расширению."""
        create_request = CreateArtifactRequest(
            user_id=user_id,
            art_source=art_source,
            type=infer_artifact_type(filename),
            description=f"Файл '{filename}', размер {len(payload)} байт",
            filename=filename,
            payload_bytes=payload,
        )
        return await self.create(create_request)

    async def read_bytes(self, storage_key: str) -> bytes:
        """Читает байты артефакта по полному storage_key ({user_id}/{artifact_id}/data)."""
        return await _run_storage_io(self._storage.get_object, storage_key)

    async def read_bytes_for_user(self, user_id: UUID, artifact_id: str) -> bytes:
        """Читает байты артефакта по (user_id, artifact_id).

        Ключ строится здесь из user_id сессии — тем же f-string, что и в create
        (UUID → канонический str). Хранёному storage_key не доверяем (защита
        от подмены чужого ключа в истории).
        """
        return await self.read_bytes(f"{user_id}/{artifact_id}/data")


_EXTENSION_TO_TYPE: dict[str, str] = {
    ".txt": "document",
    ".md": "document",
    ".pdf": "document",
    ".docx": "document",
    ".doc": "document",
    ".rtf": "document",
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".gif": "image",
    ".webp": "image",
    ".bmp": "image",
    ".svg": "image",
    ".mp3": "audio",
    ".wav": "audio",
    ".ogg": "audio",
    ".flac": "audio",
    ".m4a": "audio",
    ".aac": "audio",
    ".mp4": "video",
    ".mov": "video",
    ".avi": "video",
    ".mkv": "video",
    ".webm": "video",
    ".zip": "archive",
    ".tar": "archive",
    ".gz": "archive",
    ".rar": "archive",
    ".7z": "archive",
    ".json": "table",
    ".csv": "table",
    ".tsv": "table",
    ".xlsx": "table",
    ".xls": "table",
    ".py": "code",
    ".js": "code",
    ".ts": "code",
    ".tsx": "code",
    ".jsx": "code",
    ".go": "code",
    ".rs": "code",
    ".java": "code",
    ".c": "code",
    ".cpp": "code",
    ".h": "code",
    ".hpp": "code",
    ".sql": "code",
    ".sh": "code",
    ".html": "code",
    ".css": "code",
    ".yaml": "code",
    ".yml": "code",
    ".toml": "code",
}
_IMAGE_MIME_BY_EXTENSION = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def infer_artifact_type(filename: str) -> str:
    """Определяет тип артефакта по расширению filename."""
    extension = PurePosixPath(filename).suffix.lower()
    return _EXTENSION_TO_TYPE.get(extension, "binary")


def image_mime_type(filename: str) -> str | None:
    """Mime-тип картинки по расширению filename; None — расширение не картиночное."""
    extension = PurePosixPath(filename).suffix.lower()
    return _IMAGE_MIME_BY_EXTENSION.get(extension)


_SUPPORTED_ARTIFACT_TYPES = frozenset(
    {
        "image",
        "document",
        "table",
        "code",
        "archive",
        "audio",
        "video",
        "binary",
        "other",
    }
)
_ART_SOURCE_SAFE_CHARS = re.compile(r"[^a-z0-9_-]")
_WHITESPACE = re.compile(r"\s+")


def _require_non_empty_str(value: str, *, field_name: str) -> str:
    """Проверяет обязательные строковые аргументы сервиса."""
    normalized_value = (value or "").strip()
    if not normalized_value:
        raise ArtifactInvalidRequestError(f"Field '{field_name}' must be non-empty.")
    return normalized_value


def _normalize_type(raw_type: str) -> str:
    """Нормализует и проверяет тип артефакта."""
    normalized_type = _require_non_empty_str(raw_type, field_name="type").lower()
    if normalized_type not in _SUPPORTED_ARTIFACT_TYPES:
        raise ArtifactUnsupportedTypeError(
            f"Unsupported artifact type: '{normalized_type}'."
        )
    return normalized_type


def _normalize_art_source(raw_source: str) -> str:
    """Режет art_source до имени сервиса (часть до ':') и слагует для meta.json."""
    service = _require_non_empty_str(raw_source, field_name="art_source").split(":", 1)[
        0
    ]
    slug = _WHITESPACE.sub("-", service.strip().lower())
    slug = _ART_SOURCE_SAFE_CHARS.sub("", slug).strip("-_")
    if not slug:
        raise ArtifactInvalidRequestError(
            "Field 'art_source' is empty after normalization."
        )
    return slug


def _ensure_payload_within_limit(
    request: CreateArtifactRequest,
    settings: ArtifactSettings,
) -> bytes:
    """Проверяет лимит размера payload и возвращает bytes."""
    payload_bytes = request.payload_bytes
    max_payload_size_bytes = settings.artifact_max_payload_size_mb * 1024 * 1024
    if len(payload_bytes) > max_payload_size_bytes:
        raise ArtifactTooLargeError(
            "Artifact payload size exceeds ARTIFACT_MAX_PAYLOAD_SIZE_MB limit."
        )
    return payload_bytes


_StorageResultT = TypeVar("_StorageResultT")


async def _run_storage_io(
    operation: Callable[..., _StorageResultT],
    /,
    *args: object,
) -> _StorageResultT:
    """Выполняет blocking storage I/O вне event loop."""
    return await asyncio.to_thread(operation, *args)
