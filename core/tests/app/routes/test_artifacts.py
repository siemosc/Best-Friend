"""Контрактные тесты POST /internal/artifacts (trusted-create эндпоинт)."""

import base64
from typing import Any, cast
from uuid import uuid4

from fastapi.testclient import TestClient
import orjson

from bestfiend.app.http import create_app
from bestfiend.artifacts.errors import ArtifactNotFoundError
from bestfiend.artifacts.service import ArtifactService
from bestfiend.artifacts.settings import ArtifactSettings


class InMemoryObjectStorage:
    """In-memory объектный стаб (ключ → байты) для контрактных тестов эндпоинта."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

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

    def object_exists(self, key: str) -> bool:
        """Проверяет наличие объекта."""
        return key in self.objects

    def delete_object(self, key: str) -> bool:
        """Удаляет объект."""
        self.objects.pop(key, None)
        return True

    def ensure_bucket(self) -> None:
        """No-op для стаба."""


class _ArtifactsRuntimeStub:
    """Узкий стаб ArtifactsRuntime: только .service."""

    def __init__(self, service: ArtifactService) -> None:
        self.service = service


class _RuntimeStub:
    """Узкий runtime-стаб под artifacts-router: только .artifacts_runtime."""

    def __init__(self, service: ArtifactService) -> None:
        self.artifacts_runtime = _ArtifactsRuntimeStub(service)


def _build(
    *, max_payload_size_mb: int = 25
) -> tuple[ArtifactService, InMemoryObjectStorage]:
    """Реальный ArtifactService с in-memory стораджем (контракт прогоняется целиком)."""
    store = InMemoryObjectStorage()
    settings = ArtifactSettings().model_copy(  # pyright: ignore[reportCallIssue]
        update={"artifact_max_payload_size_mb": max_payload_size_mb}
    )
    service = ArtifactService(settings=settings, storage=store)
    return service, store


def _client(service: ArtifactService) -> TestClient:
    """TestClient с core-приложением и внедрённым runtime-стабом."""
    return TestClient(create_app(cast(Any, _RuntimeStub(service))))


def _headers(user_id: Any = None) -> dict[str, str]:
    """Internal user-id header (валидный UUID по умолчанию)."""
    return {"x-bestfiend-user-id": str(user_id or uuid4())}


def _b64(data: bytes) -> str:
    """base64-кодирует payload."""
    return base64.b64encode(data).decode("ascii")


def test_create_minimal_body_uses_default_art_source() -> None:
    """Минимум payload_b64+filename → 200; art_source дефолтится в 'sandbox'."""
    service, store = _build()
    with _client(service) as client:
        response = client.post(
            "/internal/artifacts",
            json={"payload_b64": _b64(b"hello"), "filename": "report.txt"},
            headers=_headers(),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "document"  # выведено из .txt
    assert body["artifact_user_name"] == "report.txt"
    assert body["storage_key"].endswith("/data")
    assert store.objects[body["storage_key"]] == b"hello"
    prefix = body["storage_key"].removesuffix("data")
    meta = orjson.loads(store.objects[f"{prefix}meta.json"])
    assert meta["art_source"] == "sandbox"


def test_create_explicit_art_source_normalized_in_meta() -> None:
    """Явный art_source режется до сервиса и пишется в meta.json."""
    service, store = _build()
    with _client(service) as client:
        response = client.post(
            "/internal/artifacts",
            json={
                "payload_b64": _b64(b"x"),
                "filename": "a.txt",
                "art_source": "web_search:google",
            },
            headers=_headers(),
        )

    assert response.status_code == 200
    prefix = response.json()["storage_key"].removesuffix("data")
    meta = orjson.loads(store.objects[f"{prefix}meta.json"])
    assert meta["art_source"] == "web_search"


def test_create_explicit_type_respected() -> None:
    """Явный type не перебивается выводом из расширения."""
    service, _ = _build()
    with _client(service) as client:
        response = client.post(
            "/internal/artifacts",
            json={"payload_b64": _b64(b"x"), "filename": "a.txt", "type": "image"},
            headers=_headers(),
        )

    assert response.status_code == 200
    assert response.json()["type"] == "image"


def test_create_invalid_base64_returns_400() -> None:
    """Некорректный base64 → 400 ARTIFACT_INVALID_REQUEST."""
    service, _ = _build()
    with _client(service) as client:
        response = client.post(
            "/internal/artifacts",
            json={"payload_b64": "not-base64-content!!!", "filename": "a.txt"},
            headers=_headers(),
        )

    assert response.status_code == 400
    assert response.json()["error_code"] == "ARTIFACT_INVALID_REQUEST"


def test_create_missing_filename_returns_422() -> None:
    """Отсутствие обязательного filename → 422 (pydantic-валидация ингресса)."""
    service, _ = _build()
    with _client(service) as client:
        response = client.post(
            "/internal/artifacts",
            json={"payload_b64": _b64(b"x")},
            headers=_headers(),
        )

    assert response.status_code == 422


def test_create_missing_user_id_header_returns_400() -> None:
    """Отсутствие user_id-header → 400 ARTIFACT_INVALID_REQUEST."""
    service, _ = _build()
    with _client(service) as client:
        response = client.post(
            "/internal/artifacts",
            json={"payload_b64": _b64(b"x"), "filename": "a.txt"},
        )

    assert response.status_code == 400
    assert response.json()["error_code"] == "ARTIFACT_INVALID_REQUEST"


def test_create_without_request_id_header_succeeds() -> None:
    """request_id больше не требуется: только user_id-header → 200."""
    service, _ = _build()
    with _client(service) as client:
        response = client.post(
            "/internal/artifacts",
            json={"payload_b64": _b64(b"x"), "filename": "a.txt"},
            headers={"x-bestfiend-user-id": str(uuid4())},
        )

    assert response.status_code == 200


def test_create_bad_user_id_header_returns_400() -> None:
    """user_id-header не-UUID → 400 ARTIFACT_INVALID_REQUEST (не 500)."""
    service, _ = _build()
    with _client(service) as client:
        response = client.post(
            "/internal/artifacts",
            json={"payload_b64": _b64(b"x"), "filename": "a.txt"},
            headers={"x-bestfiend-user-id": "not-a-uuid"},
        )

    assert response.status_code == 400
    assert response.json()["error_code"] == "ARTIFACT_INVALID_REQUEST"


def test_create_payload_too_large_returns_413() -> None:
    """Payload сверх лимита → 413 ARTIFACT_TOO_LARGE."""
    service, _ = _build(max_payload_size_mb=0)
    with _client(service) as client:
        response = client.post(
            "/internal/artifacts",
            json={"payload_b64": _b64(b"x"), "filename": "a.txt"},
            headers=_headers(),
        )

    assert response.status_code == 413
    assert response.json()["error_code"] == "ARTIFACT_TOO_LARGE"
