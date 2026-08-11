"""Доменные модели среза control_plane (users + session)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


UserRole = Literal["user", "admin"]
UserStatus = Literal["pending", "active", "banned"]


class UserProfile(BaseModel):
    """Профиль пользователя (outbound-модель без password_hash)."""

    user_id: UUID
    role: UserRole = "user"
    status: UserStatus = "pending"
    telegram_chat_id: int | None = None
    discord_user_id: str | None = None
    login: str | None = None
    timezone: str = Field(default="Europe/Belgrade")
    city: str | None = None
    country: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
