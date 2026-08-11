"""Доменные модели model_registry."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ModelConfigRecord(BaseModel):
    """Строка таблицы models."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime
