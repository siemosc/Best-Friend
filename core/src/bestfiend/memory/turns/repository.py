"""PostgreSQL repository для лога — лента ходов (core.turns)."""

from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg
from loguru import logger
import orjson

from bestfiend.memory.db import MemoryDatabaseClient
from bestfiend.memory.turns.contracts import Turn


class TurnRepository:
    """Доступ к таблице turns (одна строка = один ход)."""

    __slots__ = ("_db",)

    def __init__(self, db: MemoryDatabaseClient) -> None:
        self._db = db

    async def append_turn(
        self,
        *,
        user_id: UUID,
        request_id: str,
        user_message: list[dict[str, Any]],
        react_loop: list[dict[str, Any]],
        ai_message: list[dict[str, Any]],
        token_count_full: int,
        token_count_loop: int,
        created_at: datetime,
    ) -> None:
        """Вставляет ход; повтор по (user_id, request_id) — no-op (идемпотентность)."""
        try:
            await self._db.execute(
                """
                INSERT INTO turns (
                    user_id, request_id, user_message, react_loop, ai_message,
                    token_count_full, token_count_loop, created_at
                ) VALUES (
                    $1, $2, $3::jsonb, $4::jsonb, $5::jsonb, $6, $7, $8
                )
                ON CONFLICT (user_id, request_id) DO NOTHING
                """,
                user_id,
                request_id,
                orjson.dumps(user_message).decode(),
                orjson.dumps(react_loop).decode(),
                orjson.dumps(ai_message).decode(),
                token_count_full,
                token_count_loop,
                created_at,
            )
        except asyncpg.PostgresError:
            logger.exception("TurnRepository: append_turn failed user_id={}", user_id)
            raise

    async def recent_turns(self, user_id: UUID, limit: int) -> list[Turn]:
        """Возвращает последние `limit` ходов пользователя, newest-first."""
        rows = await self._db.fetch(
            """
            SELECT id, user_id, request_id, user_message, react_loop, ai_message,
                   token_count_full, token_count_loop, created_at
            FROM turns
            WHERE user_id = $1
            ORDER BY id DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )
        return [_row_to_turn(row) for row in rows]

    async def turns_after(self, user_id: UUID, after_id: int, limit: int) -> list[Turn]:
        """Ходы после позиции `after_id` (хронологически), не больше `limit`."""
        rows = await self._db.fetch(
            """
            SELECT id, user_id, request_id, user_message, react_loop, ai_message,
                   token_count_full, token_count_loop, created_at
            FROM turns
            WHERE user_id = $1 AND id > $2
            ORDER BY id ASC
            LIMIT $3
            """,
            user_id,
            after_id,
            limit,
        )
        return [_row_to_turn(row) for row in rows]

    async def turns_range(
        self, user_id: UUID, from_id: int, to_id: int, *, cap: int
    ) -> list[Turn]:
        """Ходы диапазона [from_id, to_id] хронологически, не больше `cap` (memory_read_log)."""
        rows = await self._db.fetch(
            """
            SELECT id, user_id, request_id, user_message, react_loop, ai_message,
                   token_count_full, token_count_loop, created_at
            FROM turns
            WHERE user_id = $1 AND id >= $2 AND id <= $3
            ORDER BY id ASC
            LIMIT $4
            """,
            user_id,
            from_id,
            to_id,
            cap,
        )
        return [_row_to_turn(row) for row in rows]

    async def unprocessed_token_sum(self, user_id: UUID, after_id: int) -> int:
        """Сумма token_count_full ходов после позиции `after_id` (триггер Observer)."""
        row = await self._db.fetch_one(
            """
            SELECT COALESCE(SUM(token_count_full), 0) AS total
            FROM turns
            WHERE user_id = $1 AND id > $2
            """,
            user_id,
            after_id,
        )
        return int(row["total"]) if row is not None else 0


def _row_to_turn(row: asyncpg.Record) -> Turn:
    """asyncpg row → Turn."""
    return Turn(
        id=row["id"],
        user_id=row["user_id"],
        request_id=row["request_id"],
        user_message=_as_list(row["user_message"]),
        react_loop=_as_list(row["react_loop"]),
        ai_message=_as_list(row["ai_message"]),
        token_count_full=row["token_count_full"],
        token_count_loop=row["token_count_loop"],
        created_at=row["created_at"],
    )


def _as_list(raw: str | bytes | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """JSONB-колонка → list[dict] (asyncpg отдаёт jsonb строкой)."""
    if isinstance(raw, (str, bytes)):
        return orjson.loads(raw)
    return raw
