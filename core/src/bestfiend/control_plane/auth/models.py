"""Доменные модели аутентификации и сессий."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class SessionRecord(BaseModel):
    """Запись sessions — зеркало БД."""

    model_config = ConfigDict(extra="forbid")

    session_id: UUID
    user_id: UUID
    created_at: datetime
    expires_at: datetime


class BindingCodeRecord(BaseModel):
    """Запись auth_binding_codes — зеркало БД."""

    model_config = ConfigDict(extra="forbid")

    code: str
    user_id: UUID
    expires_at: datetime
    created_at: datetime


class AuthCredentials(BaseModel):
    """Учётные данные пользователя для проверки пароля."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    login: str
    password_hash: str
    status: str
