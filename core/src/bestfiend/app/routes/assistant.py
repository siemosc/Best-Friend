"""HTTP-маршруты пользовательской конфигурации ассистента."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from bestfiend.app.routes.dependencies import get_runtime, require_self_or_admin
from bestfiend.control_plane.assistant.models import UserAssistantConfigRecord
from bestfiend.control_plane.users.models import UserProfile


_INSTRUCTION_MAX_LEN = 8000

_require_self_or_admin = require_self_or_admin()


class UserAssistantConfigResponse(BaseModel):
    """Исходящая модель per-user настроек ассистента."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    user_instruction: str
    llm_custom_config: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime


class UpdateUserAssistantConfigRequest(BaseModel):
    """Тело PATCH /users/{user_id}/assistant-config.

    `llm_custom_config` — свободный jsonb (= models.config), ALLOW-ALL: без
    валидации denied-keys/слотов. Непустой = полная замена дефолтной модели.
    """

    model_config = ConfigDict(extra="forbid")

    user_instruction: str | None = Field(
        default=None,
        max_length=_INSTRUCTION_MAX_LEN,
    )
    llm_custom_config: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_explicit_null_instruction(cls, data: Any) -> Any:
        # user_instruction пишется в NOT NULL колонку — явный null недопустим
        # (omit поля = не менять). llm_custom_config null безопасен (repo → {}).
        if (
            isinstance(data, dict)
            and "user_instruction" in data
            and data["user_instruction"] is None
        ):
            raise ValueError(
                "user_instruction cannot be null; omit the field to keep current value"
            )
        return data


def create_assistant_router() -> APIRouter:
    """Создаёт маршруты пользовательской конфигурации ассистента."""
    router = APIRouter()

    @router.get(
        "/users/{user_id}/assistant-config",
        response_model=UserAssistantConfigResponse,
    )
    async def get_user_assistant_config(
        user_id: UUID,
        request: Request,
        _guard: UserProfile = Depends(_require_self_or_admin),
    ) -> JSONResponse:
        runtime = get_runtime(request)
        await runtime.user_service.get_by_id(user_id)
        service = runtime.assistant_service
        record = await service.get_for_user(user_id)
        return JSONResponse(content=_assistant_config_payload(record))

    @router.patch(
        "/users/{user_id}/assistant-config",
        response_model=UserAssistantConfigResponse,
    )
    async def update_user_assistant_config(
        user_id: UUID,
        payload: UpdateUserAssistantConfigRequest,
        request: Request,
        _guard: UserProfile = Depends(_require_self_or_admin),
    ) -> JSONResponse:
        runtime = get_runtime(request)
        await runtime.user_service.get_by_id(user_id)
        service = runtime.assistant_service
        record = await service.update_for_user(
            user_id,
            **payload.model_dump(exclude_unset=True),
        )
        return JSONResponse(content=_assistant_config_payload(record))

    @router.post(
        "/users/{user_id}/assistant-config/reset",
        response_model=UserAssistantConfigResponse,
    )
    async def reset_user_assistant_config(
        user_id: UUID,
        request: Request,
        _guard: UserProfile = Depends(_require_self_or_admin),
    ) -> JSONResponse:
        runtime = get_runtime(request)
        await runtime.user_service.get_by_id(user_id)
        service = runtime.assistant_service
        record = await service.reset_to_defaults(user_id)
        return JSONResponse(content=_assistant_config_payload(record))

    return router


def _assistant_config_payload(
    record: UserAssistantConfigRecord,
) -> dict[str, object]:
    return UserAssistantConfigResponse(
        user_id=record.user_id,
        user_instruction=record.user_instruction,
        llm_custom_config=record.llm_custom_config,
        updated_at=record.updated_at,
    ).model_dump(mode="json")
