"""Тесты create-flow для ArtifactService (S3-сторадж, объектный стаб)."""

from uuid import uuid4

import orjson
import pytest

from bestfiend.artifacts.contracts import CreateArtifactRequest
from bestfiend.artifacts.errors import (
    ArtifactNotFoundError,
    ArtifactTooLargeError,
    ArtifactUnsupportedTypeError,
)
from bestfiend.artifacts.service import ArtifactService, image_mime_type
from bestfiend.artifacts.settings import ArtifactSettings


class InMemoryObjectStorage:
    """In-memory объектный стаб (ключ → байты) для unit-тестов ArtifactService."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.bucket_ensured = 0

    def put_object(
        self, key: str, body: bytes, content_type: str | None = None
    ) -> None:
        """Сохраняет объект по ключу."""
        self.objects[key] = body

    def get_object(self, key: str) -> bytes:
        """Читает объект по ключу."""
        if key not in self.objects:
            raise ArtifactNotFoundError(f"no object: {key}")
        return self.objects[key]

    def ensure_bucket(self) -> None:
        """Считает вызовы ensure_bucket."""
        self.bucket_ensured += 1


def _settings(*, max_payload_size_mb: int = 25) -> ArtifactSettings:
    """Settings с override лимита payload."""
    base = ArtifactSettings()  # pyright: ignore[reportCallIssue]
    return base.model_copy(update={"artifact_max_payload_size_mb": max_payload_size_mb})


def _build_service(
    *,
    storage: InMemoryObjectStorage | None = None,
    max_payload_size_mb: int = 25,
) -> tuple[ArtifactService, InMemoryObjectStorage]:
    """Собирает ArtifactService с in-memory стабом."""
    store = storage or InMemoryObjectStorage()
    service = ArtifactService(
        settings=_settings(max_payload_size_mb=max_payload_size_mb),
        storage=store,
    )
    return service, store


def _request(
    *,
    artifact_type: str = "document",
    description: str = "Report file",
    filename: str = "report.md",
    art_source: str = "web_search:google",
    payload_bytes: bytes = b"test payload",
) -> CreateArtifactRequest:
    """Формирует CreateArtifactRequest для тестов."""
    return CreateArtifactRequest(
        user_id=uuid4(),
        art_source=art_source,
        type=artifact_type,
        description=description,
        filename=filename,
        payload_bytes=payload_bytes,
    )


@pytest.mark.asyncio
async def test_create_writes_data_and_meta() -> None:
    service, store = _build_service()

    ref = await service.create(_request(payload_bytes=b"hello"))

    assert ref.type == "document"
    assert ref.storage_key.endswith("/data")
    assert "/web_search/" not in ref.storage_key  # art_source не в ключе
    assert store.objects[ref.storage_key] == b"hello"
    prefix = ref.storage_key.removesuffix("data")
    meta = orjson.loads(store.objects[f"{prefix}meta.json"])
    assert meta["filename"] == "report.md"
    assert meta["type"] == "document"
    assert meta["art_source"] == "web_search"  # урезано до имени сервиса
    assert ref.artifact_user_name == "report.md"


@pytest.mark.asyncio
async def test_create_keeps_art_source_in_meta_not_in_key() -> None:
    service, store = _build_service()

    ref = await service.create(_request(art_source="agent:notifications"))

    assert "/agent/" not in ref.storage_key  # art_source ушёл из ключа
    prefix = ref.storage_key.removesuffix("data")
    meta = orjson.loads(store.objects[f"{prefix}meta.json"])
    assert meta["art_source"] == "agent"  # урезано до сервиса, живёт в meta.json


@pytest.mark.asyncio
async def test_create_rejects_invalid_type() -> None:
    service, store = _build_service()

    with pytest.raises(ArtifactUnsupportedTypeError):
        await service.create(_request(artifact_type="unknown"))

    assert store.objects == {}


@pytest.mark.asyncio
async def test_create_rejects_payload_over_limit() -> None:
    service, store = _build_service(max_payload_size_mb=0)

    with pytest.raises(ArtifactTooLargeError):
        await service.create(_request(payload_bytes=b"x"))

    assert store.objects == {}


@pytest.mark.asyncio
async def test_create_offloads_storage_io(monkeypatch: pytest.MonkeyPatch) -> None:
    operations: list[str] = []

    async def fake_to_thread(
        operation: object, /, *args: object, **kwargs: object
    ) -> object:
        del kwargs
        assert callable(operation)
        operations.append(getattr(operation, "__name__", "?"))
        return operation(*args)

    monkeypatch.setattr("bestfiend.artifacts.service.asyncio.to_thread", fake_to_thread)
    service, _ = _build_service()

    await service.create(_request(payload_bytes=b"offloaded"))

    assert operations == ["put_object", "put_object"]


@pytest.mark.asyncio
async def test_create_from_raw_bytes_keeps_exact_bytes_and_original_filename() -> None:
    service, store = _build_service()
    raw_bytes = b"\x89PNG\r\n\x1a\n\x00\x01\xff"

    ref = await service.create_from_raw(
        user_id=uuid4(),
        art_source="telegram",
        filename="Отчёт по продажам.png",
        payload=raw_bytes,
    )

    assert ref.type == "image"
    assert (
        ref.artifact_user_name == "Отчёт по продажам.png"
    )  # оригинал, кириллица как есть
    assert store.objects[ref.storage_key] == raw_bytes
    prefix = ref.storage_key.removesuffix("data")
    meta = orjson.loads(store.objects[f"{prefix}meta.json"])
    assert meta["filename"] == "Отчёт по продажам.png"


@pytest.mark.asyncio
async def test_read_bytes_for_user_builds_key_from_user_id() -> None:
    """Ключ чтения строится из (user_id, artifact_id) и совпадает с ключом create."""
    service, _ = _build_service()
    request = _request(
        artifact_type="image",
        filename="photo.jpg",
        payload_bytes=b"jpg-bytes",
    )
    ref = await service.create(request)

    data = await service.read_bytes_for_user(request.user_id, ref.artifact_id)

    assert data == b"jpg-bytes"


def test_image_mime_type_known_and_unknown() -> None:
    """Mime по расширению без учёта регистра; не-картиночное/пустое → None."""
    assert image_mime_type("photo.JPG") == "image/jpeg"
    assert image_mime_type("shot.png") == "image/png"
    assert image_mime_type("anim.webp") == "image/webp"
    assert image_mime_type("meme.gif") == "image/gif"
    assert image_mime_type("doc.pdf") is None
    assert image_mime_type("scan.heic") is None
    assert image_mime_type("noext") is None
