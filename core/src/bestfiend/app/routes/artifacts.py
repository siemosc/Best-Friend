"""HTTP route trusted-create артефактов для внешних MCP-работников.

`POST /internal/artifacts`: работник (драйвер — sandbox-сервис) шлёт base64-payload +
имя файла, получает `ArtifactRef`. Путь — trusted `ArtifactService.create()` (явные
метаданные, без LLM-enrichment). Чтение байтов работник делает прямым S3 GET по
`storage_key` — read-ручки тут нет (вне scope).
"""

import base64
import binascii
from typing import Any
from uuid import UUID

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from bestfiend.app.routes.dependencies import get_runtime
from bestfiend.artifacts.contracts import ArtifactErrorResponse, CreateArtifactRequest
from bestfiend.artifacts.errors import ArtifactError, ArtifactInvalidRequestError
from bestfiend.artifacts.service import infer_artifact_type
from bestfiend.contracts.artifacts import ArtifactRef


# Header внутреннего trusted-вызова: UUID юзера-владельца создаваемого артефакта.
INTERNAL_USER_ID_HEADER = "x-bestfiend-user-id"


class ArtifactCreateIngress(BaseModel):
    """Transport-shape тела `POST /internal/artifacts` (base64 payload + метаданные).

    Минимум — `payload_b64` + `filename`; остальное опционально. `art_source` без
    значения дефолтится на `"sandbox"` (главный driver). Сервис принимает уже
    декодированные байты — этот контракт живёт в route-слое, не в `artifacts/`.
    """

    payload_b64: str = Field(min_length=1)
    filename: str = Field(min_length=1, max_length=255)
    art_source: str = Field(default="sandbox", min_length=1)
    type: str | None = Field(default=None, min_length=1)
    description: str = Field(default="", max_length=500)
    art_meta: dict[str, Any] = Field(default_factory=dict)


def create_artifacts_router() -> APIRouter:
    """Возвращает router с POST /internal/artifacts (trusted-create)."""
    router = APIRouter()

    @router.post("/internal/artifacts", response_model=ArtifactRef)
    async def create_artifact(
        payload: ArtifactCreateIngress,
        request: Request,
    ) -> ArtifactRef:
        """Сохраняет артефакт от внешнего работника → ArtifactRef.

        user_id — из internal-header; payload — из тела. Все ошибки входа
        сворачиваются в ArtifactError → стабильный error-контракт.
        """
        service = get_runtime(request).artifacts_runtime.service
        user_id = _extract_user_id(request)
        create_request = CreateArtifactRequest(
            user_id=_parse_user_id(user_id),
            art_source=payload.art_source,
            type=payload.type or infer_artifact_type(payload.filename),
            description=payload.description,
            filename=payload.filename,
            art_meta=payload.art_meta,
            payload_bytes=_decode_payload(payload.payload_b64),
        )
        return await service.create(create_request)

    return router


def register_artifacts_exception_handlers(app: FastAPI) -> None:
    """Маппит доменные ArtifactError в стабильный artifact error-контракт."""

    @app.exception_handler(ArtifactError)
    async def _handle_artifact_error(
        _request: Request,
        exc: ArtifactError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=ArtifactErrorResponse(
                error_code=exc.error_code,
                detail=str(exc),
            ).model_dump(mode="json"),
        )


def _extract_user_id(request: Request) -> str:
    """Достаёт user_id из internal-header; отсутствие/пустой → 400."""
    user_id = request.headers.get(INTERNAL_USER_ID_HEADER, "").strip()
    if not user_id:
        raise ArtifactInvalidRequestError(
            f"Header {INTERNAL_USER_ID_HEADER} is required and must be non-empty."
        )
    return user_id


def _parse_user_id(raw_user_id: str) -> UUID:
    """Парсит user_id из header в UUID; невалидный → 400."""
    try:
        return UUID(raw_user_id)
    except ValueError as exc:
        raise ArtifactInvalidRequestError(
            f"Header x-bestfiend-user-id is not a valid UUID: {raw_user_id!r}"
        ) from exc


def _decode_payload(payload_b64: str) -> bytes:
    """Декодирует base64 payload; невалидный → 400."""
    try:
        return base64.b64decode(payload_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ArtifactInvalidRequestError(
            f"payload_b64 is not valid base64: {exc}"
        ) from exc
