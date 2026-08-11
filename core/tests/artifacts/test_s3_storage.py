"""Тесты S3ArtifactStorage через botocore Stubber (без сети)."""

import io

from botocore.response import StreamingBody
from botocore.stub import Stubber
import pytest

from bestfiend.artifacts.errors import (
    ArtifactNotFoundError,
    ArtifactStorageUnavailableError,
)
from bestfiend.artifacts.s3_storage import S3ArtifactStorage
from bestfiend.artifacts.settings import ArtifactSettings


def _storage() -> S3ArtifactStorage:
    """S3-backend на дефолтных settings (клиент не ходит в сеть под Stubber)."""
    settings = ArtifactSettings()  # pyright: ignore[reportCallIssue]
    return S3ArtifactStorage(settings=settings)


def test_put_object_success() -> None:
    storage = _storage()
    stubber = Stubber(storage._client)  # noqa: SLF001
    stubber.add_response(
        "put_object", {}, {"Bucket": "artifacts", "Key": "k", "Body": b"data"}
    )
    with stubber:
        storage.put_object("k", b"data")
    stubber.assert_no_pending_responses()


def test_put_object_error_maps_to_unavailable() -> None:
    storage = _storage()
    stubber = Stubber(storage._client)  # noqa: SLF001
    stubber.add_client_error("put_object", service_error_code="InternalError")
    with stubber, pytest.raises(ArtifactStorageUnavailableError):
        storage.put_object("k", b"data")


def test_get_object_returns_bytes() -> None:
    storage = _storage()
    stubber = Stubber(storage._client)  # noqa: SLF001
    payload = b"payload-bytes"
    body = StreamingBody(io.BytesIO(payload), len(payload))
    stubber.add_response(
        "get_object", {"Body": body}, {"Bucket": "artifacts", "Key": "k"}
    )
    with stubber:
        assert storage.get_object("k") == payload


def test_get_object_missing_maps_to_not_found() -> None:
    storage = _storage()
    stubber = Stubber(storage._client)  # noqa: SLF001
    stubber.add_client_error(
        "get_object", service_error_code="NoSuchKey", http_status_code=404
    )
    with stubber, pytest.raises(ArtifactNotFoundError):
        storage.get_object("k")


def test_ensure_bucket_idempotent_on_existing() -> None:
    storage = _storage()
    stubber = Stubber(storage._client)  # noqa: SLF001
    stubber.add_client_error(
        "create_bucket", service_error_code="BucketAlreadyOwnedByYou"
    )
    with stubber:
        storage.ensure_bucket()
    stubber.assert_no_pending_responses()
