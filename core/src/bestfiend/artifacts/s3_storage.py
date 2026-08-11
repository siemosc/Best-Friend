"""S3-объектный storage backend для артефактов (SeaweedFS / S3-совместимый).

Методы синхронные (boto3); вызывающий оборачивает их в `asyncio.to_thread`,
чтобы не блокировать event loop.
"""

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from bestfiend.artifacts.errors import (
    ArtifactNotFoundError,
    ArtifactStorageUnavailableError,
)
from bestfiend.artifacts.settings import ArtifactSettings


_NOT_FOUND_CODES = frozenset({"404", "NoSuchKey", "NoSuchBucket"})
_BUCKET_EXISTS_CODES = frozenset({"BucketAlreadyOwnedByYou", "BucketAlreadyExists"})


class S3ArtifactStorage:
    """Объектный backend поверх S3-совместимого хранилища (SeaweedFS)."""

    __slots__ = ("_bucket", "_client")

    def __init__(self, settings: ArtifactSettings) -> None:
        self._bucket = settings.artifact_s3_bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.artifact_s3_endpoint_url,
            aws_access_key_id=settings.artifact_s3_access_key,
            aws_secret_access_key=settings.artifact_s3_secret_key,
            region_name=settings.artifact_s3_region,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            ),
        )

    def put_object(
        self, key: str, body: bytes, content_type: str | None = None
    ) -> None:
        """Кладёт объект по ключу (overwrite-семантика)."""
        kwargs: dict[str, object] = {"Bucket": self._bucket, "Key": key, "Body": body}
        if content_type:
            kwargs["ContentType"] = content_type
        try:
            self._client.put_object(**kwargs)
        except (ClientError, BotoCoreError) as exc:
            raise ArtifactStorageUnavailableError(
                f"Failed to put object '{key}': {exc}"
            ) from exc

    def get_object(self, key: str) -> bytes:
        """Читает байты объекта по ключу."""
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
            return response["Body"].read()
        except ClientError as exc:
            if _client_error_code(exc) in _NOT_FOUND_CODES:
                raise ArtifactNotFoundError(
                    f"Artifact object not found by key: {key}"
                ) from exc
            raise ArtifactStorageUnavailableError(
                f"Failed to get object '{key}': {exc}"
            ) from exc
        except BotoCoreError as exc:
            raise ArtifactStorageUnavailableError(
                f"Failed to get object '{key}': {exc}"
            ) from exc

    def ensure_bucket(self) -> None:
        """Идемпотентно создаёт bucket артефактов."""
        try:
            self._client.create_bucket(Bucket=self._bucket)
        except ClientError as exc:
            if _client_error_code(exc) in _BUCKET_EXISTS_CODES:
                return
            raise ArtifactStorageUnavailableError(
                f"Failed to ensure bucket '{self._bucket}': {exc}"
            ) from exc
        except BotoCoreError as exc:
            raise ArtifactStorageUnavailableError(
                f"Failed to ensure bucket '{self._bucket}': {exc}"
            ) from exc


def _client_error_code(exc: ClientError) -> str:
    """Извлекает S3 error code из ClientError."""
    return str(exc.response.get("Error", {}).get("Code", ""))
