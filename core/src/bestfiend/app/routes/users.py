"""HTTP-маршруты профилей пользователей: чтение, самоправка, админский PATCH."""

from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from bestfiend.app.routes.dependencies import (
    get_runtime,
    require_admin,
    require_session,
)
from bestfiend.app.routes.user_views import (
    UserResponse,
    profile_payload,
    profile_response,
)
from bestfiend.control_plane.users.models import UserProfile, UserRole, UserStatus


_DISCORD_MAX_LEN = 64
_TIMEZONE_MAX_LEN = 64
_CITY_COUNTRY_MAX_LEN = 128


class UpdateUserRequest(BaseModel):
    """Админский PATCH /users/{user_id}: любое поле опционально."""

    model_config = ConfigDict(extra="forbid")

    role: UserRole | None = None
    status: UserStatus | None = None
    discord_user_id: str | None = Field(default=None, max_length=_DISCORD_MAX_LEN)


class UpdateProfileRequest(BaseModel):
    """Тело PATCH /users/me.

    Все поля опциональны. Для `city` и `country` явный `null` означает
    очистку (SET NULL). `timezone` не может быть `null` (колонка NOT NULL).
    """

    model_config = ConfigDict(extra="forbid")

    timezone: str | None = Field(
        default=None, min_length=1, max_length=_TIMEZONE_MAX_LEN
    )
    city: str | None = Field(default=None, max_length=_CITY_COUNTRY_MAX_LEN)
    country: str | None = Field(default=None, max_length=_CITY_COUNTRY_MAX_LEN)

    @model_validator(mode="before")
    @classmethod
    def _reject_explicit_null_timezone(cls, data: Any) -> Any:
        if isinstance(data, dict) and "timezone" in data and data["timezone"] is None:
            raise ValueError(
                "timezone cannot be null; omit the field to keep current value"
            )
        return data

    @field_validator("timezone")
    @classmethod
    def _validate_iana_timezone(cls, value: str | None) -> str | None:
        # Downstream сервисы вызывают ZoneInfo(timezone) — невалидные значения
        # ловим на границе, чтобы не сломать runtime.
        if value is None:
            return value
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {value!r}") from exc
        return value


def create_users_router() -> APIRouter:
    """Создаёт маршруты чтения и изменения профилей."""
    router = APIRouter()

    @router.get("/users", response_model=list[UserResponse])
    async def list_users(
        request: Request,
        _admin: UserProfile = Depends(require_admin),
    ) -> JSONResponse:
        runtime = get_runtime(request)
        profiles = await runtime.user_service.list_users()
        return JSONResponse(content=[profile_payload(profile) for profile in profiles])

    @router.patch("/users/me", response_model=UserResponse)
    async def update_my_profile(
        payload: UpdateProfileRequest,
        request: Request,
        current_user: UserProfile = Depends(require_session),
    ) -> JSONResponse:
        runtime = get_runtime(request)
        profile = await runtime.user_service.update_own_profile(
            current_user.user_id,
            payload.model_dump(exclude_unset=True),
        )
        return profile_response(profile)

    @router.patch("/users/{user_id}", response_model=UserResponse)
    async def update_user(
        user_id: UUID,
        payload: UpdateUserRequest,
        request: Request,
        current_admin: UserProfile = Depends(require_admin),
    ) -> JSONResponse:
        runtime = get_runtime(request)
        profile = await runtime.user_service.update_profile(
            user_id,
            role=payload.role,
            status=payload.status,
            discord_user_id=payload.discord_user_id,
            current_user_id=current_admin.user_id,
        )
        return profile_response(profile)

    return router
