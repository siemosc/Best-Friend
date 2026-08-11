"""Исходящее представление профиля пользователя и его сериализация.

Общее для users- и auth-маршрутов: оба отдают наружу один и тот же профиль.
"""

from datetime import datetime
from uuid import UUID

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from bestfiend.control_plane.users.models import UserProfile, UserRole, UserStatus


class UserResponse(BaseModel):
    """Исходящая модель пользователя (без password_hash)."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    role: UserRole
    status: UserStatus
    telegram_chat_id: int | None
    discord_user_id: str | None
    login: str | None
    timezone: str
    city: str | None
    country: str | None
    created_at: datetime
    updated_at: datetime | None


def profile_payload(profile: UserProfile) -> dict[str, object]:
    """Преобразует доменный профиль в JSON-совместимый payload."""
    return UserResponse(
        user_id=profile.user_id,
        role=profile.role,
        status=profile.status,
        telegram_chat_id=profile.telegram_chat_id,
        discord_user_id=profile.discord_user_id,
        login=profile.login,
        timezone=profile.timezone,
        city=profile.city,
        country=profile.country,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
    ).model_dump(mode="json")


def profile_response(profile: UserProfile) -> JSONResponse:
    """Создаёт JSON-ответ с профилем пользователя."""
    return JSONResponse(content=profile_payload(profile))
