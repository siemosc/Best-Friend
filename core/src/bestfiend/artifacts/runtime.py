"""Runtime container для artifacts service."""

import asyncio
from dataclasses import dataclass

from bestfiend.artifacts.s3_storage import S3ArtifactStorage
from bestfiend.artifacts.service import ArtifactService
from bestfiend.artifacts.settings import ArtifactSettings


@dataclass(slots=True)
class ArtifactsRuntime:
    """Собранный runtime artifacts service (S3-сторадж, без PG-пула)."""

    service: ArtifactService
    storage: S3ArtifactStorage

    async def start(self) -> None:
        """Идемпотентно создаёт bucket артефактов в SeaweedFS."""
        await asyncio.to_thread(self.storage.ensure_bucket)

    async def stop(self) -> None:
        """Останавливает runtime (boto3-клиент stateless — нечего закрывать)."""


def create_artifacts_runtime() -> ArtifactsRuntime:
    """Собирает runtime artifacts service (без I/O). Для запуска — runtime.start()."""
    # pyright не распознаёт сигнатуру __init__ у pydantic BaseSettings с
    # Field(validation_alias=...) — значения читаются из env / .env / дефолтов.
    artifact_settings = ArtifactSettings()  # pyright: ignore[reportCallIssue]
    storage = S3ArtifactStorage(settings=artifact_settings)
    service = ArtifactService(settings=artifact_settings, storage=storage)
    return ArtifactsRuntime(service=service, storage=storage)
