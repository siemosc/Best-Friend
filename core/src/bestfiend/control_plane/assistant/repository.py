"""PostgreSQL repository для user_assistant_configs.

Reader + `bootstrap` writer (identity-creation path) + полный writer-набор
(`reset`, `update`) для web-админ-эндпоинтов.
"""

from typing import Any
from uuid import UUID

import asyncpg
import orjson

from bestfiend.control_plane.assistant.errors import (
    AssistantConfigNotFoundError,
    AssistantConfigUnavailableError,
)
from bestfiend.control_plane.assistant.models import UserAssistantConfigRecord
from bestfiend.control_plane.db import ControlPlaneDatabaseClient


_USER_CONFIG_COLUMNS = "user_id, user_instruction, llm_custom_config, updated_at"

_UPDATABLE_FIELDS: frozenset[str] = frozenset({"user_instruction", "llm_custom_config"})


class UserAssistantConfigRepository:
    """Reader + bootstrap + admin writers для user_assistant_configs."""

    __slots__ = ("_db",)

    def __init__(self, db_client: ControlPlaneDatabaseClient) -> None:
        self._db = db_client

    async def get_by_user(
        self,
        user_id: UUID,
    ) -> UserAssistantConfigRecord | None:
        """Возвращает конфиг пользователя или None."""
        query = (
            f"SELECT {_USER_CONFIG_COLUMNS} "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            "FROM user_assistant_configs WHERE user_id = $1"
        )
        try:
            row = await self._db.fetch_one(query, user_id)
        except asyncpg.PostgresError as exc:
            raise AssistantConfigUnavailableError(
                f"Failed to fetch user_assistant_configs user_id={user_id}"
            ) from exc
        return _row_to_user_config(row) if row else None

    async def bootstrap(
        self,
        user_id: UUID,
    ) -> UserAssistantConfigRecord:
        """Создаёт пустую запись пользователя. Идемпотентно (ON CONFLICT DO NOTHING)."""
        query = """
            INSERT INTO user_assistant_configs (user_id)
            VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
        """
        try:
            await self._db.execute(query, user_id)
        except asyncpg.ForeignKeyViolationError as exc:
            raise AssistantConfigUnavailableError(
                f"Foreign key violation: user_id={user_id} does not exist in users"
            ) from exc
        except asyncpg.PostgresError as exc:
            raise AssistantConfigUnavailableError(
                f"Failed to bootstrap user_assistant_configs user_id={user_id}"
            ) from exc
        record = await self.get_by_user(user_id)
        if record is None:
            raise AssistantConfigUnavailableError(
                f"Bootstrap failed: no row after INSERT for user_id={user_id}"
            )
        return record

    async def reset(
        self,
        user_id: UUID,
    ) -> UserAssistantConfigRecord:
        """Обнуляет все instructions и overrides."""
        query = (
            "UPDATE user_assistant_configs SET "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            "user_instruction = '', llm_custom_config = '{}', updated_at = NOW() "
            f"WHERE user_id = $1 RETURNING {_USER_CONFIG_COLUMNS}"
        )
        try:
            row = await self._db.fetch_one(query, user_id)
        except asyncpg.PostgresError as exc:
            raise AssistantConfigUnavailableError(
                f"Failed to reset user_assistant_configs user_id={user_id}"
            ) from exc
        if row is None:
            raise AssistantConfigNotFoundError(
                f"user_assistant_configs user_id={user_id} not found"
            )
        return _row_to_user_config(row)

    async def update(
        self,
        user_id: UUID,
        **fields: Any,
    ) -> UserAssistantConfigRecord:
        """Частичное обновление. Принимает только whitelisted поля."""
        updates: list[str] = []
        values: list[Any] = []
        for field, value in fields.items():
            if field not in _UPDATABLE_FIELDS:
                raise ValueError(
                    f"Unknown field '{field}' for user_assistant_configs.update"
                )
            placeholder = f"${len(values) + 2}"
            if field == "llm_custom_config":
                updates.append(f"{field} = {placeholder}::jsonb")
                values.append(orjson.dumps(value or {}).decode("utf-8"))
            else:
                updates.append(f"{field} = {placeholder}")
                values.append(value)

        if not updates:
            existing = await self.get_by_user(user_id)
            if existing is None:
                raise AssistantConfigNotFoundError(
                    f"user_assistant_configs user_id={user_id} not found"
                )
            return existing

        updates.append("updated_at = NOW()")
        query = (
            f"UPDATE user_assistant_configs "  # nosec B608 — SQL из внутренних констант, значения через $N-параметры
            f"SET {', '.join(updates)} "
            f"WHERE user_id = $1 "
            f"RETURNING {_USER_CONFIG_COLUMNS}"
        )
        try:
            row = await self._db.fetch_one(query, user_id, *values)
        except asyncpg.PostgresError as exc:
            raise AssistantConfigUnavailableError(
                f"Failed to update user_assistant_configs user_id={user_id}"
            ) from exc
        if row is None:
            raise AssistantConfigNotFoundError(
                f"user_assistant_configs user_id={user_id} not found"
            )
        return _row_to_user_config(row)


def _row_to_user_config(row: Any) -> UserAssistantConfigRecord:
    custom_raw = row["llm_custom_config"]
    if isinstance(custom_raw, str):
        llm_custom_config = orjson.loads(custom_raw)
    else:
        llm_custom_config = dict(custom_raw) if custom_raw else {}

    return UserAssistantConfigRecord(
        user_id=row["user_id"],
        user_instruction=row["user_instruction"],
        llm_custom_config=llm_custom_config,
        updated_at=row["updated_at"],
    )
