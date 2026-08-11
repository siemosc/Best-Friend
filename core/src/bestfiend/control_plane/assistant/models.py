"""Доменные модели assistant-конфигов для control_plane."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UserAssistantConfigRecord(BaseModel):
    """Запись user_assistant_configs — per-user настройки ассистента.

    user_instruction — единая инструкция (NOT NULL DEFAULT '').
    llm_custom_config — свободный jsonb по структуре models.config; непустой =
    полная замена дефолтной модели графа (NOT NULL DEFAULT '{}').
    """

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    user_instruction: str
    llm_custom_config: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime
