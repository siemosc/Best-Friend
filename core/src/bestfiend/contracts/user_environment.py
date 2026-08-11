"""Кросс-модульный контракт окружения пользователя: timezone + геолокация."""

from pydantic import BaseModel


class UserEnvironment(BaseModel):
    """Окружение пользователя для LLM-промптов и scheduling: timezone, город, страна."""

    timezone: str
    city: str | None = None
    country: str | None = None
