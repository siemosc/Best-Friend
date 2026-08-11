"""PostgreSQL repository для таблицы models."""

from typing import Any

import asyncpg
import orjson

from bestfiend.control_plane.db import ControlPlaneDatabaseClient
from bestfiend.control_plane.model_registry.errors import (
    ModelNotFoundError,
    ModelRegistryError,
)
from bestfiend.control_plane.model_registry.models import ModelConfigRecord


_COLUMNS = "id, name, config, created_at, updated_at"


class ModelConfigRepository:
    """CRUD для таблицы models."""

    __slots__ = ("_db",)

    def __init__(self, db_client: ControlPlaneDatabaseClient) -> None:
        self._db = db_client

    async def get_by_id(self, model_id: str) -> ModelConfigRecord:
        """Возвращает конфиг модели или бросает ModelNotFoundError."""
        query = f"SELECT {_COLUMNS} FROM models WHERE id = $1"  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
        try:
            row = await self._db.fetch_one(query, model_id)
        except asyncpg.PostgresError as exc:
            raise ModelRegistryError(f"Failed to fetch model id={model_id}") from exc
        if row is None:
            raise ModelNotFoundError(f"Model '{model_id}' not found")
        return _row_to_record(row)


def _row_to_record(row: Any) -> ModelConfigRecord:
    raw_config = row["config"]
    config = orjson.loads(raw_config) if isinstance(raw_config, str) else raw_config
    return ModelConfigRecord(
        id=row["id"],
        name=row["name"],
        config=config,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
